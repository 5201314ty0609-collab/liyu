#!/usr/bin/env python3
"""
鲤鱼 统一测试框架 (Unified Test Framework)

解决的问题：
- 各系统测试方法不一致
- 无统一发现、注册、运行、报告机制
- 无统一 setup/teardown 和断言工具

功能：
1. 统一测试基类 (PhoenixTestCase) — 提供 setup/teardown、断言、临时环境
2. 测试注册机制 — 装饰器 @liyu_test + 自动发现 test-*.py
3. 测试运行器 — 支持全量运行、按模块过滤、并行执行
4. 测试报告生成 — JSON + 终端彩色输出 + 覆盖率摘要

用法：
    # 运行所有测试
    python3 liyu-test-framework.py run

    # 运行指定模块
    python3 liyu-test-framework.py run --filter knowledge-base

    # 只列出测试，不运行
    python3 liyu-test-framework.py list

    # 生成 JSON 报告
    python3 liyu-test-framework.py run --report json --output report.json

    # 在代码中使用
    from liyu_test_framework import liyu_test, PhoenixTestCase, assert_equals
"""

from __future__ import annotations

import dataclasses
import enum
import ast
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

# ============================================================
# Constants
# ============================================================

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
TEST_FILE_PATTERN = "test-*.py"
TEST_FUNC_PREFIX = "test_"
MAX_FILE_LINES = 800

# ANSI colors for terminal output
class _C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    CYAN    = "\033[96m"
    DIM     = "\033[2m"

    @staticmethod
    def strip(text: str) -> str:
        return re.sub(r"\033\[[0-9;]*m", "", text)


# ============================================================
# Enums and Data Classes
# ============================================================

class TestStatus(enum.Enum):
    PASSED  = "passed"
    FAILED  = "failed"
    SKIPPED = "skipped"
    ERROR   = "error"


@dataclass(frozen=True)
class TestResult:
    """单个测试函数的运行结果。"""
    name: str
    module: str
    status: TestStatus
    duration_ms: float
    message: str = ""
    traceback_str: str = ""
    test_class: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "status": self.status.value,
            "duration_ms": round(self.duration_ms, 2),
            "message": self.message,
            "traceback": self.traceback_str or None,
        }


@dataclass
class ModuleResult:
    """单个测试模块的汇总结果。"""
    module_name: str
    file_path: str
    results: List[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIPPED)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.ERROR)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def total_duration_ms(self) -> float:
        return sum(r.duration_ms for r in self.results)


