#!/usr/bin/env python3
"""
鲤鱼 Mutation Gate — TDD 变体验证步骤。

受 Tautest (canblmz1) + SWE-Mutation (ACL 2026) 启发：
- 在测试通过后、重构前，对源码施加简单变体
- 验证测试是否能捕获这些变体（即测试真的在保护行为）
- 生成 fix-prompt.md 用于 AI 辅助加强测试

核心洞察（SWE-Mutation）：
  RDR 从 71% 跌至 40% 当使用 agentic mutant 代替 rule-based mutant
  → 传统测试对 AI 生成代码的判别力被严重高估
  → 即使是 Claude Sonnet 4.5 也只能达到 81.15% test repair

Tautest 的 fix-prompt 规则（内化）：
  - 不能修改生产代码
  - 新测试必须对原始代码通过
  - 新测试必须对变体代码失败
  - 不能弱化现有断言
  - 不能写占位测试

Usage:
  liyu-mutation-gate.py run <source_file> <test_file> [--threshold 80]
  liyu-mutation-gate.py check <source_file> <test_file> [--mutants N]
  liyu-mutation-gate.py fix-prompt <source_file> <test_file>
"""

import ast
import subprocess
import sys
import tempfile
import shutil
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import datetime, timezone

# ──────────────────── Data Structures ────────────────────

@dataclass(frozen=True)
class MutationPoint:
    """源码中的一个可变异点"""
    line: int
    col: int
    description: str
    original: str
    mutated: str
    strategy: str  # conditional, operator, assertion, argument, constant


@dataclass
class MutationResult:
    """单个变体的测试结果"""
    mutation: MutationPoint
    killed: bool  # True = test caught the mutant
    test_output: str = ""
    error: Optional[str] = None


@dataclass
class MutationReport:
    """完整的变体测试报告"""
    source_file: str
    test_file: str
    total_mutants: int = 0
    killed: int = 0
    survived: int = 0
    errors: int = 0
    results: list = field(default_factory=list)
    mutation_score: float = 0.0
    threshold: int = 80
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ──────────────────── Mutation Strategies ────────────────────

class MutationStrategy:
    """变体策略基类"""

    @staticmethod
    def conditional_boundary(node: ast.Compare) -> list[tuple[str, str, str]]:
        """翻转条件边界: > ↔ >=, < ↔ <=, == ↔ !="""
        mutations = []
        for i, op in enumerate(node.ops):
            if isinstance(op, ast.Gt):
                mutations.append((">", ">=", "conditional_boundary"))
            elif isinstance(op, ast.GtE):
                mutations.append((">=", ">", "conditional_boundary"))
            elif isinstance(op, ast.Lt):
                mutations.append(("<", "<=", "conditional_boundary"))
            elif isinstance(op, ast.LtE):
                mutations.append(("<=", "<", "conditional_boundary"))
            elif isinstance(op, ast.Eq):
                mutations.append(("==", "!=", "conditional_boundary"))
            elif isinstance(op, ast.NotEq):
                mutations.append(("!=", "==", "conditional_boundary"))
        return mutations

    @staticmethod
    def operator_swap(node: ast.BinOp) -> list[tuple[str, str, str]]:
        """交换运算符: + ↔ -, * ↔ /, // ↔ /"""
        if isinstance(node.op, ast.Add):
            return [("+", "-", "operator_swap")]
        elif isinstance(node.op, ast.Sub):
            return [("-", "+", "operator_swap")]
        elif isinstance(node.op, ast.Mult):
            return [("*", "/", "operator_swap")]
        elif isinstance(node.op, ast.Div):
            return [("/", "*", "operator_swap")]
        elif isinstance(node.op, ast.FloorDiv):
            return [("//", "/", "operator_swap")]
        return []

    @staticmethod
    def boolean_flip(node: ast.BoolOp) -> list[tuple[str, str, str]]:
        """翻转布尔运算: and ↔ or"""
        if isinstance(node.op, ast.And):
            return [("and", "or", "boolean_flip")]
        elif isinstance(node.op, ast.Or):
            return [("or", "and", "boolean_flip")]
        return []

    @staticmethod
    def constant_shift(node: ast.Constant) -> list[tuple[str, str, str]]:
        """常量偏移: 数值 ±1, 字符串变空"""
        if isinstance(node.value, (int, float)) and node.value != 0:
            v = node.value
            return [
                (str(v), str(v + 1), "constant_shift"),
                (str(v), str(v - 1), "constant_shift"),
            ]
        return []


