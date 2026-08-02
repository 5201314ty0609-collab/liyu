#!/usr/bin/env python3
"""鲤鱼 智能错误恢复 v1.0 — 痛觉的智慧

吸收自 MUNDO Agent v2.2.3 intelligent_recovery.py (LiHongwei-cn)，适配 鲤鱼 七感架构。

核心升级（相对旧 heal-rules.json）：
- 旧：7条静态正则规则，无分类，无自适应
- 新：6大错误类别 + 9种恢复策略 + 置信度打分 + 熔断器模式

集成点：
- Metacog Nociception（痛觉）— 错误级联检测 + 自适应恢复
- Event Bus — 错误事件发布/订阅
- Timeline — 恢复历史记录
- Tool Guard — 工具级熔断器
- Policy Engine — 恢复策略审计

知识来源：
- Circuit Breaker Pattern (Nygard, 2018)
- Retry with Exponential Backoff + Jitter
- Graceful Degradation
- Bulkhead Pattern
"""

from __future__ import annotations

import re
import time
import json
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

# ═══════════════════════════════════════════════
# 鲤鱼 路径
# ═══════════════════════════════════════════════

鲤鱼_DIR = Path(__file__).parent
HEAL_RULES_PATH = 鲤鱼_DIR / "heal-rules.json"
RECOVERY_LOG_PATH = 鲤鱼_DIR / "recovery-history.jsonl"
NOCICEPTION_PATH = 鲤鱼_DIR / "senses" / "nociception.json"


# ═══════════════════════════════════════════════
# 错误分类系统
# ═══════════════════════════════════════════════

class ErrorCategory(Enum):
    """六类错误 — 覆盖所有已知失败模式"""
    TRANSIENT = "transient"       # 瞬时错误（超时、限流、503）
    RESOURCE = "resource"         # 资源不足（内存、token、配额）
    PERMISSION = "permission"     # 权限不足（401/403）
    NETWORK = "network"           # 网络问题（DNS、连接中断）
    LOGIC = "logic"               # 逻辑错误（工具失败、参数错误）
    CONTEXT = "context"           # 上下文问题（溢出、截断）
    UNKNOWN = "unknown"           # 兜底


class RecoveryStrategy(Enum):
    """九种恢复策略 — 每种对应一类错误的黄金路径"""
    RETRY_IMMEDIATE = "retry_immediate"       # 立即重试（轻微瞬时错误）
    RETRY_BACKOFF = "retry_backoff"           # 指数退避重试（限流/服务不可用）
    RETRY_WITH_VARIATION = "retry_variation"   # 改变参数重试（逻辑错误）
    SWITCH_ENDPOINT = "switch_endpoint"        # 切换 API 端点（网络问题）
    SWITCH_MODEL = "switch_model"              # 🆕 切换模型（鲤鱼特有）
    COMPRESS_CONTEXT = "compress_context"      # 压缩上下文（溢出）
    DEGRADE_QUALITY = "degrade_quality"        # 降级质量（资源不足）
    ASK_USER = "ask_user"                      # 询问用户（权限/关键错误）
    SWITCH_TOOL = "switch_tool"                # 切换工具（逻辑错误）
    ABORT = "abort"                            # 中止任务（不可恢复）


# ═══════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════

@dataclass
class RecoveryPlan:
    """恢复计划 — 针对一次错误的完整恢复方案"""
    strategy: RecoveryStrategy
    max_attempts: int
    backoff_base: float       # 指数退避基数（秒）
    backoff_max: float        # 退避上限（秒）
    jitter: bool              # 是否加随机抖动
    fallback_strategy: Optional[RecoveryStrategy] = None
    parameters: dict = field(default_factory=dict)

    @property
    def is_retryable(self) -> bool:
        return self.strategy in (
            RecoveryStrategy.RETRY_IMMEDIATE,
            RecoveryStrategy.RETRY_BACKOFF,
            RecoveryStrategy.RETRY_WITH_VARIATION,
        )

    @property
    def is_fatal(self) -> bool:
        return self.strategy == RecoveryStrategy.ABORT