@dataclass
class SuiteResult:
    """整个测试套件的汇总结果。"""
    modules: List[ModuleResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def total_passed(self) -> int:
        return sum(m.passed for m in self.modules)

    @property
    def total_failed(self) -> int:
        return sum(m.failed for m in self.modules)

    @property
    def total_skipped(self) -> int:
        return sum(m.skipped for m in self.modules)

    @property
    def total_errors(self) -> int:
        return sum(m.errors for m in self.modules)

    @property
    def total_tests(self) -> int:
        return sum(m.total for m in self.modules)

    @property
    def total_duration_ms(self) -> float:
        return sum(m.total_duration_ms for m in self.modules)

    @property
    def success(self) -> bool:
        return self.total_failed == 0 and self.total_errors == 0


# ============================================================
# Assertion Helpers (standalone functions, no class required)
# ============================================================

class AssertionError(Exception):
    """自定义断言错误，用于区分测试断言失败和其他异常。"""
    pass


def assert_equals(actual: Any, expected: Any, msg: str = "") -> None:
    if actual != expected:
        detail = msg or f"Expected {expected!r}, got {actual!r}"
        raise AssertionError(detail)


def assert_not_equals(actual: Any, unexpected: Any, msg: str = "") -> None:
    if actual == unexpected:
        detail = msg or f"Expected value != {unexpected!r}"
        raise AssertionError(detail)


def assert_true(value: Any, msg: str = "") -> None:
    if not value:
        raise AssertionError(msg or f"Expected truthy value, got {value!r}")


def assert_false(value: Any, msg: str = "") -> None:
    if value:
        raise AssertionError(msg or f"Expected falsy value, got {value!r}")


def assert_none(value: Any, msg: str = "") -> None:
    if value is not None:
        raise AssertionError(msg or f"Expected None, got {value!r}")


def assert_not_none(value: Any, msg: str = "") -> None:
    if value is None:
        raise AssertionError(msg or "Expected non-None value")


def assert_in(item: Any, container: Any, msg: str = "") -> None:
    if item not in container:
        raise AssertionError(msg or f"{item!r} not found in {container!r}")


def assert_not_in(item: Any, container: Any, msg: str = "") -> None:
    if item in container:
        raise AssertionError(msg or f"{item!r} unexpectedly found in {container!r}")


def assert_raises(exc_type: Type[Exception], func: Callable, *args: Any, **kwargs: Any) -> Exception:
    """断言函数抛出指定类型的异常，返回该异常实例。"""
    try:
        func(*args, **kwargs)
    except exc_type as e:
        return e
    except Exception as e:
        raise AssertionError(
            f"Expected {exc_type.__name__}, got {type(e).__name__}: {e}"
        )
    raise AssertionError(f"Expected {exc_type.__name__} to be raised, but no exception occurred")


def assert_contains(text: str, substring: str, msg: str = "") -> None:
    if substring not in text:
        raise AssertionError(msg or f"{substring!r} not found in text")


def assert_starts_with(text: str, prefix: str, msg: str = "") -> None:
    if not text.startswith(prefix):
        raise AssertionError(msg or f"Text does not start with {prefix!r}")


def assert_ends_with(text: str, suffix: str, msg: str = "") -> None:
    if not text.endswith(suffix):
        raise AssertionError(msg or f"Text does not end with {suffix!r}")


def assert_length(collection: Any, expected_len: int, msg: str = "") -> None:
    actual_len = len(collection)
    if actual_len != expected_len:
        raise AssertionError(
            msg or f"Expected length {expected_len}, got {actual_len}"
        )


def assert_greater(a: Any, b: Any, msg: str = "") -> None:
    if not (a > b):
        raise AssertionError(msg or f"Expected {a!r} > {b!r}")


def assert_less(a: Any, b: Any, msg: str = "") -> None:
    if not (a < b):
        raise AssertionError(msg or f"Expected {a!r} < {b!r}")


def skip(reason: str = "") -> None:
    """在测试中调用以跳过当前测试。"""
    raise _SkipSignal(reason)


# Internal signal for skip
class _SkipSignal(Exception):
    pass


# ============================================================
# PhoenixTestCase — 基类，提供 setup/teardown 工具
# ============================================================

class PhoenixTestCase:
    """
    测试基类。子类可以覆盖 setUp / tearDown。
    提供临时目录、临时文件等实用方法。
    """

    def setUp(self) -> None:
        """每个测试前调用。子类可覆盖。"""
        pass

    def tearDown(self) -> None:
        """每个测试后调用。子类可覆盖。"""
        pass

    @classmethod
    def setUpClass(cls) -> None:
        """整个测试类开始前调用一次。"""
        pass

    @classmethod
    def tearDownClass(cls) -> None:
        """整个测试类结束后调用一次。"""
        pass

    # -- 临时环境工具 --

    _temp_dirs: List[str] = field(default_factory=list) if hasattr(None, '_field') else []

    def make_temp_dir(self, prefix: str = "liyu_test_") -> Path:
        """创建临时目录，tearDown 时自动清理。"""
        d = Path(tempfile.mkdtemp(prefix=prefix))
        if not hasattr(self, "_temp_dirs"):
            self._temp_dirs = []
        self._temp_dirs.append(str(d))
        return d

    def make_temp_file(self, suffix: str = ".tmp", content: str = "", prefix: str = "liyu_test_") -> Path:
        """创建临时文件，tearDown 时自动清理。"""
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, text=True)
        if content:
            with os.fdopen(fd, "w") as f:
                f.write(content)
        else:
            os.close(fd)
        if not hasattr(self, "_temp_files"):
            self._temp_files = []
        self._temp_files.append(path)
        return Path(path)

    def cleanup_temps(self) -> None:
        """清理所有临时资源。"""
        for d in getattr(self, "_temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        for f in getattr(self, "_temp_files", []):
            try:
                os.unlink(f)
            except OSError:
                pass
        if hasattr(self, "_temp_dirs"):
            self._temp_dirs.clear()
        if hasattr(self, "_temp_files"):
            self._temp_files.clear()


# ============================================================
# Test Registry — 装饰器 + 注册表
# ============================================================

@dataclass
class RegisteredTest:
    """注册表中的一个测试条目。"""
    func: Callable
    module_name: str
    module_path: str
    tags: Set[str] = field(default_factory=set)
    description: str = ""
    skip_reason: Optional[str] = None
    test_class: Optional[str] = None  # 所属 PhoenixTestCase 子类名


class TestRegistry:
    """
    全局测试注册表。
    通过 @liyu_test 装饰器注册，或通过自动发现扫描 test-*.py 文件。
    """

    def __init__(self) -> None:
        self._tests: Dict[str, RegisteredTest] = {}

    def register(
        self,
        func: Callable,
        module_name: str = "",
        module_path: str = "",
        tags: Optional[Set[str]] = None,
        description: str = "",
        skip_reason: Optional[str] = None,
        test_class: Optional[str] = None,
    ) -> None:
        key = f"{module_name}::{func.__name__}"
        self._tests[key] = RegisteredTest(
            func=func,
            module_name=module_name,
            module_path=module_path,
            tags=tags or set(),
            description=description or func.__doc__ or "",
            skip_reason=skip_reason,
            test_class=test_class,
        )

    def get_all(self) -> Dict[str, RegisteredTest]:
        return dict(self._tests)

    def get_by_module(self, module_name: str) -> Dict[str, RegisteredTest]:
        return {k: v for k, v in self._tests.items() if v.module_name == module_name}

    def get_by_tag(self, tag: str) -> Dict[str, RegisteredTest]:
        return {k: v for k, v in self._tests.items() if tag in v.tags}

    def filter(self, pattern: str) -> Dict[str, RegisteredTest]:
        """按名称或模块名模糊匹配。"""
        regex = re.compile(pattern, re.IGNORECASE)
        return {
            k: v for k, v in self._tests.items()
            if regex.search(k) or regex.search(v.module_name)
        }

    def clear(self) -> None:
        self._tests.clear()

    @property
    def count(self) -> int:
        return len(self._tests)

    def list_modules(self) -> List[str]:
        return sorted(set(t.module_name for t in self._tests.values()))


# Global registry instance
_registry = TestRegistry()


def liyu_test(
    tags: Optional[Set[str]] = None,
    description: str = "",
    skip: Optional[str] = None,
) -> Callable:
    """
    装饰器：将函数注册为 鲤鱼 测试。

    @liyu_test(tags={"unit", "fast"}, description="测试解析逻辑")
    def test_parse_json():
        ...
    """
    def decorator(func: Callable) -> Callable:
        # 延迟注册：在发现阶段补全 module_name / module_path
        func._liyu_test = True
        func._liyu_tags = tags or set()
        func._liyu_description = description
        func._liyu_skip = skip
        return func
    return decorator


# ============================================================
# Test Discovery — 自动发现 test-*.py 文件
# ============================================================

def _ast_discover(test_file: Path) -> Tuple[List[str], Dict[str, str], List[Tuple[str, List[str]]]]:
    """
    用 AST 解析测试文件，不执行模块代码。
    返回: (top_level_test_funcs, class_test_methods_map, docstrings)
    - top_level_funcs: 顶层 test_* 函数名列表
    - class_methods: [(class_name, [method_names])] — PhoenixTestCase 子类的 test_* 方法
    - docstrings: {func_name: docstring}
    """
    try:
        source = test_file.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(test_file))
    except SyntaxError:
        return [], {}, {}

    top_funcs: List[str] = []
    class_methods: List[Tuple[str, List[str]]] = []
    docstrings: Dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(TEST_FUNC_PREFIX):
            top_funcs.append(node.name)
            doc = ast.get_docstring(node) or ""
            docstrings[node.name] = doc

        elif isinstance(node, ast.ClassDef):
            # 检查是否继承 PhoenixTestCase（简单名字匹配）
            is_liyu_case = any(
                (isinstance(b, ast.Name) and b.id == "PhoenixTestCase") or
                (isinstance(b, ast.Attribute) and b.attr == "PhoenixTestCase")
                for b in node.bases
            )
            methods: List[str] = []
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef) and item.name.startswith(TEST_FUNC_PREFIX):
                    methods.append(item.name)
                    doc = ast.get_docstring(item) or ""
                    docstrings[f"{node.name}.{item.name}"] = doc
            if methods:
                class_methods.append((node.name, methods))

    return top_funcs, docstrings, class_methods


