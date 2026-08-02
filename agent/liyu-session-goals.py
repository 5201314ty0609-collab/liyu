#!/usr/bin/env python3
"""
鲤鱼 Session Goals — 声明式目标驱动的自动延续
吸收自 bytedance/deer-flow 的 Session Goals 机制

核心理念：
  - 目标是线程级状态，不是技能激活
  - 目标持久跨回合，直到系统判定满足或用户清除
  - 评估循环自动检查目标完成状态

Usage:
  liyu-session-goals.py set <goal>
    设置目标

  liyu-session-goals.py get
    查看当前目标

  liyu-session-goals.py clear
    清除目标

  liyu-session-goals.py evaluate
    评估目标完成状态

  liyu-session-goals.py stats
    查看目标统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
GOALS_STATE_FILE = 鲤鱼_HOME / "session-goals-state.json"
GOALS_LOG_FILE = 鲤鱼_HOME / "session-goals-log.jsonl"

# ── 阻塞器类型 ──────────────────────────────────────────────────────────
BLOCKER_TYPES = [
    "missing_evidence",    # 缺少证据
    "needs_user_input",    # 需要用户输入
    "run_failed",          # 运行失败
    "external_wait",       # 等待外部
    "goal_not_met_yet",    # 目标未达成
]

# ── 安全上限 ──────────────────────────────────────────────────────────
MAX_HIDDEN_CONTINUATIONS = 8      # 最大隐藏延续次数
MAX_SAME_BLOCKER_STOPS = 2        # 相同阻塞器最大停止次数

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class SessionGoal:
    """会话目标"""
    goal_id: str
    goal_text: str
    created_at: str
    status: str                    # active / completed / cleared
    blocker_type: Optional[str]    # 当前阻塞器类型
    blocker_evidence: Optional[str] # 阻塞器证据
    continuation_count: int        # 隐藏延续次数
    same_blocker_count: int        # 相同阻塞器次数
    last_evaluated_at: Optional[str]
    completed_at: Optional[str]

# ── 目标管理 ──────────────────────────────────────────────────────────────

def load_goals_state() -> dict:
    """加载目标状态"""
    if GOALS_STATE_FILE.exists():
        try:
            return json.loads(GOALS_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "current_goal": None,
        "goal_history": [],
        "stats": {
            "total_goals": 0,
            "completed_goals": 0,
            "cleared_goals": 0,
            "total_continuations": 0,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_goals_state(state: dict) -> None:
    """持久化目标状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    GOALS_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def log_goal_event(event_type: str, details: dict) -> None:
    """记录目标事件到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details,
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(GOALS_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[session-goals] Warning: log write failed: {e}", file=sys.stderr)

# ── 目标操作 ──────────────────────────────────────────────────────────────

def set_goal(goal_text: str) -> dict:
    """设置目标"""
    state = load_goals_state()
    now = datetime.now(timezone.utc).isoformat()

    # 如果已有活跃目标，先归档
    if state["current_goal"]:
        old_goal = state["current_goal"]
        old_goal["status"] = "cleared"
        old_goal["completed_at"] = now
        state["goal_history"].append(old_goal)

    # 创建新目标
    goal_id = f"goal-{len(state['goal_history']) + 1}"
    new_goal = {
        "goal_id": goal_id,
        "goal_text": goal_text,
        "created_at": now,
        "status": "active",
        "blocker_type": None,
        "blocker_evidence": None,
        "continuation_count": 0,
        "same_blocker_count": 0,
        "last_evaluated_at": None,
        "completed_at": None,
    }

    state["current_goal"] = new_goal
    state["stats"]["total_goals"] += 1

    save_goals_state(state)
    log_goal_event("goal_set", {"goal_id": goal_id, "goal_text": goal_text})

    return {
        "goal_id": goal_id,
        "goal_text": goal_text,
        "status": "active",
    }

def get_goal() -> Optional[dict]:
    """获取当前目标"""
    state = load_goals_state()
    return state.get("current_goal")

def clear_goal() -> dict:
    """清除目标"""
    state = load_goals_state()
    now = datetime.now(timezone.utc).isoformat()

    if not state["current_goal"]:
        return {"message": "No active goal to clear"}

    goal = state["current_goal"]
    goal["status"] = "cleared"
    goal["completed_at"] = now

    state["goal_history"].append(goal)
    state["current_goal"] = None
    state["stats"]["cleared_goals"] += 1

    save_goals_state(state)
    log_goal_event("goal_cleared", {"goal_id": goal["goal_id"]})

    return {
        "goal_id": goal["goal_id"],
        "goal_text": goal["goal_text"],
        "status": "cleared",
    }

def evaluate_goal(conversation_text: str) -> dict:
    """评估目标完成状态

    Args:
        conversation_text: 当前对话文本

    Returns:
        评估结果，包括是否应该继续、阻塞器类型等
    """
    state = load_goals_state()

    if not state["current_goal"]:
        return {"should_continue": False, "reason": "No active goal"}

    goal = state["current_goal"]
    now = datetime.now(timezone.utc).isoformat()
    goal["last_evaluated_at"] = now

    # 简化版评估：基于关键词检测
    blocker_type = None
    blocker_evidence = None

    goal_lower = goal["goal_text"].lower()
    conv_lower = conversation_text.lower()

    # 检查是否完成
    if any(word in conv_lower for word in ["完成", "done", "completed", "finished", "✅"]):
        # 检查是否有明确的完成证据
        if any(word in conv_lower for word in ["测试通过", "tests pass", "构建成功", "build success"]):
            goal["status"] = "completed"
            goal["completed_at"] = now
            state["current_goal"] = None
            state["stats"]["completed_goals"] += 1

            save_goals_state(state)
            log_goal_event("goal_completed", {"goal_id": goal["goal_id"]})

            return {
                "should_continue": False,
                "reason": "Goal completed",
                "goal_id": goal["goal_id"],
            }

    # 检查阻塞器
    if any(word in conv_lower for word in ["错误", "error", "failed", "失败"]):
        blocker_type = "run_failed"
        blocker_evidence = "检测到错误关键词"

    elif any(word in conv_lower for word in ["等待", "waiting", "pending"]):
        blocker_type = "external_wait"
        blocker_evidence = "检测到等待关键词"

    elif any(word in conv_lower for word in ["需要", "need", "require"]):
        blocker_type = "needs_user_input"
        blocker_evidence = "检测到需求关键词"

    else:
        blocker_type = "goal_not_met_yet"
        blocker_evidence = "目标未达成"

    # 更新阻塞器
    if blocker_type:
        if blocker_type == goal.get("blocker_type"):
            goal["same_blocker_count"] += 1
        else:
            goal["same_blocker_count"] = 1

        goal["blocker_type"] = blocker_type
        goal["blocker_evidence"] = blocker_evidence

    # 检查安全上限
    if goal["continuation_count"] >= MAX_HIDDEN_CONTINUATIONS:
        save_goals_state(state)
        return {
            "should_continue": False,
            "reason": "Max continuations reached",
            "blocker_type": blocker_type,
        }

    if goal["same_blocker_count"] >= MAX_SAME_BLOCKER_STOPS:
        save_goals_state(state)
        return {
            "should_continue": False,
            "reason": "Same blocker too many times",
            "blocker_type": blocker_type,
        }

    # 应该继续
    goal["continuation_count"] += 1
    state["stats"]["total_continuations"] += 1

    save_goals_state(state)
    log_goal_event("goal_evaluated", {
        "goal_id": goal["goal_id"],
        "blocker_type": blocker_type,
        "continuation_count": goal["continuation_count"],
    })

    return {
        "should_continue": True,
        "reason": "Goal not met yet",
        "blocker_type": blocker_type,
        "blocker_evidence": blocker_evidence,
        "continuation_count": goal["continuation_count"],
    }

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "set":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-goals.py set '<goal>'", file=sys.stderr)
            sys.exit(1)

        goal_text = " ".join(sys.argv[2:])
        result = set_goal(goal_text)

        print(f"🎯 目标已设置:")
        print(f"  ID: {result['goal_id']}")
        print(f"  目标: {result['goal_text']}")
        print(f"  状态: {result['status']}")

    elif cmd == "get":
        goal = get_goal()

        if goal:
            print(f"🎯 当前目标:")
            print(f"  ID: {goal['goal_id']}")
            print(f"  目标: {goal['goal_text']}")
            print(f"  状态: {goal['status']}")
            print(f"  创建时间: {goal['created_at']}")
            print(f"  延续次数: {goal['continuation_count']}")
            if goal.get("blocker_type"):
                print(f"  阻塞器: {goal['blocker_type']}")
                print(f"  证据: {goal['blocker_evidence']}")
        else:
            print("❌ 没有活跃目标")

    elif cmd == "clear":
        result = clear_goal()

        if result.get("goal_id"):
            print(f"✅ 目标已清除:")
            print(f"  ID: {result['goal_id']}")
            print(f"  目标: {result['goal_text']}")
        else:
            print(f"ℹ️ {result['message']}")

    elif cmd == "evaluate":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-goals.py evaluate '<conversation>'", file=sys.stderr)
            sys.exit(1)

        conversation = " ".join(sys.argv[2:])
        result = evaluate_goal(conversation)

        if result["should_continue"]:
            print(f"🔄 应该继续:")
            print(f"  原因: {result['reason']}")
            print(f"  阻塞器: {result.get('blocker_type', 'none')}")
            print(f"  证据: {result.get('blocker_evidence', 'none')}")
            print(f"  延续次数: {result.get('continuation_count', 0)}")
        else:
            print(f"⏹️ 不应继续:")
            print(f"  原因: {result['reason']}")
            if result.get("blocker_type"):
                print(f"  阻塞器: {result['blocker_type']}")

    elif cmd == "stats":
        state = load_goals_state()
        stats = state.get("stats", {})

        print("═══ 鲤鱼 Session Goals Statistics ═══")
        print(f"  总计目标:     {stats.get('total_goals', 0)}")
        print(f"  已完成:       {stats.get('completed_goals', 0)}")
        print(f"  已清除:       {stats.get('cleared_goals', 0)}")
        print(f"  总延续次数:   {stats.get('total_continuations', 0)}")
        print()

        if state.get("current_goal"):
            goal = state["current_goal"]
            print(f"  🎯 当前目标: {goal['goal_text']}")
            print(f"     状态: {goal['status']}")
            print(f"     延续次数: {goal['continuation_count']}")

    elif cmd == "reset":
        save_goals_state({
            "current_goal": None,
            "goal_history": [],
            "stats": {
                "total_goals": 0,
                "completed_goals": 0,
                "cleared_goals": 0,
                "total_continuations": 0,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Session Goals 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
