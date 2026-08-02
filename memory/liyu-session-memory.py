#!/usr/bin/env python3
"""
鲤鱼 Session Memory — 跨会话持久化记忆
吸收自 thedotmack/claude-mem 的跨 session 记忆机制

核心理念：
  - 捕获会话中的一切行为
  - AI 压缩后存储
  - 在未来会话中注入相关上下文

Usage:
  liyu-session-memory.py capture
    捕获当前会话

  liyu-session-memory.py inject <query>
    注入相关上下文

  liyu-session-memory.py search <query>
    搜索历史会话

  liyu-session-memory.py stats
    查看统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import hashlib
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SESSION_MEMORY_STATE_FILE = 鲤鱼_HOME / "session-memory-state.json"
SESSIONS_DIR = 鲤鱼_HOME / "sessions"
COMPRESSED_DIR = 鲤鱼_HOME / "compressed-sessions"

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class SessionRecord:
    """会话记录"""
    session_id: str
    started_at: str
    ended_at: Optional[str]
    summary: str
    key_decisions: List[str]
    files_modified: List[str]
    tools_used: List[str]
    importance: float

# ── 会话管理 ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    """加载状态"""
    if SESSION_MEMORY_STATE_FILE.exists():
        try:
            return json.loads(SESSION_MEMORY_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_sessions": 0,
        "total_compressed": 0,
        "last_capture": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_state(state: dict) -> None:
    """持久化状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    SESSION_MEMORY_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def generate_session_id() -> str:
    """生成会话 ID"""
    now = datetime.now(timezone.utc)
    return f"session-{now.strftime('%Y%m%d-%H%M%S')}"

# ── 会话捕获 ──────────────────────────────────────────────────────────────

def capture_session(summary: str, key_decisions: List[str] = None,
                   files_modified: List[str] = None, tools_used: List[str] = None,
                   importance: float = 0.5) -> dict:
    """捕获当前会话"""
    state = load_state()
    now = datetime.now(timezone.utc)

    session_id = generate_session_id()

    record = {
        "session_id": session_id,
        "started_at": now.isoformat(),
        "ended_at": now.isoformat(),
        "summary": summary,
        "key_decisions": key_decisions or [],
        "files_modified": files_modified or [],
        "tools_used": tools_used or [],
        "importance": importance,
    }

    # 保存会话记录
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_file = SESSIONS_DIR / f"{session_id}.json"
    session_file.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    # 更新状态
    state["total_sessions"] += 1
    state["last_capture"] = now.isoformat()
    save_state(state)

    return {
        "session_id": session_id,
        "status": "captured",
        "summary": summary[:100],
    }

# ── 会话压缩 ──────────────────────────────────────────────────────────────

def compress_session(session_id: str) -> dict:
    """压缩会话记录"""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        return {"error": f"Session not found: {session_id}"}

    session = json.loads(session_file.read_text())

    # 生成压缩摘要
    compressed = {
        "session_id": session["session_id"],
        "date": session["started_at"][:10],
        "summary": session["summary"],
        "key_decisions": session["key_decisions"][:5],  # 最多 5 个
        "files_count": len(session["files_modified"]),
        "tools_count": len(session["tools_used"]),
        "importance": session["importance"],
    }

    # 保存压缩记录
    COMPRESSED_DIR.mkdir(parents=True, exist_ok=True)
    compressed_file = COMPRESSED_DIR / f"{session_id}.json"
    compressed_file.write_text(json.dumps(compressed, ensure_ascii=False, indent=2))

    # 更新状态
    state = load_state()
    state["total_compressed"] += 1
    save_state(state)

    return {
        "session_id": session_id,
        "status": "compressed",
        "original_size": len(json.dumps(session)),
        "compressed_size": len(json.dumps(compressed)),
    }

# ── 上下文注入 ──────────────────────────────────────────────────────────────

def inject_context(query: str, limit: int = 5) -> str:
    """注入相关上下文"""
    # 搜索相关会话
    results = search_sessions(query, limit)

    if not results:
        return "No relevant sessions found."

    # 构建注入内容
    lines = ["## 相关历史会话\n"]

    for result in results:
        lines.append(f"### {result['session_id']} ({result['date']})")
        lines.append(f"**摘要**: {result['summary']}")

        if result.get("key_decisions"):
            lines.append("**关键决策**:")
            for decision in result["key_decisions"][:3]:
                lines.append(f"- {decision}")

        lines.append("")

    return "\n".join(lines)

# ── 搜索功能 ──────────────────────────────────────────────────────────────

def search_sessions(query: str, limit: int = 5) -> List[Dict]:
    """搜索历史会话"""
    results = []
    query_lower = query.lower()

    # 搜索压缩会话
    if COMPRESSED_DIR.exists():
        for session_file in COMPRESSED_DIR.glob("*.json"):
            try:
                session = json.loads(session_file.read_text())

                # 计算相关性分数
                score = 0.0

                # 摘要匹配
                if query_lower in session.get("summary", "").lower():
                    score += 1.0

                # 关键决策匹配
                for decision in session.get("key_decisions", []):
                    if query_lower in decision.lower():
                        score += 0.5

                if score > 0:
                    results.append({
                        "session_id": session["session_id"],
                        "date": session["date"],
                        "summary": session["summary"],
                        "key_decisions": session.get("key_decisions", []),
                        "score": score,
                    })
            except Exception:
                pass

    # 按分数排序
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "capture":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-memory.py capture '<summary>'", file=sys.stderr)
            sys.exit(1)

        summary = sys.argv[2]
        result = capture_session(summary)

        print(f"📸 会话已捕获:")
        print(f"  ID: {result['session_id']}")
        print(f"  摘要: {result['summary']}")

    elif cmd == "compress":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-memory.py compress <session_id>", file=sys.stderr)
            sys.exit(1)

        session_id = sys.argv[2]
        result = compress_session(session_id)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"🗜️ 会话已压缩:")
        print(f"  ID: {result['session_id']}")
        print(f"  原始大小: {result['original_size']} bytes")
        print(f"  压缩大小: {result['compressed_size']} bytes")

    elif cmd == "inject":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-memory.py inject '<query>'", file=sys.stderr)
            sys.exit(1)

        query = sys.argv[2]
        result = inject_context(query)
        print(result)

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: liyu-session-memory.py search '<query>'", file=sys.stderr)
            sys.exit(1)

        query = sys.argv[2]
        results = search_sessions(query)

        if not results:
            print("No results found.")
        else:
            print(f"🔍 搜索结果 ({len(results)}):")
            for result in results:
                print(f"  - {result['session_id']}: {result['summary'][:50]}...")

    elif cmd == "stats":
        state = load_state()

        print("═══ 鲤鱼 Session Memory Statistics ═══")
        print(f"  总计会话: {state.get('total_sessions', 0)}")
        print(f"  已压缩:   {state.get('total_compressed', 0)}")
        print(f"  上次捕获: {state.get('last_capture', 'never')}")

        # 统计文件
        if SESSIONS_DIR.exists():
            session_files = list(SESSIONS_DIR.glob("*.json"))
            print(f"  会话文件: {len(session_files)}")

        if COMPRESSED_DIR.exists():
            compressed_files = list(COMPRESSED_DIR.glob("*.json"))
            print(f"  压缩文件: {len(compressed_files)}")

    elif cmd == "reset":
        save_state({
            "total_sessions": 0,
            "total_compressed": 0,
            "last_capture": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Session Memory 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
