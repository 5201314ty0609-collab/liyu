#!/usr/bin/env python3
"""
鲤鱼 Eval Engine — 借鉴 MUNDO v2.2.7 的评估框架。

提供简单的评估框架来验证 鲤鱼 的效果。
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable


@dataclass
class EvalCase:
    """评估用例"""
    id: str
    name: str
    description: str
    category: str  # knowledge, search, memory, coordination
    input_data: Dict[str, Any]
    expected_output: Optional[Dict[str, Any]] = None
    tolerance: float = 0.8  # 相似度阈值


@dataclass
class EvalResult:
    """评估结果"""
    case_id: str
    case_name: str
    passed: bool
    score: float
    actual_output: Any
    expected_output: Any
    duration_ms: float
    details: str = ""


@dataclass
class EvalSuite:
    """评估套件"""
    name: str
    description: str
    cases: List[EvalCase] = field(default_factory=list)
    results: List[EvalResult] = field(default_factory=list)


class 鲤鱼EvalEngine:
    """鲤鱼 评估引擎"""
    
    def __init__(self, results_dir: Optional[Path] = None):
        self.results_dir = results_dir or Path.home() / ".claude/liyu/eval_results"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.suites: Dict[str, EvalSuite] = {}
        self._register_builtin_suites()
    
    def _register_builtin_suites(self):
        """注册内置评估套件"""
        # 知识库搜索评估
        self.register_suite(EvalSuite(
            name="knowledge_search",
            description="知识库搜索质量评估",
            cases=[
                EvalCase(
                    id="ks_001",
                    name="基本关键词搜索",
                    description="测试基本的关键词搜索功能",
                    category="knowledge",
                    input_data={"query": "鲤鱼 架构", "mode": "fts5"},
                    expected_output={"min_results": 1, "max_duration_ms": 1000},
                ),
                EvalCase(
                    id="ks_002",
                    name="混合搜索",
                    description="测试 FTS5 + Vector 混合搜索",
                    category="knowledge",
                    input_data={"query": "向量检索", "mode": "hybrid"},
                    expected_output={"min_results": 1, "max_duration_ms": 2000},
                ),
                EvalCase(
                    id="ks_003",
                    name="向量搜索",
                    description="测试纯向量搜索",
                    category="knowledge",
                    input_data={"query": "memory system", "mode": "vector"},
                    expected_output={"min_results": 0, "max_duration_ms": 3000},
                ),
            ]
        ))
        
        # 7-Sense 健康评估
        self.register_suite(EvalSuite(
            name="seven_sense",
            description="7-Sense 系统健康评估",
            cases=[
                EvalCase(
                    id="ss_001",
                    name="仪表盘生成",
                    description="测试 7-Sense 仪表盘生成",
                    category="observability",
                    input_data={"command": "dashboard"},
                    expected_output={"has_all_senses": True, "health_score_min": 0},
                ),
                EvalCase(
                    id="ss_002",
                    name="会话评分",
                    description="测试会话健康评分",
                    category="observability",
                    input_data={"command": "score", "session_id": "test"},
                    expected_output={"score_min": 0, "score_max": 100},
                ),
            ]
        ))
        
        # 规则健康评估
        self.register_suite(EvalSuite(
            name="rule_health",
            description="规则系统健康评估",
            cases=[
                EvalCase(
                    id="rh_001",
                    name="规则健康报告",
                    description="测试规则健康报告生成",
                    category="rules",
                    input_data={"command": "dashboard"},
                    expected_output={"has_rules": True, "healthy_ratio_min": 0.5},
                ),
            ]
        ))
    
    def register_suite(self, suite: EvalSuite):
        """注册评估套件"""
        self.suites[suite.name] = suite
    
    def run_suite(self, suite_name: str, executor: Callable[[EvalCase], Any]) -> List[EvalResult]:
        """运行评估套件"""
        if suite_name not in self.suites:
            raise ValueError(f"Unknown suite: {suite_name}")
        
        suite = self.suites[suite_name]
        results = []
        
        for case in suite.cases:
            print(f"Running: {case.name}...")
            start_time = time.time()
            
            try:
                actual_output = executor(case)
                duration_ms = (time.time() - start_time) * 1000
                
                # 评估结果
                passed, score, details = self._evaluate(case, actual_output, duration_ms)
                
                result = EvalResult(
                    case_id=case.id,
                    case_name=case.name,
                    passed=passed,
                    score=score,
                    actual_output=actual_output,
                    expected_output=case.expected_output,
                    duration_ms=duration_ms,
                    details=details,
                )
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                result = EvalResult(
                    case_id=case.id,
                    case_name=case.name,
                    passed=False,
                    score=0.0,
                    actual_output=None,
                    expected_output=case.expected_output,
                    duration_ms=duration_ms,
                    details=f"Error: {e}",
                )
            
            results.append(result)
            suite.results.append(result)
            
            status = "✅ PASS" if result.passed else "❌ FAIL"
            print(f"  {status} | Score: {result.score:.2f} | {result.duration_ms:.0f}ms")
        
        return results
    
    def _evaluate(self, case: EvalCase, actual: Any, duration_ms: float) -> tuple:
        """评估单个用例"""
        if case.expected_output is None:
            return True, 1.0, "No expected output defined"
        
        expected = case.expected_output
        score = 1.0
        details = []
        passed = True
        
        # 检查最小结果数
        if "min_results" in expected:
            min_results = expected["min_results"]
            actual_results = len(actual) if isinstance(actual, (list, dict)) else 0
            if actual_results < min_results:
                score *= 0.5
                passed = False
                details.append(f"Results count {actual_results} < {min_results}")
        
        # 检查最大持续时间
        if "max_duration_ms" in expected:
            max_duration = expected["max_duration_ms"]
            if duration_ms > max_duration:
                score *= 0.8
                details.append(f"Duration {duration_ms:.0f}ms > {max_duration}ms")
        
        # 检查布尔字段
        for key in ["has_all_senses", "has_rules"]:
            if key in expected:
                expected_val = expected[key]
                actual_val = actual.get(key) if isinstance(actual, dict) else None
                if actual_val != expected_val:
                    score *= 0.7
                    passed = False
                    details.append(f"{key}: expected {expected_val}, got {actual_val}")
        
        # 检查数值范围
        for key in ["health_score_min", "score_min", "score_max", "healthy_ratio_min"]:
            if key in expected:
                expected_val = expected[key]
                actual_val = actual.get(key) if isinstance(actual, dict) else None
                if actual_val is None:
                    score *= 0.5
                    details.append(f"{key}: missing")
                elif "min" in key and actual_val < expected_val:
                    score *= 0.8
                    details.append(f"{key}: {actual_val} < {expected_val}")
                elif "max" in key and actual_val > expected_val:
                    score *= 0.8
                    details.append(f"{key}: {actual_val} > {expected_val}")
        
        return passed, score, "; ".join(details) if details else "All checks passed"
    
    def generate_report(self, suite_name: Optional[str] = None) -> str:
        """生成评估报告"""
        report_lines = [
            "=" * 60,
            "  鲤鱼 Evaluation Report",
            f"  Generated: {datetime.now(timezone.utc).isoformat()}",
            "=" * 60,
            "",
        ]
        
        suites_to_report = [suite_name] if suite_name else list(self.suites.keys())
        
        for name in suites_to_report:
            if name not in self.suites:
                continue
            
            suite = self.suites[name]
            report_lines.extend([
                f"── {suite.name} ──",
                f"Description: {suite.description}",
                f"Cases: {len(suite.cases)}",
                "",
            ])
            
            if not suite.results:
                report_lines.append("  No results yet.")
                report_lines.append("")
                continue
            
            # 统计
            total = len(suite.results)
            passed = sum(1 for r in suite.results if r.passed)
            failed = total - passed
            avg_score = sum(r.score for r in suite.results) / total if total > 0 else 0
            avg_duration = sum(r.duration_ms for r in suite.results) / total if total > 0 else 0
            
            report_lines.extend([
                f"Results: {passed}/{total} passed ({passed/total*100:.0f}%)",
                f"Average Score: {avg_score:.2f}",
                f"Average Duration: {avg_duration:.0f}ms",
                "",
            ])
            
            # 详细结果
            for result in suite.results:
                status = "✅" if result.passed else "❌"
                report_lines.append(
                    f"  {status} [{result.case_id}] {result.case_name}: "
                    f"{result.score:.2f} | {result.duration_ms:.0f}ms | {result.details}"
                )
            
            report_lines.append("")
        
        return "\n".join(report_lines)
    
    def save_results(self, filename: Optional[str] = None):
        """保存评估结果"""
        if filename is None:
            filename = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = self.results_dir / filename
        
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suites": {},
        }
        
        for name, suite in self.suites.items():
            data["suites"][name] = {
                "name": suite.name,
                "description": suite.description,
                "results": [
                    {
                        "case_id": r.case_id,
                        "case_name": r.case_name,
                        "passed": r.passed,
                        "score": r.score,
                        "duration_ms": r.duration_ms,
                        "details": r.details,
                    }
                    for r in suite.results
                ]
            }
        
        filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"Results saved to: {filepath}")


# 命令行接口
def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: eval_engine.py <command> [args]")
        print()
        print("Commands:")
        print("  list                    List all evaluation suites")
        print("  run <suite_name>        Run evaluation suite")
        print("  report [suite_name]     Generate evaluation report")
        return
    
    command = sys.argv[1]
    engine = 鲤鱼EvalEngine()
    
    if command == "list":
        print("Evaluation Suites:")
        print("=" * 40)
        for name, suite in engine.suites.items():
            print(f"  {name}: {suite.description}")
            print(f"    Cases: {len(suite.cases)}")
            print()
    
    elif command == "run":
        if len(sys.argv) < 3:
            print("Usage: eval_engine.py run <suite_name>")
            return
        
        suite_name = sys.argv[2]
        
        # 简单的执行器示例
        def executor(case):
            # 这里应该调用实际的 鲤鱼 功能
            # 为了演示，返回模拟数据
            if case.category == "knowledge":
                return {"results": [], "count": 0}
            elif case.category == "observability":
                return {"has_all_senses": True, "health_score": 100}
            elif case.category == "rules":
                return {"has_rules": True, "healthy_ratio": 0.93}
            return {}
        
        results = engine.run_suite(suite_name, executor)
        engine.save_results()
        print()
        print(engine.generate_report(suite_name))
    
    elif command == "report":
        suite_name = sys.argv[2] if len(sys.argv) > 2 else None
        print(engine.generate_report(suite_name))
    
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