def _make_lazy_func(func_name: str, module_path: str, test_class: Optional[str] = None) -> Callable:
    """
    创建一个延迟加载的测试函数代理。
    只在实际调用时才导入模块并获取真实函数。
    对于类方法，自动调用 setUp/tearDown 生命周期。
    """
    def lazy_runner(*args: Any, **kwargs: Any) -> Any:
        module_name = Path(module_path).stem
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(module_path).parent))
        spec.loader.exec_module(module)

        if test_class:
            cls = getattr(module, test_class)
            instance = cls()
            instance.setUp()
            try:
                method = getattr(instance, func_name)
                return method()
            finally:
                instance.tearDown()
                instance.cleanup_temps()
        else:
            func = getattr(module, func_name)
            return func()

    lazy_runner.__name__ = func_name
    if test_class:
        lazy_runner.__qualname__ = f"{test_class}.{func_name}"
    return lazy_runner


def discover_tests(
    search_dir: Path = 鲤鱼_HOME,
    pattern: str = TEST_FILE_PATTERN,
    registry: TestRegistry = _registry,
) -> int:
    """
    扫描 search_dir 下匹配 pattern 的文件，用 AST 解析提取 test_* 函数名。
    不执行模块代码（避免副作用），只在运行时延迟加载。
    返回发现的测试数量。
    """
    registry.clear()
    test_files = sorted(search_dir.glob(pattern))

    for test_file in test_files:
        module_name = test_file.stem
        module_path = str(test_file)

        top_funcs, docstrings, class_methods = _ast_discover(test_file)

        # 1) 顶层 test_* 函数
        for func_name in top_funcs:
            desc = docstrings.get(func_name, "")
            lazy = _make_lazy_func(func_name, module_path)
            registry.register(
                func=lazy,
                module_name=module_name,
                module_path=module_path,
                description=desc,
            )

        # 2) PhoenixTestCase 子类中的 test_* 方法
        for cls_name, methods in class_methods:
            for method_name in methods:
                desc_key = f"{cls_name}.{method_name}"
                desc = docstrings.get(desc_key, "")
                lazy = _make_lazy_func(method_name, module_path, test_class=cls_name)
                registry.register(
                    func=lazy,
                    module_name=module_name,
                    module_path=module_path,
                    description=desc,
                    test_class=cls_name,
                )

    return registry.count


