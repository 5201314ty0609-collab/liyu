#!/usr/bin/env python3
"""
鲤鱼 Circuit Breaker — LLM 调用熔断器模式
吸收自 MUNDO v2.2.9 的 circuit breaker + 指数退避重试

三种状态:
  CLOSED → OPEN → HALF_OPEN → CLOSED

熔断条件:
  - 连续 N 次失败 → OPEN
  - 超时后 → HALF_OPEN
  - 成功 → CLOSED

Usage:
  liyu-circuit-breaker.py check <service>
    检查服务状态

  liyu-circuit-breaker.py record <service> <success|failure>
    记录调用结果

  liyu-circuit-breaker.py stats
    查看熔断统计

  liyu-circuit-breaker.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import json
import sys
import time

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
CIRCUIT_STATE_FILE = 鲤鱼_HOME / "circuit-breaker-state.json"
CIRCUIT_LOG_FILE = 鲤鱼_HOME / "circuit-breaker-log.jsonl"

# ── 熔断配置 ──────────────────────────────────────────────────────────────

CIRCUIT_CONFIG = {
    "failure_threshold": 5,      # 连续失败次数触发 OPEN
    "success_threshold": 3,      # HALF_OPEN 时成功次数恢复 CLOSED
    "timeout_seconds": 60,       # OPEN 状态超时时间
    "exponential_base": 2,       # 指数退避基数
    "max_backoff_seconds": 300,  # 最大退避时间
}

# ── 状态枚举 ──────────────────────────────────────────────────────────────

CIRCUIT_STATES = {
    "CLOSED": "closed",      # 正常状态
    "OPEN": "open",          # 熔断状态
    "HALF_OPEN": "half_open", # 半开状态
}

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """熔断器状态"""
    service: str
    state: str                    # closed / open / half_open
    failure_count: int
    success_count: int
    last_failure_time: Optional[str]
    last_success_time: Optional[str]
    consecutive_failures: int
    consecutive_successes: int
    total_failures: int
    total_successes: int
    backoff_seconds: float
    created_at: str
    updated_at: str

# ── 熔断器管理 ──────────────────────────────────────────────────────────

def load_circuits() -> dict[str, CircuitBreaker]:
    """加载所有熔断器"""
    if CIRCUIT_STATE_FILE.exists():
        try:
            data = json.loads(CIRCUIT_STATE_FILE.read_text())
            circuits = {}
            for service, info in data.items():
                circuits[service] = CircuitBreaker(**info)
            return circuits
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_circuits(circuits: dict[str, CircuitBreaker]) -> None:
    """持久化熔断器状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    data = {service: vars(circuit) for service, circuit in circuits.items()}
    CIRCUIT_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def get_or_create_circuit(circuits: dict[str, CircuitBreaker], service: str) -> CircuitBreaker:
    """获取或创建熔断器"""
    if service not in circuits:
        now = datetime.now(timezone.utc).isoformat()
        circuits[service] = CircuitBreaker(
            service=service,
            state=CIRCUIT_STATES["CLOSED"],
            failure_count=0,
            success_count=0,
            last_failure_time=None,
            last_success_time=None,
            consecutive_failures=0,
            consecutive_successes=0,
            total_failures=0,
            total_successes=0,
            backoff_seconds=0,
            created_at=now,
            updated_at=now,
        )
    return circuits[service]


def check_circuit(circuit: CircuitBreaker) -> tuple[str, str]:
    """检查熔断器状态

    Returns:
        (state, reason)
    """
    now = datetime.now(timezone.utc)

    # CLOSED 状态：检查是否需要熔断
    if circuit.state == CIRCUIT_STATES["CLOSED"]:
        if circuit.consecutive_failures >= CIRCUIT_CONFIG["failure_threshold"]:
            # 触发熔断
            circuit.state = CIRCUIT_STATES["OPEN"]
            circuit.backoff_seconds = min(
                CIRCUIT_CONFIG["exponential_base"] ** circuit.consecutive_failures,
                CIRCUIT_CONFIG["max_backoff_seconds"]
            )
            circuit.updated_at = now.isoformat()
            return CIRCUIT_STATES["OPEN"], f"Circuit OPEN: {circuit.consecutive_failures} consecutive failures"

    # OPEN 状态：检查是否超时
    elif circuit.state == CIRCUIT_STATES["OPEN"]:
        if circuit.last_failure_time:
            last_failure = datetime.fromisoformat(circuit.last_failure_time)
            timeout = timedelta(seconds=CIRCUIT_CONFIG["timeout_seconds"])
            if now - last_failure > timeout:
                # 超时，进入半开状态
                circuit.state = CIRCUIT_STATES["HALF_OPEN"]
                circuit.success_count = 0
                circuit.updated_at = now.isoformat()
                return CIRCUIT_STATES["HALF_OPEN"], "Circuit HALF_OPEN: timeout reached, testing recovery"
            else:
                remaining = (last_failure + timeout - now).total_seconds()
                return CIRCUIT_STATES["OPEN"], f"Circuit OPEN: {remaining:.0f}s remaining before retry"

    # HALF_OPEN 状态：检查是否恢复
    elif circuit.state == CIRCUIT_STATES["HALF_OPEN"]:
        if circuit.success_count >= CIRCUIT_CONFIG["success_threshold"]:
            # 恢复正常
            circuit.state = CIRCUIT_STATES["CLOSED"]
            circuit.consecutive_failures = 0
            circuit.consecutive_successes = 0
            circuit.backoff_seconds = 0
            circuit.updated_at = now.isoformat()
            return CIRCUIT_STATES["CLOSED"], "Circuit CLOSED: recovery confirmed"

    return circuit.state, f"Circuit {circuit.state.upper()}: normal operation"