@dataclass
class ErrorRecord:
    """错误记录 — 每次错误和恢复的完整追踪"""
    timestamp: float = field(default_factory=time.time)
    error_type: str = ""
    error_message: str = ""
    category: ErrorCategory = ErrorCategory.UNKNOWN
    confidence: float = 0.0
    tool_name: str = ""
    args_hash: str = ""
    attempt: int = 0
    recovery_strategy: Optional[RecoveryStrategy] = None
    recovery_success: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "error_type": self.error_type,
            "error_message": self.error_message[:500],
            "category": self.category.value,
            "confidence": self.confidence,
            "tool_name": self.tool_name,
            "args_hash": self.args_hash,
            "attempt": self.attempt,
            "recovery_strategy": self.recovery_strategy.value if self.recovery_strategy else None,
            "recovery_success": self.recovery_success,
            "duration_ms": self.duration_ms,
        }


# ═══════════════════════════════════════════════
# 错误分类器
# ═══════════════════════════════════════════════

class ErrorClassifier:
    """智能错误分类器 — 不是简单正则匹配，是语义级分类

    MUNDO 精华: 每个正则模式带 base_confidence，多模式竞争取最高分。
    """

    ERROR_PATTERNS: dict[ErrorCategory, list[tuple[str, float]]] = {
        ErrorCategory.TRANSIENT: [
            (r"timeout", 0.90),
            (r"timed?\s*out", 0.90),
            (r"temporary", 0.80),
            (r"retry", 0.70),
            (r"429", 0.95),         # Rate limit
            (r"rate\s*limit", 0.95),
            (r"503", 0.90),         # Service unavailable
            (r"502", 0.85),         # Bad gateway
            (r"504", 0.85),         # Gateway timeout
            (r"internal\s*server", 0.85),
            (r"超时", 0.90),
            (r"服务暂不可用", 0.90),
            (r"请求过于频繁", 0.95),
        ],
        ErrorCategory.RESOURCE: [
            (r"out\s+of\s+memory", 0.95),
            (r"quota\s+exceeded", 0.90),
            (r"token\s+limit", 0.90),
            (r"too\s+many\s+tokens", 0.90),
            (r"413", 0.90),         # Payload too large
            (r"context\s+(length|window)", 0.85),
            (r"内存不足", 0.95),
            (r"配额已用", 0.90),
        ],
        ErrorCategory.PERMISSION: [
            (r"permission\s+denied", 0.95),
            (r"access\s+denied", 0.95),
            (r"forbidden", 0.90),
            (r"401", 0.95),         # Unauthorized
            (r"403", 0.95),         # Forbidden
            (r"unauthorized", 0.90),
            (r"invalid\s+api\s+key", 0.95),
            (r"EACCES", 0.90),
            (r"not\s+permitted", 0.85),
            (r"权限", 0.90),
            (r"无权", 0.90),
        ],
        ErrorCategory.NETWORK: [
            (r"connection\s+(refused|reset|error)", 0.95),
            (r"ECONNREFUSED", 0.95),
            (r"ECONNRESET", 0.90),
            (r"dns\s+resolution", 0.90),
            (r"network\s+unreachable", 0.95),
            (r"broken\s+pipe", 0.90),
            (r"EPIPE", 0.90),
            (r"eof", 0.85),
            (r"ssl", 0.80),
            (r"TLS", 0.80),
            (r"远程主机强迫关闭", 0.95),
            (r"连接被拒绝", 0.95),
        ],
        ErrorCategory.LOGIC: [
            (r"not\s+found", 0.80),
            (r"file\s+not\s+found", 0.90),
            (r"no\s+such\s+file", 0.90),
            (r"ENOENT", 0.90),
            (r"invalid\s+(argument|parameter)", 0.85),
            (r"type\s+error", 0.85),
            (r"TypeError", 0.85),
            (r"value\s+error", 0.85),
            (r"ValueError", 0.85),
            (r"key\s+error", 0.85),
            (r"KeyError", 0.85),
            (r"assertion\s+error", 0.80),
            (r"AssertionError", 0.80),
            (r"找不到文件", 0.90),
            (r"参数.*无效", 0.85),
        ],
        ErrorCategory.CONTEXT: [
            (r"context\s+(length|window)\s+exceeded", 0.95),
            (r"message\s+too\s+long", 0.90),
            (r"prompt\s+is\s+too\s+long", 0.95),
            (r"maximum\s+context", 0.90),
            (r"上下文.*溢出", 0.95),
            (r"token.*超出", 0.90),
        ],
    }

    def classify(self, error: Exception | str) -> tuple[ErrorCategory, float]:
        """分类错误 → (类别, 置信度)

        支持传入 Exception 对象或错误消息字符串。
        """
        if isinstance(error, Exception):
            msg = str(error).lower()
            error_type = type(error).__name__.lower()
        else:
            msg = str(error).lower()
            error_type = ""

        best_category = ErrorCategory.UNKNOWN
        best_confidence = 0.0

        for category, patterns in self.ERROR_PATTERNS.items():
            for pattern, base_confidence in patterns:
                if re.search(pattern, msg) or (error_type and re.search(pattern, error_type)):
                    if base_confidence > best_confidence:
                        best_category = category
                        best_confidence = base_confidence

        return best_category, best_confidence