# ============================================================
# Test Runner — 运行注册的测试
# ============================================================

class TestRunner:
    """
    运行注册的测试，收集结果。
    支持过滤、超时、setUp/tearDown 生命周期。
    """

    def __init__(
        self,
        registry: TestRegistry = _registry,
        verbose: bool = True,
        fail_fast: bool = False,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._registry = registry
        self._verbose = verbose
        self._fail_fast = fail_fast
        self._timeout = timeout_seconds

    def run_all(self, filter_pattern: Optional[str] = None) -> SuiteResult:
        """运行所有（或匹配的）测试，返回 SuiteResult。"""
        suite = SuiteResult()
        suite.started_at = datetime.now(timezone.utc).isoformat()

        tests = self._registry.get_all()
        if filter_pattern:
            tests = self._registry.filter(filter_pattern)

        if not tests:
            if self._verbose:
                print(f"{_C.YELLOW}No tests found matching '{filter_pattern or '*'}'{_C.RESET}")
            suite.finished_at = datetime.now(timezone.utc).isoformat()
            return suite

        # 按模块分组
        modules: Dict[str, Dict[str, RegisteredTest]] = {}
        for key, reg in tests.items():
            modules.setdefault(reg.module_name, {})[key] = reg

        if self._verbose:
            print(f"\n{_C.BOLD}{'=' * 60}")
            print(f"  鲤鱼 Test Runner")
            print(f"{'=' * 60}{_C.RESET}")
            print(f"  Modules: {len(modules)}  |  Tests: {len(tests)}")
            print()

        for module_name, module_tests in sorted(modules.items()):
            mod_result = self._run_module(module_name, module_tests)
            suite.modules.append(mod_result)

            if self._fail_fast and (mod_result.failed > 0 or mod_result.errors > 0):
                break

        suite.finished_at = datetime.now(timezone.utc).isoformat()

        if self._verbose:
            self._print_summary(suite)

        return suite

    def _run_module(
        self, module_name: str, tests: Dict[str, RegisteredTest]
    ) -> ModuleResult:
        """运行一个模块的所有测试。"""
        mod_result = ModuleResult(
            module_name=module_name,
            file_path=list(tests.values())[0].module_path,
        )

        if self._verbose:
            print(f"  {_C.CYAN}[{module_name}]{_C.RESET}")

        for key, reg in sorted(tests.items()):
            result = self._run_single(reg, {})
            mod_result.results.append(result)

            if self._verbose:
                self._print_test_result(result)

            if self._fail_fast and result.status in (TestStatus.FAILED, TestStatus.ERROR):
                break

        if self._verbose:
            status_icon = _C.GREEN + "PASS" + _C.RESET if mod_result.failed == 0 and mod_result.errors == 0 else _C.RED + "FAIL" + _C.RESET
            print(f"  {_C.DIM}  -> {status_icon}  "
                  f"{mod_result.passed}/{mod_result.total} passed "
                  f"({mod_result.total_duration_ms:.0f}ms){_C.RESET}")
            print()

        return mod_result

    def _run_single(
        self,
        reg: RegisteredTest,
        class_instances: Dict[str, PhoenixTestCase],
    ) -> TestResult:
        """运行单个测试函数。懒加载模式：模块只在调用时才导入。"""
        tc = reg.test_class or ""

        # Skip check
        if reg.skip_reason:
            return TestResult(
                name=reg.func.__name__,
                module=reg.module_name,
                status=TestStatus.SKIPPED,
                duration_ms=0.0,
                message=reg.skip_reason,
                test_class=tc,
            )

        start = time.monotonic()
        try:
            reg.func()

            duration = (time.monotonic() - start) * 1000
            return TestResult(
                name=reg.func.__name__,
                module=reg.module_name,
                status=TestStatus.PASSED,
                duration_ms=duration,
                test_class=tc,
            )

        except _SkipSignal as e:
            duration = (time.monotonic() - start) * 1000
            return TestResult(
                name=reg.func.__name__,
                module=reg.module_name,
                status=TestStatus.SKIPPED,
                duration_ms=duration,
                message=str(e),
                test_class=tc,
            )

        except AssertionError as e:
            duration = (time.monotonic() - start) * 1000
            return TestResult(
                name=reg.func.__name__,
                module=reg.module_name,
                status=TestStatus.FAILED,
                duration_ms=duration,
                message=str(e),
                traceback_str=traceback.format_exc(),
                test_class=tc,
            )

        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return TestResult(
                name=reg.func.__name__,
                module=reg.module_name,
                status=TestStatus.ERROR,
                duration_ms=duration,
                message=f"{type(e).__name__}: {e}",
                traceback_str=traceback.format_exc(),
                test_class=tc,
            )

    def _print_test_result(self, result: TestResult) -> None:
        icons = {
            TestStatus.PASSED:  _C.GREEN  + "  PASS" + _C.RESET,
            TestStatus.FAILED:  _C.RED    + "  FAIL" + _C.RESET,
            TestStatus.SKIPPED: _C.YELLOW + "  SKIP" + _C.RESET,
            TestStatus.ERROR:   _C.RED    + "  ERR " + _C.RESET,
        }
        icon = icons[result.status]
        duration = f"{_C.DIM}({result.duration_ms:.1f}ms){_C.RESET}"
        name = result.name
        if result.test_class:
            name = f"{result.test_class}.{result.name}"
        msg = ""
        if result.message and result.status != TestStatus.PASSED:
            msg = f" {_C.DIM}-- {result.message}{_C.RESET}"
        print(f"    {icon} {name} {duration}{msg}")

    def _print_summary(self, suite: SuiteResult) -> None:
        print(f"{_C.BOLD}{'=' * 60}")
        print(f"  Results")
        print(f"{'=' * 60}{_C.RESET}")

        total = suite.total_tests
        passed = suite.total_passed
        failed = suite.total_failed
        skipped = suite.total_skipped
        errors = suite.total_errors
        duration = suite.total_duration_ms

        parts = []
        if passed:
            parts.append(f"{_C.GREEN}{passed} passed{_C.RESET}")
        if failed:
            parts.append(f"{_C.RED}{failed} failed{_C.RESET}")
        if errors:
            parts.append(f"{_C.RED}{errors} errors{_C.RESET}")
        if skipped:
            parts.append(f"{_C.YELLOW}{skipped} skipped{_C.RESET}")

        print(f"  Tests: {total}  |  {'  '.join(parts)}")
        print(f"  Duration: {duration:.0f}ms  |  Modules: {len(suite.modules)}")

        if suite.success:
            print(f"\n  {_C.GREEN}{_C.BOLD}ALL TESTS PASSED{_C.RESET}")
        else:
            print(f"\n  {_C.RED}{_C.BOLD}SOME TESTS FAILED{_C.RESET}")

            # Print failure details
            print(f"\n  {_C.BOLD}Failures:{_C.RESET}")
            for mod in suite.modules:
                for r in mod.results:
                    if r.status in (TestStatus.FAILED, TestStatus.ERROR):
                        print(f"    {_C.RED}x{_C.RESET} {r.module}::{r.name}")
                        if r.message:
                            print(f"      {_C.DIM}{r.message}{_C.RESET}")

        print()


# ============================================================
# Report Generator — JSON 报告
# ============================================================

class ReportGenerator:
    """生成测试报告（JSON 格式 + 终端摘要）。"""

    @staticmethod
    def to_json(suite: SuiteResult) -> Dict[str, Any]:
        return {
            "framework": "liyu-test-framework",
            "version": "1.0.0",
            "started_at": suite.started_at,
            "finished_at": suite.finished_at,
            "summary": {
                "total": suite.total_tests,
                "passed": suite.total_passed,
                "failed": suite.total_failed,
                "errors": suite.total_errors,
                "skipped": suite.total_skipped,
                "duration_ms": round(suite.total_duration_ms, 2),
                "success": suite.success,
            },
            "modules": [
                {
                    "name": m.module_name,
                    "file": m.file_path,
                    "total": m.total,
                    "passed": m.passed,
                    "failed": m.failed,
                    "errors": m.errors,
                    "skipped": m.skipped,
                    "duration_ms": round(m.total_duration_ms, 2),
                    "tests": [r.to_dict() for r in m.results],
                }
                for m in suite.modules
            ],
        }

    @staticmethod
    def save_json(suite: SuiteResult, output_path: Path) -> None:
        report = ReportGenerator.to_json(suite)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"  {_C.DIM}Report saved to: {output_path}{_C.RESET}")

    @staticmethod
    def to_markdown(suite: SuiteResult) -> str:
        """生成 Markdown 格式的报告。"""
        lines = [
            "# 鲤鱼 Test Report",
            "",
            f"**Date**: {suite.started_at}",
            f"**Duration**: {suite.total_duration_ms:.0f}ms",
            f"**Status**: {'PASS' if suite.success else 'FAIL'}",
            "",
            "## Summary",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total | {suite.total_tests} |",
            f"| Passed | {suite.total_passed} |",
            f"| Failed | {suite.total_failed} |",
            f"| Errors | {suite.total_errors} |",
            f"| Skipped | {suite.total_skipped} |",
            "",
            "## Modules",
            "",
        ]

        for mod in suite.modules:
            status = "PASS" if mod.failed == 0 and mod.errors == 0 else "FAIL"
            lines.append(f"### {mod.module_name} [{status}]")
            lines.append("")
            lines.append(f"| Test | Status | Duration |")
            lines.append(f"|------|--------|----------|")
            for r in mod.results:
                lines.append(f"| {r.name} | {r.status.value} | {r.duration_ms:.1f}ms |")
            lines.append("")

        return "\n".join(lines)


