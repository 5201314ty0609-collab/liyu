#!/usr/bin/env python3
"""鲤鱼 语义上下文映射器 v1.0 — O2 感知的核心引擎

吸收自 MUNDO Agent v2.2.1 context_mapper.py (LiHongwei-cn)，适配 鲤鱼 架构。

核心升级（相对旧版 token 计数）：
- 旧：简单 token 估算 + 阈值告警
- 新：语义 Chunk 分块 + 优先级淘汰 + 三层压缩策略

集成点：
- Metacog O2 (Vitality): 实时 token 压力 + 分级预警
- intelligent_recovery: 上下文溢出自动触发压缩恢复
- Event Bus: 压缩/淘汰事件发布
- Timeline: 上下文操作记录

设计哲学：
- 用户消息 > assistant 回复 > tool 输出 > 系统消息 > 注入内容
- 新消息 > 旧消息
- 有标记的 > 无标记的
- 压缩 tool 输出优于淘汰对话
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════
# 鲤鱼 路径
# ═══════════════════════════════════════════════

鲤鱼_DIR = Path(__file__).parent
O2_PATH = 鲤鱼_DIR / "senses" / "o2.json"
CONTEXT_LOG_PATH = 鲤鱼_DIR / "context-operations.jsonl"


# ═══════════════════════════════════════════════
# 核心类型
# ═══════════════════════════════════════════════

class ChunkType(IntEnum):
    """消息块类型 — 值越大优先级越低"""
    SYSTEM = 1
    USER = 2
    ASSISTANT = 3
    TOOL_CALL = 4
    TOOL_RESULT = 5
    SUMMARY = 6
    INJECTED = 7
    THINKING = 8          # CTM 思维节点


class EvictionPriority(IntEnum):
    """淘汰优先级 — 值越大越容易被淘汰"""
    KEEP_FOREVER = 0       # system prompt
    KEEP_HIGH = 5          # 用户标记的重要内容
    KEEP_RECENT = 10       # 最近的对话
    COMPRESS_FIRST = 20    # tool 输出，先压缩
    EVICT_FIRST = 30       # 旧的注入内容


@dataclass
class Chunk:
    """上下文块 — 上下文管理的最小单元"""
    content: str
    chunk_type: ChunkType
    token_estimate: int = 0
    priority: EvictionPriority = EvictionPriority.EVICT_FIRST
    turn_id: str = ""
    timestamp: float = field(default_factory=time.time)
    compressed: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.token_estimate == 0:
            self.token_estimate = self._estimate_tokens(self.content)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略 token 估算: 1 token ≈ 2.5 字符（中英混合）"""
        return max(1, int(len(text) * 0.4))


@dataclass
class ContextBudget:
    """上下文预算 — O2 感知的硬约束"""
    max_tokens: int = 128000
    system_reserve: int = 4000       # 系统提示词保留
    response_reserve: int = 8000     # 模型回复保留
    safety_margin: int = 2000        # 安全边界

    @property
    def usable_tokens(self) -> int:
        return max(1000, self.max_tokens - self.system_reserve
                   - self.response_reserve - self.safety_margin)


# ═══════════════════════════════════════════════
# 上下文映射器
# ═══════════════════════════════════════════════

