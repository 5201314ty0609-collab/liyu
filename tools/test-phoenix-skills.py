#!/usr/bin/env python3
"""
鲤鱼 Skills — 测试套件。
"""

from pathlib import Path
import json
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))


def test_code_tidy():
    """测试 code-tidy skill"""
    print("Testing code-tidy...")

    # 创建临时 Python 文件
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write("""import os
import sys

def hello():
    print("Hello, world!")

# def old_function():
#     pass
""")
        tmp_path = f.name

    try:
        # 导入模块
        import importlib.util
        spec = importlib.util.spec_from_file_location("code-tidy", Path(__file__).parent / "skills" / "code-tidy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 测试 dry-run
        result = module.tidy_file(Path(tmp_path), dry_run=True)

        # 调试输出
        if "error" in result:
            print(f"  Error: {result['error']}")

        assert result["unused_imports"] > 0, "Should find unused imports"
        assert result["commented_code"] > 0, "Should find commented code"
        print(f"  ✓ Found {result['unused_imports']} unused imports, {result['commented_code']} commented code")

    finally:
        os.unlink(tmp_path)


def test_verify_completion():
    """测试 verify-completion skill"""
    print("Testing verify-completion...")

    # 创建临时 Python 文件
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write("""def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
""")
        tmp_path = f.name

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("verify", Path(__file__).parent / "skills" / "verify-completion.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.verify_file(Path(tmp_path))

        assert result["valid"], f"File should be valid, got errors: {result['syntax_errors']}"
        print(f"  ✓ File verified successfully")

    finally:
        os.unlink(tmp_path)


def test_systematic_debug():
    """测试 systematic-debug skill"""
    print("Testing systematic-debug...")

    import importlib.util
    spec = importlib.util.spec_from_file_location("debug", Path(__file__).parent / "skills" / "systematic-debug.py")
    module = importlib.util.module_from_spec(spec)

    # 临时设置路径
    tmp_dir = tempfile.mkdtemp()
    module.DEBUG_SESSIONS_FILE = Path(tmp_dir) / "debug-sessions.jsonl"

    try:
        spec.loader.exec_module(module)

        # 测试完整流程
        session_id = module.start_session("Test problem")
        assert session_id.startswith("debug-")

        module.add_observation(session_id, "First observation")
        module.add_observation(session_id, "Second observation")
        module.add_hypothesis(session_id, "Test hypothesis")
        module.verify_hypothesis(session_id, "Test verification", "Pass")
        module.resolve(session_id, "Test solution")

        # 验证会话已保存
        assert module.DEBUG_SESSIONS_FILE.exists()
        print(f"  ✓ Debug session flow works")

    finally:
        import shutil
        shutil.rmtree(tmp_dir)


def test_dispatch_parallel():
    """测试 dispatch-parallel skill"""
    print("Testing dispatch-parallel...")

    import importlib.util
    spec = importlib.util.spec_from_file_location("dispatch", Path(__file__).parent / "skills" / "dispatch-parallel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 测试分析
    analysis = module.analyze_task("同时处理文件 A 和文件 B")
    assert analysis["can_parallel"], "Should detect parallelizable task"
    print(f"  ✓ Parallel analysis works")

    # 测试计划创建
    plan = module.create_plan(["Task A", "Task B", "Task C"])
    assert len(plan.tasks) == 3
    assert len(plan.groups) == 1
    print(f"  ✓ Plan creation works")


def test_knowledge_sync():
    """测试 knowledge-sync skill"""
    print("Testing knowledge-sync...")

    import importlib.util
    spec = importlib.util.spec_from_file_location("knowledge-sync", Path(__file__).parent / "skills" / "knowledge-sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 测试获取文件列表
    files = module.get_memory_files()
    assert len(files) > 0, "Should find memory files"
    print(f"  ✓ Found {len(files)} memory files")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("鲤鱼 Skills — Test Suite")
    print("=" * 60)
    print()

    tests = [
        test_code_tidy,
        test_verify_completion,
        test_systematic_debug,
        test_dispatch_parallel,
        test_knowledge_sync,
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
