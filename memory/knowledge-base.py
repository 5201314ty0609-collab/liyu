#!/usr/bin/env python3
"""
鲤鱼 Knowledge Base — SQLite + FTS5 + Vector + Graph 混合检索系统。

v2.0 — Hybrid Retrieval Upgrade (2026-06-17)
  - FTS5 BM25 全文搜索
  - Vector 语义向量搜索 (numpy n-gram / sentence-transformers 可选)
  - Graph 关系图谱遍历
  - RRF (Reciprocal Rank Fusion) 混合排序
  - 向后兼容 v1.x 所有命令

Usage:
  knowledge-base.py import                         从 memory/ 导入
  knowledge-base.py search <query>                 全文搜索 (FTS5)
  knowledge-base.py search <query> --hybrid        混合搜索 (FTS5 + Vector RRF)
  knowledge-base.py search <query> --vector        纯向量搜索
  knowledge-base.py graph <node_id>                查看关系图谱
  knowledge-base.py graph <node_id> --depth 3      多跳遍历
  knowledge-base.py stats                          统计信息
  knowledge-base.py sync                           同步 memory/ 变更
  knowledge-base.py context <query>                获取上下文片段
  knowledge-base.py rebuild-vectors                重建所有向量
  knowledge-base.py relations --auto               自动检测并建议关系
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Sequence
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys

# ── 可选依赖 ────────────────────────────────────────────────────────────────

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_ST = True
except ImportError:
    HAS_ST = False

# ── 路径 ─────────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude/liyu"
MEMORY_DIR = Path.home() / ".claude/projects/-Users-holyty/memory"
DB_PATH = 鲤鱼_HOME / "knowledge-base.db"

# ── 向量配置 ────────────────────────────────────────────────────────────────

VECTOR_DIM = 256          # n-gram 向量维度
ST_MODEL_NAME = "BAAI/bge-small-en-v1.5"
ST_VECTOR_DIM = 384       # bge-small-en 输出维度

# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class MemoryChunk:
    """记忆片段"""
    id: str
    name: str
    description: str
    type: str  # user/feedback/project/reference
    content: str
    tags: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    access_count: int = 0
    is_active: bool = True


@dataclass
class SearchResult:
    """搜索结果"""
    chunk: MemoryChunk
    score: float
    match_reasons: List[str]


@dataclass
class Relation:
    """记忆间关系"""
    source_id: str
    target_id: str
    relation_type: str  # related_to, supersedes, contradicts, supports, depends_on
    weight: float
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# 向量嵌入引擎
# ═══════════════════════════════════════════════════════════════════════════════

class VectorEngine:
    """多层向量嵌入引擎。

    优先级: sentence-transformers > numpy n-gram > pure-python n-gram
    """

    def __init__(self):
        self._st_model = None
        self._initialized = False

    def _ensure_init(self):
        if self._initialized:
            return
        if HAS_ST:
            try:
                self._st_model = SentenceTransformer(ST_MODEL_NAME)
                print(f"[VectorEngine] Loaded {ST_MODEL_NAME} ({ST_VECTOR_DIM}d)")
            except Exception as e:
                print(f"[VectorEngine] sentence-transformers unavailable: {e}")
                self._st_model = None
        self._initialized = True

    @property
    def dim(self) -> int:
        self._ensure_init()
        if self._st_model is not None:
            return ST_VECTOR_DIM
        return VECTOR_DIM

    @property
    def backend(self) -> str:
        self._ensure_init()
        if self._st_model is not None:
            return "sentence-transformers"
        if HAS_NUMPY:
            return "numpy-ngram"
        return "pure-ngram"

    def encode(self, text: str) -> bytes:
        """将文本编码为向量字节串。"""
        self._ensure_init()
        if self._st_model is not None:
            vec = self._st_model.encode(text, normalize_embeddings=True)
            return vec.astype(np.float32).tobytes()
        if HAS_NUMPY:
            return self._encode_numpy(text)
        return self._encode_pure(text)

    def encode_batch(self, texts: List[str]) -> List[bytes]:
        """批量编码。"""
        self._ensure_init()
        if self._st_model is not None:
            vecs = self._st_model.encode(texts, normalize_embeddings=True)
            return [v.astype(np.float32).tobytes() for v in vecs]
        return [self.encode(t) for t in texts]

    # ── NumPy n-gram 向量 ────────────────────────────────────────────────

    def _encode_numpy(self, text: str) -> bytes:
        """基于 NumPy 的字符 n-gram 向量 (256-dim, L2 归一化)。"""
        text = re.sub(r'\s+', ' ', text.lower().strip())
        vec = np.zeros(VECTOR_DIM, dtype=np.float32)

        for n in [2, 3, 4, 5]:
            weight = 1.0 / n  # 短 n-gram 权重更高
            for i in range(len(text) - n + 1):
                ngram = text[i:i + n]
                h = hashlib.md5(ngram.encode()).digest()
                idx = int.from_bytes(h[:4], 'little') % VECTOR_DIM
                vec[idx] += weight

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tobytes()

    def _encode_pure(self, text: str) -> bytes:
        """纯 Python n-gram 向量 (无 NumPy 依赖)。"""
        text = re.sub(r'\s+', ' ', text.lower().strip())
        vec = [0.0] * VECTOR_DIM

        for n in [2, 3, 4, 5]:
            weight = 1.0 / n
            for i in range(len(text) - n + 1):
                ngram = text[i:i + n]
                h = hashlib.md5(ngram.encode()).digest()
                idx = int.from_bytes(h[:4], 'little') % VECTOR_DIM
                vec[idx] += weight

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]

        # 打包为 float32 字节
        return struct.pack(f'{VECTOR_DIM}f', *vec)

    # ── 相似度计算 ──────────────────────────────────────────────────────

    def similarity(self, vec_bytes_a: bytes, vec_bytes_b: bytes) -> float:
        """计算两个向量字节串的余弦相似度。"""
        if self._st_model is not None:
            dim = ST_VECTOR_DIM
        else:
            dim = VECTOR_DIM

        if HAS_NUMPY:
            a = np.frombuffer(vec_bytes_a, dtype=np.float32)
            b = np.frombuffer(vec_bytes_b, dtype=np.float32)
            if len(a) != len(b):
                return 0.0
            dot = np.dot(a, b)
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na == 0 or nb == 0:
                return 0.0
            return float(dot / (na * nb))
        else:
            fmt = f'{dim}f'
            a = struct.unpack(fmt, vec_bytes_a[:dim * 4])
            b = struct.unpack(fmt, vec_bytes_b[:dim * 4])
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

    def batch_similarity(self, query_vec: bytes, doc_vecs: List[bytes]) -> List[float]:
        """计算查询向量与一批文档向量的相似度。"""
        if HAS_NUMPY:
            dim = ST_VECTOR_DIM if self._st_model else VECTOR_DIM
            q = np.frombuffer(query_vec, dtype=np.float32)
            if len(doc_vecs) == 0:
                return []
            stacked = np.vstack([
                np.frombuffer(v, dtype=np.float32) for v in doc_vecs
            ])
            dots = np.dot(stacked, q)
            norms_q = np.linalg.norm(q)
            norms_d = np.linalg.norm(stacked, axis=1)
            denom = norms_q * norms_d
            denom[denom == 0] = 1.0
            return (dots / denom).tolist()
        else:
            return [self.similarity(query_vec, v) for v in doc_vecs]


# 全局向量引擎实例
_vector_engine: Optional[VectorEngine] = None


def get_vector_engine() -> VectorEngine:
    global _vector_engine
    if _vector_engine is None:
        _vector_engine = VectorEngine()
    return _vector_engine


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库
# ═══════════════════════════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db():
    """初始化数据库 schema（含向量表和关系表）。"""
    db = get_db()
    db.executescript("""
        -- 主记忆表
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            type TEXT DEFAULT 'reference',
            content TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            links TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            access_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
        CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(is_active);

        -- FTS5 全文搜索
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            name, description, content, tags,
            content='memories', content_rowid='rowid'
        );

        -- FTS5 触发器
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, name, description, content, tags)
            VALUES (new.rowid, new.name, new.description, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, name, description, content, tags)
            VALUES ('delete', old.rowid, old.name, old.description, old.content, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, name, description, content, tags)
            VALUES ('delete', old.rowid, old.name, old.description, old.content, old.tags);
            INSERT INTO memories_fts(rowid, name, description, content, tags)
            VALUES (new.rowid, new.name, new.description, new.content, new.tags);
        END;

        -- 向量表 (v2.0)
        CREATE TABLE IF NOT EXISTS memory_vectors (
            memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            vector BLOB NOT NULL,
            model TEXT NOT NULL DEFAULT 'ngram',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_vectors_model ON memory_vectors(model);

        -- 关系表 (v2.0)
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL DEFAULT 'related_to',
            weight REAL NOT NULL DEFAULT 1.0,
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, relation_type)
        );

        CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
        CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
        CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type);

        -- 模式版本追踪
        CREATE TABLE IF NOT EXISTS schema_version (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # 检查并记录 schema 版本
    row = db.execute(
        "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    if not row or row["version"] != "2.0.0":
        db.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES ('2.0.0')"
        )

    db.commit()
    db.close()


def migrate_v2():
    """v1.x → v2.0 迁移：确保向量表和关系表存在。"""
    db = get_db()

    # 检查向量表
    has_vectors = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_vectors'"
    ).fetchone()

    # 检查关系表
    has_relations = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='relations'"
    ).fetchone()

    if not has_vectors or not has_relations:
        print("[Migrate] v1.x → v2.0: adding vector + relation tables...")
        db.close()
        init_db()

        if not has_vectors:
            print("[Migrate] Vectors table created. Run 'rebuild-vectors' to populate.")
        if not has_relations:
            print("[Migrate] Relations table created. Run 'relations --auto' to detect.")
    else:
        db.close()


