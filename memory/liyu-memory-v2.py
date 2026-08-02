#!/usr/bin/env python3
"""
鲤鱼 Memory System v2.0 — Unified Memory Manager.

Consolidates knowledge-base, auto-memory, reflection-engine, and memory/*.md
into a single coherent memory system with:
  - Session-start memory priming (context injection)
  - Session-end memory capture (automatic)
  - Cross-component search (unified query across all stores)
  - Memory consolidation (periodic merge and summarization)
  - Importance-weighted retrieval with recency boost
  - Memory graph linking across all memory types

Usage:
  python3 liyu-memory-v2.py prime                Prime context for session start
  python3 liyu-memory-v2.py capture [--text FILE] Capture memories from session text
  python3 liyu-memory-v2.py search <query>        Unified search across all stores
  python3 liyu-memory-v2.py consolidate            Merge/simplify stale memories
  python3 liyu-memory-v2.py stats                  System-wide memory statistics
  python3 liyu-memory-v2.py link                   Build cross-store memory links
  python3 liyu-memory-v2.py inject                 Generate context injection block
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import json
import math
import re
import sqlite3
import sys
import uuid

# ── Paths ─────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-holyty" / "memory"
DB_PATH = 鲤鱼_HOME / "knowledge-base.db"
STORY_FILE = 鲤鱼_HOME / "story.jsonl"
REFLECTIONS_FILE = 鲤鱼_HOME / "reflections.jsonl"
LAST_SESSION_FILE = 鲤鱼_HOME / "last-session.json"
SESSION_CAPTURE_FILE = 鲤鱼_HOME / "session-capture.jsonl"

# ── Constants ─────────────────────────────────────────────────────────────

PRIME_MAX_TOKENS = 2000       # Max tokens for session-start injection
SEARCH_DEFAULT_K = 10         # Default search result count
CONSOLIDATION_THRESHOLD = 5   # Merge when >5 similar memories exist


# ── Data Classes ──────────────────────────────────────────────────────────

@dataclass
class UnifiedMemory:
    """A memory from any source, normalized to a common format."""
    id: str
    source: str           # knowledge | auto | reflection | markdown
    content: str
    summary: str          # Short summary (for injection)
    mem_type: str          # semantic | episodic | procedural | relational
    importance: float      # 0.0 - 1.0
    confidence: float      # 0.0 - 1.0
    created_at: str
    last_accessed: str
    access_count: int
    decay_score: float     # Computed decay (higher = fresher)
    tags: List[str]
    links: List[str]       # IDs of related memories


# ── Database ──────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def init_v2_tables():
    """Create v2 tables if they don't exist."""
    db = get_db()
    try:
        db.executescript("""
        -- Cross-store memory links (knowledge <-> auto <-> reflection)
        CREATE TABLE IF NOT EXISTS memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_store TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_store TEXT NOT NULL,
            target_id TEXT NOT NULL,
            link_type TEXT DEFAULT 'related_to',
            weight REAL DEFAULT 1.0,
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(source_store, source_id, target_store, target_id, link_type)
        );

        CREATE INDEX IF NOT EXISTS idx_mem_links_source
            ON memory_links(source_store, source_id);
        CREATE INDEX IF NOT EXISTS idx_mem_links_target
            ON memory_links(target_store, target_id);

        -- Session capture log (auto-captured at session end)
        CREATE TABLE IF NOT EXISTS session_captures (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            capture_time TEXT NOT NULL,
            summary TEXT NOT NULL,
            key_decisions TEXT DEFAULT '[]',
            key_events TEXT DEFAULT '[]',
            open_questions TEXT DEFAULT '[]',
            user_mood TEXT DEFAULT '',
            duration_minutes REAL DEFAULT 0,
            tokens_used INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_session_cap_time
            ON session_captures(capture_time);

        -- Memory consolidation log
        CREATE TABLE IF NOT EXISTS consolidation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            consolidated_at TEXT NOT NULL,
            source_ids TEXT NOT NULL,
            merged_id TEXT NOT NULL,
            merged_content TEXT NOT NULL,
            reason TEXT DEFAULT ''
        );
    """)
        db.commit()
    finally:
        db.close()


# ── Unified Search ────────────────────────────────────────────────────────

def _build_fts_query(query: str) -> str:
    """Build an FTS5 query that uses OR for multi-term searches."""
    terms = query.strip().split()
    if len(terms) <= 1:
        return query
    # Use OR so any term matches (more lenient for CJK mixed queries)
    return " OR ".join(f'"{t}"' for t in terms if len(t) >= 1)