class MutationFinder(ast.NodeVisitor):
    """遍历 AST 发现所有变异点"""

    def __init__(self):
        self.mutation_points: list[MutationPoint] = []

    def visit_Compare(self, node: ast.Compare):
        for orig, mutated, strategy in MutationStrategy.conditional_boundary(node):
            self.mutation_points.append(MutationPoint(
                line=node.lineno, col=node.col_offset,
                description=f"Flip '{orig}' to '{mutated}'",
                original=orig, mutated=mutated, strategy=strategy,
            ))
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp):
        for orig, mutated, strategy in MutationStrategy.operator_swap(node):
            self.mutation_points.append(MutationPoint(
                line=node.lineno, col=node.col_offset,
                description=f"Swap '{orig}' to '{mutated}'",
                original=orig, mutated=mutated, strategy=strategy,
            ))
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp):
        for orig, mutated, strategy in MutationStrategy.boolean_flip(node):
            self.mutation_points.append(MutationPoint(
                line=node.lineno, col=node.col_offset,
                description=f"Flip '{orig}' to '{mutated}'",
                original=orig, mutated=mutated, strategy=strategy,
            ))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (int, float)) and node.value not in (0, 1, -1, True, False, None):
            for orig, mutated, strategy in MutationStrategy.constant_shift(node):
                self.mutation_points.append(MutationPoint(
                    line=node.lineno, col=node.col_offset,
                    description=f"Shift constant {orig} → {mutated}",
                    original=orig, mutated=mutated, strategy=strategy,
                ))
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert):
        """移除断言: 将 assert X 变为 pass"""
        self.mutation_points.append(MutationPoint(
            line=node.lineno, col=node.col_offset,
            description="Remove assertion",
            original="assert ...", mutated="pass",
            strategy="remove_assertion",
        ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """交换函数调用的前两个参数（如果有 2+ 个位置参数）"""
        if len(node.args) >= 2:
            self.mutation_points.append(MutationPoint(
                line=node.lineno, col=node.col_offset,
                description="Swap first two arguments",
                original="...", mutated="...",
                strategy="swap_arguments",
            ))
        self.generic_visit(node)


# ──────────────────── Source Mutation ────────────────────

def find_mutations(source_code: str) -> list[MutationPoint]:
    """解析源码并找到所有变异点"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        print(f"Syntax error in source: {e}")
        return []

    finder = MutationFinder()
    finder.visit(tree)
    return finder.mutation_points


def apply_mutation(source_code: str, mutation: MutationPoint, lines: list[str]) -> str:
    """对源码施加单个变体，返回变异后的代码"""
    line_idx = mutation.line - 1
    if line_idx >= len(lines):
        return source_code

    line = lines[line_idx]

    if mutation.strategy == "remove_assertion":
        indent = len(line) - len(line.lstrip())
        lines[line_idx] = " " * indent + "pass  # [MUTATED] assertion removed\n"
    elif mutation.strategy in ("conditional_boundary", "operator_swap", "boolean_flip", "constant_shift"):
        # 简单文本替换
        lines[line_idx] = line.replace(mutation.original, mutation.mutated, 1)
    elif mutation.strategy == "swap_arguments":
        # 交换前两个参数 — 使用 AST 精确操作
        pass  # 留给更精确的实现

    return "".join(lines)


def apply_mutation_ast(source_code: str, mutation: MutationPoint) -> str:
    """基于 AST 的精确变体应用（用于 swap_arguments）"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code

    if mutation.strategy == "remove_assertion":
        mutator = _AssertionRemover(mutation.line)
        return mutator.visit(tree) if mutator.found else source_code

    if mutation.strategy == "swap_arguments":
        mutator = _ArgumentSwapper(mutation.line)
        result = mutator.visit(tree)
        return result if mutator.found else source_code

    return source_code


class _AssertionRemover(ast.NodeTransformer):
    def __init__(self, target_line: int):
        self.target_line = target_line
        self.found = False

    def visit_Assert(self, node: ast.Assert):
        if node.lineno == self.target_line and not self.found:
            self.found = True
            return ast.Pass()
        return node


class _ArgumentSwapper(ast.NodeTransformer):
    def __init__(self, target_line: int):
        self.target_line = target_line
        self.found = False

    def visit_Call(self, node: ast.Call):
        if node.lineno == self.target_line and not self.found and len(node.args) >= 2:
            self.found = True
            new_args = list(node.args)
            new_args[0], new_args[1] = new_args[1], new_args[0]
            node.args = new_args
        return node


# ──────────────────── Test Runner ────────────────────

def run_tests(test_file: str, cwd: Optional[str] = None) -> tuple[bool, str]:
    """运行 pytest 并返回 (pass, output)"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "-x", "--tb=short", "-q"],
            capture_output=True, text=True, timeout=60,
            cwd=cwd or str(Path(test_file).parent),
        )
        passed = result.returncode == 0
        output = result.stdout + "\n" + result.stderr
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out"
    except FileNotFoundError:
        return False, "pytest not found — install with: pip install pytest"


# ──────────────────── Mutation Execution ────────────────────

def run_mutation_gate(
    source_file: str,
    test_file: str,
    threshold: int = 80,
    max_mutants: Optional[int] = None,
) -> MutationReport:
    """运行完整的变体门控检查"""
    source_path = Path(source_file).resolve()
    test_path = Path(test_file).resolve()

    if not source_path.exists():
        sys.exit(f"Source file not found: {source_file}")
    if not test_path.exists():
        sys.exit(f"Test file not found: {test_file}")

    source_code = source_path.read_text()
    mutations = find_mutations(source_code)

    if max_mutants and len(mutations) > max_mutants:
        import random
        random.seed(42)
        mutations = random.sample(mutations, max_mutants)

    report = MutationReport(
        source_file=str(source_path),
        test_file=str(test_path),
        threshold=threshold,
    )

    if not mutations:
        print("No mutation points found in source.")
        report.mutation_score = 100.0
        return report

    print(f"Found {len(mutations)} mutation points")
    print(f"Running mutation gate (threshold: {threshold}%)...")
    print("-" * 60)

    with tempfile.TemporaryDirectory(prefix="liyu-mutation-") as tmp_dir:
        tmp_source = Path(tmp_dir) / source_path.name
        shutil.copy2(source_path, tmp_source)
        lines = source_code.splitlines(keepends=True)

        for i, mutation in enumerate(mutations):
            # 应用变体
            if mutation.strategy in ("remove_assertion", "swap_arguments"):
                mutated_code = apply_mutation_ast(source_code, mutation)
            else:
                mutated_code = apply_mutation(source_code, mutation, lines.copy())

            tmp_source.write_text(mutated_code)

            # 运行测试
            passed, output = run_tests(str(test_path))

            result = MutationResult(
                mutation=mutation,
                killed=not passed,
                test_output=output,
            )
            report.results.append(result)

            if not passed:
                report.killed += 1
                status = "KILLED"
            else:
                report.survived += 1
                status = "SURVIVED"

            bar = "▌" if not passed else " "
            print(f"  [{i+1:3d}/{len(mutations)}] {bar} L{mutation.line:4d} {mutation.description:40s} {status}")

            # 恢复原文件
            shutil.copy2(source_path, tmp_source)

    report.total_mutants = len(mutations)
    report.mutation_score = (report.killed / report.total_mutants * 100) if report.total_mutants else 100.0

    return report


# ──────────────────── Fix Prompt Generator ────────────────────

def generate_fix_prompt(report: MutationReport) -> str:
    """生成 Tautest 风格的 AI fix-prompt（Markdown）"""
    survived = [r for r in report.results if not r.killed]
    killed = [r for r in report.results if r.killed]

    lines = []
    lines.append("# 鲤鱼 Mutation Gate — Fix Prompt")
    lines.append("")
    lines.append(f"**Generated**: {report.timestamp}")
    lines.append(f"**Source**: `{report.source_file}`")
    lines.append(f"**Test**: `{report.test_file}`")
    lines.append(f"**Score**: {report.mutation_score:.1f}% ({report.killed}/{report.total_mutants} killed)")
    lines.append("")

    lines.append("## Rules (STRICT)")
    lines.append("")
    lines.append("| # | Rule |")
    lines.append("|---|------|")
    lines.append("| 1 | **Do NOT modify production code** — only edit/add test files |")
    lines.append("| 2 | Every new test **MUST PASS** against original (unmutated) code |")
    lines.append("| 3 | Every new test **MUST FAIL** against the mutant behavior below |")
    lines.append("| 4 | **Do NOT weaken** existing assertions |")
    lines.append("| 5 | **No filler tests** — no `assert True`, no `assert 1 == 1` |")
    lines.append("")

    if not survived:
        lines.append("## All Mutants Killed")
        lines.append("")
        lines.append("No surviving mutants. Test suite is strong against basic mutations.")
        lines.append("")
        lines.append("Consider adding more sophisticated mutation checks (agentic mutants) ")
        lines.append("to verify robustness against semantically complex bugs.")
    else:
        lines.append(f"## Surviving Mutants ({len(survived)})")
        lines.append("")
        lines.append("These mutations were NOT caught by the current test suite.")
        lines.append("Add tests that specifically target these behaviors:")
        lines.append("")

        # 按策略分组
        by_strategy: dict[str, list] = {}
        for r in survived:
            s = r.mutation.strategy
            by_strategy.setdefault(s, []).append(r)

        for strategy, mutants in sorted(by_strategy.items()):
            strategy_names = {
                "conditional_boundary": "Conditional Boundary",
                "operator_swap": "Operator Swap",
                "boolean_flip": "Boolean Logic Flip",
                "constant_shift": "Constant Shift",
                "remove_assertion": "Removed Assertion",
                "swap_arguments": "Swapped Arguments",
            }
            display = strategy_names.get(strategy, strategy)
            lines.append(f"### {display} ({len(mutants)})")
            lines.append("")
            for r in mutants:
                lines.append(f"- **Line {r.mutation.line}**: {r.mutation.description}")
                lines.append(f"  - Original: `{r.mutation.original}` → Mutant: `{r.mutation.mutated}`")
            lines.append("")

        lines.append("## Suggested Test Additions")
        lines.append("")
        lines.append("For each surviving mutant above, add a test that:")
        lines.append("1. Exercises the specific boundary/logic that was mutated")
        lines.append("2. Passes against original code")
        lines.append("3. Fails against the mutated version")
        lines.append("")
        lines.append("Example for conditional boundary (L{0}):".format(survived[0].mutation.line if survived else 0))
        lines.append("```python")
        lines.append("# If age >= 65 was mutated to age > 65, add:")
        lines.append("def test_senior_discount_at_exactly_65():")
        lines.append("    result = calculate_discount(age=65, price=100)")
        lines.append("    assert result == 80  # boundary value")
        lines.append("```")

    if killed:
        lines.append("")
        lines.append(f"## Killed Mutants ({len(killed)}) — Reference")
        lines.append("")
        lines.append("These mutations were correctly caught. Do NOT remove or weaken the tests that catch them.")
        lines.append("")
        for r in killed[:10]:  # Show first 10
            lines.append(f"- L{r.mutation.line}: {r.mutation.description}")
        if len(killed) > 10:
            lines.append(f"- ... and {len(killed) - 10} more")

    return "\n".join(lines)


# ──────────────────── Report Formatters ────────────────────

def print_report(report: MutationReport):
    """打印终端报告"""
    print()
    print("=" * 60)
    print("鲤鱼 Mutation Gate — Report")
    print("=" * 60)
    print()

    passed = report.mutation_score >= report.threshold
    status = "STRONG" if passed else "NEEDS WORK"
    icon = "✓" if passed else "✗"

    print(f"  {icon} Score: {report.mutation_score:.1f}% (threshold: {report.threshold}%)")
    print(f"  Status: {status}")
    print(f"  Killed: {report.killed} | Survived: {report.survived} | Errors: {report.errors}")
    print()

    if report.survived > 0:
        print("Top surviving mutants:")
        for r in report.results:
            if not r.killed:
                print(f"  - L{r.mutation.line:4d} [{r.mutation.strategy:20s}] {r.mutation.description}")
        print()

    print(f"Run 'liyu-mutation-gate.py fix-prompt {report.source_file} {report.test_file}'")
    print(f"to generate an AI-ready fix prompt.")

    if not passed:
        sys.exit(1)


def save_json_report(report: MutationReport, output_path: str):
    """保存 JSON 报告"""
    data = {
        "source_file": report.source_file,
        "test_file": report.test_file,
        "mutation_score": report.mutation_score,
        "threshold": report.threshold,
        "total_mutants": report.total_mutants,
        "killed": report.killed,
        "survived": report.survived,
        "errors": report.errors,
        "timestamp": report.timestamp,
        "results": [
            {
                "line": r.mutation.line,
                "description": r.mutation.description,
                "strategy": r.mutation.strategy,
                "original": r.mutation.original,
                "mutated": r.mutation.mutated,
                "killed": r.killed,
            }
            for r in report.results
        ],
    }
    Path(output_path).write_text(json.dumps(data, indent=2))


# ──────────────────── CLI ────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="鲤鱼 Mutation Gate — TDD mutation verification step",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser("run", help="Run mutation gate on source+test")
    run_p.add_argument("source", help="Source file to mutate")
    run_p.add_argument("test", help="Test file to run")
    run_p.add_argument("--threshold", type=int, default=80, help="Pass threshold %% (default: 80)")
    run_p.add_argument("--mutants", type=int, default=None, help="Max mutants to test")
    run_p.add_argument("--json", type=str, default=None, help="Save JSON report to path")
    run_p.add_argument("--fix-prompt", type=str, default=None, help="Save fix prompt to path")

    # check (lightweight, fast-fail)
    check_p = sub.add_parser("check", help="Quick check — exit 1 if threshold not met")
    check_p.add_argument("source", help="Source file to mutate")
    check_p.add_argument("test", help="Test file to run")
    check_p.add_argument("--threshold", type=int, default=80, help="Pass threshold %% (default: 80)")
    check_p.add_argument("--mutants", type=int, default=10, help="Max mutants (default: 10, for speed)")

    # fix-prompt
    fp_p = sub.add_parser("fix-prompt", help="Generate AI-ready fix prompt")
    fp_p.add_argument("source", help="Source file that was mutated")
    fp_p.add_argument("test", help="Test file that was run")
    fp_p.add_argument("--output", type=str, default=".liyu-mutation-fix-prompt.md",
                      help="Output path (default: .liyu-mutation-fix-prompt.md)")
    fp_p.add_argument("--threshold", type=int, default=80, help="Pass threshold %%")
    fp_p.add_argument("--mutants", type=int, default=None, help="Max mutants to test")

    args = parser.parse_args()

    if args.command == "run":
        report = run_mutation_gate(args.source, args.test, args.threshold, args.mutants)
        print_report(report)

        if args.json:
            save_json_report(report, args.json)
            print(f"JSON report saved: {args.json}")

        if args.fix_prompt:
            prompt = generate_fix_prompt(report)
            Path(args.fix_prompt).write_text(prompt)
            print(f"Fix prompt saved: {args.fix_prompt}")

        # 总是生成 fix-prompt 到默认位置
        default_prompt = generate_fix_prompt(report)
        Path(".liyu-mutation-fix-prompt.md").write_text(default_prompt)
        print(f"Fix prompt auto-saved: .liyu-mutation-fix-prompt.md")

    elif args.command == "check":
        report = run_mutation_gate(args.source, args.test, args.threshold, args.mutants)
        print_report(report)

    elif args.command == "fix-prompt":
        report = run_mutation_gate(args.source, args.test, args.threshold, args.mutants)
        prompt = generate_fix_prompt(report)
        Path(args.output).write_text(prompt)
        print(f"Fix prompt written to: {args.output}")
        print_report(report)


if __name__ == "__main__":
    main()
