#!/usr/bin/env python3
"""
鲤鱼 Tool Guard — 工具循环防护系统
Absorbed from MUNDO v2.0.9 tool_guard.py (Hermes Agent pattern)

三种检测器：
  1. 精确失败 — 同工具+同参数连续失败 → 死循环
  2. 同工具失败 — 同工具不同参数连续失败 → 工具本身坏了
  3. 无进展 — 幂等工具连续调用结果不变 → 原地踏步

四级决策链：
  ALLOW → WARN → BLOCK → HALT

Usage:
  tool-guard.py observe <tool_name> <args_json> [--result <text>] [--error]
    观察一次工具调用，更新计数器，返回决策

  tool-guard.py check <tool_name> <args_json>
    在工具执行前检查历史，返回是否应该阻止

  tool-guard.py reset [<tool_name>]
    重置所有（或指定工具的）计数器

  tool-guard.py stats
    查看当前防护状态

  tool-guard.py config
    查看当前配置

Hook 集成（settings.json）:
  {
    "matcher": "",
    "command": "python3 ~/.claude/liyu/tool-guard.py observe $TOOL_NAME $TOOL_ARGS_JSON --result $TOOL_RESULT"
  }
"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import hashlib
import json
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
STATE_FILE = 鲤鱼_HOME / "tool-guard-state.json"
CONFIG_FILE = 鲤鱼_HOME / "tool-guard-config.json"
HISTORY_FILE = 鲤鱼_HOME / "tool-guard-history.jsonl"

# ── Constants ──────────────────────────────────────────────────────────────

# 幂等工具：读取操作，重复调用无副作用
IDEMPOTENT_TOOLS = frozenset({
    "Read", "Grep", "Glob", "WebSearch", "WebFetch",
    "TaskList", "TaskGet", "CronList",
})

# 变更工具：写入操作，重复调用有副作用
MUTATING_TOOLS = frozenset({
    "Bash", "Write", "Edit", "TaskCreate", "TaskUpdate",
    "NotebookEdit", "CronCreate", "CronDelete",
})

# 只读 Bash 模式（高概率幂等）
READONLY_BASH_PATTERNS = [
    "ls", "cat", "head", "tail", "find", "grep", "git log",
    "git diff", "git status", "git show", "echo", "wc",
    "which", "type", "python --version", "node --version",
    "git branch", "git remote", "gh pr view", "gh issue view",
    "du ", "df ", "ps ", "whoami", "pwd", "date",
]

# ── Default Configuration ──────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "source": "MUNDO v2.0.9 Tool Guard",
    "thresholds": {
        "exact_fail": {
            "warn": 2,    # 同参数连续失败 2 次 → 警告
            "block": 4,   # 同参数连续失败 4 次 → 阻断
            "halt": 6,    # 同参数连续失败 6 次 → 强制中止
        },
        "same_tool_fail": {
            "warn": 3,    # 同工具连续失败 3 次 → 警告
            "block": 6,   # 同工具连续失败 6 次 → 阻断
            "halt": 10,   # 同工具连续失败 10 次 → 强制中止
        },
        "no_progress": {
            "warn": 2,    # 幂等工具连续 2 次相同结果 → 警告
            "block": 4,   # 幂等工具连续 4 次相同结果 → 阻断
            "halt": 6,    # 幂等工具连续 6 次相同结果 → 强制中止
        },
    },
    "hard_stop_enabled": True,   # HALT 级别是否真正阻断 (exit 2)
    "observe_window": 50,         # 观察窗口（最近 N 次调用）
    "reset_on_success": True,     # 成功后重置对应计数器
}

# ── State ──────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """加载持久化状态"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "exact_failures": {},        # {"tool_name:args_hash": count}
        "tool_failures": {},         # {"tool_name": count}
        "no_progress": {},           # {"tool_name": count}
        "last_idempotent_results": {},  # {"tool_name": "result_hash"}
        "recent_calls": [],          # 最近调用记录
        "total_observed": 0,
        "total_warned": 0,
        "total_blocked": 0,
        "total_halted": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_state(state: dict) -> None:
    """持久化状态到 JSON"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def load_config() -> dict:
    """加载配置，深度合并用户覆盖"""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return deepcopy(DEFAULT_CONFIG)
        merged = deepcopy(DEFAULT_CONFIG)
        _deep_merge(merged, cfg)
        return merged
    return deepcopy(DEFAULT_CONFIG)


def _deep_merge(base: dict, override: dict) -> None:
    """递归合并 override 到 base（原地修改 base）"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value