# ═══════════════════════════════════════════════
# 恢复策略选择器
# ═══════════════════════════════════════════════

class RecoveryStrategySelector:
    """恢复策略选择器 — 错误类别 → 黄金恢复路径

    每条路径有 max_attempts 和 fallback。fallback 在所有尝试耗尽后触发。
    """

    STRATEGY_MAP: dict[ErrorCategory, list[RecoveryPlan]] = {
        ErrorCategory.TRANSIENT: [
            RecoveryPlan(
                strategy=RecoveryStrategy.RETRY_IMMEDIATE,
                max_attempts=1, backoff_base=0, backoff_max=0, jitter=False,
            ),
            RecoveryPlan(
                strategy=RecoveryStrategy.RETRY_BACKOFF,
                max_attempts=3, backoff_base=1.0, backoff_max=30.0, jitter=True,
                fallback_strategy=RecoveryStrategy.SWITCH_ENDPOINT,
            ),
        ],
        ErrorCategory.RESOURCE: [
            RecoveryPlan(
                strategy=RecoveryStrategy.COMPRESS_CONTEXT,
                max_attempts=2, backoff_base=0, backoff_max=0, jitter=False,
                fallback_strategy=RecoveryStrategy.DEGRADE_QUALITY,
            ),
        ],
        ErrorCategory.PERMISSION: [
            RecoveryPlan(
                strategy=RecoveryStrategy.ASK_USER,
                max_attempts=1, backoff_base=0, backoff_max=0, jitter=False,
            ),
        ],
        ErrorCategory.NETWORK: [
            RecoveryPlan(
                strategy=RecoveryStrategy.SWITCH_ENDPOINT,
                max_attempts=2, backoff_base=2.0, backoff_max=30.0, jitter=True,
                fallback_strategy=RecoveryStrategy.RETRY_BACKOFF,
            ),
        ],
        ErrorCategory.LOGIC: [
            RecoveryPlan(
                strategy=RecoveryStrategy.SWITCH_TOOL,
                max_attempts=2, backoff_base=0, backoff_max=0, jitter=False,
                fallback_strategy=RecoveryStrategy.RETRY_WITH_VARIATION,
            ),
        ],
        ErrorCategory.CONTEXT: [
            RecoveryPlan(
                strategy=RecoveryStrategy.COMPRESS_CONTEXT,
                max_attempts=3, backoff_base=0, backoff_max=0, jitter=False,
                fallback_strategy=RecoveryStrategy.DEGRADE_QUALITY,
            ),
        ],
        ErrorCategory.UNKNOWN: [
            RecoveryPlan(
                strategy=RecoveryStrategy.RETRY_BACKOFF,
                max_attempts=2, backoff_base=2.0, backoff_max=15.0, jitter=True,
                fallback_strategy=RecoveryStrategy.ABORT,
            ),
        ],
    }

    def select(self, category: ErrorCategory, attempt: int) -> RecoveryPlan:
        """根据错误类别和当前尝试次数选择恢复策略"""
        plans = self.STRATEGY_MAP.get(category, self.STRATEGY_MAP[ErrorCategory.UNKNOWN])

        # 累积 max_attempts，找到当前 attempt 对应的 plan
        cumulative = 0
        for plan in plans:
            cumulative += plan.max_attempts
            if attempt < cumulative:
                return plan

        # 所有策略耗尽 → 使用最后一个的 fallback
        last_plan = plans[-1]
        if last_plan.fallback_strategy:
            return RecoveryPlan(
                strategy=last_plan.fallback_strategy,
                max_attempts=1, backoff_base=0, backoff_max=0, jitter=False,
            )

        return RecoveryPlan(
            strategy=RecoveryStrategy.ABORT,
            max_attempts=0, backoff_base=0, backoff_max=0, jitter=False,
        )


