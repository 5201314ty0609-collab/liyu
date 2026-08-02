#!/usr/bin/env python3
"""
鲤鱼 Auto-Memory — OMEGA-style Memory System.
吸收自 OMEGA Agent 的 Auto-Capture + Ebbinghaus Decay + Cross-Agent Sharing 模式。

自动从会话中提取事实/决策/教训，应用艾宾浩斯遗忘曲线衰减，
支持跨 Agent 记忆类型（语义/情景/程序/关系）。

Usage:
  python3 liyu-auto-memory.py capture [session-id]  从会话中自动捕获记忆
  python3 liyu-auto-memory.py recall <query>         带衰减评分检索记忆
  python3 liyu-auto-memory.py decay                  查看所有记忆的衰减状态
  python3 liyu-auto-memory.py stats                  记忆统计
  python3 liyu-auto-memory.py clean                  清理过期记忆（TTL=0）
"""

from collections import defaultdict
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

# ── 路径 ─────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
STORY_FILE = 鲤鱼_HOME / "story.jsonl"
REFLECTIONS_FILE = 鲤鱼_HOME / "reflections.jsonl"
MEMORY_CACHE_FILE = 鲤鱼_HOME / "auto-memory-cache.json"

# 复用 knowledge-base.db 的 schema，在其上添加 auto-memory 表
DB_PATH = 鲤鱼_HOME / "knowledge-base.db"

# ── 艾宾浩斯衰减参数 ─────────────────────────────────────────────────────
# 基于 Ebbinghaus (1885) 遗忘曲线: R = e^(-t/S)
# S = 相对记忆强度（越高越慢遗忘）
# 加上 frequency 和 importance 调节

DEFAULT_STRENGTH = 1.0       # 基准记忆强度
RECALL_BOOST = 0.3           # 每次 recall 增加的强度
IMPORTANCE_WEIGHT = 0.4      # importance 在衰减中的权重
FREQUENCY_WEIGHT = 0.3       # frequency 在衰减中的权重
RECENCY_WEIGHT = 0.3         # recency 在衰减中的权重
DECAY_THRESHOLD = 0.05       # 低于此值视为遗忘
MAX_TTL_DAYS = 90            # 最大存活天数（未 recall 的情况下）

# ── 记忆类型 ─────────────────────────────────────────────────────────────

MEMORY_TYPES = {
    "semantic": "事实知识（what）—— 概念、定义、配置",
    "episodic": "事件记忆（when）—— 会话摘要、任务完成",
    "procedural": "操作记忆（how）—— 工作流、模式、skill",
    "relational": "关系记忆（who/where）—— Agent 关系、依赖图",
}


# ── 数据类 ───────────────────────────────────────────────────────────────

@dataclass
class AutoMemory:
    """自动捕获的记忆条目"""
    id: str
    session_id: str
    content: str
    mem_type: str           # semantic/episodic/procedural/relational
    importance: float        # 0.0-1.0 重要性
    confidence: float        # 0.0-1.0 提取置信度
    source: str              # 来源（story/reflection/extracted）
    created_at: str
    last_recalled: str
    recall_count: int = 0
    decay_strength: float = DEFAULT_STRENGTH
    ttl_days: int = MAX_TTL_DAYS
    tags: List[str] = field(default_factory=list)
    linked_ids: List[str] = field(default_factory=list)


