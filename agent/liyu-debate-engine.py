#!/usr/bin/env python3
"""
鲤鱼 Debate Engine — 结构化辩论决策机制
吸收自 TauricResearch/TradingAgents 的辩论决策模式

核心理念：
  - 通过结构化辩论让多个 Agent 产生超越个体的集体智慧
  - 辩论历史注入每个参与者的 prompt，确保真正的交锋
  - 裁判使用深度推理模型做最终裁决

Usage:
  liyu-debate-engine.py start <topic> <participants>
    开始辩论

  liyu-debate-engine.py respond <debate_id> <participant> <response>
    添加辩论发言

  liyu-debate-engine.py judge <debate_id>
    裁判裁决

  liyu-debate-engine.py stats
    查看辩论统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DEBATE_STATE_FILE = 鲤鱼_HOME / "debate-engine-state.json"
DEBATE_LOG_FILE = 鲤鱼_HOME / "debate-engine-log.jsonl"

# ── 辩论状态 ──────────────────────────────────────────────────────────────

@dataclass
class DebateState:
    """辩论状态"""
    debate_id: str
    topic: str
    participants: List[str]
    histories: Dict[str, List[str]]  # 每个参与者的发言历史
    current_speaker: str
    count: int
    max_rounds: int
    status: str                      # active / completed / judged
    judge_decision: Optional[str]
    created_at: str
    completed_at: Optional[str]

# ── 辩论管理 ──────────────────────────────────────────────────────────────

def load_debate_state() -> dict:
    """加载辩论状态"""
    if DEBATE_STATE_FILE.exists():
        try:
            return json.loads(DEBATE_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "debates": {},
        "stats": {
            "total_debates": 0,
            "completed_debates": 0,
            "total_rounds": 0,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_debate_state(state: dict) -> None:
    """持久化辩论状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    DEBATE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def log_debate_event(event_type: str, details: dict) -> None:
    """记录辩论事件到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details,
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(DEBATE_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[debate-engine] Warning: log write failed: {e}", file=sys.stderr)

# ── 辩论操作 ──────────────────────────────────────────────────────────────

def start_debate(topic: str, participants: List[str], max_rounds: int = 3) -> dict:
    """开始辩论"""
    state = load_debate_state()
    now = datetime.now(timezone.utc).isoformat()

    debate_id = f"debate-{len(state['debates']) + 1}"

    # 初始化每个参与者的历史
    histories = {p: [] for p in participants}

    debate = {
        "debate_id": debate_id,
        "topic": topic,
        "participants": participants,
        "histories": histories,
        "current_speaker": participants[0],
        "count": 0,
        "max_rounds": max_rounds,
        "status": "active",
        "judge_decision": None,
        "created_at": now,
        "completed_at": None,
    }

    state["debates"][debate_id] = debate
    state["stats"]["total_debates"] += 1

    save_debate_state(state)
    log_debate_event("debate_started", {
        "debate_id": debate_id,
        "topic": topic,
        "participants": participants,
    })

    return {
        "debate_id": debate_id,
        "topic": topic,
        "participants": participants,
        "current_speaker": participants[0],
        "status": "active",
    }

def add_response(debate_id: str, participant: str, response: str) -> dict:
    """添加辩论发言"""
    state = load_debate_state()

    if debate_id not in state["debates"]:
        return {"error": f"Debate not found: {debate_id}"}

    debate = state["debates"][debate_id]

    if debate["status"] != "active":
        return {"error": f"Debate is not active: {debate['status']}"}

    if participant not in debate["participants"]:
        return {"error": f"Participant not in debate: {participant}"}

    if participant != debate["current_speaker"]:
        return {"error": f"Not {participant}'s turn to speak"}

    # 添加发言
    debate["histories"][participant].append(response)
    debate["count"] += 1

    # 更新统计
    state["stats"]["total_rounds"] += 1

    # 判断下一个发言人
    current_idx = debate["participants"].index(participant)
    next_idx = (current_idx + 1) % len(debate["participants"])
    debate["current_speaker"] = debate["participants"][next_idx]

    # 检查是否应该结束辩论
    if debate["count"] >= debate["max_rounds"] * len(debate["participants"]):
        debate["status"] = "completed"
        debate["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["stats"]["completed_debates"] += 1

    save_debate_state(state)
    log_debate_event("response_added", {
        "debate_id": debate_id,
        "participant": participant,
        "count": debate["count"],
    })

    return {
        "debate_id": debate_id,
        "participant": participant,
        "count": debate["count"],
        "next_speaker": debate["current_speaker"],
        "status": debate["status"],
    }

def get_debate_history(debate_id: str) -> dict:
    """获取辩论历史"""
    state = load_debate_state()

    if debate_id not in state["debates"]:
        return {"error": f"Debate not found: {debate_id}"}

    debate = state["debates"][debate_id]

    # 构建完整辩论历史
    history_lines = []
    for i in range(max(len(h) for h in debate["histories"].values())):
        for participant in debate["participants"]:
            if i < len(debate["histories"][participant]):
                history_lines.append(f"[{participant}]: {debate['histories'][participant][i]}")

    return {
        "debate_id": debate_id,
        "topic": debate["topic"],
        "participants": debate["participants"],
        "count": debate["count"],
        "max_rounds": debate["max_rounds"],
        "status": debate["status"],
        "history": "\n\n".join(history_lines),
        "current_speaker": debate["current_speaker"],
    }

def judge_debate(debate_id: str, decision: str) -> dict:
    """裁判裁决"""
    state = load_debate_state()

    if debate_id not in state["debates"]:
        return {"error": f"Debate not found: {debate_id}"}

    debate = state["debates"][debate_id]

    if debate["status"] not in ["active", "completed"]:
        return {"error": f"Debate cannot be judged: {debate['status']}"}

    debate["judge_decision"] = decision
    debate["status"] = "judged"
    debate["completed_at"] = datetime.now(timezone.utc).isoformat()

    save_debate_state(state)
    log_debate_event("debate_judged", {
        "debate_id": debate_id,
        "decision": decision[:100],
    })

    return {
        "debate_id": debate_id,
        "status": "judged",
        "decision": decision,
    }

def get_next_speaker(debate_id: str) -> str:
    """获取下一个发言人"""
    state = load_debate_state()

    if debate_id not in state["debates"]:
        return None

    debate = state["debates"][debate_id]
    return debate["current_speaker"]

def should_continue_debate(debate_id: str) -> bool:
    """判断是否应该继续辩论"""
    state = load_debate_state()

    if debate_id not in state["debates"]:
        return False

    debate = state["debates"][debate_id]

    if debate["status"] != "active":
        return False

    # 检查轮次
    if debate["count"] >= debate["max_rounds"] * len(debate["participants"]):
        return False

    return True

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "start":
        if len(sys.argv) < 4:
            print("Usage: liyu-debate-engine.py start '<topic>' participant1 participant2 [participant3]", file=sys.stderr)
            sys.exit(1)

        topic = sys.argv[2]
        participants = sys.argv[3:]

        result = start_debate(topic, participants)

        print(f"🎤 辩论已开始:")
        print(f"  ID: {result['debate_id']}")
        print(f"  主题: {result['topic']}")
        print(f"  参与者: {', '.join(result['participants'])}")
        print(f"  当前发言人: {result['current_speaker']}")

    elif cmd == "respond":
        if len(sys.argv) < 5:
            print("Usage: liyu-debate-engine.py respond <debate_id> <participant> '<response>'", file=sys.stderr)
            sys.exit(1)

        debate_id = sys.argv[2]
        participant = sys.argv[3]
        response = " ".join(sys.argv[4:])

        result = add_response(debate_id, participant, response)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"✅ 发言已添加:")
        print(f"  辩论 ID: {result['debate_id']}")
        print(f"  发言者: {result['participant']}")
        print(f"  轮次: {result['count']}")
        print(f"  下一个发言人: {result['next_speaker']}")
        print(f"  状态: {result['status']}")

    elif cmd == "history":
        if len(sys.argv) < 3:
            print("Usage: liyu-debate-engine.py history <debate_id>", file=sys.stderr)
            sys.exit(1)

        debate_id = sys.argv[2]
        result = get_debate_history(debate_id)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"🎤 辩论历史:")
        print(f"  ID: {result['debate_id']}")
        print(f"  主题: {result['topic']}")
        print(f"  参与者: {', '.join(result['participants'])}")
        print(f"  轮次: {result['count']}/{result['max_rounds'] * len(result['participants'])}")
        print(f"  状态: {result['status']}")
        print(f"  当前发言人: {result['current_speaker']}")
        print()
        print("辩论内容:")
        print(result['history'])

    elif cmd == "judge":
        if len(sys.argv) < 4:
            print("Usage: liyu-debate-engine.py judge <debate_id> '<decision>'", file=sys.stderr)
            sys.exit(1)

        debate_id = sys.argv[2]
        decision = " ".join(sys.argv[3:])

        result = judge_debate(debate_id, decision)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"⚖️ 裁决已做出:")
        print(f"  辩论 ID: {result['debate_id']}")
        print(f"  状态: {result['status']}")
        print(f"  裁决: {result['decision']}")

    elif cmd == "stats":
        state = load_debate_state()
        stats = state.get("stats", {})

        print("═══ 鲤鱼 Debate Engine Statistics ═══")
        print(f"  总计辩论:     {stats.get('total_debates', 0)}")
        print(f"  已完成:       {stats.get('completed_debates', 0)}")
        print(f"  总轮次:       {stats.get('total_rounds', 0)}")
        print()

        # 显示活跃辩论
        active_debates = [d for d in state.get("debates", {}).values() if d["status"] == "active"]
        if active_debates:
            print("  活跃辩论:")
            for d in active_debates:
                print(f"    - {d['debate_id']}: {d['topic'][:50]}...")

    elif cmd == "reset":
        save_debate_state({
            "debates": {},
            "stats": {
                "total_debates": 0,
                "completed_debates": 0,
                "total_rounds": 0,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Debate Engine 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
