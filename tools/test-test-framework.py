#!/usr/bin/env python3
"""
鲤鱼 统一测试框架 — 自测套件。

验证：
- 所有断言函数正确工作
- PhoenixTestCase 基类生命周期
- 跳过机制 (skip)
- 错误捕获 (assert_raises)
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Load the framework module (hyphenated filename requires util)
_spec = importlib.util.spec_from_file_location(
    "liyu-test-framework",
    str(Path(__file__).parent / "liyu-test-framework.py"),
)
_fw = importlib.util.module_from_spec(_spec)
sys.modules["liyu-test-framework"] = _fw
_spec.loader.exec_module(_fw)

assert_equals = _fw.assert_equals
assert_not_equals = _fw.assert_not_equals
assert_true = _fw.assert_true
assert_false = _fw.assert_false
assert_none = _fw.assert_none
assert_not_none = _fw.assert_not_none
assert_in = _fw.assert_in
assert_not_in = _fw.assert_not_in
assert_raises = _fw.assert_raises
assert_contains = _fw.assert_contains
assert_starts_with = _fw.assert_starts_with
assert_ends_with = _fw.assert_ends_with
assert_length = _fw.assert_length
assert_greater = _fw.assert_greater
assert_less = _fw.assert_less
skip = _fw.skip
PhoenixTestCase = _fw.PhoenixTestCase
AssertionError = _fw.AssertionError  # Note: framework uses AssertionError (no 's')


# ============================================================
# Assertion function tests
# ============================================================

def test_assert_equals_pass():
    assert_equals(1, 1)
    assert_equals("a", "a")
    assert_equals([], [])


def test_assert_equals_fail():
    try:
        assert_equals(1, 2)
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_not_equals():
    assert_not_equals(1, 2)
    try:
        assert_not_equals(1, 1)
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_true_false():
    assert_true(1)
    assert_true("x")
    assert_false(0)
    assert_false("")
    assert_false(None)


def test_assert_none_not_none():
    assert_none(None)
    assert_not_none(0)
    assert_not_none("")
    try:
        assert_none(42)
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_in_not_in():
    assert_in(1, [1, 2, 3])
    assert_not_in(4, [1, 2, 3])
    assert_in("a", "abc")
    try:
        assert_in(4, [1, 2, 3])
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_raises():
    e = assert_raises(ValueError, int, "not_a_number")
    assert isinstance(e, ValueError)
    # Should fail when no exception
    try:
        assert_raises(ValueError, int, "42")
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_contains():
    assert_contains("hello world", "world")
    try:
        assert_contains("hello", "xyz")
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_starts_ends_with():
    assert_starts_with("hello world", "hello")
    assert_ends_with("hello world", "world")


def test_assert_length():
    assert_length([1, 2, 3], 3)
    assert_length("abc", 3)
    try:
        assert_length([1], 2)
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_assert_greater_less():
    assert_greater(2, 1)
    assert_less(1, 2)
    try:
        assert_greater(1, 2)
        raise RuntimeError("Should have raised")
    except AssertionError:
        pass


def test_custom_message():
    try:
        assert_equals(1, 2, "custom msg")
        raise RuntimeError("Should have raised")
    except AssertionError as e:
        assert "custom msg" in str(e)


def test_skip_signal():
    try:
        skip("not ready")
        raise RuntimeError("Should have raised")
    except Exception as e:
        assert "not ready" in str(e)


# ============================================================
# PhoenixTestCase lifecycle test
# ============================================================

class TestLifecycle(PhoenixTestCase):
    """验证 setUp/tearDown/make_temp_dir 生命周期。"""

    def test_temp_dir_created(self):
        d = self.make_temp_dir()
        assert d.exists(), f"Temp dir {d} should exist"
        assert d.is_dir()

    def test_temp_file_created(self):
        f = self.make_temp_file(suffix=".txt", content="hello")
        assert f.exists()
        assert f.read_text() == "hello"

    def test_make_temp_dir_clean(self):
        """每个测试都会创建新的临时目录。"""
        d = self.make_temp_dir()
        assert d.exists()


# ============================================================
# Module-level run (for standalone execution)
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  鲤鱼 Test Framework — Self-Test Suite")
    print("=" * 60)

    passed = 0
    failed = 0
    errors = 0

    # Run standalone functions
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  PASS {name}")
                passed += 1
            except AssertionError as e:
                print(f"  FAIL {name}: {e}")
                failed += 1
            except Exception as e:
                print(f"  ERR  {name}: {type(e).__name__}: {e}")
                errors += 1

    # Run class-based tests
    for cls in [TestLifecycle]:
        instance = cls()
        for name in sorted(dir(instance)):
            if name.startswith("test_") and callable(getattr(instance, name)):
                try:
                    instance.setUp()
                    getattr(instance, name)()
                    instance.tearDown()
                    instance.cleanup_temps()
                    print(f"  PASS {cls.__name__}.{name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL {cls.__name__}.{name}: {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERR  {cls.__name__}.{name}: {type(e).__name__}: {e}")
                    errors += 1

    print()
    total = passed + failed + errors
    print(f"  Tests: {total}  |  {passed} passed  {failed} failed  {errors} errors")
    if failed == 0 and errors == 0:
        print("\n  ALL TESTS PASSED")
    else:
        print("\n  SOME TESTS FAILED")
    sys.exit(0 if failed == 0 and errors == 0 else 1)
