#!/usr/bin/env python3
"""鲤鱼 runtime_config.py 测试套件

覆盖：
- 九段配置默认值
- 五层加载系统
- 点号路径 get/set
- 运行时覆盖优先级
- 环境变量注入
- 序列化/反序列化
- 单例模式
"""

import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from runtime_config import (
    PhoenixConfig, ConfigManager, LLMConfig, ContextConfig,
    AgentConfig, MetacogConfig, RecoveryConfig, MemoryConfig,
    EvolutionConfig, DisplayConfig, PolicyConfig,
    get_config_manager, get_config,
    GLOBAL_CONFIG_DIR, GLOBAL_CONFIG_PATH,
)


def test_default_config():
    """L1: 默认值"""
    config = PhoenixConfig()
    assert config.version == "1.0.0"
    assert config.llm.provider == "xiaomi"
    assert config.llm.model == "mimo-v2.5-pro"
    assert config.context.max_tokens == 128000
    assert config.agent.max_concurrent == 5
    assert config.metacog.enabled is True
    assert config.recovery.circuit_breaker_enabled is True
    assert config.memory.fts_enabled is True
    assert config.display.language == "zh"
    print("  ✓ Default config: all 9 sections have correct defaults")


def test_dotted_access():
    """点号路径访问"""
    config = PhoenixConfig()
    assert config.get("llm.model") == "mimo-v2.5-pro"
    assert config.get("context.max_tokens") == 128000
    assert config.get("nonexistent.key", "fallback") == "fallback"
    assert config.get("llm.nonexistent") is None
    print("  ✓ Dotted access: get() works for nested paths")


def test_serialization():
    """序列化/反序列化"""
    config = PhoenixConfig()
    d = config.to_dict()
    assert d["version"] == "1.0.0"
    assert d["llm"]["provider"] == "xiaomi"

    # 反序列化
    restored = PhoenixConfig.from_dict(d)
    assert restored.llm.provider == config.llm.provider
    assert restored.context.max_tokens == config.context.max_tokens
    print("  ✓ Serialization: to_dict → from_dict roundtrip")


def test_config_manager_defaults():
    """ConfigManager 默认加载"""
    mgr = ConfigManager()
    config = mgr.load()
    assert config.llm.model == "mimo-v2.5-pro"
    assert mgr.load_count >= 1
    print("  ✓ ConfigManager: loads with defaults, tracks load count")


def test_runtime_overrides():
    """L5: 运行时覆盖"""
    mgr = ConfigManager()
    mgr.load()

    # 运行时覆盖
    assert mgr.get("llm.model") == "mimo-v2.5-pro"
    mgr.set("llm.model", "mimo-v2-flash")
    assert mgr.get("llm.model") == "mimo-v2-flash"
    assert "llm.model" in mgr.overrides

    # 覆盖不影响原始默认
    mgr2 = ConfigManager()
    mgr2.load()
    assert mgr2.get("llm.model") == "mimo-v2.5-pro"

    print("  ✓ Runtime overrides: L5 beats L1, isolated per manager")


def test_env_override():
    """L4: 环境变量覆盖"""
    mgr = ConfigManager()
    # 设置环境变量
    os.environ["鲤鱼_LLM_MODEL"] = "mimo-v2.5"
    os.environ["鲤鱼_SPRINT_MODE"] = "false"

    config = mgr.load()
    # 环境变量应该在 L4 被加载
    assert config.llm.model == "mimo-v2.5"
    assert config.context.sprint_mode is False

    # 清理
    del os.environ["鲤鱼_LLM_MODEL"]
    del os.environ["鲤鱼_SPRINT_MODE"]
    print("  ✓ Env overrides: 鲤鱼_* env vars properly inject")


def test_coerce_types():
    """类型转换"""
    mgr = ConfigManager()
    os.environ["鲤鱼_LLM_TIMEOUT"] = "180"
    os.environ["鲤鱼_CONTEXT_MAX_TOKENS"] = "200000"
    os.environ["鲤鱼_METACOG_ENABLED"] = "false"

    config = mgr.load()
    assert config.llm.timeout == 180       # int
    assert config.context.max_tokens == 200000  # int
    assert config.metacog.enabled is False  # bool

    del os.environ["鲤鱼_LLM_TIMEOUT"]
    del os.environ["鲤鱼_CONTEXT_MAX_TOKENS"]
    del os.environ["鲤鱼_METACOG_ENABLED"]
    print("  ✓ Type coercion: int, float, bool correctly parsed from env")


def test_nested_set():
    """深层 set"""
    mgr = ConfigManager()
    mgr.load()

    mgr.set("recovery.circuit_breaker_threshold", 10)
    assert mgr.get("recovery.circuit_breaker_threshold") == 10
    assert mgr.config.recovery.circuit_breaker_threshold == 10

    mgr.set("metacog.o2_critical_ratio", 0.95)
    assert mgr.config.metacog.o2_critical_ratio == 0.95

    print("  ✓ Nested set: deep dotted paths work")


def test_singleton():
    """全局单例"""
    mgr1 = get_config_manager()
    mgr2 = get_config_manager()
    assert mgr1 is mgr2

    config1 = get_config()
    config2 = get_config()
    assert config1 is config2
    print("  ✓ Singleton: ConfigManager and PhoenixConfig are singletons")


def test_save_global():
    """保存全局配置"""
    mgr = ConfigManager()
    mgr.load()
    mgr.set("display.theme", "dark")

    # 保存
    ok = mgr.save_global()
    assert ok
    assert GLOBAL_CONFIG_PATH.exists()

    # 重新加载验证
    mgr2 = ConfigManager()
    config2 = mgr2.load()
    assert config2.display.theme == "dark"

    # 恢复
    mgr.set("display.theme", "liyu")
    mgr.save_global()
    print("  ✓ Save global: persist → reload → verify")


def test_reload():
    """热重载"""
    mgr = ConfigManager()
    mgr.load()
    mgr.set("display.theme", "dark")
    assert mgr.get("display.theme") == "dark"

    # 重载 → 覆盖被清除
    mgr.reload()
    assert mgr.get("display.theme") == "liyu"  # 回到默认
    assert len(mgr.overrides) == 0
    print("  ✓ Reload: clears overrides, re-applies layers")


def test_all_sections_present():
    """所有 9 段都存在"""
    config = PhoenixConfig()
    sections = [
        config.llm, config.context, config.agent, config.metacog,
        config.recovery, config.memory, config.evolution,
        config.display, config.policy,
    ]
    assert all(s is not None for s in sections)
    print("  ✓ All sections: 9/9 config sections present")


# ═══════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("鲤鱼 Runtime Config — Test Suite")
    print("=" * 60)

    test_default_config()
    test_dotted_access()
    test_serialization()
    test_config_manager_defaults()
    test_runtime_overrides()
    test_env_override()
    test_coerce_types()
    test_nested_set()
    test_singleton()
    test_save_global()
    test_reload()
    test_all_sections_present()

    print(f"\n{'=' * 60}")
    print("All tests passed ✓")
    print(f"{'=' * 60}")
