#!/usr/bin/env python3
"""
鲤鱼 Knowledge Base — 测试套件。
"""

from pathlib import Path
import json
import os
import sys
import tempfile

# 添加 liyu 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

import importlib
kb = importlib.import_module("knowledge-base")


def setup_test_db():
    """创建临时测试数据库"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    kb.DB_PATH = Path(tmp.name)
    kb.init_db()
    return tmp.name


def cleanup_test_db(path):
    """清理测试数据库"""
    os.unlink(path)
    kb.DB_PATH = kb.鲤鱼_HOME / "knowledge-base.db"


def test_parse_frontmatter():
    """测试 frontmatter 解析"""
    print("Testing parse_frontmatter...")

    content = """---
name: test-memory
description: A test memory
type: user
domains: liyu, testing
---

This is the body content with [[other-link]] reference."""

    meta, body = kb.parse_frontmatter(content)

    assert meta["name"] == "test-memory", f"Expected 'test-memory', got '{meta['name']}'"
    assert meta["description"] == "A test memory"
    assert meta["type"] == "user"
    assert "This is the body" in body
    print("  ✓ parse_frontmatter works")


def test_extract_links():
    """测试链接提取"""
    print("Testing extract_links...")

    content = "See [[liyu-genesis]] and [[user-profile]] for details."
    links = kb.extract_links(content)

    assert "liyu-genesis" in links
    assert "user-profile" in links
    assert len(links) == 2
    print("  ✓ extract_links works")


def test_semantic_hash():
    """测试语义哈希索引"""
    print("Testing SemanticHashIndex...")

    index = kb.SemanticHashIndex(hash_bits=64)

    index.add("doc1", "鲤鱼 is a self-evolving agent system")
    index.add("doc2", "MUNDO Agent uses three-source fusion")
    index.add("doc3", "The quick brown fox jumps over the lazy dog")

    # 相似查询应该找到 doc1
    results = index.search("鲤鱼 agent evolution", top_k=2)
    assert len(results) > 0
    assert results[0][0] == "doc1", f"Expected doc1, got {results[0][0]}"

    # 不相关查询应该低分
    results = index.search("cooking recipe pasta", top_k=3)
    assert results[0][1] < 0.5  # 低相似度

    print("  ✓ SemanticHashIndex works")


def test_insert_and_search():
    """测试插入和搜索"""
    print("Testing insert and search...")

    db_path = setup_test_db()
    try:
        db = kb.get_db()

        # 插入测试数据
        db.execute("""
            INSERT INTO memories (id, name, description, type, content, tags, links, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "test-1",
            "鲤鱼 Genesis",
            "The birth record of 鲤鱼 system",
            "reference",
            "鲤鱼 was created by merging ECC, MUNDO, Metacog, Claude Soul, and Taste-Skill.",
            json.dumps(["liyu", "genesis"]),
            json.dumps(["ecc", "mundo"]),
            "2026-06-05T00:00:00Z",
            "2026-06-05T00:00:00Z",
        ))

        db.execute("""
            INSERT INTO memories (id, name, description, type, content, tags, links, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "test-2",
            "User Profile",
            "李时宇的个人档案",
            "user",
            "李时宇 (HolyTy)，鲤鱼 的创造者，复合型创作者。",
            json.dumps(["user", "profile"]),
            json.dumps([]),
            "2026-06-05T00:00:00Z",
            "2026-06-05T00:00:00Z",
        ))

        db.commit()
        db.close()

        # 测试搜索
        kb._semantic_index = None  # 重置语义索引
        results = kb.search("鲤鱼", top_k=5)

        assert len(results) > 0, "Should find at least one result"
        assert results[0].chunk.name in ["鲤鱼 Genesis", "User Profile"]
        print(f"  ✓ Found {len(results)} results for '鲤鱼'")

        # 测试类型过滤
        results = kb.search("鲤鱼", mem_type="user")
        for r in results:
            assert r.chunk.type == "user"
        print("  ✓ Type filter works")

        # 测试上下文
        context = kb.get_context_for_query("鲤鱼 agent")
        assert len(context) > 0
        print(f"  ✓ Context generation works ({len(context)} chars)")

    finally:
        cleanup_test_db(db_path)


def test_fts5_search():
    """测试 FTS5 全文搜索"""
    print("Testing FTS5 search...")

    db_path = setup_test_db()
    try:
        db = kb.get_db()

        db.execute("""
            INSERT INTO memories (id, name, description, type, content, tags, links, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "fts-test",
            "MUNDO Agent Analysis",
            "Deep analysis of MUNDO v2.0.9",
            "reference",
            "MUNDO Agent uses a three-source fusion architecture combining Claude Code, Codex, and Hermes.",
            json.dumps(["mundo", "analysis"]),
            json.dumps([]),
            "2026-06-12T00:00:00Z",
            "2026-06-12T00:00:00Z",
        ))

        db.commit()
        db.close()

        kb._semantic_index = None
        results = kb.search("three-source fusion", top_k=5)

        assert len(results) > 0
        assert "MUNDO" in results[0].chunk.name or "fusion" in results[0].chunk.content.lower()
        print(f"  ✓ FTS5 found {len(results)} results for 'three-source fusion'")

    finally:
        cleanup_test_db(db_path)


def test_stats():
    """测试统计功能"""
    print("Testing stats...")

    db_path = setup_test_db()
    try:
        db = kb.get_db()

        # 插入几条记录
        for i in range(3):
            db.execute("""
                INSERT INTO memories (id, name, description, type, content, tags, links, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"stat-{i}",
                f"Test Memory {i}",
                f"Description {i}",
                "reference",
                f"Content for test memory {i}",
                json.dumps([]),
                json.dumps([]),
                "2026-06-14T00:00:00Z",
                "2026-06-14T00:00:00Z",
            ))

        db.commit()
        db.close()

        # 捕获 stdout
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        kb.stats()

        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

        assert "Total memories: 3" in output
        print("  ✓ stats works")

    finally:
        cleanup_test_db(db_path)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("鲤鱼 Knowledge Base — Test Suite")
    print("=" * 60)
    print()

    tests = [
        test_parse_frontmatter,
        test_extract_links,
        test_semantic_hash,
        test_insert_and_search,
        test_fts5_search,
        test_stats,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__} FAILED: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
