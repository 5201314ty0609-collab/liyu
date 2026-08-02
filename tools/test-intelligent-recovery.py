#!/usr/bin/env python3
"""鲤鱼 intelligent-recovery.py 测试套件

覆盖：
- 错误分类：7 类别 × 多种输入格式
- 恢复策略选择：正常路径 + 重试耗尽 fallback
- 指数退避延迟计算
- Circuit Breaker 三态转换
- Nociception 痛觉报告
- 上下文压缩
"""

import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from intelligent_recovery import (
    IntelligentRecovery, ErrorClassifier, RecoveryStrategySelector,
    ErrorCategory, RecoveryStrategy, RecoveryPlan,
    CircuitBreaker, CircuitBreakerOpenError,
    ContextCompressor, ErrorRecord,
    get_recovery, get_compressor,
)

# ═══════════════════════════════════════════════
# Test 1: Error Classification
# ═══════════════════════════════════════════════

def test_classification():
    classifier = ErrorClassifier()
    test_cases = [
        # (error_message, expected_category, min_confidence)
        ("Request timeout after 30s", ErrorCategory.TRANSIENT, 0.8),
        ("429 Too Many Requests", ErrorCategory.TRANSIENT, 0.9),
        ("Out of memory: cannot allocate", ErrorCategory.RESOURCE, 0.9),
        ("Permission denied: /etc/config", ErrorCategory.PERMISSION, 0.9),
        ("401 Unauthorized", ErrorCategory.PERMISSION, 0.9),
        ("Connection refused: ECONNREFUSED", ErrorCategory.NETWORK, 0.9),
        ("File not found: /tmp/x.txt", ErrorCategory.LOGIC, 0.8),
        ("Context window exceeded: 150k tokens", ErrorCategory.CONTEXT, 0.9),
        ("Some random weird error nobody knows", ErrorCategory.UNKNOWN, 0.0),
        # 中文
        ("请求超时，请重试", ErrorCategory.TRANSIENT, 0.8),
        ("权限不足，无法写入", ErrorCategory.PERMISSION, 0.8),
        ("连接被拒绝", ErrorCategory.NETWORK, 0.8),
    ]

    passed = 0
    for msg, expected_cat, min_conf in test_cases:
        cat, conf = classifier.classify(msg)
        ok = cat == expected_cat and conf >= min_conf
        if ok:
            passed += 1
        else:
            print(f"  FAIL: '{msg}' → {cat.value}({conf:.0%}), expected {expected_cat.value}(>={min_conf:.0%})")

    assert passed == len(test_cases), f"{passed}/{len(test_cases)} classification tests passed"
    print(f"  ✓ Classification: {passed}/{len(test_cases)} passed")


# ═══════════════════════════════════════════════
# Test 2: Recovery Strategy Selection
# ═══════════════════════════════════════════════

def test_strategy_selection():
    selector = RecoveryStrategySelector()

    # 正常路径
    plan = selector.select(ErrorCategory.TRANSIENT, attempt=0)
    assert plan.strategy == RecoveryStrategy.RETRY_IMMEDIATE
    assert plan.max_attempts == 1

    plan = selector.select(ErrorCategory.TRANSIENT, attempt=1)
    assert plan.strategy == RecoveryStrategy.RETRY_BACKOFF
    assert plan.jitter is True

    plan = selector.select(ErrorCategory.PERMISSION, attempt=0)
    assert plan.strategy == RecoveryStrategy.ASK_USER

    plan = selector.select(ErrorCategory.CONTEXT, attempt=0)
    assert plan.strategy == RecoveryStrategy.COMPRESS_CONTEXT
    assert plan.max_attempts == 3

    # Fallback: 所有尝试耗尽
    plan = selector.select(ErrorCategory.PERMISSION, attempt=10)
    assert plan.strategy == RecoveryStrategy.ABORT

    plan = selector.select(ErrorCategory.UNKNOWN, attempt=10)
    assert plan.strategy == RecoveryStrategy.ABORT

    print(f"  ✓ Strategy selection: all paths correct")


# ═══════════════════════════════════════════════

def test_delay_calculation():
    recovery = IntelligentRecovery()

    # 无退避
    plan = RecoveryPlan(strategy=RecoveryStrategy.RETRY_IMMEDIATE,
                        max_attempts=1, backoff_base=0, backoff_max=0, jitter=False)
    assert recovery.calculate_delay(plan, 0) == 0.0

    # 指数退避 + 抖动
    plan = RecoveryPlan(strategy=RecoveryStrategy.RETRY_BACKOFF,
                        max_attempts=3, backoff_base=1.0, backoff_max=30.0, jitter=True)
    delay = recovery.calculate_delay(plan, 2)  # attempt 2 → 1.0 * 2^2 = 4s ± jitter
    assert 2.0 <= delay <= 4.0, f"Expected delay in [2.0, 4.0], got {delay}"

    # 达到上限
    delay = recovery.calculate_delay(plan, 10)  # 1.0 * 2^10 = 1024s → capped at 30s
    assert 15.0 <= delay <= 30.0, f"Expected delay in [15.0, 30.0], got {delay}"

    print(f"  ✓ Delay calculation: correct exponential backoff + jitter + cap")