# ── 数据库 ───────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def init_auto_memory():
    """初始化 auto-memory 表（在 knowledge-base.db 中）"""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS auto_memories (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            mem_type TEXT DEFAULT 'semantic',
            importance REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'extracted',
            created_at TEXT NOT NULL,
            last_recalled TEXT NOT NULL,
            recall_count INTEGER DEFAULT 0,
            decay_strength REAL DEFAULT 1.0,
            ttl_days INTEGER DEFAULT 90,
            tags TEXT DEFAULT '[]',
            linked_ids TEXT DEFAULT '[]',
            is_active BOOLEAN DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_auto_mem_type ON auto_memories(mem_type);
        CREATE INDEX IF NOT EXISTS idx_auto_mem_session ON auto_memories(session_id);
        CREATE INDEX IF NOT EXISTS idx_auto_mem_active ON auto_memories(is_active);
        CREATE INDEX IF NOT EXISTS idx_auto_mem_decay ON auto_memories(decay_strength);

        -- FTS5 全文搜索
        CREATE VIRTUAL TABLE IF NOT EXISTS auto_memories_fts USING fts5(
            content, tags,
            content='auto_memories', content_rowid='rowid'
        );

        -- Triggers for FTS sync
        CREATE TRIGGER IF NOT EXISTS auto_mem_ai AFTER INSERT ON auto_memories BEGIN
            INSERT INTO auto_memories_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS auto_mem_ad AFTER DELETE ON auto_memories BEGIN
            INSERT INTO auto_memories_fts(auto_memories_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS auto_mem_au AFTER UPDATE ON auto_memories BEGIN
            INSERT INTO auto_memories_fts(auto_memories_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
            INSERT INTO auto_memories_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;
    """)
    db.commit()
    db.close()


# ── 提取引擎 ─────────────────────────────────────────────────────────────

def _extract_facts(text: str) -> List[Tuple[str, str, float]]:
    """从文本中提取事实（semantic memories）"""
    facts = []

    # 技术决策模式
    decision_patterns = [
        (r'(决定|选择|采用|使用|确定)[：:]\s*(.+?)(?:[。\n]|$)', 0.7),
        (r'(升级|迁移|切换到|替换为)\s*(.+?)(?:[。\n]|$)', 0.65),
        (r'(配置|设置|安装)[：:]\s*(.+?)(?:[。\n]|$)', 0.6),
        (r'鲤鱼\s*(v[\d.]+|升级|吸收|整合).*?(.+?)(?:[。\n]|$)', 0.75),
        (r'(创建|建立|搭建)[：:]\s*(.+?)(?:[。\n]|$)', 0.55),
        (r'(规则|Rule|Framework)[：:]\s*(.+?)(?:[。\n]|$)', 0.6),
        (r'(吸收自|借鉴|参考)[：:]\s*(.+?)(?:[。\n]|$)', 0.7),
    ]

    for pattern, confidence in decision_patterns:
        for match in re.finditer(pattern, text):
            facts.append(("semantic", match.group(0).strip(), confidence))

    return facts


def _extract_events(text: str) -> List[Tuple[str, str, float]]:
    """从文本中提取事件（episodic memories）"""
    events = []

    event_patterns = [
        (r'(完成|结束|达成)[：:]\s*(.+?)(?:[。\n]|$)', 0.65),
        (r'(任务|Task|Session)[：:]\s*(.+?)(?:[。\n]|$)', 0.55),
        (r'(修复|解决|Fix)[：:]\s*(.+?)(?:[。\n]|$)', 0.6),
        (r'(发现|遇到|出现)\s*(.+?)(?:[。\n]|$)', 0.5),
        (r'(成功|失败)[：:]\s*(.+?)(?:[。\n]|$)', 0.6),
    ]

    for pattern, confidence in event_patterns:
        for match in re.finditer(pattern, text):
            events.append(("episodic", match.group(0).strip(), confidence))

    return events


def _extract_procedures(text: str) -> List[Tuple[str, str, float]]:
    """从文本中提取操作模式（procedural memories）"""
    procedures = []

    proc_patterns = [
        (r'(工作流|workflow|流程)[：:]\s*(.+?)(?:[。\n]|$)', 0.65),
        (r'(先|然后|接着|最后)\s*(.+?)(?:[。\n]|$)', 0.4),
        (r'(步骤|Step|阶段)[：:]\s*(.+?)(?:[。\n]|$)', 0.55),
        (r'python3?\s+[~./].+?\.py\s+(\S+(?:\s+\S+)*)', 0.5),
        (r'(CLI|命令|command)[：:]\s*(.+?)(?:[。\n]|$)', 0.55),
    ]

    for pattern, confidence in proc_patterns:
        for match in re.finditer(pattern, text):
            procedures.append(("procedural", match.group(0).strip(), confidence))

    return procedures


def _extract_relations(text: str) -> List[Tuple[str, str, float]]:
    """从文本中提取关系（relational memories）"""
    relations = []

    rel_patterns = [
        (r'(Agent|代理|agent)[：:]\s*(.+?)(?:[。\n]|$)', 0.55),
        (r'(依赖|depend|requires?)\s*(.+?)(?:[。\n]|$)', 0.55),
        (r'(架构|Architecture)[：:]\s*(.+?)(?:[。\n]|$)', 0.6),
        (r'(多\s*Agent|Multi[- ]Agent|agent orchestration)\s*(.+?)(?:[。\n]|$)', 0.65),
    ]

    for pattern, confidence in rel_patterns:
        for match in re.finditer(pattern, text):
            relations.append(("relational", match.group(0).strip(), confidence))

    return relations


def extract_memories_from_session(session_id: str, text: str) -> List[AutoMemory]:
    """从会话文本中提取所有类型的记忆"""
    now = datetime.now(timezone.utc).isoformat()
    memories = []

    extractors = [
        _extract_facts,
        _extract_events,
        _extract_procedures,
        _extract_relations,
    ]

    seen = set()
    for extractor in extractors:
        for mem_type, content, confidence in extractor(text):
            # 去重
            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            if content_hash in seen:
                continue
            seen.add(content_hash)

            # 估计重要性
            importance = _estimate_importance(content)

            memories.append(AutoMemory(
                id=f"am-{uuid.uuid4().hex[:10]}",
                session_id=session_id,
                content=content,
                mem_type=mem_type,
                importance=importance,
                confidence=confidence,
                source="extracted",
                created_at=now,
                last_recalled=now,
                recall_count=0,
                decay_strength=DEFAULT_STRENGTH,
                ttl_days=MAX_TTL_DAYS,
                tags=[mem_type],
                linked_ids=[],
            ))

    return memories


def _estimate_importance(content: str) -> float:
    """启发式估计内容重要性"""
    importance = 0.5

    # 鲤鱼 相关内容更重要
    if re.search(r'鲤鱼|liyu|框架|Framework|进化|evolution', content):
        importance += 0.2

    # 决策/架构类更重要
    if re.search(r'决定|架构|Architecture|升级|迁移|吸收', content):
        importance += 0.15

    # 用户指令更重要
    if re.search(r'用户|时宇|李时宇|HolyTy|指令|要求', content):
        importance += 0.15

    # 配置类中等
    if re.search(r'配置|config|settings|安装|install', content):
        importance += 0.05

    return min(importance, 1.0)


# ── 艾宾浩斯衰减引擎 ─────────────────────────────────────────────────────

def compute_decay_score(memory: AutoMemory) -> float:
    """
    计算记忆的当前衰减分数。
    Score = recency × frequency × importance

    - recency: 基于 last_recalled 的艾宾浩斯衰减
    - frequency: recall_count 的对数加成
    - importance: 原始重要性评分
    """
    # Recency: Ebbinghaus curve R = e^(-t/S)
    try:
        last = datetime.fromisoformat(memory.last_recalled)
        now = datetime.now(timezone.utc)
        hours_elapsed = (now - last).total_seconds() / 3600
    except Exception:
        hours_elapsed = 24.0

    # 衰减强度越高，遗忘越慢
    S = memory.decay_strength * 24.0  # 转换为小时单位
    recency = math.exp(-hours_elapsed / max(S, 1.0))

    # Frequency: 对数增长，避免线性膨胀
    freq_bonus = math.log(1 + memory.recall_count) * 0.1
    frequency = 1.0 + freq_bonus

    # Importance: 直接使用
    importance = memory.importance

    # 综合分数
    score = (RECENCY_WEIGHT * recency +
             FREQUENCY_WEIGHT * min(frequency, 2.0) / 2.0 +
             IMPORTANCE_WEIGHT * importance)

    return round(score, 4)


def update_decay_for_all(db) -> Dict[str, List[str]]:
    """
    更新所有记忆的衰减状态。
    返回 {"active": [...], "decayed": [...], "forgotten": [...]}
    """
    now = datetime.now(timezone.utc)

    rows = db.execute(
        "SELECT * FROM auto_memories WHERE is_active = 1"
    ).fetchall()

    result = {"active": [], "decayed": [], "forgotten": []}

    for row in rows:
        memory = AutoMemory(
            id=row["id"],
            session_id=row["session_id"],
            content=row["content"],
            mem_type=row["mem_type"],
            importance=row["importance"],
            confidence=row["confidence"],
            source=row["source"],
            created_at=row["created_at"],
            last_recalled=row["last_recalled"],
            recall_count=row["recall_count"],
            decay_strength=row["decay_strength"],
            ttl_days=row["ttl_days"],
            tags=json.loads(row["tags"]),
            linked_ids=json.loads(row["linked_ids"]),
        )

        score = compute_decay_score(memory)

        # 更新 TTL
        try:
            created = datetime.fromisoformat(memory.created_at)
            days_old = (now - created).total_seconds() / 86400
            new_ttl = max(1, memory.ttl_days - int(days_old * (1.0 - score)))
        except Exception:
            new_ttl = memory.ttl_days

        if score < DECAY_THRESHOLD:
            # 完全遗忘 → 标记为 inactive
            db.execute(
                "UPDATE auto_memories SET is_active = 0, decay_strength = ? WHERE id = ?",
                (score, memory.id),
            )
            result["forgotten"].append(memory.id)
        elif score < 0.3:
            # 衰减中
            db.execute(
                "UPDATE auto_memories SET decay_strength = ?, ttl_days = ? WHERE id = ?",
                (score, new_ttl, memory.id),
            )
            result["decayed"].append(memory.id)
        else:
            result["active"].append(memory.id)

    db.commit()
    return result


# ── 捕获 ─────────────────────────────────────────────────────────────────

def capture(session_id: Optional[str] = None):
    """从最近的会话中捕获记忆"""
    init_auto_memory()

    if session_id:
        # 指定 session
        text = _load_session_text(session_id)
    else:
        # 使用最近的 story.jsonl + reflections
        text = _load_recent_text()

    if not text:
        print("No session text found to capture from.")
        return

    print(f"Extracting memories from {len(text)} chars of session text...")

    memories = extract_memories_from_session(session_id or "auto", text)

    db = get_db()
    saved = 0
    skipped = 0

    for mem in memories:
        # 检查是否已存在相似记忆
        existing = db.execute(
            "SELECT id FROM auto_memories WHERE content = ? AND is_active = 1",
            (mem.content,)
        ).fetchone()

        if existing:
            # 已存在 → 强化
            db.execute(
                """UPDATE auto_memories SET
                    recall_count = recall_count + 1,
                    last_recalled = ?,
                    decay_strength = MIN(decay_strength + ?, 3.0),
                    importance = MAX(importance, ?)
                WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(),
                 RECALL_BOOST, mem.importance, existing["id"]),
            )
            skipped += 1
        else:
            db.execute(
                """INSERT INTO auto_memories
                (id, session_id, content, mem_type, importance, confidence,
                 source, created_at, last_recalled, recall_count,
                 decay_strength, ttl_days, tags, linked_ids, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (mem.id, mem.session_id, mem.content, mem.mem_type,
                 mem.importance, mem.confidence, mem.source,
                 mem.created_at, mem.last_recalled, mem.recall_count,
                 mem.decay_strength, mem.ttl_days,
                 json.dumps(mem.tags, ensure_ascii=False),
                 json.dumps(mem.linked_ids, ensure_ascii=False)),
            )
            saved += 1

    db.commit()

    # 更新衰减
    decay_result = update_decay_for_all(db)
    db.close()

    print(f"Capture complete: {saved} new, {skipped} reinforced")
    print(f"Decay state: {len(decay_result['active'])} active, "
          f"{len(decay_result['decayed'])} decaying, "
          f"{len(decay_result['forgotten'])} forgotten")


def _load_session_text(session_id: str) -> str:
    """加载指定 session 的文本"""
    parts = []

    # 从 story.jsonl 加载
    if STORY_FILE.exists():
        for line in STORY_FILE.read_text().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if session_id in entry.get("summary", ""):
                    parts.append(entry.get("summary", ""))
            except Exception:
                pass

    # 从 reflections 加载
    if REFLECTIONS_FILE.exists():
        for line in REFLECTIONS_FILE.read_text().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if session_id in entry.get("task_id", ""):
                    parts.append(entry.get("result_summary", ""))
                    for lesson in entry.get("lessons", []):
                        parts.append(lesson)
            except Exception:
                pass

    return "\n".join(parts)


def _load_recent_text(max_chars: int = 50000) -> str:
    """加载最近的会话文本"""
    parts = []

    # 从 story.jsonl 加载最近的条目
    if STORY_FILE.exists():
        lines = STORY_FILE.read_text().strip().split("\n")
        for line in reversed(lines[-30:]):  # 最近 30 条
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                summary = entry.get("summary", "")
                if summary and "详见日记" not in summary:
                    parts.append(summary)
            except Exception:
                pass

    # 从 reflections 加载最近的
    if REFLECTIONS_FILE.exists():
        lines = REFLECTIONS_FILE.read_text().strip().split("\n")
        for line in reversed(lines[-20:]):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                parts.append(entry.get("result_summary", ""))
                for lesson in entry.get("lessons", []):
                    parts.append(lesson)
            except Exception:
                pass

    text = "\n".join(parts)
    return text[:max_chars]


# ── 召回 ─────────────────────────────────────────────────────────────────

def recall(query: str, top_k: int = 10, mem_type: str = ""):
    """召回记忆，带衰减评分排序"""
    init_auto_memory()
    db = get_db()

    # 先更新衰减
    update_decay_for_all(db)

    # FTS5 搜索（带 LIKE 回退）
    results = []
    fts_failed = False
    try:
        fts_rows = db.execute("""
            SELECT am.*, rank
            FROM auto_memories_fts
            JOIN auto_memories am ON auto_memories_fts.rowid = am.rowid
            WHERE auto_memories_fts MATCH ? AND am.is_active = 1
            ORDER BY rank
            LIMIT ?
        """, (query, top_k * 3)).fetchall()

        for row in fts_rows:
            if mem_type and row["mem_type"] != mem_type:
                continue
            score = compute_decay_score(_row_to_memory(row))
            results.append((_row_to_memory(row), score, ["FTS5 匹配"]))

        # FTS5 无结果时回退到 LIKE
        if not results:
            fts_failed = True
    except Exception:
        fts_failed = True

    if fts_failed:
        like_query = f"%{query}%"
        like_rows = db.execute("""
            SELECT * FROM auto_memories
            WHERE content LIKE ? AND is_active = 1
            ORDER BY decay_strength DESC
            LIMIT ?
        """, (like_query, top_k * 3)).fetchall()

        for row in like_rows:
            if mem_type and row["mem_type"] != mem_type:
                continue
            score = compute_decay_score(_row_to_memory(row))
            reason = "FTS5 回退 → LIKE 匹配" if not results else "LIKE 匹配"
            results.append((_row_to_memory(row), score, [reason]))

    # 按衰减分数排序（FTS5 rank 和 decay score 结合）
    results.sort(key=lambda x: x[1], reverse=True)
    results = results[:top_k]

    # 更新 recall 计数
    for memory, _, _ in results:
        db.execute(
            """UPDATE auto_memories SET
                recall_count = recall_count + 1,
                last_recalled = ?,
                decay_strength = MIN(decay_strength + ?, 3.0)
            WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), RECALL_BOOST, memory.id),
        )
    db.commit()
    db.close()

    # 输出结果
    if not results:
        print("No memories found.")
        return

    for memory, score, reasons in results:
        type_label = {
            "semantic": "语义",
            "episodic": "情景",
            "procedural": "程序",
            "relational": "关系",
        }.get(memory.mem_type, memory.mem_type)

        bar = _decay_bar(score)
        print(f"  [{bar}] {score:.3f} | {type_label} | recalls: {memory.recall_count}")
        print(f"       {memory.content[:100]}")
        print(f"       Reasons: {', '.join(reasons)}")
        print()