# ── Helpers ────────────────────────────────────────────────────────────────

def args_hash(args: dict) -> str:
    """计算参数的标准哈希（排序键确保一致性）"""
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

def result_hash(result: str) -> str:
    """计算结果的标准哈希"""
    return hashlib.sha256(result.encode()).hexdigest()[:16]

def is_idempotent(tool_name: str, args: Optional[dict] = None) -> bool:
    """判断工具调用是否幂等"""
    if tool_name in IDEMPOTENT_TOOLS:
        return True
    if tool_name in MUTATING_TOOLS:
        return False
    # Bash 特殊处理：检查命令是否只读
    if tool_name == "Bash" and args:
        command = args.get("command", "")
        for pattern in READONLY_BASH_PATTERNS:
            if command.strip().startswith(pattern):
                return True
    return False

def _format_args(args: dict, max_len: int = 120) -> str:
    """格式化参数用于日志显示"""
    s = json.dumps(args, ensure_ascii=False)
    return s[:max_len] + ("..." if len(s) > max_len else "")

# ── Core Engine ────────────────────────────────────────────────────────────

class ToolGuard:
    """工具循环防护引擎"""

    def __init__(self):
        self.config = load_config()
        self.state = load_state()
        self.thresholds = self.config["thresholds"]

    # ── Observe ──────────────────────────────────────────────────────────

    def observe(self, tool_name: str, args: dict, result: str = "",
                is_error: bool = False) -> dict:
        """观察一次工具调用，更新计数器，返回决策"""
        ahash = args_hash(args)
        cfg_exact = self.thresholds["exact_fail"]
        cfg_tool = self.thresholds["same_tool_fail"]
        cfg_progress = self.thresholds["no_progress"]
        hard_stop = self.config["hard_stop_enabled"]

        decision = {"action": "allow", "code": "ok", "message": "", "detectors": []}

        # ── 1. 精确失败检测 ──
        exact_key = f"{tool_name}:{ahash}"
        if is_error:
            self.state["exact_failures"][exact_key] = \
                self.state["exact_failures"].get(exact_key, 0) + 1
            count = self.state["exact_failures"][exact_key]

            if count >= cfg_exact["halt"] and hard_stop:
                decision = _merge_decision(decision, {
                    "action": "halt", "code": "exact_fail_halt",
                    "message": f"[御史弹劾] {tool_name} 同参数连续失败 {count} 次，判定死循环，强制中止",
                    "detectors": ["exact_fail"],
                })
            elif count >= cfg_exact["block"]:
                decision = _merge_decision(decision, {
                    "action": "block", "code": "exact_fail_block",
                    "message": f"[御史谏言] {tool_name} 同参数连续失败 {count} 次，建议阻断",
                    "detectors": ["exact_fail"],
                })
            elif count >= cfg_exact["warn"]:
                decision = _merge_decision(decision, {
                    "action": "warn", "code": "exact_fail_warn",
                    "message": f"[朝臣谏言] {tool_name} 同参数连续失败 {count} 次，建议换策略",
                    "detectors": ["exact_fail"],
                })
        else:
            # 成功 → 重置精确失败计数（针对这个具体参数）
            if self.config["reset_on_success"]:
                self.state["exact_failures"].pop(exact_key, None)

        # ── 2. 同工具失败检测 ──
        if is_error:
            self.state["tool_failures"][tool_name] = \
                self.state["tool_failures"].get(tool_name, 0) + 1
            count = self.state["tool_failures"][tool_name]

            if count >= cfg_tool["halt"] and hard_stop:
                decision = _merge_decision(decision, {
                    "action": "halt", "code": "same_tool_halt",
                    "message": f"[御史弹劾] {tool_name} 连续失败 {count} 次，强制中止",
                    "detectors": ["same_tool_fail"],
                })
            elif count >= cfg_tool["block"]:
                decision = _merge_decision(decision, {
                    "action": "block", "code": "same_tool_block",
                    "message": f"[御史谏言] {tool_name} 连续失败 {count} 次，工具可能不可用",
                    "detectors": ["same_tool_fail"],
                })
            elif count >= cfg_tool["warn"]:
                decision = _merge_decision(decision, {
                    "action": "warn", "code": "same_tool_warn",
                    "message": f"[朝臣谏言] {tool_name} 连续失败 {count} 次，建议换工具",
                    "detectors": ["same_tool_fail"],
                })
        else:
            if self.config["reset_on_success"]:
                self.state["tool_failures"].pop(tool_name, None)

        # ── 3. 无进展检测（仅幂等工具）──
        if not is_error and is_idempotent(tool_name, args):
            rhash = result_hash(result)
            last_hash = self.state["last_idempotent_results"].get(tool_name)

            if last_hash == rhash and result.strip():
                self.state["no_progress"][tool_name] = \
                    self.state["no_progress"].get(tool_name, 0) + 1
                count = self.state["no_progress"][tool_name]

                if count >= cfg_progress["halt"] and hard_stop:
                    decision = _merge_decision(decision, {
                        "action": "halt", "code": "no_progress_halt",
                        "message": f"[御史弹劾] {tool_name} 连续 {count + 1} 次返回相同结果，原地踏步，强制中止",
                        "detectors": ["no_progress"],
                    })
                elif count >= cfg_progress["block"]:
                    decision = _merge_decision(decision, {
                        "action": "block", "code": "no_progress_block",
                        "message": f"[御史谏言] {tool_name} 连续 {count + 1} 次返回相同结果，原地踏步",
                        "detectors": ["no_progress"],
                    })
                elif count >= cfg_progress["warn"]:
                    decision = _merge_decision(decision, {
                        "action": "warn", "code": "no_progress_warn",
                        "message": f"[朝臣谏言] {tool_name} 连续 {count + 1} 次返回相同结果，可能在原地踏步",
                        "detectors": ["no_progress"],
                    })
            else:
                self.state["no_progress"].pop(tool_name, None)

            if result.strip():
                self.state["last_idempotent_results"][tool_name] = rhash

        # ── 更新统计 ──
        self.state["total_observed"] += 1
        if decision["action"] == "warn":
            self.state["total_warned"] += 1
        elif decision["action"] == "block":
            self.state["total_blocked"] += 1
        elif decision["action"] == "halt":
            self.state["total_halted"] += 1

        # ── 记录最近调用 ──
        self.state["recent_calls"].append({
            "tool": tool_name,
            "args_hash": ahash,
            "is_error": is_error,
            "decision": decision["action"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self.state["recent_calls"]) > self.config["observe_window"]:
            self.state["recent_calls"] = \
                self.state["recent_calls"][-self.config["observe_window"]:]

        # ── 持久化 ──
        save_state(self.state)

        # ── 记录历史 ──
        _log_history(tool_name, ahash, decision, is_error)

        return decision

    # ── Check (Pre-execution) ─────────────────────────────────────────────

    def check(self, tool_name: str, args: dict) -> dict:
        """工具执行前的预检查。基于历史，返回是否应该阻止。"""
        ahash = args_hash(args)

        # 检查精确失败：这个具体参数之前是否失败过？
        exact_key = f"{tool_name}:{ahash}"
        exact_count = self.state["exact_failures"].get(exact_key, 0)
        if exact_count >= self.thresholds["exact_fail"]["block"]:
            return {
                "action": "block", "code": "exact_fail_history",
                "message": f"[御史预检] {tool_name} 该参数已连续失败 {exact_count} 次，拒绝重复执行",
            }

        # 检查同工具失败：该工具整体是否处于失败状态？
        tool_count = self.state["tool_failures"].get(tool_name, 0)
        if tool_count >= self.thresholds["same_tool_fail"]["block"]:
            return {
                "action": "block", "code": "tool_fail_history",
                "message": f"[御史预检] {tool_name} 已连续失败 {tool_count} 次，该工具可能不可用",
            }

        # 检查无进展：幂等工具是否在循环？
        if is_idempotent(tool_name, args):
            np_count = self.state["no_progress"].get(tool_name, 0)
            if np_count >= self.thresholds["no_progress"]["block"]:
                return {
                    "action": "block", "code": "no_progress_history",
                    "message": f"[御史预检] {tool_name} 已连续 {np_count + 1} 次无新结果，拒绝重复执行",
                }

        return {"action": "allow", "code": "ok", "message": "", "detectors": []}

    # ── Reset ─────────────────────────────────────────────────────────────

    def reset(self, tool_name: str = ""):
        """重置计数器"""
        if tool_name:
            # 重置特定工具的所有计数
            exact_keys = [k for k in self.state["exact_failures"] if k.startswith(f"{tool_name}:")]
            for k in exact_keys:
                del self.state["exact_failures"][k]
            self.state["tool_failures"].pop(tool_name, None)
            self.state["no_progress"].pop(tool_name, None)
            self.state["last_idempotent_results"].pop(tool_name, None)
        else:
            # 全量重置
            self.state["exact_failures"] = {}
            self.state["tool_failures"] = {}
            self.state["no_progress"] = {}
            self.state["last_idempotent_results"] = {}
            self.state["recent_calls"] = []

        save_state(self.state)
        return {"action": "reset", "tool": tool_name or "all"}

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """返回当前防护状态"""
        return {
            "config_version": self.config["version"],
            "hard_stop": self.config["hard_stop_enabled"],
            "thresholds": self.thresholds,
            "exact_failures": dict(self.state["exact_failures"]),
            "tool_failures": dict(self.state["tool_failures"]),
            "no_progress": dict(self.state["no_progress"]),
            "summary": {
                "total_observed": self.state["total_observed"],
                "total_warned": self.state["total_warned"],
                "total_blocked": self.state["total_blocked"],
                "total_halted": self.state["total_halted"],
                "active_alerts": len([
                    k for k, v in self.state["exact_failures"].items()
                    if v >= self.thresholds["exact_fail"]["warn"]
                ]),
            },
        }


# ── Helpers ────────────────────────────────────────────────────────────────

def _merge_decision(existing: dict, new: dict) -> dict:
    """合并决策，取最严重的。优先级: halt > block > warn > allow。
    同级别时合并 message 和 detectors 列表。不修改传入的 dict。"""
    severity = {"allow": 0, "warn": 1, "block": 2, "halt": 3}
    new_sev = severity.get(new["action"], 0)
    existing_sev = severity.get(existing["action"], 0)

    if new_sev > existing_sev:
        return new
    if new_sev == existing_sev:
        merged = dict(existing)
        if new.get("message") and new["message"] != merged.get("message", ""):
            merged["message"] = (
                f"{merged['message']}; {new['message']}"
                if merged.get("message") else new["message"]
            )
        merged["detectors"] = list(merged.get("detectors", [])) + list(new.get("detectors", []))
        return merged
    return existing

def _log_history(tool_name: str, ahash: str, decision: dict, is_error: bool) -> None:
    """记录到历史日志（best-effort，失败不阻塞主流程）"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "args_hash": ahash,
        "is_error": is_error,
        "decision": decision["action"],
        "code": decision["code"],
        "message": decision["message"],
    }
    try:
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[tool-guard] Warning: history write failed: {e}", file=sys.stderr)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    guard = ToolGuard()

    if cmd == "hook-pre":
        # Hook mode: read JSON from stdin, run pre-execution check
        # Input:  {"tool_name": "Bash", "tool_input": {"command": "..."}}
        # Output: {"decision": "allow|block", ...}
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, OSError):
            print(json.dumps({"decision": "allow", "reason": "invalid input"}))
            sys.exit(0)

        tool_name = hook_input.get("tool_name", "")
        args = hook_input.get("tool_input", {})
        decision = guard.check(tool_name, args)

        # Claude Code hook output format
        output = {
            "decision": decision["action"],
            "reason": decision["message"],
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny" if decision["action"] == "block" else "allow",
                "permissionDecisionReason": decision["message"],
            },
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(2 if decision["action"] == "block" else 0)

    elif cmd == "hook-post":
        # Hook mode: read JSON from stdin, observe tool result
        # Input:  {"tool_name": "Bash", "tool_input": {...}, "tool_output": "..."}
        try:
            hook_input = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, OSError):
            print(json.dumps({"decision": "allow", "reason": "invalid input"}))
            sys.exit(0)

        tool_name = hook_input.get("tool_name", "")
        args = hook_input.get("tool_input", {})
        result = hook_input.get("tool_output", "")
        is_error = hook_input.get("is_error", False)

        decision = guard.observe(tool_name, args, str(result), is_error)

        output = {
            "decision": decision["action"],
            "reason": decision["message"],
            "detectors": decision.get("detectors", []),
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "permissionDecision": "deny" if decision["action"] in ("halt", "block") else "allow",
                "permissionDecisionReason": decision["message"],
            },
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(2 if decision["action"] in ("halt", "block") else 0)

    elif cmd == "observe":
        # tool-guard.py observe <tool_name> <args_json> [--result <text>] [--error]
        if len(sys.argv) < 4:
            print("Usage: tool-guard.py observe <tool_name> <args_json> [--result <text>] [--error]")
            sys.exit(1)

        tool_name = sys.argv[2]
        try:
            args = json.loads(sys.argv[3])
        except json.JSONDecodeError:
            args = {"raw": sys.argv[3]}

        result_text = ""
        is_error = False
        for i, arg in enumerate(sys.argv):
            if arg == "--result" and i + 1 < len(sys.argv):
                result_text = sys.argv[i + 1]
            if arg == "--error":
                is_error = True

        decision = guard.observe(tool_name, args, result_text, is_error)

        # 输出决策 JSON（供 hook 解析）
        print(json.dumps(decision, ensure_ascii=False))

        # 根据决策返回 exit code
        if decision["action"] == "halt":
            sys.exit(2)   # 阻断
        elif decision["action"] == "block":
            sys.exit(2)   # 阻断
        elif decision["action"] == "warn":
            print(f"⚠️ {decision['message']}", file=sys.stderr)
            sys.exit(0)   # 警告但不阻断
        else:
            sys.exit(0)

    elif cmd == "check":
        # tool-guard.py check <tool_name> <args_json>
        if len(sys.argv) < 4:
            print("Usage: tool-guard.py check <tool_name> <args_json>")
            sys.exit(1)

        tool_name = sys.argv[2]
        try:
            args = json.loads(sys.argv[3])
        except json.JSONDecodeError:
            args = {"raw": sys.argv[3]}

        decision = guard.check(tool_name, args)
        print(json.dumps(decision, ensure_ascii=False))

        if decision["action"] == "block":
            print(f"🚫 {decision['message']}", file=sys.stderr)
            sys.exit(2)
        else:
            sys.exit(0)

    elif cmd == "reset":
        tool_name = sys.argv[2] if len(sys.argv) > 2 else ""
        result = guard.reset(tool_name)
        print(f"✅ 已重置: {result['tool']}")

    elif cmd == "stats":
        s = guard.stats()
        print("═══ 鲤鱼 Tool Guard ───")
        print(f"  配置版本: {s['config_version']}")
        print(f"  硬停开关: {'🟢 开启' if s['hard_stop'] else '🔴 关闭'}")
        print()
        print(f"  总计观测: {s['summary']['total_observed']}")
        print(f"  警告次数: {s['summary']['total_warned']}")
        print(f"  阻断次数: {s['summary']['total_blocked']}")
        print(f"  强制中止: {s['summary']['total_halted']}")
        print(f"  活跃警报: {s['summary']['active_alerts']}")
        print()
        if s["exact_failures"]:
            print("  精确失败 (同参数):")
            for k, v in sorted(s["exact_failures"].items(), key=lambda x: -x[1]):
                tool, ahash = k.split(":", 1)
                threshold = s["thresholds"]["exact_fail"]
                icon = "🚫" if v >= threshold["halt"] else "⚠️" if v >= threshold["warn"] else "·"
                print(f"    {icon} {tool}:{ahash[:8]} → {v} 次")
        if s["tool_failures"]:
            print("  工具失败 (同工具):")
            for k, v in sorted(s["tool_failures"].items(), key=lambda x: -x[1]):
                threshold = s["thresholds"]["same_tool_fail"]
                icon = "🚫" if v >= threshold["halt"] else "⚠️" if v >= threshold["warn"] else "·"
                print(f"    {icon} {k} → {v} 次")
        if s["no_progress"]:
            print("  无进展 (幂等工具):")
            for k, v in sorted(s["no_progress"].items(), key=lambda x: -x[1]):
                threshold = s["thresholds"]["no_progress"]
                icon = "🚫" if v >= threshold["halt"] else "⚠️" if v >= threshold["warn"] else "·"
                print(f"    {icon} {k} → {v} 次")

    elif cmd == "config":
        cfg = load_config()
        print(json.dumps(cfg, ensure_ascii=False, indent=2))

    elif cmd == "init":
        # 初始化配置
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        print(f"✅ 配置已写入: {CONFIG_FILE}")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