# ── Frontmatter 解析 ─────────────────────────────────────────────────────────

def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """解析 YAML frontmatter。"""
    if not content.startswith("---"):
        return {}, content

    end = content.find("---", 3)
    if end == -1:
        return {}, content

    frontmatter_str = content[3:end].strip()
    body = content[end + 3:].strip()

    meta = {}
    for line in frontmatter_str.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if "." in key:
                parts = key.split(".")
                if parts[0] not in meta:
                    meta[parts[0]] = {}
                meta[parts[0]][parts[1]] = value
            else:
                meta[key] = value

    return meta, body


def extract_links(content: str) -> List[str]:
    """提取 [[link]] 引用。"""
    return re.findall(r'\[\[(\w[\w-]*)\]\]', content)


# ═══════════════════════════════════════════════════════════════════════════════
# 向量操作
# ═══════════════════════════════════════════════════════════════════════════════

def rebuild_vectors():
    """重建所有记忆的向量嵌入。"""
    db = get_db()
    engine = get_vector_engine()

    rows = db.execute("SELECT id, name, description, content FROM memories WHERE is_active = 1").fetchall()

    if not rows:
        print("No active memories to vectorize.")
        db.close()
        return

    texts = []
    ids = []
    for row in rows:
        text = f"{row['name']} {row['description']} {row['content'][:2000]}"
        texts.append(text)
        ids.append(row["id"])

    print(f"[Vectors] Encoding {len(texts)} memories with {engine.backend} ({engine.dim}d)...")

    if engine.backend == "sentence-transformers":
        # ST 有自己的批量优化
        embeddings = engine._st_model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        for mem_id, emb in zip(ids, embeddings):
            vec_bytes = emb.astype(np.float32).tobytes()
            db.execute(
                """INSERT OR REPLACE INTO memory_vectors (memory_id, vector, model)
                   VALUES (?, ?, ?)""",
                (mem_id, vec_bytes, engine.backend)
            )
    else:
        for i, (mem_id, text) in enumerate(zip(ids, texts)):
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(texts)}...")
            vec_bytes = engine.encode(text)
            db.execute(
                """INSERT OR REPLACE INTO memory_vectors (memory_id, vector, model)
                   VALUES (?, ?, ?)""",
                (mem_id, vec_bytes, engine.backend)
            )

    db.commit()
    db.close()
    print(f"[Vectors] Done. {len(texts)} vectors stored ({engine.backend}, {engine.dim}d).")


