#!/usr/bin/env python3
"""
鲤鱼 Reflection Engine — 测试套件。
"""

from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

import importlib
re = importlib.import_module("reflection-engine")


def setup_test_env():
    """创建临时测试环境"""
    tmp_dir = tempfile.mkdtemp()
    re.鲤鱼_HOME = Path(tmp_dir)
    re.REFLECTIONS_FILE = Path(tmp_dir) / "reflections.jsonl"
    re.ACTIVE_TASKS_FILE = Path(tmp_dir) / "active-tasks.json"
    return tmp_dir


def cleanup_test_env(tmp_dir):
    """清理测试环境"""
    import shutil
    shutil.rmtree(tmp_dir)
    re.鲤鱼_HOME = Path.home() / ".claude/liyu"
    re.REFLECTIONS_FILE = re.鲤鱼_HOME / "reflections.jsonl"
    re.ACTIVE_TASKS_FILE = re.鲤鱼_HOME / "active-tasks.json"


def test_start_task():
    """测试开始任务"""
    print("Testing start_task...")

    tmp_dir = setup_test_env()
    try:
        task_id = re.start_task("Test task for 鲤鱼")

        assert task_id.startswith("task-"), f"Invalid task_id: {task_id}"

        tasks = re.load_active_tasks()
        assert task_id in tasks
        assert tasks[task_id].task_name == "Test task for 鲤鱼"
        assert tasks[task_id].status == "active"

        print(f"  ✓ start_task works (id: {task_id})")

    finally:
        cleanup_test_env(tmp_dir)


def test_checkpoint():
    """测试检查点"""
    print("Testing checkpoint...")

    tmp_dir = setup_test_env()
    try:
        task_id = re.start_task("Checkpoint test")

        re.add_checkpoint(task_id, "First checkpoint")
        re.add_checkpoint(task_id, "Second checkpoint", {"progress": 50})

        tasks = re.load_active_tasks()
        task = tasks[task_id]

        assert len(task.checkpoints) == 2
        assert task.checkpoints[0]["note"] == "First checkpoint"
        assert task.checkpoints[1]["metrics"]["progress"] == 50

        print("  ✓ checkpoint works")

    finally:
        cleanup_test_env(tmp_dir)


def test_finish_task():
    """测试完成任务"""
    print("Testing finish_task...")

    tmp_dir = setup_test_env()
    try:
        task_id = re.start_task("Finish test")
        re.add_checkpoint(task_id, "Did something")

        reflection = re.finish_task(
            task_id,
            status="success",
            result_summary="All tests passed",
            lessons=["Always write tests first"],
            next_actions=["Deploy to production"],
        )

        # 任务应该从活跃列表中移除
        tasks = re.load_active_tasks()
        assert task_id not in tasks

        # 反思记录应该存在
        assert re.REFLECTIONS_FILE.exists()
        lines = re.REFLECTIONS_FILE.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["task_id"] == task_id
        assert record["status"] == "success"
        assert record["result_summary"] == "All tests passed"
        assert "Always write tests first" in record["lessons"]

        # 反思文本应该包含关键内容
        assert "What was done" in reflection
        assert "How it went" in reflection
        assert "Lessons learned" in reflection
        assert "Next actions" in reflection

        print("  ✓ finish_task works")

    finally:
        cleanup_test_env(tmp_dir)


def test_list_tasks():
    """测试列出任务"""
    print("Testing list_tasks...")

    tmp_dir = setup_test_env()
    try:
        # 创建几个任务
        for i in range(3):
            task_id = re.start_task(f"Task {i}")
            re.finish_task(task_id, "success", f"Result {i}")

        # 捕获 stdout
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        re.list_tasks(5)

        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

        assert "Task 0" in output
        assert "Task 1" in output
        assert "Task 2" in output

        print("  ✓ list_tasks works")

    finally:
        cleanup_test_env(tmp_dir)


def test_reflect_on_task():
    """测试手动反思"""
    print("Testing reflect_on_task...")

    tmp_dir = setup_test_env()
    try:
        task_id = re.start_task("Reflect test")
        re.finish_task(task_id, "partial", "Half done", ["Need more time"])

        # 捕获 stdout
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()

        re.reflect_on_task(task_id)

        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

        assert "What was done" in output
        assert "Half done" in output
        assert "Need more time" in output

        print("  ✓ reflect_on_task works")

    finally:
        cleanup_test_env(tmp_dir)


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("鲤鱼 Reflection Engine — Test Suite")
    print("=" * 60)
    print()

    tests = [
        test_start_task,
        test_checkpoint,
        test_finish_task,
        test_list_tasks,
        test_reflect_on_task,
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
