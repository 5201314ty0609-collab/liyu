#!/usr/bin/env python3
"""鲤鱼 运行时配置系统 v1.0 — 五层声明式配置

吸收自 MUNDO Agent v2.1.1 runtime_config.py (LiHongwei-cn)，适配 鲤鱼 七感架构。

设计哲学：
- 配置是分层的：默认 → 全局 → 项目 → 会话 → 运行时
- 高层覆盖低层（类似 CSS 特异性）
- 类型安全：dataclass + 类型注解
- 热重载：文件变更自动感知
- 所有行为都可通过配置覆盖，不需要改代码

层级说明：
  L1 DEFAULT   — 硬编码默认值（dataclass 默认字段）
  L2 GLOBAL    — ~/.claude/liyu/config/settings.json
  L3 PROJECT   — <project>/.liyu/config.json
  L4 SESSION   — 环境变量 鲤鱼_*
  L5 RUNTIME   — ConfigManager.set() 运行时覆盖

集成点：
- settings.json (Claude Code harness config)
- context_mapper (上下文预算)
- intelligent_recovery (恢复策略参数)
- Metacog 7-sense (各感知阈值)
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# ═══════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════

鲤鱼_DIR = Path.home() / ".claude" / "liyu"
GLOBAL_CONFIG_DIR = 鲤鱼_DIR / "config"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "settings.json"
PROJECT_CONFIG_NAME = Path(".liyu") / "config.json"


# ═══════════════════════════════════════════════
# 配置段定义
# ═══════════════════════════════════════════════

@dataclass
class LLMConfig:
    """模型配置 — 当前统一使用 MiMo v2.5-Pro"""
    provider: str = "xiaomi"
    model: str = "mimo-v2.5-pro"
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    anthropic_base_url: str = "https://token-plan-cn.xiaomimimo.com/anthropic"
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0
    stream: bool = True
    timeout: int = 120
    retry_count: int = 3
    retry_delay: float = 2.0

    # 模型切换（鲤鱼 特有）
    auto_select_model: bool = False
    fallback_model: str = "mimo-v2.5"
    fast_model: str = "mimo-v2-flash"


@dataclass
class ContextConfig:
    """上下文配置 — 集成 context_mapper.py"""
    max_tokens: int = 128000
    system_reserve: int = 4000
    response_reserve: int = 8000
    safety_margin: int = 2000

    # O2 阈值（来自 CLAUDE.md）
    sprint_warn_threshold: float = 0.85
    sprint_force_threshold: float = 0.90
    normal_warn_threshold: float = 0.70
    normal_force_threshold: float = 0.85
    proactive_clear_threshold: float = 0.20

    # 压缩参数
    compress_target_ratio: float = 0.50
    keep_recent_turns: int = 8
    tool_output_max_chars: int = 2000

    # 注入
    inject_memory: bool = True
    inject_project_rules: bool = True
    inject_soul_shadow: bool = True
    memory_budget_tokens: int = 3000

    # Sprint Mode（至 2026-06-24）
    sprint_mode: bool = True


@dataclass
class AgentConfig:
    """Agent 编排配置"""
    enabled: bool = True
    max_concurrent: int = 5
    default_timeout: int = 300
    worktree_isolation: bool = False

    # Agent 注册表
    agents: dict[str, str] = field(default_factory=lambda: {
        "planner": "planner",
        "architect": "architect",
        "code-reviewer": "code-reviewer",
        "security-reviewer": "security-reviewer",
        "tdd-guide": "tdd-guide",
        "build-error-resolver": "build-error-resolver",
        "deep-research": "deep-research-agent",
    })

    # 自动触发规则
    auto_trigger: dict[str, list[str]] = field(default_factory=lambda: {
        "after_code_write": ["code-reviewer"],
        "before_commit": ["security-reviewer"],
        "on_build_failure": ["build-error-resolver"],
        "complex_feature": ["planner"],
    })


@dataclass
class MetacogConfig:
    """Metacog 七感感知配置"""
    enabled: bool = True
    monitor_interval_seconds: int = 10

    # 各感知阈值
    o2_warn_ratio: float = 0.70
    o2_critical_ratio: float = 0.85
    nociception_cascade_window: int = 5
    nociception_critical_count: int = 5
    chronos_idle_minutes: int = 5
    spatial_file_churn_limit: int = 5
    vestibular_diversity_min: float = 0.20
    echo_repeat_limit: int = 3
    drift_deviation_limit: float = 0.30

    # 自动响应
    auto_compact_on_critical: bool = True
    auto_pause_on_cascade: bool = True
    auto_notify_on_warning: bool = False


@dataclass
class RecoveryConfig:
    """错误恢复配置 — 集成 intelligent_recovery.py"""
    enabled: bool = True
    max_total_retries: int = 10
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0

    # 各错误类别的重试配置
    transient_max_retries: int = 4
    network_max_retries: int = 3
    resource_max_retries: int = 2
    logic_max_retries: int = 2
    context_max_retries: int = 3

    # 退避参数
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    jitter_enabled: bool = True


@dataclass
class MemoryConfig:
    """知识库记忆配置"""
    enabled: bool = True
    db_path: str = str(鲤鱼_DIR / "knowledge-base.db")
    fts_enabled: bool = True
    auto_sync: bool = True
    auto_extract: bool = True
    auto_consolidate: bool = True
    max_facts: int = 200
    max_context_chars: int = 12000


@dataclass
class EvolutionConfig:
    """自我进化配置"""
    enabled: bool = True
    min_observations: int = 10
    promotion_confidence: float = 0.60
    promotion_success_rate: float = 0.70
    hardening_confidence: float = 0.90
    hardening_observations: int = 50
    auto_amend: bool = False  # 修正需要人类审核


@dataclass
class DisplayConfig:
    """显示配置"""
    theme: str = "liyu"
    language: str = "zh"
    stream_output: bool = True
    show_tool_output: bool = True
    max_tool_output_lines: int = 10
    show_stats_on_complete: bool = True
    compact_mode: bool = False


@dataclass
class PolicyConfig:
    """策略引擎配置"""
    enabled: bool = True
    audit_log: bool = True
    max_audit_entries: int = 1000
    require_approval_for_destructive: bool = True
    auto_approve_safe_tools: bool = True

    # 自定义允许/禁止列表
    allowed_tools_extra: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)


@dataclass
class PhoenixConfig:
    """鲤鱼 总配置 — 九段合一"""
    version: str = "1.0.0"
    llm: LLMConfig = field(default_factory=LLMConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    metacog: MetacogConfig = field(default_factory=MetacogConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    evolution: EvolutionConfig = field(default_factory=EvolutionConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)

    _SECTION_NAMES = [
        "llm", "context", "agent", "metacog", "recovery",
        "memory", "evolution", "display", "policy",
    ]

    def to_dict(self) -> dict:
        return {"version": self.version, **{
            name: asdict(getattr(self, name))
            for name in self._SECTION_NAMES
        }}

    @classmethod
    def from_dict(cls, data: dict) -> "PhoenixConfig":
        config = cls()
        if "version" in data:
            config.version = data["version"]
        for section in cls._SECTION_NAMES:
            if section in data and isinstance(data[section], dict):
                target = getattr(config, section)
                for k, v in data[section].items():
                    if hasattr(target, k):
                        setattr(target, k, v)
        return config

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """点号路径访问: 'llm.model' → 'mimo-v2.5-pro'"""
        parts = dotted_key.split(".")
        obj = self
        for part in parts:
            obj = getattr(obj, part, None)
            if obj is None:
                return default
        return obj


# ═══════════════════════════════════════════════
# 配置管理器 — 五层加载
# ═══════════════════════════════════════════════

class ConfigManager:
    """鲤鱼 配置管理器 — 五层声明式配置

    加载顺序（后加载覆盖前加载）:
      L1: 硬编码默认值（PhoenixConfig dataclass 默认字段）
      L2: ~/.claude/liyu/config/settings.json（全局）
      L3: <project>/.liyu/config.json（项目级）
      L4: 环境变量 鲤鱼_*（会话级）
      L5: set() 运行时覆盖（运行时）
    """

    def __init__(self):
        self._config = PhoenixConfig()
        self._overrides: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._load_count: int = 0

    # ── 加载 ──

    def load(self, project_dir: str | Path | None = None) -> PhoenixConfig:
        """五层加载配置"""
        start = time.time()

        # L1: 默认值（已通过 dataclass 默认字段设置）
        self._config = PhoenixConfig()

        # L2: 全局配置
        self._load_layer(GLOBAL_CONFIG_PATH, "global")

        # L3: 项目配置
        if project_dir:
            project_path = Path(project_dir) / PROJECT_CONFIG_NAME
            self._load_layer(project_path, "project")

        # L4: 环境变量
        self._load_env()

        # L5: 运行时覆盖
        for key, value in self._overrides.items():
            self._set_nested(key, value)

        self._loaded_at = time.time()
        self._load_count += 1
        return self._config

    def reload(self, project_dir: str | Path | None = None) -> PhoenixConfig:
        """热重载 — 重新加载所有层级"""
        self._overrides.clear()
        return self.load(project_dir)

    # ── 读写 ──

    def set(self, dotted_key: str, value: Any) -> None:
        """运行时覆盖（L5）"""
        self._overrides[dotted_key] = value
        self._set_nested(dotted_key, value)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._get_nested(dotted_key, default)

    def save_global(self) -> bool:
        """保存到全局配置文件"""
        try:
            GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            GLOBAL_CONFIG_PATH.write_text(
                json.dumps(self._config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    def save_project(self, project_dir: str | Path) -> bool:
        """保存到项目配置文件"""
        try:
            project_path = Path(project_dir) / PROJECT_CONFIG_NAME
            project_path.parent.mkdir(parents=True, exist_ok=True)
            project_path.write_text(
                json.dumps(self._config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    # ── 属性 ──

    @property
    def config(self) -> PhoenixConfig:
        return self._config

    @property
    def loaded_at(self) -> float:
        return self._loaded_at

    @property
    def load_count(self) -> int:
        return self._load_count

    @property
    def overrides(self) -> dict:
        return dict(self._overrides)

    # ── 内部 ──

    def _load_layer(self, path: Path, layer_name: str) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            overlay = PhoenixConfig.from_dict(data)
            self._merge(overlay)
        except (json.JSONDecodeError, OSError):
            pass

    def _load_env(self) -> None:
        """从环境变量加载（鲤鱼_* 前缀）"""
        env_map = {
            "鲤鱼_LLM_MODEL": "llm.model",
            "鲤鱼_LLM_PROVIDER": "llm.provider",
            "鲤鱼_LLM_TIMEOUT": "llm.timeout",
            "鲤鱼_CONTEXT_MAX_TOKENS": "context.max_tokens",
            "鲤鱼_SPRINT_MODE": "context.sprint_mode",
            "鲤鱼_METACOG_ENABLED": "metacog.enabled",
            "鲤鱼_RECOVERY_ENABLED": "recovery.enabled",
            "鲤鱼_MEMORY_ENABLED": "memory.enabled",
            "鲤鱼_LANGUAGE": "display.language",
        }

        for env_key, dotted_key in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                # 类型转换
                converted = self._coerce_type(val)
                if converted is not None:
                    self._set_nested(dotted_key, converted)

    def _coerce_type(self, value: str) -> Any:
        """尝试智能类型转换"""
        if value.lower() in ("true", "yes", "1"):
            return True
        if value.lower() in ("false", "no", "0"):
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def _merge(self, overlay: PhoenixConfig) -> None:
        """合并配置：overlay 的非默认值覆盖 base"""
        for section_name in PhoenixConfig._SECTION_NAMES:
            src = getattr(overlay, section_name)
            dst = getattr(self._config, section_name)
            if src and dst:
                for k, v in vars(src).items():
                    if v is not None:
                        setattr(dst, k, v)

    def _set_nested(self, dotted_key: str, value: Any) -> None:
        parts = dotted_key.split(".")
        obj = self._config
        for part in parts[:-1]:
            obj = getattr(obj, part, None)
            if obj is None:
                return
        if hasattr(obj, parts[-1]):
            setattr(obj, parts[-1], value)

    def _get_nested(self, dotted_key: str, default: Any = None) -> Any:
        parts = dotted_key.split(".")
        obj = self._config
        for part in parts:
            obj = getattr(obj, part, None)
            if obj is None:
                return default
        return obj


# ═══════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════

_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """获取配置管理器单例"""
    global _manager
    if _manager is None:
        _manager = ConfigManager()
        _manager.load()
    return _manager


def get_config() -> PhoenixConfig:
    """获取当前生效配置"""
    return get_config_manager().config


# ═══════════════════════════════════════════════
# CLI 演示
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    mgr = ConfigManager()
    config = mgr.load()

    print("=" * 60)
    print("鲤鱼 Runtime Config — Five-Layer Demo")
    print("=" * 60)

    # 展示关键配置
    sections = [
        ("LLM", f"provider={config.llm.provider}, model={config.llm.model}"),
        ("Context", f"max={config.context.max_tokens}, sprint={config.context.sprint_mode}"),
        ("Agent", f"max_concurrent={config.agent.max_concurrent}, agents={len(config.agent.agents)}"),
        ("Metacog", f"enabled={config.metacog.enabled}, interval={config.metacog.monitor_interval_seconds}s"),
        ("Recovery", f"enabled={config.recovery.enabled}, cb_threshold={config.recovery.circuit_breaker_threshold}"),
        ("Memory", f"enabled={config.memory.enabled}, max_facts={config.memory.max_facts}"),
        ("Evolution", f"enabled={config.evolution.enabled}, min_obs={config.evolution.min_observations}"),
        ("Display", f"theme={config.display.theme}, lang={config.display.language}"),
        ("Policy", f"enabled={config.policy.enabled}, audit={config.policy.audit_log}"),
    ]

    for name, detail in sections:
        print(f"  [{name}] {detail}")

    # 演示覆盖
    print(f"\n  --- Runtime Override Demo ---")
    mgr.set("llm.model", "mimo-v2-flash")
    mgr.set("context.sprint_mode", False)
    print(f"  llm.model = {mgr.get('llm.model')}")
    print(f"  context.sprint_mode = {mgr.get('context.sprint_mode')}")
    print(f"  non.existent.key = {mgr.get('non.existent.key', 'N/A')}")

    # 演示环境变量
    if os.environ.get("鲤鱼_LLM_MODEL"):
        print(f"\n  鲤鱼_LLM_MODEL env override: {os.environ['鲤鱼_LLM_MODEL']}")

    print(f"\n  Config loaded at: {time.strftime('%H:%M:%S', time.localtime(mgr.loaded_at))}")
    print(f"  Load count: {mgr.load_count}")
    print(f"  Active overrides: {len(mgr.overrides)}")
    print(f"\n{'=' * 60}")
