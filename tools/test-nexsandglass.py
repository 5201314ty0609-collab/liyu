#!/usr/bin/env python3
"""
鲤鱼 NexSandglass 测试套件

测试覆盖:
  L1: 沙粒写入、SQLite 双写、去重
  L2: 偏移率计算、趋势检测、稳定性分级、7-Sense 集成
  L3: 决策粒子检测、双语模式、决策链追踪
  L4: 画像构建、四层扫描、交互协议生成
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

# Import the module
sys.path.insert(0, str(Path.home() / ".claude" / "liyu"))
from nexsandglass import (
    NexSandglass, SandWriter, DriftEngine, DecisionDetector, PersonaBuilder,
    SandGrain, SAND_DB, SAND_FILE, DRIFT_FILE, DECISIONS_FILE, PERSONA_FILE,
)

PASS = 0
FAIL = 0


def cleanup():
    """清理测试数据"""
    for f in [SAND_DB, SAND_FILE, DRIFT_FILE, DECISIONS_FILE, PERSONA_FILE]:
        try:
            f.unlink()
        except (OSError, FileNotFoundError):
            pass


def assert_equals(actual, expected, desc):
    global PASS, FAIL
    if actual == expected:
        PASS += 1; print(f"  ✅ {desc}")
    else:
        FAIL += 1; print(f"  ❌ {desc}: expected={expected}, actual={actual}")


def assert_contains(haystack, needle, desc):
    global PASS, FAIL
    if needle in str(haystack):
        PASS += 1; print(f"  ✅ {desc}")
    else:
        FAIL += 1; print(f"  ❌ {desc}: '{needle}' not found")


def assert_greater(a, b, desc):
    global PASS, FAIL
    if a > b:
        PASS += 1; print(f"  ✅ {desc}")
    else:
        FAIL += 1; print(f"  ❌ {desc}: {a} > {b}")


# ── Test 1: L1 Sand Write ──────────────────────────────────────────────
print("\n═══ 测试1: L1 沙粒写入 ═══")
cleanup()
ns = NexSandglass()

gids = []
# Write 15 grains (enough for persona)
messages = [
    ("user", "我是做全栈开发的，主要用 Python 和 TypeScript"),
    ("assistant", "了解了，全栈工程师"),
    ("user", "我觉得免费开源方案更好，不需要付费工具"),
    ("user", "直接开始写代码吧，不需要太多解释"),
    ("assistant", "好的，直接给代码"),
    ("user", "算了，还是用 Rust 吧，虽然学习曲线陡但性能更好"),
    ("user", "我更喜欢短小精悍的回复，不要啰嗦"),
    ("assistant", "明白"),
    ("user", "go with the simple approach, I prefer local tools"),
    ("user", "whatever, just pick something and start coding"),
    ("user", "I decided to use SQLite instead of PostgreSQL"),
    ("user", "安全性很重要，代码里的密钥必须检查"),
    ("user", "自动化的测试比手动测试更可靠"),
    ("assistant", "完全同意"),
    ("user", "先设计架构再写实现，不要上来就堆代码"),
]

for i, (role, content) in enumerate(messages):
    gids.append(ns.ingest(f"test-session-{i % 3}", role, content, f"turn-{i}"))

assert_equals(ns.writer.count(), 15, "写入 15 条沙粒")
assert_greater(SAND_FILE.stat().st_size if SAND_FILE.exists() else 0, 100, "纯文本文件非空")

# SQLite double-write
import sqlite3
db = sqlite3.connect(str(SAND_DB))
count = db.execute("SELECT COUNT(*) FROM sand").fetchone()[0]
db.close()
assert_equals(count, 15, "SQLite 双写 15 条")

# FTS5 search
db = sqlite3.connect(str(SAND_DB))
fts_result = db.execute(
    "SELECT content FROM sand WHERE rowid IN (SELECT rowid FROM sand_fts WHERE sand_fts MATCH ?)",
    ("SQLite",)
).fetchall()
db.close()
assert_greater(len(fts_result), 0, "FTS5 搜索 'SQLite' 有结果")


# ── Test 2: L2 Drift Velocity ──────────────────────────────────────────
print("\n═══ 测试2: L2 偏移率 ═══")
drift = ns.drift.compute_range(7)

assert_contains(["frugal", "spend", "drift"], drift["direction"], "偏移方向有效")
assert_contains(["highly_stable", "stable", "volatile", "highly_volatile"],
                drift["stability"], "稳定性标签有效")
assert_greater(len(drift["daily"]), 0, "有日均数据")
assert_contains(drift["trend_description"], "倾向", "趋势描述非空")

# Trend description with specific content
desc = drift["trend_description"]
valid = any(kw in desc for kw in ["保守", "投入", "探索", "无明显", "决策"])
assert_equals(valid, True, "趋势描述包含有效关键词")


# ── Test 3: L2 → 7-Sense Integration ───────────────────────────────────
print("\n═══ 测试3: L2 → 7-Sense 集成 ═══")
trends = ns.drift.sense_trends()
assert_greater(len(trends), 0, "产生 sense 趋势预测")
assert_contains(str(trends), "may_", "趋势包含有效预测值")


# ── Test 4: L3 Decision Detection (Chinese) ────────────────────────────
print("\n═══ 测试4: L3 决策检测 — 中文 ═══")

cn_tests = [
    ("我选Python吧，生态更好", "explicit_cn"),
    ("还是用Rust好了", "explicit_cn"),
    ("就做这个吧", "explicit_cn"),
    ("不管了，随便吧", "fallback_cn"),
    ("随便吧", "fallback_cn"),
    ("算了算了", "fallback_cn"),
    ("无所谓了", "fallback_cn"),
    ("用git吧来管理", "command_cn"),
    ("删掉那个文件一下", "command_cn"),
    ("我更喜欢直接有效的沟通", "preference"),
    ("倾向于用简单的方案", "preference"),
]

for text, expected_type in cn_tests:
    result = ns.detector.detect(text, "test")
    types = [r["type"] for r in result]
    assert_contains(types, expected_type, f"'{text[:20]}' → {expected_type}")


# ── Test 5: L3 Decision Detection (English) ────────────────────────────
print("\n═══ 测试5: L3 决策检测 — 英文 ═══")

en_tests = [
    ("I'll go with Python for this", "explicit_en"),
    ("let's use the local approach", "explicit_en"),
    ("whatever, just do it", "fallback_en"),
    ("never mind, I'll handle it", "fallback_en"),
    ("delete that file", "command_en"),
    ("switch to the new API", "command_en"),
    ("I prefer concise responses", "preference"),
    ("I tend to over-engineer things", "preference"),
]

for text, expected_type in en_tests:
    result = ns.detector.detect(text, "test")
    types = [r["type"] for r in result]
    assert_contains(types, expected_type, f"'{text[:30]}' → {expected_type}")


# ── Test 6: L3 Decision Chain ──────────────────────────────────────────
print("\n═══ 测试6: L3 决策链 ═══")
# Simulate a decision chain: user considers A, then B, then gives up
chain_msgs = [
    "我觉得应该用 PostgreSQL",
    "还是 MongoDB 更灵活",
    "算了不管了，随便选一个吧",
]
chain = ns.detector.detect_chain(chain_msgs, "chain-test")
assert_greater(len(chain), 2, f"检测到决策链 ({len(chain)} 个粒子)")

summary = ns.detector.decision_summary()
assert_greater(summary["total"], 0, "决策摘要有效")


# ── Test 7: L4 Persona Building ────────────────────────────────────────
print("\n═══ 测试7: L4 画像构建 ═══")
persona = ns.persona.build()

if persona.get("status") == "insufficient_data":
    FAIL += 1; print(f"  ❌ 画像数据不足: {persona['grains']} 条")
else:
    assert_greater(persona["total_grains"], 0, "画像有沙粒计数")
    layers = persona["layers"]

    # L1 Anchors
    assert_greater(len(layers["anchors"].get("tech_keywords", [])), 0,
                   "L1 锚点: 技术关键词非空")

    # L3 Protocol
    protocol = layers["protocol"]
    assert_greater(len(protocol.get("style_signals", {})), 0,
                   "L3 协议: 风格信号存在")

    # L4 Kernel
    kernel = layers["kernel"]
    assert_greater(len(kernel.get("core_values", [])), 0,
                   "L4 内核: 核心价值非空")

    # Source tracing
    assert_contains(kernel.get("source", ""), "[src:", "源码溯源标记存在")


# ── Test 8: Interaction Guide ──────────────────────────────────────────
print("\n═══ 测试8: 交互协议指南 ═══")
guide = ns.interaction_guide()
assert_contains(guide, "鲤鱼", "指南标题正确")
assert_contains(guide, "沙粒", "提及沙粒数量")


# ── Test 9: Analyze (Full Pipeline) ────────────────────────────────────
print("\n═══ 测试9: 全管道分析 ═══")
result = ns.analyze()
assert_contains(result, "drift", "分析包含 drift")
assert_contains(result, "sense_trends", "分析包含 sense_trends")
assert_contains(result, "decisions", "分析包含 decisions")
assert_contains(result, "persona", "分析包含 persona")
assert_greater(result.get("total_grains", 0), 10, "总沙粒 > 10")


# ── Test 10: Immutability (SandGrain) ──────────────────────────────────
print("\n═══ 测试10: 沙粒不可变性 ═══")
import dataclasses
grain = SandGrain(
    id="test-1", session_id="s1",
    timestamp=datetime.now(timezone.utc).isoformat(),
    role="user", content="test content",
)
assert_equals(dataclasses.is_dataclass(grain), True, "SandGrain 是 dataclass")
assert_equals(len(grain.content_hash), 16, "content_hash 长度 16")
assert_greater(grain.tokens, 0, "token 自动估算")


# ── Test 11: Edge Cases ────────────────────────────────────────────────
print("\n═══ 测试11: 边界情况 ═══")

# Empty content
grain = SandGrain(id="e1", session_id="s1",
                  timestamp=datetime.now(timezone.utc).isoformat(),
                  role="user", content="")
assert_greater(grain.tokens, 0, "空内容仍有 token min")

# Empty message detection
result = ns.detector.detect("", "test")
assert_equals(len(result), 0, "空文本无决策粒子")

# Single grain (not enough for persona)
cleanup()
ns2 = NexSandglass()
ns2.ingest("solo", "user", "hello")
persona_early = ns2.persona.build()
assert_equals(persona_early.get("status"), "insufficient_data",
              "沙粒不足时报告 insufficient_data")

# Very long content
long_content = "test " * 500
gid = ns2.ingest("s2", "user", long_content)
assert_greater(len(gid), 0, "长内容可以写入")


# ── Test 12: Four-Layer Separation ─────────────────────────────────────
print("\n═══ 测试12: 四层隔离 ═══")
# Each layer should have its own state file
assert_equals(SAND_DB.exists(), True, "L1: sand.db 存在")
# L2 drift state creates on first compute
ns2.drift.compute_range(1)
assert_equals(DRIFT_FILE.exists(), True, "L2: drift.json 存在")
# L3 decisions logged during ingest
# L4 persona written on build
# (tested implicitly above)


# ── Summary ─────────────────────────────────────────────────────────────
cleanup()
print(f"\n{'═'*50}")
print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
print(f"  通过率: {PASS / (PASS + FAIL) * 100:.0f}%")
print(f"{'═'*50}")

sys.exit(0 if FAIL == 0 else 1)