def _row_to_memory(row) -> AutoMemory:
    """将 SQL row 转换为 AutoMemory"""
    return AutoMemory(
        id=row["id"],
        session_id=row["session_id"],
        content=row["content"],
        mem_type=row["mem_type"],
        importance=row["importance"],
        confidence=row["confidence"],
        source=row["source"],
        created_at=row["created_at"],
        last_recalled=row["last_recalled"],
        recall_count=row["recall_count"],
        decay_strength=row["decay_strength"],
        ttl_days=row["ttl_days"],
        tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"],
        linked_ids=json.loads(row["linked_ids"]) if isinstance(row["linked_ids"], str) else row["linked_ids"],
    )


def _decay_bar(score: float, width: int = 10) -> str:
    """可视化衰减条"""
    filled = int(score * width)
    if score > 0.7:
        bar = "█" * filled + "░" * (width - filled)
    elif score > 0.3:
        bar = "▓" * filled + "░" * (width - filled)
    else:
        bar = "▒" * filled + "░" * (width - filled)
    return bar


# ── 统计 ─────────────────────────────────────────────────────────────────

def show_stats():
    """显示记忆统计"""
    init_auto_memory()
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM auto_memories").fetchone()[0]
    active = db.execute(
        "SELECT COUNT(*) FROM auto_memories WHERE is_active = 1"
    ).fetchone()[0]
    forgotten = total - active

    by_type = {}
    for row in db.execute(
        "SELECT mem_type, COUNT(*) as c FROM auto_memories WHERE is_active = 1 GROUP BY mem_type"
    ).fetchall():
        by_type[row["mem_type"]] = row["c"]

    # 衰减分布
    avg_decay = db.execute(
        "SELECT AVG(decay_strength) FROM auto_memories WHERE is_active = 1"
    ).fetchone()[0] or 0

    total_recalls = db.execute(
        "SELECT SUM(recall_count) FROM auto_memories"
    ).fetchone()[0] or 0

    db.close()

    print(f"Auto-Memories: {total} total ({active} active, {forgotten} forgotten)")
    print(f"By type: {by_type}")
    print(f"Average decay strength: {avg_decay:.3f}")
    print(f"Total recalls: {total_recalls}")
    print()
    print("Memory Types:")
    for mt, desc in MEMORY_TYPES.items():
        count = by_type.get(mt, 0)
        print(f"  {mt:12s} ({count:3d}) — {desc}")