# ============================================================
# Coverage Scanner — 静态扫描未测试模块
# ============================================================

class CoverageScanner:
    """扫描 鲤鱼 目录，识别有/无测试的模块。"""

    def __init__(self, liyu_dir: Path = 鲤鱼_HOME) -> None:
        self._dir = liyu_dir

    def scan(self) -> Dict[str, Any]:
        """扫描并返回覆盖率信息。"""
        # 所有 Python 模块（排除 test-* 和本框架）
        all_modules = []
        for f in sorted(self._dir.glob("*.py")):
            if f.name.startswith("test-") or f.name == "liyu-test-framework.py":
                continue
            if f.name.startswith("_"):
                continue
            all_modules.append(f)

        # 所有测试文件
        test_files = set()
        for f in sorted(self._dir.glob(TEST_FILE_PATTERN)):
            # test-knowledge-base.py -> knowledge-base
            name = f.stem
            if name.startswith("test-"):
                name = name[5:]
            test_files.add(name)

        # 子目录中的模块
        for subdir in sorted(self._dir.iterdir()):
            if subdir.is_dir() and not subdir.name.startswith((".", "_")):
                for f in sorted(subdir.glob("*.py")):
                    if f.name.startswith("test-"):
                        continue
                    all_modules.append(f)

        covered = []
        uncovered = []
        for mod in all_modules:
            mod_name = mod.stem
            # 检查是否有对应的测试
            has_test = mod_name in test_files
            # 也检查 test_<module> 格式
            if not has_test:
                has_test = f"test_{mod_name}" in test_files or f"test-{mod_name}" in test_files

            entry = {
                "module": mod_name,
                "path": str(mod),
                "has_test": has_test,
            }
            if has_test:
                covered.append(entry)
            else:
                uncovered.append(entry)

        total = len(all_modules)
        covered_count = len(covered)
        pct = (covered_count / total * 100) if total > 0 else 0

        return {
            "total_modules": total,
            "covered": covered_count,
            "uncovered": len(uncovered),
            "coverage_pct": round(pct, 1),
            "covered_modules": covered,
            "uncovered_modules": uncovered,
        }

    def print_report(self) -> None:
        """打印覆盖率报告到终端。"""
        result = self.scan()
        print(f"\n{_C.BOLD}{'=' * 60}")
        print(f"  鲤鱼 Test Coverage Scan")
        print(f"{'=' * 60}{_C.RESET}")
        print(f"  Modules: {result['total_modules']}")
        print(f"  With tests: {_C.GREEN}{result['covered']}{_C.RESET}")
        print(f"  Without tests: {_C.RED}{result['uncovered']}{_C.RESET}")
        print(f"  Coverage: {result['coverage_pct']}%")

        if result["uncovered_modules"]:
            print(f"\n  {_C.YELLOW}Modules without tests:{_C.RESET}")
            for m in result["uncovered_modules"]:
                print(f"    - {m['module']}  ({m['path']})")
        print()