class ContextMapper:
    """语义感知的上下文分块管理器

    O2 (Vitality) 集成:
    - usage_ratio > 70% → O2 warning
    - usage_ratio > 85% → O2 critical → 自动压缩
    - usage_ratio ≤ 20% → O2 主动清理

    CLAUDE.md 阈值:
    - Sprint Mode: >85% 压缩 (当前至 2026-06-24)
    - Normal Mode: >70% 警告, >85% 强制压缩
    """

    # O2 阈值（来自 CLAUDE.md）
    SPRINT_WARN_THRESHOLD = 0.85
    SPRINT_FORCE_THRESHOLD = 0.90
    NORMAL_WARN_THRESHOLD = 0.70
    NORMAL_FORCE_THRESHOLD = 0.85
    CLEAR_THRESHOLD = 0.20       # ≤20% → 主动清理建议

    def __init__(self, budget: ContextBudget | None = None,
                 sprint_mode: bool = True):
        self._budget = budget or ContextBudget()
        self._chunks: list[Chunk] = []
        self.sprint_mode = sprint_mode
        self._compression_count: int = 0
        self._eviction_count: int = 0

    # ── 属性 ──

    @property
    def total_tokens(self) -> int:
        return sum(c.token_estimate for c in self._chunks)

    @property
    def usage_ratio(self) -> float:
        usable = self._budget.usable_tokens
        return self.total_tokens / usable if usable > 0 else 0.0

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def warn_threshold(self) -> float:
        return self.SPRINT_WARN_THRESHOLD if self.sprint_mode else self.NORMAL_WARN_THRESHOLD

    @property
    def force_threshold(self) -> float:
        return self.SPRINT_FORCE_THRESHOLD if self.sprint_mode else self.NORMAL_FORCE_THRESHOLD

    # ── O2 状态 ──

    @property
    def o2_status(self) -> str:
        """O2 (Vitality) 状态

        healthy: ≤70%/85% (normal/sprint)
        warning: >70%/85%
        critical: >85%/90% → 必须压缩
        clearing: ≤20% → 可主动清理
        """
        if self.usage_ratio <= self.CLEAR_THRESHOLD:
            return "clearing"
        elif self.usage_ratio > self.force_threshold:
            return "critical"
        elif self.usage_ratio > self.warn_threshold:
            return "warning"
        return "healthy"

    @property
    def should_warn(self) -> bool:
        return self.o2_status == "warning"

    @property
    def should_force_compress(self) -> bool:
        return self.o2_status == "critical"

    @property
    def should_proactive_clear(self) -> bool:
        return self.o2_status == "clearing"

    # ── 添加 Chunk ──

    def add(self, content: str, chunk_type: ChunkType,
            priority: EvictionPriority = EvictionPriority.EVICT_FIRST,
            turn_id: str = "", metadata: dict | None = None) -> Chunk:
        chunk = Chunk(
            content=content,
            chunk_type=chunk_type,
            priority=priority,
            turn_id=turn_id,
            metadata=metadata or {},
        )
        self._chunks.append(chunk)
        return chunk

    def add_system(self, content: str) -> Chunk:
        return self.add(content, ChunkType.SYSTEM, EvictionPriority.KEEP_FOREVER)

    def add_user(self, content: str, turn_id: str = "") -> Chunk:
        return self.add(content, ChunkType.USER, EvictionPriority.KEEP_RECENT, turn_id)

    def add_assistant(self, content: str, turn_id: str = "") -> Chunk:
        return self.add(content, ChunkType.ASSISTANT, EvictionPriority.KEEP_RECENT, turn_id)

    def add_tool_result(self, content: str, tool_name: str = "",
                        turn_id: str = "") -> Chunk:
        return self.add(content, ChunkType.TOOL_RESULT,
                        EvictionPriority.COMPRESS_FIRST, turn_id,
                        {"tool": tool_name})

    def add_tool_call(self, content: str, tool_name: str = "",
                      turn_id: str = "") -> Chunk:
        return self.add(content, ChunkType.TOOL_CALL,
                        EvictionPriority.KEEP_RECENT, turn_id,
                        {"tool": tool_name})

    def inject(self, content: str, source: str = "",
               priority: EvictionPriority = EvictionPriority.EVICT_FIRST) -> Chunk:
        """注入外部上下文（记忆/项目规则等）"""
        return self.add(content, ChunkType.INJECTED, priority,
                        metadata={"source": source})

    def add_thinking_node(self, content: str, stream_id: str = "",
                          depth: int = 0, confidence: float = 0.5,
                          thinking_state: str = "flowing",
                          turn_id: str = "") -> Chunk:
        """添加 CTM 思维节点到上下文

        Args:
            content: 思维内容
            stream_id: CTM 思维流 ID
            depth: 思维深度 (0=表层, >3=深层)
            confidence: 置信度 (0-1)
            thinking_state: 思维状态 (init/flowing/deepening/converging/completed)
            turn_id: 关联的对话轮次
        """
        # 深层思维优先级更高（不容易被淘汰）
        if depth >= 3:
            priority = EvictionPriority.KEEP_HIGH
        elif confidence >= 0.8:
            priority = EvictionPriority.KEEP_RECENT
        else:
            priority = EvictionPriority.COMPRESS_FIRST

        return self.add(content, ChunkType.THINKING, priority, turn_id,
                        metadata={"stream_id": stream_id, "depth": depth,
                                  "confidence": confidence,
                                  "thinking_state": thinking_state,
                                  "source": "ctm"})

    # ── 压缩策略 ──

    def compress(self, target_ratio: float = 0.5) -> tuple[int, int]:
        """三层智能压缩

        策略 1: 压缩 tool 输出（头尾保留，中间省略）
        策略 2: 淘汰旧的注入内容
        策略 3: 摘要旧对话

        Returns: (压缩前tokens, 压缩后tokens)
        """
        old_tokens = self.total_tokens
        target_tokens = int(self._budget.usable_tokens * target_ratio)

        if self.total_tokens <= target_tokens:
            return old_tokens, self.total_tokens

        # 策略 1: 压缩 tool 输出
        for chunk in self._chunks:
            if chunk.compressed:
                continue
            if chunk.chunk_type == ChunkType.TOOL_RESULT and chunk.token_estimate > 200:
                original = chunk.content
                chunk.content = (
                    original[:200]
                    + f"\n... ({len(original)} 字符，已压缩) ...\n"
                    + original[-100:]
                )
                chunk.token_estimate = Chunk._estimate_tokens(chunk.content)
                chunk.compressed = True
                chunk.priority = EvictionPriority.EVICT_FIRST
                self._compression_count += 1
                if self.total_tokens <= target_tokens:
                    self._log_operation("compress_tool_outputs", old_tokens, self.total_tokens)
                    return old_tokens, self.total_tokens

        # 策略 2: 淘汰旧的注入内容
        before = len(self._chunks)
        self._chunks = [
            c for c in self._chunks
            if not (c.chunk_type == ChunkType.INJECTED
                    and c.priority >= EvictionPriority.EVICT_FIRST)
        ]
        self._eviction_count += before - len(self._chunks)
        if self.total_tokens <= target_tokens:
            self._log_operation("evict_injected", old_tokens, self.total_tokens)
            return old_tokens, self.total_tokens

        # 策略 3: 摘要旧对话
        recent = self._get_recent(8)
        old = [c for c in self._chunks if c not in recent]
        if old:
            parts = []
            for c in old:
                if c.chunk_type in (ChunkType.USER, ChunkType.ASSISTANT):
                    parts.append(f"[{c.chunk_type.name}] {c.content[:80]}")
                elif c.chunk_type == ChunkType.THINKING:
                    depth = c.metadata.get("depth", 0)
                    parts.append(f"[THINKING d={depth}] {c.content[:60]}")
            if parts:
                summary = " | ".join(parts[-10:])
                summary_chunk = Chunk(
                    content=f"[历史摘要] {summary[:600]}",
                    chunk_type=ChunkType.SUMMARY,
                    priority=EvictionPriority.EVICT_FIRST,
                )
                self._chunks = [summary_chunk] + recent
                self._compression_count += 1

        self._log_operation("summarize_old_dialogue", old_tokens, self.total_tokens)
        return old_tokens, self.total_tokens

    def auto_compress(self) -> bool:
        """自动压缩 — O2 触发时调用

        Returns: True if compression was performed
        """
        if not self.should_force_compress:
            return False

        target = 0.5 if self.o2_status == "critical" else 0.65
        old, new = self.compress(target_ratio=target)
        return old != new

    # ── 消息转换 ──

    def to_messages(self) -> list[dict[str, str]]:
        """转换为 LLM 消息格式"""
        role_map = {
            ChunkType.SYSTEM: "system",
            ChunkType.USER: "user",
            ChunkType.ASSISTANT: "assistant",
            ChunkType.TOOL_RESULT: "tool",
            ChunkType.TOOL_CALL: "assistant",
            ChunkType.SUMMARY: "system",
            ChunkType.INJECTED: "system",
            ChunkType.THINKING: "assistant",
        }
        return [
            {"role": role_map.get(c.chunk_type, "system"), "content": c.content}
            for c in self._chunks
        ]

    # ── O2 报告 ──

    def get_o2_report(self) -> dict:
        """生成 O2 (Vitality) 感知报告"""
        status = self.o2_status
        recommendations = []

        if status == "critical":
            recommendations.append("FORCE COMPACT: Context usage critical, compress immediately")
        elif status == "warning":
            recommendations.append("WARN: Context usage elevated, consider compression")
        elif status == "clearing":
            recommendations.append("Proactive clear: Context is light, good time for cleanup")

        return {
            "status": status,
            "last_updated": time.time(),
            "metrics": {
                "estimated_tokens": self.total_tokens,
                "context_limit": self._budget.max_tokens,
                "usage_percent": round(self.usage_ratio * 100, 1),
                "chunk_count": self.chunk_count,
                "compression_count": self._compression_count,
                "eviction_count": self._eviction_count,
            },
            "warnings": recommendations,
            "recommendation": (
                "force_compact" if status == "critical"
                else "warn" if status == "warning"
                else "proactive_clear" if status == "clearing"
                else "continue"
            ),
            "trend": "stable",  # 需要多点数据才能计算趋势
        }

    # ── 快照 ──

    def snapshot(self) -> dict:
        """上下文快照 — 用于调试和监控"""
        by_type: dict[str, dict] = {}
        for c in self._chunks:
            t = c.chunk_type.name
            if t not in by_type:
                by_type[t] = {"count": 0, "tokens": 0, "compressed": 0}
            by_type[t]["count"] += 1
            by_type[t]["tokens"] += c.token_estimate
            if c.compressed:
                by_type[t]["compressed"] += 1

        return {
            "total_chunks": len(self._chunks),
            "total_tokens": self.total_tokens,
            "usage_ratio": f"{self.usage_ratio:.1%}",
            "budget": {
                "max": self._budget.max_tokens,
                "usable": self._budget.usable_tokens,
            },
            "o2_status": self.o2_status,
            "by_type": by_type,
            "compression_total": self._compression_count,
            "eviction_total": self._eviction_count,
        }

    # ── 内部 ──

    def _get_recent(self, count: int) -> list[Chunk]:
        return self._chunks[-count:] if len(self._chunks) > count else list(self._chunks)

    def _log_operation(self, op: str, before: int, after: int):
        try:
            entry = {
                "timestamp": time.time(),
                "operation": op,
                "tokens_before": before,
                "tokens_after": after,
                "reduction": before - after,
                "chunks_before": self.chunk_count,
            }
            with open(CONTEXT_LOG_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def reset(self):
        self._chunks.clear()
        self._compression_count = 0
        self._eviction_count = 0


# ═══════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════

_mapper: Optional[ContextMapper] = None


def get_context_mapper(sprint_mode: bool = True) -> ContextMapper:
    """获取上下文映射器单例"""
    global _mapper
    if _mapper is None:
        _mapper = ContextMapper(sprint_mode=sprint_mode)
    return _mapper


# ═══════════════════════════════════════════════
# CLI 演示
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    mapper = ContextMapper(sprint_mode=True)

    # 模拟对话
    mapper.add_system("You are a helpful AI assistant. 系统提示词...")
    for i in range(10):
        mapper.add_user(f"用户问题 {i}: " + "这是一个测试问题。" * 20, turn_id=f"turn_{i}")
        mapper.add_assistant(f"助手回复 {i}: " + "这是助手的回复内容。" * 30, turn_id=f"turn_{i}")
        mapper.add_tool_result("文件内容: " + "data " * 200, tool_name="read_file", turn_id=f"turn_{i}")

    print("=" * 60)
    print("鲤鱼 Context Mapper — O2 Vitality Demo")
    print("=" * 60)

    print(f"\n  总 Tokens: {mapper.total_tokens}")
    print(f"  使用率: {mapper.usage_ratio:.1%}")
    print(f"  O2 状态: {mapper.o2_status}")
    print(f"  Chunk 数: {mapper.chunk_count}")

    if mapper.should_force_compress:
        print("\n  ⚠️  触发自动压缩...")
        old, new = mapper.compress(target_ratio=0.5)
        print(f"  Tokens: {old} → {new} (减少 {old - new})")
        print(f"  O2 状态: {mapper.o2_status}")

    print(f"\n快照:\n{json.dumps(mapper.snapshot(), indent=2, ensure_ascii=False)}")
