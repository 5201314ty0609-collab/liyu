#!/usr/bin/env python3
"""
鲤鱼 Tool Guard 测试套件

测试三种检测器的完整升级链路：
  1. 精确失败: ALLOW → WARN(2) → BLOCK(4) → HALT(6)
  2. 同工具失败: ALLOW → WARN(3) → BLOCK(6) → HALT(10)
  3. 无进展: ALLOW → WARN(2) → BLOCK(4) → HALT(6)
"""

from pathlib import Path
import json
import subprocess
import sys

GUARD = Path.home() / ".claude/liyu/tool-guard.py"
PASS = 0
FAIL = 0


def run(cmd: list, stdin_text: str = None) -> subprocess.CompletedProcess:
    """运行 tool-guard 命令"""
    full_cmd = [sys.executable, str(GUARD)] + cmd
    result = subprocess.run(
        full_cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result


def reset():
    """重置状态"""
    run(["reset"])


def assert_decision(cmd: list, expected_action: str, description: str,
                    stdin_text: str = None, exit_ok: bool = True):
    """断言决策级别"""
    global PASS, FAIL
    result = run(cmd, stdin_text)
    try:
        data = json.loads(result.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        data = {}

    actual = data.get("action", data.get("decision", "unknown"))

    if actual == expected_action:
        PASS += 1
        print(f"  ✅ {description}")
    else:
        FAIL += 1
        print(f"  ❌ {description}")
        print(f"     expected={expected_action} actual={actual}")
        print(f"     stdout={result.stdout[:200]}")


# ── Test 1: Exact Failure Detection ─────────────────────────────────────
print("\n═══ 测试1: 精确失败 (同参数连续失败) ═══")
reset()
TOOL = "Bash"
ARGS = '{"command":"cat /nonexistent"}'

# 1st failure → ALLOW
assert_decision(["observe", TOOL, ARGS, "--error"], "allow",
                "第1次失败 → ALLOW")
# 2nd failure → WARN (exact_fail.warn=2)
assert_decision(["observe", TOOL, ARGS, "--error"], "warn",
                "第2次失败 → WARN")
# 3rd failure → still WARN
assert_decision(["observe", TOOL, ARGS, "--error"], "warn",
                "第3次失败 → WARN")
# 4th failure → BLOCK (exact_fail.block=4)
assert_decision(["observe", TOOL, ARGS, "--error"], "block",
                "第4次失败 → BLOCK")
# 5th failure → still BLOCK
assert_decision(["observe", TOOL, ARGS, "--error"], "block",
                "第5次失败 → BLOCK")
# 6th failure → HALT (exact_fail.halt=6)
assert_decision(["observe", TOOL, ARGS, "--error"], "halt",
                "第6次失败 → HALT")

# Success resets counter
assert_decision(["observe", TOOL, ARGS, "--result", "ok"], "allow",
                "成功后重置 → ALLOW")


# ── Test 2: Same-Tool Failure Detection ─────────────────────────────────
print("\n═══ 测试2: 同工具失败 (不同参数连续失败) ═══")
reset()
TOOL = "Bash"

for i in range(1, 11):
    args = f'{{"command":"cmd{i}"}}'
    if i < 3:
        expected = "allow"
    elif i < 6:
        expected = "warn"
    elif i < 10:
        expected = "block"
    else:
        expected = "halt"
    assert_decision(["observe", TOOL, args, "--error"], expected,
                    f"第{i}次不同参数失败 → {expected.upper()}")


# ── Test 3: No-Progress Detection ───────────────────────────────────────
print("\n═══ 测试3: 无进展 (幂等工具相同结果) ═══")
reset()
TOOL = "Read"
ARGS = '{"file_path":"/tmp/test.txt"}'
RESULT = "hello world"

for i in range(1, 7):
    if i < 3:
        expected = "allow"
    elif i < 5:
        expected = "warn"
    else:
        expected = "block"
    assert_decision(["observe", TOOL, ARGS, "--result", RESULT], expected,
                    f"第{i}次相同结果 → {expected.upper()}")

# Different result resets counter
assert_decision(["observe", TOOL, ARGS, "--result", "different content"], "allow",
                "不同结果 → 重置计数器 → ALLOW")


# ── Test 4: Pre-execution Check ─────────────────────────────────────────
print("\n═══ 测试4: 预检查 (hook-pre) ═══")
reset()

# Accumulate failures
for _ in range(4):
    run(["observe", "Bash", '{"command":"dangerous"}', "--error"])

# Check should block the same args
stdin = json.dumps({"tool_name": "Bash", "tool_input": {"command": "dangerous"}})
result = run(["hook-pre"], stdin)
data = json.loads(result.stdout.strip())
if data["decision"] == "block":
    PASS += 1
    print("  ✅ 预检查阻断: 已失败的同参数调用被阻止")
else:
    FAIL += 1
    print(f"  ❌ 预检查应阻断但返回了 {data['decision']}")

# Check should allow different args
stdin2 = json.dumps({"tool_name": "Bash", "tool_input": {"command": "safe"}})
result2 = run(["hook-pre"], stdin2)
data2 = json.loads(result2.stdout.strip())
if data2["decision"] == "allow":
    PASS += 1
    print("  ✅ 预检查放行: 新参数调用被允许")
else:
    FAIL += 1
    print(f"  ❌ 预检查应放行但返回了 {data2['decision']}")


# ── Test 5: Idempotent Tool Classification ──────────────────────────────
print("\n═══ 测试5: 幂等工具分类 ═══")
reset()

# Read is idempotent
for _ in range(3):
    run(["observe", "Read", '{"file_path":"/tmp/x"}', "--result", "same"])

# After 3 same results (count=2), should be WARN
assert_decision(["observe", "Read", '{"file_path":"/tmp/x"}', "--result", "same"],
                "warn", "Read(幂等) 3次相同结果 → WARN (无进展检测触发)")

reset()
# Write is mutating, no-progress should NOT trigger
for _ in range(5):
    assert_decision(["observe", "Write", '{"file_path":"/tmp/x"}', "--result", "same"],
                    "allow", f"Write(变更) 相同结果 → ALLOW (不触发无进展检测)")


# ── Test 6: Stats ───────────────────────────────────────────────────────
print("\n═══ 测试6: 统计信息 ═══")
result = run(["stats"])
if result.returncode == 0:
    PASS += 1
    print("  ✅ stats 命令正常执行")
else:
    FAIL += 1
    print(f"  ❌ stats 命令失败: {result.stderr}")


# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'═'*50}")
print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
print(f"  通过率: {PASS / (PASS + FAIL) * 100:.0f}%")
print(f"{'═'*50}")

sys.exit(0 if FAIL == 0 else 1)