# ═══════════════════════════════════════════════
# Test 3: Circuit Breaker
# ═══════════════════════════════════════════════

def test_circuit_breaker():
    breaker = CircuitBreaker("test-tool", failure_threshold=3, recovery_timeout=0.1, half_open_max=1)

    # 正常调用
    result = breaker.call(lambda x: x * 2, 21)
    assert result == 42
    assert breaker.state == CircuitBreaker.State.CLOSED

    # 触发熔断
    failures = 0
    for _ in range(3):
        try:
            breaker.call(lambda: (_ for _ in ()).throw(Exception("boom")))
        except Exception:
            failures += 1
    assert failures == 3
    assert breaker.state == CircuitBreaker.State.OPEN

    # 熔断拒绝
    try:
        breaker.call(lambda: 42)
        assert False, "Should have raised CircuitBreakerOpenError"
    except CircuitBreakerOpenError:
        pass

    # 等恢复超时 → half-open
    time.sleep(0.15)
    result = breaker.call(lambda x: x + 1, 41)
    assert result == 42
    assert breaker.state == CircuitBreaker.State.CLOSED

    print(f"  ✓ Circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED")


# ═══════════════════════════════════════════════
# Test 4: Full Recovery Flow
# ═══════════════════════════════════════════════

def test_full_recovery_flow():
    recovery = IntelligentRecovery()

    # 分析 + 获取计划
    category, confidence, plan = recovery.classify_and_plan(
        "429 Too Many Requests", tool_name="api_call", attempt=1
    )
    assert category == ErrorCategory.TRANSIENT
    assert confidence >= 0.9
    assert plan.strategy == RecoveryStrategy.RETRY_BACKOFF

    # 记录恢复
    record = ErrorRecord(
        error_type="HTTPError",
        error_message="429 Too Many Requests",
        category=category,
        confidence=confidence,
        tool_name="api_call",
        attempt=1,
        recovery_strategy=plan.strategy,
        recovery_success=True,
        duration_ms=2500.0,
    )
    recovery.record(record)

    # 验证记录
    summary = recovery.get_error_summary()
    assert summary["total_errors"] == 1
    assert summary["by_category"]["transient"] == 1
    assert summary["pain_level"] == "healthy"

    print(f"  ✓ Full recovery flow: classify → plan → record → summary")


# ═══════════════════════════════════════════════
# Test 5: Nociception Pain Levels
# ═══════════════════════════════════════════════

def test_nociception():
    recovery = IntelligentRecovery()

    # 空状态 → healthy
    assert recovery.pain_level == "healthy"
    assert not recovery.should_analyze_root_cause

    # 模拟 5 个错误
    for i in range(5):
        record = ErrorRecord(
            error_type="TestError",
            error_message=f"Simulated error {i}",
            category=ErrorCategory.TRANSIENT,
            confidence=0.9,
            tool_name="test_tool",
            attempt=0,
            recovery_strategy=RecoveryStrategy.RETRY_BACKOFF,
            recovery_success=False,
            duration_ms=100.0,
        )
        recovery.record(record)

    assert recovery.pain_level == "critical"
    assert recovery.should_analyze_root_cause

    report = recovery.get_nociception_report()
    assert report["status"] == "critical"
    assert len(report["warnings"]) > 0
    assert "root cause" in report["recommendation"].lower()

    print(f"  ✓ Nociception: healthy → critical cascade detection")


# ═══════════════════════════════════════════════
# Test 6: Context Compressor
# ═══════════════════════════════════════════════

def test_context_compressor():
    compressor = ContextCompressor(max_tool_output=100, keep_recent=4)

    # 压缩长输出
    long_output = "A" * 500
    compressed = compressor.compress_tool_output(long_output)
    assert len(compressed) < len(long_output)
    assert "省略" in compressed

    # 压缩消息列表
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "Q3"},
        {"role": "assistant", "content": "A3"},
        {"role": "user", "content": "Q4"},
        {"role": "assistant", "content": "A4"},
        {"role": "user", "content": "Q5"},
        {"role": "assistant", "content": "A5"},
    ]
    result = compressor.compress_messages(messages, target_ratio=0.4)
    assert len(result) < len(messages)
    # 系统消息应该保留
    assert result[0]["role"] == "system"

    print(f"  ✓ Context compressor: truncation + message compression")


# ═══════════════════════════════════════════════
# Test 7: Global Singleton
# ═══════════════════════════════════════════════

def test_singleton():
    r1 = get_recovery()
    r2 = get_recovery()
    assert r1 is r2

    c1 = get_compressor()
    c2 = get_compressor()
    assert c1 is c2

    print(f"  ✓ Singleton: recovery + compressor are proper singletons")


# ═══════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("鲤鱼 Intelligent Recovery — Test Suite")
    print("=" * 60)

    test_classification()
    test_strategy_selection()
    test_delay_calculation()
    test_circuit_breaker()
    test_full_recovery_flow()
    test_nociception()
    test_context_compressor()
    test_singleton()

    print(f"\n{'=' * 60}")
    print("All tests passed ✓")
    print(f"{'=' * 60}")