def show_decay():
    """显示衰减状态明细"""
    init_auto_memory()
    db = get_db()
    update_decay_for_all(db)

    rows = db.execute(
        "SELECT * FROM auto_memories WHERE is_active = 1 ORDER BY decay_strength ASC"
    ).fetchall()

    if not rows:
        print("No active memories.")
        db.close()
        return

    print(f"{'Score':>6}  {'Type':12s}  {'R':>3}  Content")
    print("-" * 70)

    for row in rows[:30]:
        memory = _row_to_memory(row)
        score = compute_decay_score(memory)
        bar = _decay_bar(score, 8)
        print(f"{score:6.3f}  {memory.mem_type:12s}  {memory.recall_count:3d}  {bar}  {memory.content[:50]}")

    db.close()


def clean():
    """清理已遗忘的记忆"""
    init_auto_memory()
    db = get_db()

    # 先更新衰减
    update_decay_for_all(db)

    # 删除 is_active=0 的记忆
    result = db.execute("DELETE FROM auto_memories WHERE is_active = 0")
    deleted = result.rowcount
    db.commit()
    db.close()

    print(f"Cleaned {deleted} forgotten memories.")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "capture":
        session_id = sys.argv[2] if len(sys.argv) > 2 else None
        capture(session_id)

    elif cmd == "recall":
        if len(sys.argv) < 3:
            print("Usage: python3 liyu-auto-memory.py recall <query> [--type semantic|episodic|procedural|relational]")
            return
        query = sys.argv[2]
        mem_type = ""
        if len(sys.argv) > 3 and sys.argv[3] == "--type":
            mem_type = sys.argv[4] if len(sys.argv) > 4 else ""
        recall(query, mem_type=mem_type)

    elif cmd == "decay":
        show_decay()

    elif cmd == "stats":
        show_stats()

    elif cmd == "clean":
        clean()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