def record_result(circuit: CircuitBreaker, success: bool) -> tuple[str, str]:
    """记录调用结果

    Returns:
        (new_state, reason)
    """
    now = datetime.now(timezone.utc)
    circuit.updated_at = now.isoformat()

    if success:
        circuit.success_count += 1
        circuit.total_successes += 1
        circuit.consecutive_successes += 1
        circuit.consecutive_failures = 0
        circuit.last_success_time = now.isoformat()

        # HALF_OPEN 状态下成功
        if circuit.state == CIRCUIT_STATES["HALF_OPEN"]:
            if circuit.success_count >= CIRCUIT_CONFIG["success_threshold"]:
                circuit.state = CIRCUIT_STATES["CLOSED"]
                circuit.backoff_seconds = 0
                return CIRCUIT_STATES["CLOSED"], f"Circuit CLOSED: {circuit.success_count} successes in HALF_OPEN"
            else:
                return CIRCUIT_STATES["HALF_OPEN"], f"Circuit HALF_OPEN: {circuit.success_count}/{CIRCUIT_CONFIG['success_threshold']} successes"

    else:
        circuit.failure_count += 1
        circuit.total_failures += 1
        circuit.consecutive_failures += 1
        circuit.consecutive_successes = 0
        circuit.last_failure_time = now.isoformat()

        # CLOSED 状态下失败
        if circuit.state == CIRCUIT_STATES["CLOSED"]:
            if circuit.consecutive_failures >= CIRCUIT_CONFIG["failure_threshold"]:
                circuit.state = CIRCUIT_STATES["OPEN"]
                circuit.backoff_seconds = min(
                    CIRCUIT_CONFIG["exponential_base"] ** circuit.consecutive_failures,
                    CIRCUIT_CONFIG["max_backoff_seconds"]
                )
                return CIRCUIT_STATES["OPEN"], f"Circuit OPEN: {circuit.consecutive_failures} consecutive failures"

        # HALF_OPEN 状态下失败
        elif circuit.state == CIRCUIT_STATES["HALF_OPEN"]:
            circuit.state = CIRCUIT_STATES["OPEN"]
            circuit.backoff_seconds = min(
                CIRCUIT_CONFIG["exponential_base"] ** circuit.consecutive_failures,
                CIRCUIT_CONFIG["max_backoff_seconds"]
            )
            return CIRCUIT_STATES["OPEN"], f"Circuit OPEN: failure in HALF_OPEN, back to OPEN"

    return circuit.state, f"Circuit {circuit.state.upper()}: recorded {'success' if success else 'failure'}"


# ── 日志 ──────────────────────────────────────────────────────────────────

def log_circuit_event(service: str, event_type: str, state: str, reason: str) -> None:
    """记录熔断事件到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "event_type": event_type,
        "state": state,
        "reason": reason,
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(CIRCUIT_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[circuit-breaker] Warning: log write failed: {e}", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: liyu-circuit-breaker.py check <service>", file=sys.stderr)
            sys.exit(1)

        service = sys.argv[2]
        circuits = load_circuits()
        circuit = get_or_create_circuit(circuits, service)

        state, reason = check_circuit(circuit)
        save_circuits(circuits)
        log_circuit_event(service, "check", state, reason)

        state_icon = {
            "closed": "🟢",
            "open": "🔴",
            "half_open": "🟡",
        }

        print(f"{state_icon.get(state, '❓')} Service: {service}")
        print(f"  State: {state.upper()}")
        print(f"  Reason: {reason}")
        print(f"  Consecutive failures: {circuit.consecutive_failures}")
        print(f"  Consecutive successes: {circuit.consecutive_successes}")
        print(f"  Backoff: {circuit.backoff_seconds:.0f}s")

        if state == CIRCUIT_STATES["OPEN"]:
            sys.exit(2)
        else:
            sys.exit(0)

    elif cmd == "record":
        if len(sys.argv) < 4:
            print("Usage: liyu-circuit-breaker.py record <service> <success|failure>", file=sys.stderr)
            sys.exit(1)

        service = sys.argv[2]
        result = sys.argv[3].lower()

        if result not in ["success", "failure"]:
            print(f"Invalid result: {result}. Must be 'success' or 'failure'", file=sys.stderr)
            sys.exit(1)

        circuits = load_circuits()
        circuit = get_or_create_circuit(circuits, service)

        new_state, reason = record_result(circuit, result == "success")
        save_circuits(circuits)
        log_circuit_event(service, "record", new_state, reason)

        state_icon = {
            "closed": "🟢",
            "open": "🔴",
            "half_open": "🟡",
        }

        print(f"{state_icon.get(new_state, '❓')} Service: {service}")
        print(f"  State: {new_state.upper()}")
        print(f"  Reason: {reason}")
        print(f"  Total failures: {circuit.total_failures}")
        print(f"  Total successes: {circuit.total_successes}")

    elif cmd == "stats":
        circuits = load_circuits()

        print("═══ 鲤鱼 Circuit Breaker Statistics ═══")
        print(f"  Services: {len(circuits)}")
        print()

        for service, circuit in sorted(circuits.items()):
            state_icon = {
                "closed": "🟢",
                "open": "🔴",
                "half_open": "🟡",
            }
            print(f"  {state_icon.get(circuit.state, '❓')} {service}:")
            print(f"    State: {circuit.state.upper()}")
            print(f"    Consecutive failures: {circuit.consecutive_failures}")
            print(f"    Total: {circuit.total_successes} success, {circuit.total_failures} failure")
            if circuit.backoff_seconds > 0:
                print(f"    Backoff: {circuit.backoff_seconds:.0f}s")

    elif cmd == "reset":
        save_circuits({})
        print("✅ Circuit Breaker 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