def _search_knowledge(db, query: str, limit: int) -> List[UnifiedMemory]:
    """Search the knowledge-base memories table."""
    results = []
    fts_query = _build_fts_query(query)
    try:
        rows = db.execute("""
            SELECT m.*, rank
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ? AND m.is_active = 1
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        # Fallback to LIKE
        like_q = f"%{query}%"
        rows = db.execute("""
            SELECT *, 0 as rank FROM memories
            WHERE (name LIKE ? OR description LIKE ? OR content LIKE ?)
            AND is_active = 1
            LIMIT ?
        """, (like_q, like_q, like_q, limit)).fetchall()

    now = datetime.now(timezone.utc)
    for row in rows:
        try:
            updated = datetime.fromisoformat(row["updated_at"])
            hours_old = (now - updated).total_seconds() / 3600
            recency = math.exp(-hours_old / (24 * 7))  # 7-day half-life
        except Exception:
            recency = 0.5

        results.append(UnifiedMemory(
            id=row["id"],
            source="knowledge",
            content=row["content"][:500],
            summary=row["description"][:200] if row["description"] else row["name"],
            mem_type=row["type"] if row["type"] in ("semantic", "episodic", "procedural", "relational") else "semantic",
            importance=0.5 + row["access_count"] * 0.05,
            confidence=0.8,
            created_at=row["created_at"],
            last_accessed=row["updated_at"],
            access_count=row["access_count"],
            decay_score=recency,
            tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else [],
            links=json.loads(row["links"]) if isinstance(row["links"], str) else [],
        ))
    return results


def _search_auto_memory(db, query: str, limit: int) -> List[UnifiedMemory]:
    """Search auto_memories table."""
    results = []
    fts_query = _build_fts_query(query)
    try:
        rows = db.execute("""
            SELECT am.*, rank
            FROM auto_memories_fts
            JOIN auto_memories am ON auto_memories_fts.rowid = am.rowid
            WHERE auto_memories_fts MATCH ? AND am.is_active = 1
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        like_q = f"%{query}%"
        rows = db.execute("""
            SELECT *, 0 as rank FROM auto_memories
            WHERE content LIKE ? AND is_active = 1
            ORDER BY decay_strength DESC
            LIMIT ?
        """, (like_q, limit)).fetchall()

    now = datetime.now(timezone.utc)
    for row in rows:
        try:
            last = datetime.fromisoformat(row["last_recalled"])
            hours_old = (now - last).total_seconds() / 3600
            S = row["decay_strength"] * 24.0
            recency = math.exp(-hours_old / max(S, 1.0))
        except Exception:
            recency = 0.5

        results.append(UnifiedMemory(
            id=row["id"],
            source="auto",
            content=row["content"],
            summary=row["content"][:200],
            mem_type=row["mem_type"],
            importance=row["importance"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            last_accessed=row["last_recalled"],
            access_count=row["recall_count"],
            decay_score=recency,
            tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else [],
            links=json.loads(row["linked_ids"]) if isinstance(row["linked_ids"], str) else [],
        ))
    return results


def _search_reflections(query: str, limit: int) -> List[UnifiedMemory]:
    """Search reflections.jsonl."""
    if not REFLECTIONS_FILE.exists():
        return []

    results = []
    query_lower = query.lower()
    lines = REFLECTIONS_FILE.read_text().strip().split("\n")

    # Limit scan to last 100 entries for performance
    for line in reversed(lines[-100:]):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            searchable = " ".join([
                entry.get("task_name", ""),
                entry.get("result_summary", ""),
                " ".join(entry.get("lessons", [])),
                " ".join(entry.get("next_actions", [])),
            ]).lower()

            if query_lower in searchable:
                results.append(UnifiedMemory(
                    id=entry.get("task_id", f"ref-{uuid.uuid4().hex[:8]}"),
                    source="reflection",
                    content=entry.get("result_summary", ""),
                    summary=f"[{entry.get('status', '?')}] {entry.get('task_name', '')}",
                    mem_type="episodic",
                    importance=0.6 if entry.get("status") == "success" else 0.4,
                    confidence=0.9,
                    created_at=entry.get("finished_at", entry.get("started_at", "")),
                    last_accessed=entry.get("finished_at", ""),
                    access_count=0,
                    decay_score=0.5,
                    tags=["reflection", entry.get("status", "unknown")],
                    links=[],
                ))

                if len(results) >= limit:
                    break
        except Exception:
            continue

    return results


def _search_markdown(query: str, limit: int) -> List[UnifiedMemory]:
    """Search memory/*.md files."""
    if not MEMORY_DIR.exists():
        return []

    results = []
    query_lower = query.lower()

    for md_file in MEMORY_DIR.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        try:
            content = md_file.read_text(encoding="utf-8")

            # Strip YAML frontmatter before searching (regex-based)
            stripped = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL)

            if query_lower in stripped.lower():
                # Extract first meaningful line (skip frontmatter, headings, separators)
                summary = ""
                in_frontmatter = False
                for line in content.split("\n"):
                    stripped_line = line.strip()
                    if stripped_line == "---":
                        in_frontmatter = not in_frontmatter
                        continue
                    if in_frontmatter:
                        continue
                    if stripped_line and not stripped_line.startswith("#") and not stripped_line.startswith(">") and stripped_line != "---":
                        summary = stripped_line[:200]
                        break

                stat = md_file.stat()
                created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

                results.append(UnifiedMemory(
                    id=md_file.stem,
                    source="markdown",
                    content=stripped[:500],
                    summary=summary or md_file.stem,
                    mem_type="semantic",
                    importance=0.7,  # Markdown memories are curated, high importance
                    confidence=1.0,
                    created_at=created,
                    last_accessed=created,
                    access_count=0,
                    decay_score=0.8,
                    tags=["markdown"],
                    links=[],
                ))

                if len(results) >= limit:
                    break
        except Exception:
            continue

    return results


def unified_search(query: str, top_k: int = SEARCH_DEFAULT_K) -> List[UnifiedMemory]:
    """
    Search across ALL memory stores with unified ranking.

    Ranking: FTS score + importance + recency + source_weight
    """
    init_v2_tables()
    db = get_db()
    try:
        # Search all stores in parallel concept (sequential here)
        k_results = _search_knowledge(db, query, top_k)
        a_results = _search_auto_memory(db, query, top_k)
    finally:
        db.close()

    r_results = _search_reflections(query, top_k)
    m_results = _search_markdown(query, top_k)

    # Normalize scores and merge
    all_results = []
    source_weight = {
        "knowledge": 1.0,
        "auto": 0.8,
        "reflection": 0.9,
        "markdown": 1.1,  # Curated content gets slight boost
    }

    for result in k_results + a_results + r_results + m_results:
        # Composite score
        sw = source_weight.get(result.source, 1.0)
        result.decay_score = (
            result.decay_score * 0.3 +
            result.importance * 0.3 +
            result.confidence * 0.2 +
            sw * 0.2
        )
        all_results.append(result)

    # Deduplicate by content similarity
    seen_content = set()
    deduped = []
    for r in sorted(all_results, key=lambda x: x.decay_score, reverse=True):
        content_hash = hashlib.md5(r.content[:200].lower().encode()).hexdigest()[:32]
        if content_hash not in seen_content:
            seen_content.add(content_hash)
            deduped.append(r)

    # Update access counts for knowledge memories
    if deduped:
        try:
            db = get_db()
            try:
                for r in deduped[:top_k]:
                    if r.source == "knowledge":
                        db.execute(
                            "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                            (r.id,)
                        )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    return deduped[:top_k]


# ── Session Prime (Context Injection) ─────────────────────────────────────

def prime_session() -> str:
    """
    Generate a context block to inject at session start.
    Includes: last session summary, active memories, recent reflections.
    """
    init_v2_tables()
    parts = []

    # 1. Last session summary
    if LAST_SESSION_FILE.exists():
        try:
            last = json.loads(LAST_SESSION_FILE.read_text())
            parts.append(f"## 上次会话 ({last.get('date', '?')})")
            parts.append(f"- 状态: {last.get('mood', '正常')}")
            parts.append(f"- 摘要: {last.get('summary', 'N/A')}")
            parts.append("")
        except Exception:
            pass

    # 2. Recent key memories from auto-memory
    db = get_db()
    try:
        recent_auto = db.execute("""
            SELECT content, mem_type, importance, recall_count
            FROM auto_memories
            WHERE is_active = 1
            ORDER BY importance DESC, recall_count DESC
            LIMIT 5
        """).fetchall()

        if recent_auto:
            parts.append("## 活跃记忆")
            for row in recent_auto:
                parts.append(f"- [{row['mem_type']}] {row['content'][:100]}")
            parts.append("")

        # 3. Top knowledge entries
        top_knowledge = db.execute("""
            SELECT name, description, access_count
            FROM memories
            WHERE is_active = 1
            ORDER BY access_count DESC
            LIMIT 5
        """).fetchall()

        if top_knowledge:
            parts.append("## 高频知识")
            for row in top_knowledge:
                desc = row["description"] or row["name"]
                parts.append(f"- {desc[:100]} (accessed {row['access_count']}x)")
            parts.append("")
    except Exception:
        pass
    finally:
        db.close()

    # 4. Recent reflections
    if REFLECTIONS_FILE.exists():
        try:
            lines = REFLECTIONS_FILE.read_text().strip().split("\n")
            # Limit to last 100 entries for performance
            recent_reflections = []
            for line in reversed(lines[-100:]):
                if not line.strip():
                    continue
                entry = json.loads(line)
                recent_reflections.append(entry)

            if recent_reflections:
                parts.append("## 近期反思")
                for ref in recent_reflections[:3]:
                    status = ref.get("status", "?")
                    name = ref.get("task_name", "?")
                    result = ref.get("result_summary", "")[:80]
                    parts.append(f"- [{status}] {name}: {result}")
                parts.append("")
        except Exception:
            pass

    # 5. Active markdown memories (curated)
    if MEMORY_DIR.exists():
        core_files = [
            "user-profile.md", "last-session.md", "memory-curation.md",
            "website-is-mirror.md", "liyu-learning-progress.md",
        ]
        for fname in core_files:
            fpath = MEMORY_DIR / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    # Extract first meaningful paragraph
                    summary_lines = []
                    for line in content.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("```"):
                            summary_lines.append(line)
                            if len(" ".join(summary_lines)) > 150:
                                break
                    if summary_lines:
                        parts.append(f"**{fname}**: {' '.join(summary_lines)[:150]}")
                except Exception:
                    continue

    output = "\n".join(parts)

    # Truncate to max tokens (rough: 1 token ~ 4 chars for mixed CJK/EN)
    max_chars = PRIME_MAX_TOKENS * 3
    if len(output) > max_chars:
        output = output[:max_chars] + "\n..."

    return output


# ── Session Capture ───────────────────────────────────────────────────────

def capture_session(text: str = "", session_id: str = ""):
    """
    Capture memories from session text.
    If no text provided, reads from story.jsonl (last entry) and reflections.
    """
    init_v2_tables()

    if not text:
        text = _load_session_text()

    if not text:
        print("No session text to capture from.")
        return

    now = datetime.now(timezone.utc).isoformat()
    sid = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M')}"

    # Extract with improved patterns
    memories = _extract_all_memories(text, sid)

    db = get_db()
    try:
        saved = 0
        reinforced = 0

        for mem in memories:
            # Check for existing similar memory
            existing = db.execute(
                "SELECT id, recall_count, importance FROM auto_memories WHERE content = ? AND is_active = 1",
                (mem["content"],)
            ).fetchone()

            if existing:
                # Reinforce existing
                db.execute("""
                    UPDATE auto_memories SET
                        recall_count = recall_count + 1,
                        last_recalled = ?,
                        decay_strength = MIN(decay_strength + 0.3, 3.0),
                        importance = MAX(importance, ?)
                    WHERE id = ?
                """, (now, mem["importance"], existing["id"]))
                reinforced += 1
            else:
                # Insert new
                mem_id = f"am-{uuid.uuid4().hex[:10]}"
                db.execute("""
                    INSERT INTO auto_memories
                    (id, session_id, content, mem_type, importance, confidence,
                     source, created_at, last_recalled, recall_count,
                     decay_strength, ttl_days, tags, linked_ids, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1.0, 90, ?, ?, 1)
                """, (
                    mem_id, sid, mem["content"], mem["type"],
                    mem["importance"], mem["confidence"], mem["source"],
                    now, now,
                    json.dumps(mem["tags"], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                ))
                saved += 1

        db.commit()

        # Update decay for all auto-memories inline
        try:
            _update_decay_inline(db)
        except Exception:
            pass
    finally:
        db.close()

    # Log session capture
    _log_session_capture(sid, now, saved, reinforced)

    print(f"Session capture complete: {saved} new, {reinforced} reinforced")
    return saved + reinforced


def _update_decay_inline(db):
    """Inline decay update for auto-memories (avoids cross-module import)."""
    now = datetime.now(timezone.utc)
    rows = db.execute("SELECT id, last_recalled, decay_strength, created_at, ttl_days, importance, recall_count FROM auto_memories WHERE is_active = 1").fetchall()

    for row in rows:
        try:
            last = datetime.fromisoformat(row["last_recalled"])
            hours_elapsed = (now - last).total_seconds() / 3600
            S = row["decay_strength"] * 24.0
            recency = math.exp(-hours_elapsed / max(S, 1.0))
            freq_bonus = math.log(1 + row["recall_count"]) * 0.1
            score = 0.3 * recency + 0.3 * min(1.0 + freq_bonus, 2.0) / 2.0 + 0.4 * row["importance"]

            if score < 0.05:
                db.execute("UPDATE auto_memories SET is_active = 0 WHERE id = ?", (row["id"],))
            elif score < 0.3:
                db.execute("UPDATE auto_memories SET decay_strength = ? WHERE id = ?", (score, row["id"]))
        except Exception:
            continue

    db.commit()


def _load_session_text() -> str:
    """Load text from recent story entries and reflections."""
    parts = []

    if STORY_FILE.exists():
        lines = STORY_FILE.read_text().strip().split("\n")
        for line in reversed(lines[-15:]):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                summary = entry.get("summary", "")
                if summary and "详见日记" not in summary:
                    parts.append(summary)
            except Exception:
                pass

    if REFLECTIONS_FILE.exists():
        lines = REFLECTIONS_FILE.read_text().strip().split("\n")
        for line in reversed(lines[-10:]):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                parts.append(entry.get("result_summary", ""))
                for lesson in entry.get("lessons", []):
                    parts.append(lesson)
            except Exception:
                pass

    return "\n".join(parts)


def _extract_all_memories(text: str, session_id: str) -> List[dict]:
    """
    Improved memory extraction with richer patterns.
    Returns list of dicts with keys: content, type, importance, confidence, source, tags
    """
    memories = []
    seen = set()

    # ── Enhanced patterns ──

    # Semantic: decisions, configurations, preferences
    semantic_patterns = [
        (r'(决定|选择|采用|使用|确定|偏好|喜欢|倾向)[：:]\s*(.+?)(?:[。\n]|$)', 0.7, ["decision"]),
        (r'(升级|迁移|切换到|替换为|从.+迁移到)\s*(.+?)(?:[。\n]|$)', 0.65, ["upgrade"]),
        (r'(配置|设置|安装|部署)[：:]\s*(.+?)(?:[。\n]|$)', 0.6, ["config"]),
        (r'(鲤鱼|liyu)\s*(v[\d.]+|升级|吸收|整合|进化).*?(.+?)(?:[。\n]|$)', 0.75, ["liyu"]),
        (r'(创建|建立|搭建|实现|开发)[：:]\s*(.+?)(?:[。\n]|$)', 0.55, ["creation"]),
        (r'(规则|Rule|Framework|框架)[：:]\s*(.+?)(?:[。\n]|$)', 0.6, ["rule"]),
        (r'(吸收自|借鉴|参考|基于|来自)\s*[：:]?\s*(.+?)(?:[。\n]|$)', 0.7, ["absorption"]),
        (r'(关键|重要|核心|必须|强制)[：:]\s*(.+?)(?:[。\n]|$)', 0.7, ["important"]),
        (r'(禁用|禁止|不允许|不要|避免)[：:]\s*(.+?)(?:[。\n]|$)', 0.65, ["constraint"]),
        (r'(版本|version)\s*(v?[\d.]+)\s*(.+?)(?:[。\n]|$)', 0.6, ["version"]),
        (r'(架构|Architecture|设计模式|Design Pattern)[：:]\s*(.+?)(?:[。\n]|$)', 0.7, ["architecture"]),
        (r'(方案|策略|方法|approach)[：:]\s*(.+?)(?:[。\n]|$)', 0.55, ["strategy"]),
    ]

    # Episodic: events, completions, failures
    episodic_patterns = [
        (r'(完成|结束|达成|实现了?)[：:]\s*(.+?)(?:[。\n]|$)', 0.65, ["completion"]),
        (r'(修复|解决|Fix|fixed|resolved?)\s*(.+?)(?:[。\n]|$)', 0.6, ["fix"]),
        (r'(发现|遇到|出现|碰到)\s*(.+?)(?:[。\n]|$)', 0.5, ["discovery"]),
        (r'(成功|失败|成功完成|顺利完成)[：:]\s*(.+?)(?:[。\n]|$)', 0.6, ["outcome"]),
        (r'(问题|bug|issue|故障)[：:]\s*(.+?)(?:[。\n]|$)', 0.55, ["issue"]),
        (r'(部署|发布|上线|deploy)\s*(.+?)(?:[。\n]|$)', 0.65, ["deployment"]),
        (r'(测试|test)\s*(通过|失败|完成)\s*(.+?)(?:[。\n]|$)', 0.55, ["test"]),
        (r'(PR|pull request|merge)\s*(#?\d+)?\s*(.+?)(?:[。\n]|$)', 0.5, ["git"]),
    ]

    # Procedural: workflows, commands, patterns
    procedural_patterns = [
        (r'(工作流|workflow|流程)[：:]\s*(.+?)(?:[。\n]|$)', 0.65, ["workflow"]),
        (r'(步骤|Step|阶段|Phase)[：:]\s*(.+?)(?:[。\n]|$)', 0.55, ["step"]),
        (r'(python3?\s+[~./].+?\.py\s+\S+(?:\s+\S+)*)', 0.5, ["cli"]),
        (r'(CLI|命令|command|用法)[：:]\s*(.+?)(?:[。\n]|$)', 0.55, ["cli"]),
        (r'(先|然后|接着|最后|首先)\s*(.+?)(?:[。\n]|$)', 0.4, ["sequence"]),
        (r'(hook|钩子|触发器)\s*(.+?)(?:[。\n]|$)', 0.55, ["hook"]),
    ]

    # Relational: agent relationships, dependencies
    relational_patterns = [
        (r'(Agent|代理|agent)\s*(.+?)\s*(负责|处理|用于)\s*(.+?)(?:[。\n]|$)', 0.6, ["agent"]),
        (r'(依赖|depend|requires?|需要)\s*(.+?)(?:[。\n]|$)', 0.55, ["dependency"]),
        (r'(集成|integrate|联动|配合)\s*(.+?)(?:[。\n]|$)', 0.6, ["integration"]),
        (r'(与|和|with)\s*(.+?)\s*(协同|配合|联动)\s*(.+?)(?:[。\n]|$)', 0.55, ["collaboration"]),
    ]

    all_extractors = [
        (semantic_patterns, "semantic"),
        (episodic_patterns, "episodic"),
        (procedural_patterns, "procedural"),
        (relational_patterns, "relational"),
    ]

    for patterns, default_type in all_extractors:
        for pattern, confidence, tags in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                content = match.group(0).strip()
                if len(content) < 10:
                    continue

                content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
                if content_hash in seen:
                    continue
                seen.add(content_hash)

                importance = _estimate_importance(content)

                memories.append({
                    "content": content,
                    "type": default_type,
                    "importance": importance,
                    "confidence": confidence,
                    "source": "extracted",
                    "tags": tags,
                })

    return memories


def _estimate_importance(content: str) -> float:
    """Estimate importance of extracted content."""
    score = 0.5

    high_keywords = [
        '鲤鱼', 'liyu', '关键', '核心', '重要', '必须', 'critical',
        '架构', '决定', '升级', '迁移', '用户', '时宇', 'HolyTy',
    ]
    medium_keywords = [
        '配置', 'config', '规则', 'Rule', 'Framework', '进化', '吸收',
        '部署', 'deploy', '发布', '设计模式',
    ]

    for kw in high_keywords:
        if kw in content:
            score += 0.15

    for kw in medium_keywords:
        if kw in content:
            score += 0.08

    return min(score, 1.0)


def _log_session_capture(session_id: str, timestamp: str, saved: int, reinforced: int):
    """Log the session capture event."""
    db = get_db()
    try:
        db.execute("""
            INSERT OR REPLACE INTO session_captures
            (id, session_id, capture_time, summary, key_decisions, key_events)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            f"cap-{uuid.uuid4().hex[:8]}",
            session_id,
            timestamp,
            f"Captured {saved} new, {reinforced} reinforced memories",
            json.dumps([]),
            json.dumps([]),
        ))
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


# ── Memory Consolidation ─────────────────────────────────────────────────

def consolidate():
    """
    Consolidate memories:
    1. Merge near-duplicate auto-memories
    2. Strengthen frequently recalled memories
    3. Archive very old, low-importance memories
    """
    init_v2_tables()
    db = get_db()
    try:
        # Get all active auto-memories
        rows = db.execute("""
            SELECT * FROM auto_memories WHERE is_active = 1
            ORDER BY created_at
        """).fetchall()

        if not rows:
            print("No memories to consolidate.")
            return

        # Group by content similarity (simple: same first 50 chars)
        from collections import defaultdict
        groups = defaultdict(list)
        for row in rows:
            key = row["content"][:50].lower().strip()
            groups[key].append(row)

        merged = 0
        strengthened = 0
        archived = 0

        now = datetime.now(timezone.utc).isoformat()

        for key, group in groups.items():
            if len(group) >= CONSOLIDATION_THRESHOLD:
                # Merge: keep the one with highest importance, combine recall counts
                best = max(group, key=lambda r: r["importance"])
                total_recalls = sum(r["recall_count"] for r in group)

                # Update the best one
                db.execute("""
                    UPDATE auto_memories SET
                        recall_count = ?,
                        importance = MIN(importance + 0.1, 1.0),
                        decay_strength = MIN(decay_strength + 0.5, 3.0),
                        last_recalled = ?
                    WHERE id = ?
                """, (total_recalls, now, best["id"]))

                # Remove duplicates
                for row in group:
                    if row["id"] != best["id"]:
                        db.execute("UPDATE auto_memories SET is_active = 0 WHERE id = ?", (row["id"],))
                        merged += 1

                # Log consolidation
                db.execute("""
                    INSERT INTO consolidation_log
                    (consolidated_at, source_ids, merged_id, merged_content, reason)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    now,
                    json.dumps([r["id"] for r in group]),
                    best["id"],
                    best["content"][:200],
                    f"Merged {len(group)} similar memories",
                ))

            elif len(group) == 1:
                row = group[0]
                # Strengthen memories that have been recalled multiple times
                if row["recall_count"] >= 3:
                    db.execute("""
                        UPDATE auto_memories SET
                            importance = MIN(importance + 0.05, 1.0),
                            decay_strength = MIN(decay_strength + 0.2, 3.0)
                        WHERE id = ?
                    """, (row["id"],))
                    strengthened += 1

        # Archive old, low-importance memories (not recalled in 30 days)
        try:
            threshold_date = datetime.now(timezone.utc)
            from datetime import timedelta
            threshold_date = (threshold_date - timedelta(days=30)).isoformat()

            result = db.execute("""
                UPDATE auto_memories SET is_active = 0
                WHERE is_active = 1
                AND importance < 0.4
                AND recall_count = 0
                AND last_recalled < ?
            """, (threshold_date,))
            archived = result.rowcount
        except Exception:
            pass

        db.commit()

        print(f"Consolidation complete:")
        print(f"  Merged: {merged} duplicate memories")
        print(f"  Strengthened: {strengthened} frequently recalled")
        print(f"  Archived: {archived} stale low-importance")
    finally:
        db.close()


# ── Cross-Store Linking ──────────────────────────────────────────────────

def build_links():
    """
    Build cross-store links between knowledge, auto-memory, and reflections.
    Uses keyword overlap (Jaccard similarity) to find connections.
    """
    init_v2_tables()
    db = get_db()
    try:
        # Load all memories from all stores
        knowledge = []
        for row in db.execute("SELECT id, name, description, content FROM memories WHERE is_active = 1").fetchall():
            text = f"{row['name']} {row['description'] or ''} {row['content'][:300]}"
            knowledge.append(("knowledge", row["id"], text))

        auto_mem = []
        for row in db.execute("SELECT id, content FROM auto_memories WHERE is_active = 1").fetchall():
            auto_mem.append(("auto", row["id"], row["content"]))

        reflections = []
        if REFLECTIONS_FILE.exists():
            for line in REFLECTIONS_FILE.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    text = f"{entry.get('task_name', '')} {entry.get('result_summary', '')} {' '.join(entry.get('lessons', []))}"
                    reflections.append(("reflection", entry.get("task_id", ""), text))
                except Exception:
                    continue

        all_memories = knowledge + auto_mem + reflections

        # Tokenize and compute Jaccard similarity
        def tokenize(text: str) -> set:
            words = re.findall(r'[\w一-鿿]+', text.lower())
            return {w for w in words if len(w) >= 2}

        links_created = 0

        for i, (store_a, id_a, text_a) in enumerate(all_memories):
            tokens_a = tokenize(text_a)
            if not tokens_a:
                continue

            for j, (store_b, id_b, text_b) in enumerate(all_memories):
                if i >= j:
                    continue
                if store_a == store_b and id_a == id_b:
                    continue

                tokens_b = tokenize(text_b)
                if not tokens_b:
                    continue

                jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

                if jaccard >= 0.15:
                    link_type = "related_to"
                    if jaccard > 0.4:
                        link_type = "supports"
                    elif jaccard > 0.25:
                        link_type = "related_to"

                    try:
                        db.execute("""
                            INSERT OR IGNORE INTO memory_links
                            (source_store, source_id, target_store, target_id,
                             link_type, weight, reason, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            store_a, id_a, store_b, id_b,
                            link_type, round(jaccard, 3),
                            f"Jaccard similarity: {jaccard:.3f}",
                            datetime.now(timezone.utc).isoformat(),
                        ))
                        links_created += 1
                    except Exception:
                        continue

        db.commit()

        print(f"Built {links_created} cross-store links")
    finally:
        db.close()


# ── Context Injection ─────────────────────────────────────────────────────

def generate_injection(query_context: str = "") -> str:
    """
    Generate a compact context injection block for the current session.
    Combines prime + relevant search results for the given context.
    """
    parts = []

    # Prime (always include last session + core memories)
    prime = prime_session()
    if prime:
        parts.append(prime)

    # If we have a specific query context, search for relevant memories
    if query_context:
        results = unified_search(query_context, top_k=5)
        if results:
            parts.append("\n## 相关记忆")
            for r in results:
                source_label = {
                "knowledge": "知识库",
                "auto": "自动记忆",
                "reflection": "反思",
                "markdown": "文档",
            }.get(r.source, r.source)
                parts.append(f"- [{source_label}] {r.summary[:120]}")

    return "\n".join(parts)


# ── Statistics ────────────────────────────────────────────────────────────

def show_stats():
    """Show comprehensive memory system statistics."""
    init_v2_tables()
    db = get_db()
    try:
        print("=" * 60)
        print("鲤鱼 Memory System v2.0 — Statistics")
        print("=" * 60)

        # Knowledge base
        k_total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        k_active = db.execute("SELECT COUNT(*) FROM memories WHERE is_active = 1").fetchone()[0]
        k_vectors = db.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
        k_relations = db.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        print(f"\nKnowledge Base: {k_active}/{k_total} active, {k_vectors} vectors, {k_relations} relations")

        # Auto-memory
        a_total = db.execute("SELECT COUNT(*) FROM auto_memories").fetchone()[0]
        a_active = db.execute("SELECT COUNT(*) FROM auto_memories WHERE is_active = 1").fetchone()[0]
        by_type = {}
        for row in db.execute("SELECT mem_type, COUNT(*) FROM auto_memories WHERE is_active = 1 GROUP BY mem_type").fetchall():
            by_type[row[0]] = row[1]
        avg_decay = db.execute("SELECT AVG(decay_strength) FROM auto_memories WHERE is_active = 1").fetchone()[0] or 0
        print(f"\nAuto-Memory: {a_active}/{a_total} active")
        print(f"  By type: {by_type}")
        print(f"  Avg decay strength: {avg_decay:.3f}")

        # Cross-store links
        try:
            links = db.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
            print(f"\nCross-Store Links: {links}")
        except Exception:
            print("\nCross-Store Links: table not initialized")

        # Session captures
        try:
            captures = db.execute("SELECT COUNT(*) FROM session_captures").fetchone()[0]
            print(f"Session Captures: {captures}")
        except Exception:
            pass

        # Reflections
        if REFLECTIONS_FILE.exists():
            ref_count = len([l for l in REFLECTIONS_FILE.read_text().strip().split("\n") if l.strip()])
            print(f"\nReflections: {ref_count} entries")
        else:
            print("\nReflections: 0 entries")

        # Markdown memories
        if MEMORY_DIR.exists():
            md_count = len([f for f in MEMORY_DIR.glob("*.md") if not f.name.startswith("_")])
            print(f"Markdown Memories: {md_count} files")
        else:
            print(f"Markdown Memories: 0 files")

        # Story entries
        if STORY_FILE.exists():
            story_count = len([l for l in STORY_FILE.read_text().strip().split("\n") if l.strip()])
            print(f"Story Entries: {story_count}")

        total = k_active + a_active
        print(f"\n{'=' * 60}")
        print(f"Total Active Memories: {total}")
        print(f"{'=' * 60}")
    finally:
        db.close()


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "prime":
        output = prime_session()
        print(output)

    elif cmd == "capture":
        text = ""
        if "--text" in sys.argv:
            idx = sys.argv.index("--text")
            if idx + 1 < len(sys.argv):
                text = Path(sys.argv[idx + 1]).read_text()
        capture_session(text)

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: liyu-memory-v2.py search <query>")
            return
        query = " ".join(sys.argv[2:])
        results = unified_search(query)
        if not results:
            print("No results found.")
            return
        for r in results:
            source_label = {
                "knowledge": "KB",
                "auto": "AM",
                "reflection": "RF",
                "markdown": "MD",
            }.get(r.source, r.source)
            print(f"  [{source_label}] {r.decay_score:.3f} | {r.mem_type:10s} | {r.summary[:80]}")
        print(f"\n  {len(results)} results from unified search")

    elif cmd == "consolidate":
        consolidate()

    elif cmd == "link":
        build_links()

    elif cmd == "stats":
        show_stats()

    elif cmd == "inject":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        output = generate_injection(query)
        print(output)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