# ============================================================
# CLI Entry Point
# ============================================================

def _print_usage() -> None:
    print(f"""
{_C.BOLD}鲤鱼 Unified Test Framework v1.0.0{_C.RESET}

{_C.CYAN}Usage:{_C.RESET}
  python3 liyu-test-framework.py <command> [options]

{_C.CYAN}Commands:{_C.RESET}
  run              Run all discovered tests
  list             List all discovered tests without running
  scan             Show test coverage scan
  report           Run tests and generate JSON report

{_C.CYAN}Options:{_C.RESET}
  --filter <pat>   Filter tests by name/module pattern (regex)
  --dir <path>     Test search directory (default: ~/.claude/liyu)
  --output <path>  Output path for JSON report
  --fail-fast      Stop on first failure
  --quiet          Suppress per-test output
  --help           Show this help

{_C.CYAN}Examples:{_C.RESET}
  python3 liyu-test-framework.py run
  python3 liyu-test-framework.py run --filter knowledge-base
  python3 liyu-test-framework.py list
  python3 liyu-test-framework.py scan
  python3 liyu-test-framework.py report --output test-report.json
""")


def main() -> int:
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        _print_usage()
        return 0

    command = args[0]

    # Parse options
    options: Dict[str, Any] = {
        "filter": None,
        "dir": 鲤鱼_HOME,
        "output": None,
        "fail_fast": False,
        "verbose": True,
    }

    i = 1
    while i < len(args):
        if args[i] == "--filter" and i + 1 < len(args):
            options["filter"] = args[i + 1]
            i += 2
        elif args[i] == "--dir" and i + 1 < len(args):
            options["dir"] = Path(args[i + 1])
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            options["output"] = Path(args[i + 1])
            i += 2
        elif args[i] == "--fail-fast":
            options["fail_fast"] = True
            i += 1
        elif args[i] == "--quiet":
            options["verbose"] = False
            i += 1
        else:
            i += 1

    if command == "list":
        count = discover_tests(search_dir=options["dir"])
        registry = _registry
        if options["filter"]:
            tests = registry.filter(options["filter"])
        else:
            tests = registry.get_all()

        print(f"\n{_C.BOLD}Discovered {len(tests)} tests in {len(registry.list_modules())} modules:{_C.RESET}\n")
        for mod_name in sorted(set(t.module_name for t in tests.values())):
            mod_tests = {k: v for k, v in tests.items() if v.module_name == mod_name}
            print(f"  {_C.CYAN}[{mod_name}]{_C.RESET} ({len(mod_tests)} tests)")
            for key, reg in sorted(mod_tests.items()):
                name = f"{reg.test_class}.{reg.func.__name__}" if reg.test_class else reg.func.__name__
                desc = f" {_C.DIM}-- {reg.description[:60]}{_C.RESET}" if reg.description else ""
                skip = f" {_C.YELLOW}[SKIP]{_C.RESET}" if reg.skip_reason else ""
                print(f"    {name}{skip}{desc}")
        print()
        return 0

    elif command == "scan":
        scanner = CoverageScanner(liyu_dir=options["dir"])
        scanner.print_report()
        return 0

    elif command in ("run", "report"):
        count = discover_tests(search_dir=options["dir"])
        if count == 0:
            print(f"{_C.YELLOW}No test files found in {options['dir']}{_C.RESET}")
            return 1

        runner = TestRunner(
            registry=_registry,
            verbose=options["verbose"],
            fail_fast=options["fail_fast"],
        )
        suite = runner.run_all(filter_pattern=options["filter"])

        # Save JSON report if requested or if command is "report"
        if command == "report" or options["output"]:
            output_path = options["output"] or (鲤鱼_HOME / "test-report.json")
            ReportGenerator.save_json(suite, output_path)

        return 0 if suite.success else 1

    else:
        print(f"{_C.RED}Unknown command: {command}{_C.RESET}")
        _print_usage()
        return 1


if __name__ == "__main__":
    sys.exit(main())
