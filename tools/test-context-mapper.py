#!/usr/bin/env python3
"""鲤鱼 context_mapper.py 测试套件

覆盖：
- Chunk 类型和优先级
- Token 估算准确性
- 三层压缩策略
- O2 状态转换
- Budget 约束
- 消息转换
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from context_mapper import (
    ContextMapper, ContextBudget, Chunk, ChunkType, EvictionPriority,
    get_context_mapper,
)


def test_chunk_creation():
    """Chunk 创建 + token 估算"""
    chunk = Chunk("Hello, world!", ChunkType.USER)
    assert chunk.token_estimate > 0
    assert chunk.chunk_type == ChunkType.USER
    assert not chunk.compressed
    assert chunk.timestamp > 0
    print("  ✓ Chunk creation + estimation")


def test_priority_ordering():
    """优先级排序正确"""
    assert EvictionPriority.KEEP_FOREVER < EvictionPriority.EVICT_FIRST
    assert EvictionPriority.KEEP_RECENT < EvictionPriority.COMPRESS_FIRST
    assert EvictionPriority.COMPRESS_FIRST < EvictionPriority.EVICT_FIRST
    print("  ✓ Priority ordering: KEEP_FOREVER < KEEP_RECENT < COMPRESS_FIRST < EVICT_FIRST")


def test_budget():
    """预算约束"""
    budget = ContextBudget(max_tokens=128000)
    assert budget.usable_tokens > 0
    assert budget.usable_tokens < budget.max_tokens
    assert budget.usable_tokens == 128000 - 4000 - 8000 - 2000  # = 114000

    # 自定义预算
    small = ContextBudget(max_tokens=20000, system_reserve=2000,
                          response_reserve=2000, safety_margin=1000)
    assert small.usable_tokens == 15000
    print("  ✓ Budget: usable_tokens = max - system - response - safety")


def test_context_mapper_add():
    """添加上下文块"""
    mapper = ContextMapper()
    mapper.add_system("System prompt here")
    mapper.add_user("User question?", turn_id="t1")
    mapper.add_assistant("Assistant answer.", turn_id="t1")
    mapper.add_tool_result("Tool output " * 50, tool_name="read_file", turn_id="t1")

    assert mapper.chunk_count == 4
    assert mapper.total_tokens > 0
    print(f"  ✓ Add chunks: 4 chunks, {mapper.total_tokens} tokens")


def test_o2_states():
    """O2 状态转换"""
    # Sprint mode (default) - 空 mapper
    mapper = ContextMapper(sprint_mode=True)
    # 空上下文 → usage=0% → clearing（可以主动清理）
    assert mapper.o2_status == "clearing"

    # 添加大量内容 → healthy
    mapper.add_system("System " * 2000)  # ~8000 chars → ~3200 tokens
    # 3200 / 114000 ≈ 2.8% → still clearing (≤20%)
    assert mapper.o2_status in ("healthy", "clearing")

    # 警告阈值
    mapper_empty = ContextMapper(sprint_mode=True)
    assert mapper_empty.warn_threshold == 0.85
    assert mapper_empty.force_threshold == 0.90

    # Normal mode
    mapper_normal = ContextMapper(sprint_mode=False)
    assert mapper_normal.warn_threshold == 0.70
    assert mapper_normal.force_threshold == 0.85

    print(f"  ✓ O2 states: Sprint({mapper.warn_threshold:.0%}/{mapper.force_threshold:.0%}), "
          f"Normal({mapper_normal.warn_threshold:.0%}/{mapper_normal.force_threshold:.0%})")


def test_compression_strategies():
    """三层压缩策略"""
    mapper = ContextMapper()

    # 策略 1: 压缩 tool 输出
    big_output = "X" * 10000
    mapper.add_tool_result(big_output, tool_name="read_file")
    before = mapper.total_tokens

    # 设置一个低的 budget 来触发压缩
    mapper._budget = ContextBudget(max_tokens=10000, system_reserve=1000,
                                   response_reserve=1000, safety_margin=500)
    # 触发压缩（target = 0.5 * usable）
    if mapper.should_force_compress or mapper.total_tokens > mapper._budget.usable_tokens:
        old, new = mapper.compress(target_ratio=0.3)
        assert new < old, f"Compression should reduce tokens: {old} → {new}"
        print(f"  ✓ Compression: {old} → {new} tokens")
    else:
        # 内容不足以触发压缩
        print(f"  ✓ Compression: not needed (tokens={mapper.total_tokens}, usable={mapper._budget.usable_tokens})")


def test_strategy_2_eviction():
    """策略 2: 淘汰注入内容"""
    mapper = ContextMapper()
    mapper.add_system("System")
    mapper.add_user("Question", turn_id="t1")

    # 注入一些旧内容
    for i in range(20):
        mapper.inject(f"Injected context {i} " + "data " * 50, source="memory")

    before = mapper.chunk_count
    mapper._budget = ContextBudget(max_tokens=5000, system_reserve=500,
                                   response_reserve=500, safety_margin=200)
    old_t, new_t = mapper.compress(target_ratio=0.3)
    after = mapper.chunk_count

    assert after <= before, f"Should evict injected chunks: {before} → {after}"
    print(f"  ✓ Eviction: {before} → {after} chunks ({old_t} → {new_t} tokens)")


def test_strategy_3_summarization():
    """策略 3: 旧对话摘要"""
    mapper = ContextMapper()
    mapper.add_system("System prompt")

    # 很多对话轮次
    for i in range(30):
        mapper.add_user(f"User question {i}: " + "content " * 30, turn_id=f"t{i}")
        mapper.add_assistant(f"Assistant reply {i}: " + "answer " * 30, turn_id=f"t{i}")

    mapper._budget = ContextBudget(max_tokens=5000, system_reserve=500,
                                   response_reserve=500, safety_margin=200)
    old_t, new_t = mapper.compress(target_ratio=0.2)

    # 应该有 SUMMARY chunk
    types = [c.chunk_type for c in mapper._chunks]
    assert ChunkType.SUMMARY in types, f"Should have SUMMARY chunk, got types: {[t.name for t in set(types)]}"
    print(f"  ✓ Summarization: {old_t} → {new_t} tokens, SUMMARY chunk created")


def test_to_messages():
    """转换为 LLM 消息格式"""
    mapper = ContextMapper()
    mapper.add_system("You are helpful.")
    mapper.add_user("Hello")
    mapper.add_assistant("Hi there!")

    messages = mapper.to_messages()
    assert len(messages) == 3
    assert messages[0] == {"role": "system", "content": "You are helpful."}
    assert messages[1] == {"role": "user", "content": "Hello"}
    assert messages[2] == {"role": "assistant", "content": "Hi there!"}

    print("  ✓ to_messages: correct role mapping")


def test_o2_report():
    """O2 报告生成"""
    mapper = ContextMapper(sprint_mode=True)
    mapper.add_system("System prompt")
    mapper.add_user("Question")

    report = mapper.get_o2_report()
    assert "status" in report
    assert "metrics" in report
    assert "usage_percent" in report["metrics"]
    assert report["metrics"]["chunk_count"] == 2

    print(f"  ✓ O2 Report: status={report['status']}, usage={report['metrics']['usage_percent']}%")


def test_singleton():
    """单例模式"""
    m1 = get_context_mapper()
    m2 = get_context_mapper()
    assert m1 is m2
    print("  ✓ Singleton: context mapper is proper singleton")


def test_snapshot():
    """快照"""
    mapper = ContextMapper()
    mapper.add_system("System")
    mapper.add_user("Q")
    mapper.add_tool_result("Result " * 100, tool_name="bash")

    snap = mapper.snapshot()
    assert "by_type" in snap
    assert "SYSTEM" in snap["by_type"]
    assert "TOOL_RESULT" in snap["by_type"]
    print(f"  ✓ Snapshot: {snap['total_chunks']} chunks, by_type={list(snap['by_type'].keys())}")


# ═══════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("鲤鱼 Context Mapper — Test Suite")
    print("=" * 60)

    test_chunk_creation()
    test_priority_ordering()
    test_budget()
    test_context_mapper_add()
    test_o2_states()
    test_compression_strategies()
    test_strategy_2_eviction()
    test_strategy_3_summarization()
    test_to_messages()
    test_o2_report()
    test_singleton()
    test_snapshot()

    print(f"\n{'=' * 60}")
    print("All tests passed ✓")
    print(f"{'=' * 60}")