def vector_search(query: str, top_k: int = 10) -> List[Tuple[str, float]]:
    """纯向量相似度搜索。"""
    db = get_db()
    engine = get_vector_engine()

    query_text = f"{query}"
    query_vec = engine.encode(query_text)

    rows = db.execute(
        "SELECT memory_id, vector, model FROM memory_vectors"
    ).fetchall()

    if not rows:
        db.close()
        return []

    ids = [r["memory_id"] for r in rows]
    vecs = [r["vector"] for r in rows]

    if engine.backend == "sentence-transformers":
        scores = engine.batch_similarity(query_vec, vecs)
    else:
        scores = [engine.similarity(query_vec, v) for v in vecs]

    results = list(zip(ids, scores))
    results.sort(key=lambda x: x[1], reverse=True)

    db.close()
    return results[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# RRF 混合搜索
# ═══════════════════════════════════════════════════════════════════════════════

def rrf_hybrid_search(
    query: str,
    top_k: int = 10,
    k: int = 60,
    mem_type: str = "",
) -> List[SearchResult]:
    """RRF (Reciprocal Rank Fusion) 混合搜索。

    融合 FTS5 BM25 排名和向量相似度排名。
    RRF 公式: score(d) = sum(1 / (k + rank_i(d))) for each ranking i
    """
    db = get_db()
    engine = get_vector_engine()

    # ── 第1路: FTS5 BM25 ──────────────────────────────────────────────
    fts_ranked: Dict[str, int] = {}  # id → rank (1-based)
    try:
        fts_rows = db.execute("""
            SELECT m.id, rank
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, top_k * 5)).fetchall()

        for rank, row in enumerate(fts_rows, start=1):
            fts_ranked[row["id"]] = rank
    except Exception:
        pass  # FTS5 查询可能因特殊字符失败

    # ── 第2路: Vector 相似度 ──────────────────────────────────────────
    query_text = f"{query}"
    query_vec = engine.encode(query_text)

    vec_rows = db.execute(
        "SELECT memory_id, vector, model FROM memory_vectors"
    ).fetchall()

    vec_scores: Dict[str, float] = {}
    if vec_rows:
        vec_ids = [r["memory_id"] for r in vec_rows]
        if engine.backend == "sentence-transformers":
            scores = engine.batch_similarity(query_vec, [r["vector"] for r in vec_rows])
        else:
            scores = [engine.similarity(query_vec, r["vector"]) for r in vec_rows]

        # 按分数排序得到排名
        scored = sorted(zip(vec_ids, scores), key=lambda x: x[1], reverse=True)
        for rank, (vid, score) in enumerate(scored, start=1):
            vec_scores[vid] = score
    else:
        scored = []

    vec_ranked: Dict[str, int] = {}
    for rank, (vid, _) in enumerate(scored, start=1):
        vec_ranked[vid] = rank

    # ── RRF 融合 ──────────────────────────────────────────────────────
    all_ids = set(list(fts_ranked.keys()) + list(vec_ranked.keys()))
    rrf_scores: Dict[str, float] = {}

    for doc_id in all_ids:
        score = 0.0
        reasons = []
        if doc_id in fts_ranked:
            score += 1.0 / (k + fts_ranked[doc_id])
            reasons.append("FTS5")
        if doc_id in vec_ranked:
            score += 1.0 / (k + vec_ranked[doc_id])
            reasons.append("Vector")
        rrf_scores[doc_id] = score

    # ── 构建结果 ──────────────────────────────────────────────────────
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
    results = []

    for doc_id in sorted_ids:
        row = db.execute("SELECT * FROM memories WHERE id = ? AND is_active = 1", (doc_id,)).fetchone()
        if not row:
            continue
        if mem_type and row["type"] != mem_type:
            continue

        chunk = MemoryChunk(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            type=row["type"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            links=json.loads(row["links"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            access_count=row["access_count"],
            is_active=bool(row["is_active"]),
        )

        reasons = []
        if doc_id in fts_ranked:
            reasons.append(f"FTS5 rank={fts_ranked[doc_id]}")
        if doc_id in vec_ranked:
            reasons.append(f"Vector rank={vec_ranked[doc_id]} sim={vec_scores.get(doc_id, 0):.3f}")

        # 时间衰减
        try:
            updated = datetime.fromisoformat(chunk.updated_at)
            age_hours = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            time_factor = 1.0 / (1.0 + age_hours * 0.01)
        except Exception:
            time_factor = 1.0

        # 访问频率加成
        access_factor = 1.0 + min(chunk.access_count * 0.05, 0.5)

        final_score = rrf_scores[doc_id] * time_factor * access_factor

        results.append(SearchResult(
            chunk=chunk,
            score=final_score,
            match_reasons=reasons,
        ))

    results.sort(key=lambda r: r.score, reverse=True)

    # 更新访问计数
    for result in results[:top_k]:
        db.execute(
            "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
            (result.chunk.id,)
        )
    db.commit()
    db.close()

    return results[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# 传统搜索（向后兼容）
# ═══════════════════════════════════════════════════════════════════════════════

class SemanticHashIndex:
    """语义哈希索引 — SimHash 变体（保留向后兼容）。"""

    def __init__(self, hash_bits: int = 64):
        self._hash_bits = hash_bits
        self._vectors: Dict[str, List[float]] = {}

    def add(self, doc_id: str, text: str):
        self._vectors[doc_id] = self._text_to_vector(text)

    def remove(self, doc_id: str):
        self._vectors.pop(doc_id, None)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        query_vec = self._text_to_vector(query)
        scores = []
        for doc_id, doc_vec in self._vectors.items():
            sim = self._cosine_similarity(query_vec, doc_vec)
            scores.append((doc_id, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _text_to_vector(self, text: str) -> List[float]:
        text = re.sub(r'\s+', ' ', text.lower().strip())
        ngrams = Counter()
        for n in [2, 3, 4]:
            for i in range(len(text) - n + 1):
                ngrams[text[i:i + n]] += 1

        vector = [0.0] * self._hash_bits
        for ngram, count in ngrams.items():
            h = hashlib.md5(ngram.encode()).digest()
            for i in range(min(self._hash_bits, len(h) * 8)):
                byte_idx = i // 8
                bit_idx = i % 8
                if h[byte_idx] & (1 << bit_idx):
                    vector[i] += count
                else:
                    vector[i] -= count

        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            vector = [x / norm for x in vector]
        return vector

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)


_semantic_index: Optional[SemanticHashIndex] = None


def _ensure_semantic_index():
    global _semantic_index
    if _semantic_index is not None:
        return
    _semantic_index = SemanticHashIndex()
    db = get_db()
    rows = db.execute(
        "SELECT id, name, description, content FROM memories WHERE is_active = 1"
    ).fetchall()
    db.close()
    for row in rows:
        text = f"{row['name']} {row['description']} {row['content'][:500]}"
        _semantic_index.add(row["id"], text)


def search_fts5(query: str, top_k: int = 5, mem_type: str = "") -> List[SearchResult]:
    """传统 FTS5 + SimHash 混合搜索（向后兼容）。"""
    init_db()
    _ensure_semantic_index()

    db = get_db()

    # FTS5 搜索
    fts_results = {}
    try:
        fts_rows = db.execute("""
            SELECT m.id, m.name, m.description, m.type, m.content, m.tags, m.links,
                   m.created_at, m.updated_at, m.access_count, m.is_active,
                   rank
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, top_k * 3)).fetchall()

        for i, row in enumerate(fts_rows):
            score = 1.0 / (1.0 + i)
            fts_results[row["id"]] = (score, ["FTS5"])
    except Exception:
        pass

    # SimHash 语义搜索
    semantic_results = _semantic_index.search(query, top_k=top_k * 3)

    # 合并
    combined: Dict[str, Tuple[float, List[str]]] = {}
    for doc_id, (score, reasons) in fts_results.items():
        combined[doc_id] = (score * 0.6, reasons)
    for doc_id, score in semantic_results:
        if doc_id in combined:
            old_score, reasons = combined[doc_id]
            combined[doc_id] = (old_score + score * 0.4, reasons + ["SimHash"])
        else:
            combined[doc_id] = (score * 0.4, ["SimHash"])

    results = []
    for doc_id, (score, reasons) in combined.items():
        row = db.execute("SELECT * FROM memories WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            continue
        if mem_type and row["type"] != mem_type:
            continue

        chunk = MemoryChunk(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            type=row["type"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            links=json.loads(row["links"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            access_count=row["access_count"],
            is_active=bool(row["is_active"]),
        )

        try:
            updated = datetime.fromisoformat(chunk.updated_at)
            age_hours = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            time_factor = 1.0 / (1.0 + age_hours * 0.01)
        except Exception:
            time_factor = 1.0

        access_factor = 1.0 + min(chunk.access_count * 0.05, 0.5)
        final_score = score * time_factor * access_factor

        results.append(SearchResult(chunk=chunk, score=final_score, match_reasons=reasons))

    results.sort(key=lambda r: r.score, reverse=True)

    for result in results[:top_k]:
        db.execute(
            "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
            (result.chunk.id,)
        )
    db.commit()
    db.close()

    return results[:top_k]


# ── 统一 search() 入口（根据模式分发）───────────────────────────────────────

def search(query: str, top_k: int = 5, mem_type: str = "",
           mode: str = "fts5") -> List[SearchResult]:
    """统一搜索入口。

    mode: 'fts5' (默认/传统), 'hybrid' (RRF), 'vector' (纯向量)
    """
    if mode == "hybrid":
        return rrf_hybrid_search(query, top_k=top_k, mem_type=mem_type)
    elif mode == "vector":
        vec_results = vector_search(query, top_k=top_k)
        if not vec_results:
            return []
        db = get_db()
        results = []
        for doc_id, score in vec_results:
            row = db.execute(
                "SELECT * FROM memories WHERE id = ? AND is_active = 1", (doc_id,)
            ).fetchone()
            if not row:
                continue
            if mem_type and row["type"] != mem_type:
                continue
            chunk = MemoryChunk(
                id=row["id"], name=row["name"], description=row["description"],
                type=row["type"], content=row["content"],
                tags=json.loads(row["tags"]), links=json.loads(row["links"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
                access_count=row["access_count"], is_active=bool(row["is_active"]),
            )
            results.append(SearchResult(
                chunk=chunk, score=score,
                match_reasons=[f"Vector sim={score:.3f}"]
            ))
        db.close()
        return results[:top_k]
    else:
        return search_fts5(query, top_k=top_k, mem_type=mem_type)


def get_context_for_query(query: str, max_chars: int = 3000) -> str:
    """获取与查询相关的上下文片段。"""
    results = search(query, top_k=5, mode="hybrid")
    if not results:
        results = search(query, top_k=5, mode="fts5")

    if not results:
        return ""

    context_parts = []
    total_chars = 0
    for result in results:
        content = result.chunk.content
        if total_chars + len(content) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 100:
                context_parts.append(content[:remaining] + "...")
            break
        context_parts.append(f"[{result.chunk.name}] {content}")
        total_chars += len(content)

    return "\n---\n".join(context_parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 导入
# ═══════════════════════════════════════════════════════════════════════════════

def import_from_markdown():
    """从 memory/ 目录导入所有 Markdown 文件。"""
    init_db()
    migrate_v2()
    db = get_db()
    engine = get_vector_engine()
    count = 0
    skipped = 0
    vector_batch: List[Tuple[str, str]] = []  # (id, text)

    for md_file in sorted(MEMORY_DIR.rglob("*.md")):
        if md_file.name == "MEMORY.md":
            skipped += 1
            continue

        rel_path = md_file.relative_to(MEMORY_DIR)
        file_id = str(rel_path).replace("/", "_").replace(".md", "")

        content = md_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(content)

        name = meta.get("name", md_file.stem)
        description = meta.get("description", "")
        mem_type = meta.get("type", "reference")
        if isinstance(mem_type, dict):
            mem_type = mem_type.get("type", "reference")
        if mem_type == "reference" and isinstance(meta.get("metadata"), dict):
            mem_type = meta["metadata"].get("type", "reference")

        links = extract_links(content)
        tags = []
        if "domains" in meta:
            domains = meta["domains"]
            if isinstance(domains, str):
                tags = [d.strip() for d in domains.split(",")]
            elif isinstance(domains, list):
                tags = domains

        mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)
        ctime = datetime.fromtimestamp(md_file.stat().st_ctime, tz=timezone.utc)
        is_active = "_archive" not in str(rel_path)

        db.execute("""
            INSERT OR REPLACE INTO memories
            (id, name, description, type, content, tags, links, created_at, updated_at, access_count, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (
            file_id, name, description, mem_type, body,
            json.dumps(tags, ensure_ascii=False),
            json.dumps(links, ensure_ascii=False),
            ctime.isoformat(), mtime.isoformat(),
            1 if is_active else 0,
        ))

        # 准备向量
        vector_text = f"{name} {description} {body[:2000]}"
        vector_batch.append((file_id, vector_text))
        count += 1

    # 批量生成向量
    if vector_batch:
        print(f"[Vectors] Encoding {len(vector_batch)} memories with {engine.backend}...")
        for i, (mem_id, text) in enumerate(vector_batch):
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(vector_batch)}...")
            vec_bytes = engine.encode(text)
            db.execute(
                """INSERT OR REPLACE INTO memory_vectors (memory_id, vector, model)
                   VALUES (?, ?, ?)""",
                (mem_id, vec_bytes, engine.backend)
            )

    db.commit()
    db.close()
    print(f"Imported {count} memories (with vectors), skipped {skipped} (MEMORY.md)")


# ═══════════════════════════════════════════════════════════════════════════════
# 图谱关系
# ═══════════════════════════════════════════════════════════════════════════════

RELATION_TYPES = ["related_to", "supersedes", "contradicts", "supports", "depends_on"]


def add_relation(source_id: str, target_id: str, relation_type: str = "related_to",
                 weight: float = 1.0, reason: str = ""):
    """手动添加关系。"""
    if relation_type not in RELATION_TYPES:
        print(f"Invalid relation type: {relation_type}. Valid: {RELATION_TYPES}")
        return

    db = get_db()
    try:
        db.execute("""
            INSERT OR REPLACE INTO relations (source_id, target_id, relation_type, weight, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (source_id, target_id, relation_type, weight, reason))
        db.commit()
        print(f"Relation added: {source_id} --[{relation_type}]--> {target_id}")
    except sqlite3.IntegrityError as e:
        print(f"Error: {e}")
    finally:
        db.close()


def _tokenize(text: str) -> set:
    """将文本分词为小写词集合（用于关键词重叠检测）。"""
    words = re.findall(r'\b\w{3,}\b', text.lower())
    # 过滤常见停用词
    stopwords = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
        'has', 'was', 'that', 'this', 'with', 'from', 'have', 'been',
        'when', 'will', 'they', 'what', 'were', 'your', 'more', 'some',
        'than', 'about', 'also', 'each', 'into', 'most', 'only', 'other',
        'over', 'such', 'than', 'then', 'very', 'which', 'just', 'like',
    }
    return {w for w in words if w not in stopwords}


def auto_detect_relations(threshold: float = 0.15, top_k: int = 5):
    """自动检测记忆间的关键词重叠并建议关系。

    对每对记忆计算 Jaccard 相似度，超过 threshold 的建议添加关系。
    """
    db = get_db()
    rows = db.execute(
        "SELECT id, name, description, content FROM memories WHERE is_active = 1"
    ).fetchall()

    if len(rows) < 2:
        print("Need at least 2 active memories for relation detection.")
        db.close()
        return []

    # 预计算所有 token 集合
    docs = {}
    for row in rows:
        text = f"{row['name']} {row['description']} {row['content'][:1000]}"
        docs[row["id"]] = _tokenize(text)

    # 计算两两 Jaccard 相似度
    suggestions = []
    ids = list(docs.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            id_a, id_b = ids[i], ids[j]
            tokens_a, tokens_b = docs[id_a], docs[id_b]
            if not tokens_a or not tokens_b:
                continue

            intersection = tokens_a & tokens_b
            union = tokens_a | tokens_b
            jaccard = len(intersection) / len(union) if union else 0

            if jaccard >= threshold:
                # 检查是否已有关系
                existing = db.execute(
                    "SELECT id FROM relations WHERE source_id = ? AND target_id = ?",
                    (id_a, id_b)
                ).fetchone()
                if existing:
                    continue

                # 确定关系类型
                shared = sorted(intersection)[:10]
                if jaccard > 0.4:
                    rel_type = "supports"
                elif jaccard > 0.25:
                    rel_type = "related_to"
                else:
                    rel_type = "related_to"

                db.execute("""
                    INSERT OR IGNORE INTO relations (source_id, target_id, relation_type, weight, reason)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_a, id_b, rel_type, round(jaccard, 3),
                      f"Jaccard={jaccard:.3f}, shared: {', '.join(shared)}"))

                suggestions.append(Relation(
                    source_id=id_a, target_id=id_b,
                    relation_type=rel_type, weight=round(jaccard, 3),
                    reason=f"Jaccard={jaccard:.3f}, shared: {', '.join(shared)}"
                ))

    db.commit()
    total = len(suggestions)

    # 按权重排序，取每个源节点的 top_k
    by_source = defaultdict(list)
    for s in suggestions:
        by_source[s.source_id].append(s)

    filtered = []
    for src_id, rels in by_source.items():
        rels.sort(key=lambda r: r.weight, reverse=True)
        filtered.extend(rels[:top_k])

    db.close()
    print(f"Detected {total} potential relations ({len(filtered)} after top-{top_k} filter).")
    return filtered


def graph_query(node_id: str, depth: int = 1) -> Dict:
    """查询节点的关系图谱。

    返回以 node_id 为中心 depth 跳范围内的关系图。
    """
    db = get_db()

    # 验证节点存在
    node = db.execute("SELECT id, name, type FROM memories WHERE id = ?", (node_id,)).fetchone()
    if not node:
        db.close()
        return {"error": f"Node not found: {node_id}", "nodes": [], "edges": []}

    visited: set = {node_id}
    current_layer = {node_id}
    edges = []
    nodes = {node_id: {"id": node["id"], "name": node["name"], "type": node["type"]}}

    for d in range(depth):
        if not current_layer:
            break
        next_layer = set()
        for nid in current_layer:
            # 双向查询关系
            rels = db.execute("""
                SELECT source_id, target_id, relation_type, weight, reason
                FROM relations
                WHERE source_id = ? OR target_id = ?
            """, (nid, nid)).fetchall()

            for rel in rels:
                # 确定另一端
                other = rel["target_id"] if rel["source_id"] == nid else rel["source_id"]
                edges.append({
                    "source": rel["source_id"],
                    "target": rel["target_id"],
                    "type": rel["relation_type"],
                    "weight": rel["weight"],
                    "reason": rel["reason"],
                })
                if other not in visited:
                    visited.add(other)
                    next_layer.add(other)
                    # 获取节点信息
                    onode = db.execute(
                        "SELECT id, name, type FROM memories WHERE id = ?", (other,)
                    ).fetchone()
                    if onode:
                        nodes[other] = {
                            "id": onode["id"], "name": onode["name"], "type": onode["type"]
                        }

        current_layer = next_layer

    db.close()
    return {
        "center": node_id,
        "depth": depth,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def graph_print(node_id: str, depth: int = 1):
    """格式化打印关系图谱。"""
    data = graph_query(node_id, depth)
    if "error" in data:
        print(f"Error: {data['error']}")
        return

    print(f"\n{'='*60}")
    print(f"Graph: {node_id} (depth={depth})")
    print(f"{'='*60}")
    print(f"Nodes: {len(data['nodes'])} | Edges: {len(data['edges'])}")
    print()

    # 节点列表
    for n in data["nodes"]:
        marker = "★" if n["id"] == node_id else " ·"
        print(f"  {marker} [{n['type']}] {n['name']} ({n['id']})")

    # 边
    if data["edges"]:
        print(f"\nRelations ({len(data['edges'])}):")
        for e in data["edges"]:
            arrow = f"--[{e['type']}]-->"
            print(f"  {e['source']} {arrow} {e['target']} (w={e['weight']})")
            if e.get("reason"):
                print(f"    reason: {e['reason']}")

    print()


def relations_list(node_id: str = ""):
    """列出关系。"""
    db = get_db()

    if node_id:
        rels = db.execute("""
            SELECT r.*, s.name as source_name, t.name as target_name
            FROM relations r
            JOIN memories s ON r.source_id = s.id
            JOIN memories t ON r.target_id = t.id
            WHERE r.source_id = ? OR r.target_id = ?
            ORDER BY r.weight DESC
        """, (node_id, node_id)).fetchall()
    else:
        rels = db.execute("""
            SELECT r.*, s.name as source_name, t.name as target_name
            FROM relations r
            JOIN memories s ON r.source_id = s.id
            JOIN memories t ON r.target_id = t.id
            ORDER BY r.weight DESC
            LIMIT 50
        """).fetchall()

    if not rels:
        print("No relations found.")
        db.close()
        return

    print(f"\nRelations ({len(rels)}):")
    print(f"{'─'*70}")
    for r in rels:
        print(f"  {r['source_name']} --[{r['relation_type']}]--> {r['target_name']}")
        print(f"    ids: {r['source_id']} → {r['target_id']}  w={r['weight']:.3f}")
        if r["reason"]:
            print(f"    {r['reason']}")
    print()

    db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════════════════════════

def stats():
    """统计信息。"""
    init_db()
    migrate_v2()
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM memories WHERE is_active = 1").fetchone()[0]
    archived = total - active

    by_type = {}
    for row in db.execute("SELECT type, COUNT(*) as c FROM memories GROUP BY type").fetchall():
        by_type[row["type"]] = row["c"]

    total_chars = db.execute("SELECT SUM(LENGTH(content)) FROM memories").fetchone()[0] or 0
    total_links = 0
    for row in db.execute("SELECT links FROM memories").fetchall():
        total_links += len(json.loads(row["links"]))

    # v2.0 新统计
    vec_count = db.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
    vec_model = db.execute(
        "SELECT model, COUNT(*) as c FROM memory_vectors GROUP BY model"
    ).fetchone()
    vec_model_str = f"{vec_model['model']} ({vec_model['c']})" if vec_model else "none"

    rel_count = db.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    rel_by_type = {}
    for row in db.execute(
        "SELECT relation_type, COUNT(*) as c FROM relations GROUP BY relation_type"
    ).fetchall():
        rel_by_type[row["relation_type"]] = row["c"]

    engine = get_vector_engine()

    db.close()

    print(f"=== 鲤鱼 Knowledge Base v2.0 ===")
    print(f"Memories:     {total} total ({active} active, {archived} archived)")
    print(f"By type:      {by_type}")
    print(f"Total chars:  {total_chars:,}")
    print(f"Total links:  {total_links}")
    print(f"")
    print(f"Vectors:      {vec_count} stored ({vec_model_str})")
    print(f"Vector engine: {engine.backend} ({engine.dim}d)")
    print(f"Relations:    {rel_count} total")
    if rel_by_type:
        print(f"By type:      {rel_by_type}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def print_help():
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        print_help()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    # 解析 --hybrid / --vector 标志
    search_mode = "fts5"
    filter_args = []
    for a in args:
        if a == "--hybrid":
            search_mode = "hybrid"
        elif a == "--vector":
            search_mode = "vector"
        else:
            filter_args.append(a)

    # ── import ──────────────────────────────────────────────────────────
    if cmd == "import":
        import_from_markdown()

    # ── search ──────────────────────────────────────────────────────────
    elif cmd == "search":
        query = " ".join(filter_args) if filter_args else ""
        if not query:
            print("Usage: knowledge-base.py search <query> [--hybrid] [--vector]")
            return
        results = search(query, top_k=10, mode=search_mode)
        if not results:
            print("No results found.")
            return
        mode_label = {"fts5": "FTS5+SimHash", "hybrid": "HYBRID (RRF)", "vector": "VECTOR"}
        print(f"\nSearch: \"{query}\" [{mode_label.get(search_mode, search_mode)}]")
        print(f"{'─'*60}")
        for i, r in enumerate(results, 1):
            print(f"  #{i} [{r.score:.3f}] {r.chunk.name}")
            print(f"      {r.chunk.description[:80]}")
            print(f"      type={r.chunk.type} | reasons: {', '.join(r.match_reasons)}")
            print()

    # ── rebuild-vectors ─────────────────────────────────────────────────
    elif cmd == "rebuild-vectors":
        rebuild_vectors()

    # ── graph ───────────────────────────────────────────────────────────
    elif cmd == "graph":
        if not filter_args:
            print("Usage: knowledge-base.py graph <node_id> [--depth N]")
            return
        node_id = filter_args[0]
        depth = 1
        for i, a in enumerate(filter_args):
            if a == "--depth" and i + 1 < len(filter_args):
                depth = int(filter_args[i + 1])
        graph_print(node_id, depth)

    # ── relations ───────────────────────────────────────────────────────
    elif cmd == "relations":
        auto_mode = "--auto" in filter_args
        node_id = ""
        for a in filter_args:
            if a != "--auto" and not a.startswith("-"):
                node_id = a
                break

        if auto_mode:
            suggestions = auto_detect_relations()
            if suggestions:
                print(f"\nSuggested relations ({len(suggestions)}):")
                for s in suggestions:
                    print(f"  {s.source_id} --[{s.relation_type}]--> {s.target_id}")
                    print(f"    w={s.weight:.3f}  {s.reason}")
            else:
                print("No new relations suggested.")
        else:
            relations_list(node_id)

    # ── stats ───────────────────────────────────────────────────────────
    elif cmd == "stats":
        stats()

    # ── context ─────────────────────────────────────────────────────────
    elif cmd == "context":
        query = " ".join(filter_args) if filter_args else ""
        if not query:
            print("Usage: knowledge-base.py context <query>")
            return
        context = get_context_for_query(query)
        if context:
            print(context)
        else:
            print("No relevant context found.")

    # ── sync ────────────────────────────────────────────────────────────
    elif cmd == "sync":
        import_from_markdown()
        print("Sync complete.")

    # ── help ────────────────────────────────────────────────────────────
    elif cmd in ("help", "--help", "-h"):
        print_help()

    else:
        print(f"Unknown command: {cmd}")
        print_help()


if __name__ == "__main__":
    main()