# ═══════════════════════════════════════════════
# 熔断器 (Circuit Breaker)
# ═══════════════════════════════════════════════

class CircuitBreaker:
    """熔断器 — 防止级联故障

    三态: CLOSED(正常) → OPEN(熔断) → HALF_OPEN(半开探测)
    """

    class State(Enum):
        CLOSED = "closed"         # 正常通行
        OPEN = "open"             # 熔断，拒绝请求
        HALF_OPEN = "half_open"   # 半开，允许探测请求

    def __init__(self, name: str,
                 failure_threshold: int = 5,
                 recovery_timeout: float = 60.0,
                 half_open_max: int = 2):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self.state = self.State.CLOSED
        self.failure_count: int = 0
        self.success_count: int = 0
        self.last_failure_time: float = 0.0
        self.last_state_change: float = time.time()
        self.half_open_attempts: int = 0

    def call(self, fn: Callable, *args, **kwargs) -> Any:
        """通过熔断器调用函数"""
        if self.state == self.State.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self._transition_to(self.State.HALF_OPEN)
            else:
                raise CircuitBreakerOpenError(
                    f"熔断器 [{self.name}] 处于 OPEN 状态，"
                    f"{self.recovery_timeout - (time.time() - self.last_failure_time):.0f}s 后重试"
                )

        if self.state == self.State.HALF_OPEN:
            if self.half_open_attempts >= self.half_open_max:
                self._transition_to(self.State.OPEN)
                raise CircuitBreakerOpenError(
                    f"熔断器 [{self.name}] 半开探测失败，重新熔断"
                )

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _on_success(self):
        if self.state == self.State.HALF_OPEN:
            self.half_open_attempts = 0
            self._transition_to(self.State.CLOSED)
        self.failure_count = 0

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == self.State.HALF_OPEN:
            self.half_open_attempts += 1
            if self.half_open_attempts >= self.half_open_max:
                self._transition_to(self.State.OPEN)
        elif self.failure_count >= self.failure_threshold:
            self._transition_to(self.State.OPEN)

    def _transition_to(self, new_state: State):
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        self._log_state_change(old_state, new_state)

    def _log_state_change(self, old: State, new: State):
        entry = {
            "timestamp": time.time(),
            "breaker": self.name,
            "old_state": old.value,
            "new_state": new.value,
            "failure_count": self.failure_count,
        }
        try:
            with open(RECOVERY_LOG_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def reset(self):
        self._transition_to(self.State.CLOSED)
        self.failure_count = 0
        self.half_open_attempts = 0


class CircuitBreakerOpenError(Exception):
    """熔断器打开时抛出的异常"""
    pass


# ═══════════════════════════════════════════════
# 智能恢复引擎
# ═══════════════════════════════════════════════

class IntelligentRecovery:
    """鲤鱼 智能错误恢复引擎

    不是简单重试。是根据错误类型智能选择恢复策略。
    集成 Nociception（痛觉）感知 — 级联错误自动熔断。
    """

    def __init__(self):
        self._classifier = ErrorClassifier()
        self._strategy_selector = RecoveryStrategySelector()
        self._error_history: list[ErrorRecord] = []
        self._breakers: dict[str, CircuitBreaker] = {}
        self._category_counts: dict[ErrorCategory, int] = {
            cat: 0 for cat in ErrorCategory
        }
        self._strategy_success: dict[RecoveryStrategy, tuple[int, int]] = {
            s: (0, 0) for s in RecoveryStrategy
        }
        self._cascade_window: list[float] = []  # 错误级联检测窗口

    # ── 公共 API ──

    def analyze(self, error: Exception | str) -> tuple[ErrorCategory, float]:
        """分析错误 → (类别, 置信度)"""
        return self._classifier.classify(error)

    def classify_and_plan(self, error: Exception | str, tool_name: str = "",
                          attempt: int = 0, args: dict | None = None) -> tuple[
                              ErrorCategory, float, RecoveryPlan]:
        """一站式：分类 + 获取恢复计划"""
        category, confidence = self._classifier.classify(error)
        plan = self._strategy_selector.select(category, attempt)
        return category, confidence, plan

    def calculate_delay(self, plan: RecoveryPlan, attempt: int) -> float:
        """计算重试延迟（指数退避 + 随机抖动）"""
        if plan.backoff_base == 0:
            return 0.0

        delay = min(plan.backoff_base * (2 ** attempt), plan.backoff_max)

        if plan.jitter:
            delay *= (0.5 + random.random() * 0.5)

        return delay

    def record(self, record: ErrorRecord):
        """记录一次错误恢复"""
        self._error_history.append(record)
        self._category_counts[record.category] += 1

        if record.recovery_strategy:
            success, total = self._strategy_success[record.recovery_strategy]
            self._strategy_success[record.recovery_strategy] = (
                success + (1 if record.recovery_success else 0),
                total + 1,
            )

        # 痛觉：记录错误时间戳用于级联检测
        self._cascade_window.append(time.time())
        self._prune_cascade_window()

        # 持久化
        self._persist_record(record)

    def get_breaker(self, tool_name: str, **kwargs) -> CircuitBreaker:
        """获取或创建工具级熔断器"""
        if tool_name not in self._breakers:
            self._breakers[tool_name] = CircuitBreaker(name=tool_name, **kwargs)
        return self._breakers[tool_name]

    # ── Nociception 痛觉集成 ──

    @property
    def cascade_count(self) -> int:
        """最近 5 分钟内的错误数（级联检测）"""
        self._prune_cascade_window()
        return len(self._cascade_window)

    @property
    def pain_level(self) -> str:
        """痛觉等级 — 对应 Nociception 感知状态

        healthy:    <2 错误/5min
        warning:    2-4 错误/5min
        critical:   >=5 错误/5min → 触发级联分析
        """
        count = self.cascade_count
        if count < 2:
            return "healthy"
        elif count < 5:
            return "warning"
        return "critical"

    @property
    def should_analyze_root_cause(self) -> bool:
        """是否应该暂停并分析根因 — Nociception 核心判断"""
        return self.cascade_count >= 5

    def should_escalate(self, tool_name: str) -> bool:
        """判断是否需要升级处理（告警用户）"""
        recent_tool = [
            r for r in self._error_history[-10:]
            if r.tool_name == tool_name
        ]
        # 同一工具连续失败 5 次 → 升级
        if len(recent_tool) >= 5:
            return True
        # 连续权限错误 → 升级
        if any(r.category == ErrorCategory.PERMISSION for r in recent_tool[-3:]):
            return True
        return False

    # ── 统计 ──

    def get_strategy_effectiveness(self) -> dict[str, float]:
        """各策略有效率"""
        return {
            s.value: success / total if total > 0 else 0.0
            for s, (success, total) in self._strategy_success.items()
        }

    def get_error_summary(self) -> dict:
        """错误摘要"""
        return {
            "total_errors": len(self._error_history),
            "by_category": {c.value: n for c, n in self._category_counts.items()},
            "strategy_effectiveness": self.get_strategy_effectiveness(),
            "pain_level": self.pain_level,
            "cascade_count_5min": self.cascade_count,
        }

    def get_nociception_report(self) -> dict:
        """生成 Nociception 感知报告"""
        return {
            "status": "critical" if self.should_analyze_root_cause else (
                "warning" if self.pain_level == "warning" else "healthy"
            ),
            "last_updated": time.time(),
            "metrics": {
                "errors_in_window": self.cascade_count,
                "window_minutes": 5,
                "unique_categories": len([
                    c for c, n in self._category_counts.items() if n > 0
                ]),
                "top_category": max(
                    self._category_counts, key=self._category_counts.get
                ).value if self._error_history else None,
            },
            "warnings": self._generate_warnings(),
            "recommendation": self._generate_recommendation(),
        }

    def reset(self):
        """重置所有状态"""
        self._error_history.clear()
        self._breakers.clear()
        self._cascade_window.clear()
        for cat in self._category_counts:
            self._category_counts[cat] = 0
        for s in self._strategy_success:
            self._strategy_success[s] = (0, 0)

    # ── 内部方法 ──

    def _prune_cascade_window(self):
        """清理级联窗口 — 只保留最近 5 分钟"""
        cutoff = time.time() - 300
        self._cascade_window = [t for t in self._cascade_window if t > cutoff]

    def _generate_warnings(self) -> list[str]:
        warnings = []
        count = self.cascade_count
        if count >= 3:
            warnings.append(f"{count} errors in last 5 minutes")
        if count >= 5:
            # 检查是否有重复错误
            recent_msgs = [r.error_message[:100] for r in self._error_history[-count:]]
            unique = len(set(recent_msgs))
            if unique < count // 2:
                warnings.append(f"Possible error loop: {unique} unique errors out of {count}")
        return warnings

    def _generate_recommendation(self) -> str:
        count = self.cascade_count
        if count >= 5:
            return "STOP and analyze root cause. Consider changing approach or asking user."
        elif count >= 3:
            return "Notice elevated error rate. Review recent failures."
        return "All clear."

    def _persist_record(self, record: ErrorRecord):
        try:
            with open(RECOVERY_LOG_PATH, "a") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════
# 上下文压缩器
# ═══════════════════════════════════════════════

class ContextCompressor:
    """上下文压缩器 — 处理上下文溢出

    不是简单截断。是语义感知的压缩：
    1. 保留系统消息
    2. 压缩 tool 输出（头尾保留，中间省略）
    3. 保留最近 N 轮对话
    4. 早期消息生成摘要
    """

    def __init__(self, max_tool_output: int = 2000, keep_recent: int = 8):
        self.max_tool_output = max_tool_output
        self.keep_recent = keep_recent
        self._compression_count = 0

    def compress_tool_output(self, output: str) -> str:
        """压缩单条工具输出"""
        if len(output) <= self.max_tool_output:
            return output
        half = self.max_tool_output // 2
        self._compression_count += 1
        return (
            output[:half]
            + f"\n... [省略 {len(output) - self.max_tool_output} 字符] ...\n"
            + output[-half:]
        )

    def compress_messages(self, messages: list[dict],
                          target_ratio: float = 0.7) -> list[dict]:
        """压缩消息列表 — 语义感知淘汰"""
        if len(messages) <= 4:
            return messages

        self._compression_count += 1
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= 4:
            return messages

        keep = max(4, int(len(non_system) * target_ratio))
        recent = non_system[-keep:]
        early = non_system[:-keep]
        summary = self._summarize(early)

        return system_msgs + [
            {"role": "system", "content": f"[上下文压缩摘要] {summary}"}
        ] + recent

    def truncate_content(self, content: str, max_tokens: int = 4000) -> str:
        """按 token 估算截断内容"""
        # 粗略估算: 1 token ≈ 2 中文字符或 4 英文字符
        estimated = len(content) // 2
        if estimated <= max_tokens:
            return content

        ratio = max_tokens / estimated
        target = int(len(content) * ratio)
        keep_start = int(target * 0.7)
        keep_end = target - keep_start

        self._compression_count += 1
        return content[:keep_start] + "\n...(已截断)...\n" + content[-keep_end:]

    def _summarize(self, messages: list[dict]) -> str:
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                parts.append(content[:100])
        return " | ".join(parts[-10:]) if parts else "(无内容)"

    @property
    def compression_count(self) -> int:
        return self._compression_count


# ═══════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════

_recovery: Optional[IntelligentRecovery] = None
_compressor: Optional[ContextCompressor] = None


def get_recovery() -> IntelligentRecovery:
    """获取智能恢复引擎单例"""
    global _recovery
    if _recovery is None:
        _recovery = IntelligentRecovery()
    return _recovery


def get_compressor() -> ContextCompressor:
    """获取上下文压缩器单例"""
    global _compressor
    if _compressor is None:
        _compressor = ContextCompressor()
    return _compressor


# ═══════════════════════════════════════════════
# CLI 健康检查
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    recovery = get_recovery()

    # 演示：分类各种错误
    test_errors = [
        "Request timeout after 30 seconds",
        "Connection refused by remote host",
        "Permission denied: cannot write to /etc/config",
        "Context window exceeded: 150000 tokens used",
        "File not found: /tmp/nonexistent.txt",
        "Rate limit exceeded: 429 Too Many Requests",
        "Out of memory: cannot allocate 2GB",
        "invalid api key: 401 Unauthorized",
    ]

    print("=" * 60)
    print("鲤鱼 Intelligent Recovery — Error Classification Demo")
    print("=" * 60)

    for err in test_errors:
        category, confidence = recovery.analyze(err)
        plan = recovery._strategy_selector.select(category, attempt=0)
        print(f"\n  Error : {err}")
        print(f"  Category : {category.value} ({confidence:.0%})")
        print(f"  Strategy: {plan.strategy.value}")

    print(f"\n{'=' * 60}")
    print(f"Nociception Report: {json.dumps(recovery.get_nociception_report(), indent=2, ensure_ascii=False)}")
