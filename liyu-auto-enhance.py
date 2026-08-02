#!/usr/bin/env python3
"""
鲤鱼 Auto-Enhance — 自动化增强系统
整合技能发现、记忆压缩、规则优化、任务调度、错误恢复

功能：
  1. 技能自动发现 - 从使用模式中发现新技能需求
  2. 记忆自动压缩 - 智能压缩过期和低价值记忆
  3. 规则自动优化 - 基于使用数据优化规则
  4. 任务自动调度 - 智能任务优先级和调度
  5. 错误自动恢复 - 模式识别和自动修复

Usage:
  python3 liyu-auto-enhance.py discover        # 发现新技能需求
  python3 liyu-auto-enhance.py compress         # 压缩记忆
  python3 liyu-auto-enhance.py optimize-rules   # 优化规则
  python3 liyu-auto-enhance.py schedule          # 查看任务调度
  python3 liyu-auto-enhance.py recover           # 错误恢复分析
  python3 liyu-auto-enhance.py dashboard         # 综合仪表盘
  python3 liyu-auto-enhance.py auto              # 自动运行所有增强
"""

import json
import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
import re
import math

# ── 路径 ─────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DB_PATH = 鲤鱼_HOME / "knowledge-base.db"
STORY_FILE = 鲤鱼_HOME / "story.jsonl"
REFLECTIONS_FILE = 鲤鱼_HOME / "reflections.jsonl"
SKILL_USAGE_FILE = 鲤鱼_HOME / "skills" / "skill-usage.jsonl"
RULES_DIR = Path.home() / ".claude" / "rules" / "liyu"
STATE_FILE = 鲤鱼_HOME / "auto-enhance-state.json"

# ── 数据类 ───────────────────────────────────────────────────────────────

@dataclass
class SkillGap:
    """技能缺口"""
    pattern: str
    frequency: int
    suggested_skill: str
    confidence: float
    examples: List[str] = field(default_factory=list)

@dataclass
class MemoryCandidate:
    """压缩候选"""
    id: str
    content: str
    score: float
    reason: str
    action: str  # compress, merge, archive, delete

@dataclass
class RuleOptimization:
    """规则优化建议"""
    rule_id: str
    current_score: float
    issue: str
    suggestion: str
    priority: str

@dataclass
class TaskSchedule:
    """任务调度"""
    task_id: str
    description: str
    priority: int
    estimated_time: int
    dependencies: List[str]
    status: str

# ── 技能自动发现 ─────────────────────────────────────────────────────────

class SkillDiscovery:
    """从使用模式中发现新技能需求"""

    def __init__(self):
        self.usage_patterns = defaultdict(list)
        self.command_sequences = []

    def analyze_usage_patterns(self) -> List[SkillGap]:
        """分析使用模式，发现技能缺口"""
        gaps = []

        # 分析 story.jsonl 中的工具使用模式
        if STORY_FILE.exists():
            lines = STORY_FILE.read_text().strip().split("\n")
            for line in lines[-100:]:  # 最近100条记录
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    summary = entry.get("summary", "")
                    self._extract_patterns(summary)
                except Exception:
                    pass

        # 分析技能使用记录
        if SKILL_USAGE_FILE.exists():
            lines = SKILL_USAGE_FILE.read_text().strip().split("\n")
            for line in lines[-50:]:
                if not line.strip():
                    continue
                try:
                    usage = json.loads(line)
                    self._analyze_skill_usage(usage)
                except Exception:
                    pass

        # 识别常见但无对应技能的操作模式
        for pattern, occurrences in self.usage_patterns.items():
            if len(occurrences) >= 3:  # 出现3次以上
                gap = self._identify_gap(pattern, occurrences)
                if gap:
                    gaps.append(gap)

        # 按频率排序
        gaps.sort(key=lambda g: g.frequency, reverse=True)
        return gaps[:10]

    def _extract_patterns(self, text: str):
        """提取操作模式"""
        # 代码重构模式
        if re.search(r'重构|refactor|提取|extract', text):
            self.usage_patterns["refactoring"].append(text[:100])

        # 测试生成模式
        if re.search(r'测试|test|spec|单元测试', text):
            self.usage_patterns["test_generation"].append(text[:100])

        # 文档生成模式
        if re.search(r'文档|doc|README|注释', text):
            self.usage_patterns["documentation"].append(text[:100])

        # 性能优化模式
        if re.search(r'性能|performance|优化|optimize|缓存|cache', text):
            self.usage_patterns["performance"].append(text[:100])

        # 数据库操作模式
        if re.search(r'数据库|database|SQL|查询|query', text):
            self.usage_patterns["database"].append(text[:100])

        # API设计模式
        if re.search(r'API|接口|endpoint|路由|route', text):
            self.usage_patterns["api_design"].append(text[:100])

    def _analyze_skill_usage(self, usage: dict):
        """分析技能使用情况"""
        skill = usage.get("skill", "")
        context = usage.get("context", "")
        success = usage.get("success", True)

        # 记录失败的技能使用
        if not success:
            self.usage_patterns[f"failed_{skill}"].append(context)

    def _identify_gap(self, pattern: str, occurrences: List[str]) -> Optional[SkillGap]:
        """识别技能缺口"""
        # 已有技能
        existing_skills = {
            "refactoring": ["code-tidy", "complexity"],
            "test_generation": ["mutation-gate"],
            "documentation": ["doc-gen"],
            "performance": ["complexity"],
            "database": [],
            "api_design": [],
        }

        existing = existing_skills.get(pattern, [])
        if not existing:  # 没有对应技能
            return SkillGap(
                pattern=pattern,
                frequency=len(occurrences),
                suggested_skill=f"{pattern}-assistant",
                confidence=min(0.9, len(occurrences) * 0.15),
                examples=occurrences[:3]
            )

        return None

    def generate_discovery_report(self) -> str:
        """生成发现报告"""
        gaps = self.analyze_usage_patterns()

        if not gaps:
            return "未发现明显的技能缺口。"

        report = "═══ 技能自动发现报告 ═══\n\n"
        report += f"分析时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        report += f"发现缺口: {len(gaps)} 个\n\n"

        for i, gap in enumerate(gaps, 1):
            report += f"{i}. {gap.pattern}\n"
            report += f"   频率: {gap.frequency} 次\n"
            report += f"   建议技能: {gap.suggested_skill}\n"
            report += f"   置信度: {gap.confidence:.1%}\n"
            if gap.examples:
                report += f"   示例: {gap.examples[0][:50]}...\n"
            report += "\n"

        return report

# ── 记忆自动压缩 ─────────────────────────────────────────────────────────

class MemoryCompressor:
    """智能记忆压缩系统"""

    def __init__(self):
        self.compression_stats = {
            "total_processed": 0,
            "compressed": 0,
            "merged": 0,
            "archived": 0,
            "deleted": 0,
            "space_saved": 0
        }

    def analyze_compression_candidates(self) -> List[MemoryCandidate]:
        """分析压缩候选"""
        candidates = []

        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row

            # 获取所有活跃记忆
            rows = conn.execute("""
                SELECT id, content, mem_type, importance, confidence,
                       decay_strength, recall_count, created_at, last_recalled
                FROM auto_memories
                WHERE is_active = 1
                ORDER BY decay_strength ASC
            """).fetchall()

            now = datetime.now(timezone.utc)

            for row in rows:
                memory_id = row["id"]
                content = row["content"]
                importance = row["importance"]
                decay = row["decay_strength"]
                recall_count = row["recall_count"]

                # 计算记忆价值
                value_score = self._calculate_value(row, now)

                # 确定压缩策略
                if value_score < 0.2:
                    candidates.append(MemoryCandidate(
                        id=memory_id,
                        content=content[:100],
                        score=value_score,
                        reason="低价值记忆",
                        action="delete"
                    ))
                elif value_score < 0.4:
                    candidates.append(MemoryCandidate(
                        id=memory_id,
                        content=content[:100],
                        score=value_score,
                        reason="衰减严重",
                        action="archive"
                    ))
                elif self._is_duplicate(content, rows):
                    candidates.append(MemoryCandidate(
                        id=memory_id,
                        content=content[:100],
                        score=value_score,
                        reason="重复内容",
                        action="merge"
                    ))

            conn.close()

        except Exception as e:
            print(f"分析压缩候选时出错: {e}")

        # 按分数排序（低分优先处理）
        candidates.sort(key=lambda c: c.score)
        return candidates

    def _calculate_value(self, row, now) -> float:
        """计算记忆价值分数"""
        importance = row["importance"] or 0.5
        decay = row["decay_strength"] or 1.0
        recall_count = row["recall_count"] or 0

        # 时间衰减
        try:
            last_recalled = datetime.fromisoformat(row["last_recalled"])
            days_since = (now - last_recalled).days
            time_decay = math.exp(-days_since / 30)  # 30天半衰期
        except:
            time_decay = 0.5

        # 价值 = 重要性 × 衰减强度 × 时间衰减 × 使用频率
        value = importance * decay * time_decay * min(1.0, recall_count / 5 + 0.2)

        return round(value, 3)

    def _is_duplicate(self, content: str, all_rows) -> bool:
        """检查是否重复内容"""
        content_lower = content.lower()
        for row in all_rows:
            other_content = row["content"].lower()
            # 简单的相似度检查
            if content_lower != other_content and content_lower[:50] == other_content[:50]:
                return True
        return False

    def compress_memory(self, candidate: MemoryCandidate) -> bool:
        """执行记忆压缩"""
        try:
            conn = sqlite3.connect(str(DB_PATH))

            if candidate.action == "delete":
                conn.execute("DELETE FROM auto_memories WHERE id = ?", (candidate.id,))
                self.compression_stats["deleted"] += 1
            elif candidate.action == "archive":
                conn.execute("""
                    UPDATE auto_memories SET is_active = 0
                    WHERE id = ?
                """, (candidate.id,))
                self.compression_stats["archived"] += 1
            elif candidate.action == "merge":
                # 合并到更高质量的记忆
                self._merge_memory(conn, candidate.id)
                self.compression_stats["merged"] += 1

            conn.commit()
            conn.close()
            self.compression_stats["compressed"] += 1
            return True

        except Exception as e:
            print(f"压缩记忆 {candidate.id} 时出错: {e}")
            return False

    def _merge_memory(self, conn, memory_id: str):
        """合并重复记忆"""
        # 获取记忆内容
        row = conn.execute("SELECT content FROM auto_memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return

        content = row["content"]
        content_prefix = content[:50]

        # 找到相似记忆
        similar = conn.execute("""
            SELECT id, recall_count, importance
            FROM auto_memories
            WHERE content LIKE ? AND id != ? AND is_active = 1
            ORDER BY recall_count DESC, importance DESC
            LIMIT 1
        """, (f"{content_prefix}%", memory_id)).fetchone()

        if similar:
            # 增加目标记忆的重要性
            conn.execute("""
                UPDATE auto_memories
                SET importance = MIN(1.0, importance + 0.1),
                    recall_count = recall_count + 1
                WHERE id = ?
            """, (similar["id"],))

            # 删除源记忆
            conn.execute("DELETE FROM auto_memories WHERE id = ?", (memory_id,))

    def run_compression(self, max_items: int = 50) -> dict:
        """运行压缩"""
        candidates = self.analyze_compression_candidates()

        processed = 0
        for candidate in candidates[:max_items]:
            if self.compress_memory(candidate):
                processed += 1

        self.compression_stats["total_processed"] = processed
        return self.compression_stats

# ── 规则自动优化 ─────────────────────────────────────────────────────────

class RuleOptimizer:
    """基于使用数据优化规则"""

    def __init__(self):
        self.optimization_suggestions = []

    def analyze_rules(self) -> List[RuleOptimization]:
        """分析规则并生成优化建议"""
        suggestions = []

        if not RULES_DIR.exists():
            return suggestions

        # 分析每个规则文件
        for rule_file in RULES_DIR.glob("*.md"):
            if rule_file.name == "README.md":
                continue

            rule_id = rule_file.stem
            content = rule_file.read_text(encoding="utf-8")

            # 分析规则质量
            issues = self._analyze_rule_quality(content)

            for issue in issues:
                suggestions.append(RuleOptimization(
                    rule_id=rule_id,
                    current_score=self._calculate_rule_score(content),
                    issue=issue["issue"],
                    suggestion=issue["suggestion"],
                    priority=issue["priority"]
                ))

        # 按优先级排序
        priority_order = {"high": 0, "medium": 1, "low": 2}
        suggestions.sort(key=lambda s: priority_order.get(s.priority, 3))

        self.optimization_suggestions = suggestions
        return suggestions

    def _analyze_rule_quality(self, content: str) -> List[dict]:
        """分析规则质量"""
        issues = []

        # 检查是否缺少必要部分
        if "## Trigger" not in content:
            issues.append({
                "issue": "缺少触发条件部分",
                "suggestion": "添加 '## Trigger' 部分定义何时应用此规则",
                "priority": "medium"
            })

        if "## Action" not in content and "##" not in content:
            issues.append({
                "issue": "缺少执行动作部分",
                "suggestion": "添加 '## Action' 部分定义具体执行步骤",
                "priority": "medium"
            })

        # 检查token效率
        lines = content.split("\n")
        total_chars = len(content)

        # 过于冗长
        if total_chars > 5000:
            issues.append({
                "issue": f"规则过于冗长 ({total_chars} 字符)",
                "suggestion": "精简规则内容，保留核心要点",
                "priority": "low"
            })

        # 检查是否有示例
        if "example" not in content.lower() and "示例" not in content:
            issues.append({
                "issue": "缺少使用示例",
                "suggestion": "添加具体示例说明规则如何应用",
                "priority": "low"
            })

        # 检查是否有清晰的领域定义
        if "## Domains" not in content and "领域" not in content:
            issues.append({
                "issue": "缺少领域定义",
                "suggestion": "添加 '## Domains' 部分定义规则适用领域",
                "priority": "low"
            })

        return issues

    def _calculate_rule_score(self, content: str) -> float:
        """计算规则质量分数"""
        score = 0.5  # 基础分

        # 结构完整性
        if "## Trigger" in content:
            score += 0.1
        if "## Action" in content or "##" in content:
            score += 0.1
        if "## Domains" in content or "领域" in content:
            score += 0.1

        # 示例
        if "example" in content.lower() or "示例" in content:
            score += 0.1

        # 适当的长度
        if 1000 < len(content) < 3000:
            score += 0.1

        return min(1.0, score)

    def generate_optimization_report(self) -> str:
        """生成优化报告"""
        suggestions = self.analyze_rules()

        if not suggestions:
            return "所有规则质量良好，无需优化。"

        report = "═══ 规则自动优化报告 ═══\n\n"
        report += f"分析时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        report += f"发现优化点: {len(suggestions)} 个\n\n"

        # 按优先级分组
        high_priority = [s for s in suggestions if s.priority == "high"]
        medium_priority = [s for s in suggestions if s.priority == "medium"]
        low_priority = [s for s in suggestions if s.priority == "low"]

        if high_priority:
            report += "── 高优先级 ──\n"
            for s in high_priority:
                report += f"[{s.rule_id}] {s.issue}\n"
                report += f"  建议: {s.suggestion}\n"
                report += f"  当前分数: {s.current_score:.2f}\n\n"

        if medium_priority:
            report += "── 中优先级 ──\n"
            for s in medium_priority[:5]:  # 只显示前5个
                report += f"[{s.rule_id}] {s.issue}\n"
                report += f"  建议: {s.suggestion}\n\n"

        if low_priority:
            report += f"── 低优先级 ({len(low_priority)} 个) ──\n"
            report += "可选择性优化，不影响核心功能。\n"

        return report

# ── 任务自动调度 ─────────────────────────────────────────────────────────

class TaskScheduler:
    """智能任务调度系统"""

    def __init__(self):
        self.tasks = []
        self.schedule = []

    def analyze_pending_tasks(self) -> List[TaskSchedule]:
        """分析待处理任务"""
        tasks = []

        # 从story.jsonl中提取未完成任务
        if STORY_FILE.exists():
            lines = STORY_FILE.read_text().strip().split("\n")
            for line in lines[-20:]:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("event") == "task_start":
                        task = self._extract_task(entry)
                        if task:
                            tasks.append(task)
                except Exception:
                    pass

        # 从active-tasks.json加载
        active_tasks_file = 鲤鱼_HOME / "active-tasks.json"
        if active_tasks_file.exists():
            try:
                active_tasks = json.loads(active_tasks_file.read_text())
                for task in active_tasks:
                    tasks.append(TaskSchedule(
                        task_id=task.get("id", ""),
                        description=task.get("description", ""),
                        priority=task.get("priority", 5),
                        estimated_time=task.get("estimated_time", 30),
                        dependencies=task.get("dependencies", []),
                        status=task.get("status", "pending")
                    ))
            except Exception:
                pass

        self.tasks = tasks
        return tasks

    def generate_schedule(self) -> List[TaskSchedule]:
        """生成优化的任务调度"""
        if not self.tasks:
            self.analyze_pending_tasks()

        # 按优先级排序
        sorted_tasks = sorted(self.tasks, key=lambda t: t.priority)

        # 解析依赖关系
        scheduled = []
        completed = set()

        for task in sorted_tasks:
            # 检查依赖是否满足
            deps_met = all(dep in completed for dep in task.dependencies)

            if deps_met:
                scheduled.append(task)
                completed.add(task.task_id)

        self.schedule = scheduled
        return scheduled

    def _extract_task(self, entry: dict) -> Optional[TaskSchedule]:
        """从story条目提取任务"""
        summary = entry.get("summary", "")

        # 简单的任务提取
        if "任务" in summary or "task" in summary.lower():
            return TaskSchedule(
                task_id=f"task-{entry.get('timestamp', '')[:10]}",
                description=summary[:100],
                priority=5,
                estimated_time=30,
                dependencies=[],
                status="pending"
            )

        return None

    def generate_schedule_report(self) -> str:
        """生成调度报告"""
        schedule = self.generate_schedule()

        if not schedule:
            return "没有待调度的任务。"

        report = "═══ 任务自动调度报告 ═══\n\n"
        report += f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        report += f"待处理任务: {len(schedule)} 个\n\n"

        report += "── 调度顺序 ──\n"
        for i, task in enumerate(schedule, 1):
            report += f"{i}. [{task.priority}] {task.description[:50]}\n"
            report += f"   预计时间: {task.estimated_time}分钟\n"
            if task.dependencies:
                report += f"   依赖: {', '.join(task.dependencies)}\n"
            report += "\n"

        return report

# ── 错误自动恢复 ─────────────────────────────────────────────────────────

class ErrorRecovery:
    """模式识别和自动修复系统"""

    def __init__(self):
        self.error_patterns = {}
        self.recovery_strategies = {}

    def analyze_error_patterns(self) -> Dict[str, Any]:
        """分析错误模式"""
        patterns = defaultdict(list)

        # 分析story.jsonl中的错误
        if STORY_FILE.exists():
            lines = STORY_FILE.read_text().strip().split("\n")
            for line in lines[-100:]:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("event") == "error" or "error" in entry.get("summary", "").lower():
                        error_type = self._classify_error(entry.get("summary", ""))
                        patterns[error_type].append(entry)
                except Exception:
                    pass

        # 分析reflection中的错误
        if REFLECTIONS_FILE.exists():
            lines = REFLECTIONS_FILE.read_text().strip().split("\n")
            for line in lines[-50:]:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    for lesson in entry.get("lessons", []):
                        if "错误" in lesson or "error" in lesson.lower():
                            error_type = self._classify_error(lesson)
                            patterns[error_type].append({"lesson": lesson})
                except Exception:
                    pass

        self.error_patterns = dict(patterns)
        return self.error_patterns

    def _classify_error(self, text: str) -> str:
        """分类错误类型"""
        text_lower = text.lower()

        if "permission" in text_lower or "权限" in text:
            return "permission_error"
        elif "timeout" in text_lower or "超时" in text:
            return "timeout_error"
        elif "syntax" in text_lower or "语法" in text:
            return "syntax_error"
        elif "import" in text_lower or "模块" in text:
            return "import_error"
        elif "type" in text_lower or "类型" in text:
            return "type_error"
        elif "connection" in text_lower or "连接" in text:
            return "connection_error"
        else:
            return "unknown_error"

    def suggest_recovery(self, error_type: str) -> List[str]:
        """建议恢复策略"""
        strategies = {
            "permission_error": [
                "检查文件权限设置",
                "使用适当的权限提升",
                "验证用户访问级别"
            ],
            "timeout_error": [
                "增加超时时间限制",
                "优化操作性能",
                "实现重试机制"
            ],
            "syntax_error": [
                "运行语法检查工具",
                "检查代码格式",
                "验证括号匹配"
            ],
            "import_error": [
                "检查模块安装",
                "验证导入路径",
                "检查Python环境"
            ],
            "type_error": [
                "检查类型注解",
                "验证参数类型",
                "添加类型检查"
            ],
            "connection_error": [
                "检查网络连接",
                "验证服务状态",
                "实现连接池"
            ]
        }

        return strategies.get(error_type, ["分析错误日志", "检查系统状态"])

    def generate_recovery_report(self) -> str:
        """生成恢复报告"""
        patterns = self.analyze_error_patterns()

        if not patterns:
            return "未发现明显的错误模式。"

        report = "═══ 错误自动恢复报告 ═══\n\n"
        report += f"分析时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        report += f"错误类型: {len(patterns)} 种\n\n"

        for error_type, errors in patterns.items():
            report += f"── {error_type} ({len(errors)}次) ──\n"

            # 建议恢复策略
            strategies = self.suggest_recovery(error_type)
            report += "恢复建议:\n"
            for strategy in strategies:
                report += f"  • {strategy}\n"

            # 最近的错误示例
            if errors:
                recent = errors[-1]
                summary = recent.get("summary", recent.get("lesson", ""))[:80]
                report += f"最近示例: {summary}...\n"

            report += "\n"

        return report

# ── 综合仪表盘 ─────────────────────────────────────────────────────────

class AutoEnhanceDashboard:
    """自动化增强系统综合仪表盘"""

    def __init__(self):
        self.skill_discovery = SkillDiscovery()
        self.memory_compressor = MemoryCompressor()
        self.rule_optimizer = RuleOptimizer()
        self.task_scheduler = TaskScheduler()
        self.error_recovery = ErrorRecovery()

    def generate_dashboard(self) -> str:
        """生成综合仪表盘"""
        dashboard = "╔══════════════════════════════════════════════════════════════╗\n"
        dashboard += "║         鲤鱼 Auto-Enhance Dashboard                     ║\n"
        dashboard += f"║         {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'):^50} ║\n"
        dashboard += "╚══════════════════════════════════════════════════════════════╝\n\n"

        # 1. 技能发现
        dashboard += "┌─────────────────────────────────────────────────────────────┐\n"
        dashboard += "│ 技能自动发现                                                │\n"
        dashboard += "└─────────────────────────────────────────────────────────────┘\n"
        gaps = self.skill_discovery.analyze_usage_patterns()
        if gaps:
            dashboard += f"发现 {len(gaps)} 个技能缺口\n"
            for gap in gaps[:3]:
                dashboard += f"  • {gap.pattern}: {gap.frequency}次使用\n"
        else:
            dashboard += "技能覆盖良好，无明显缺口\n"
        dashboard += "\n"

        # 2. 记忆压缩
        dashboard += "┌─────────────────────────────────────────────────────────────┐\n"
        dashboard += "│ 记忆自动压缩                                                │\n"
        dashboard += "└─────────────────────────────────────────────────────────────┘\n"
        candidates = self.memory_compressor.analyze_compression_candidates()
        if candidates:
            dashboard += f"发现 {len(candidates)} 个压缩候选\n"
            dashboard += f"  • 可删除: {sum(1 for c in candidates if c.action == 'delete')} 个\n"
            dashboard += f"  • 可归档: {sum(1 for c in candidates if c.action == 'archive')} 个\n"
            dashboard += f"  • 可合并: {sum(1 for c in candidates if c.action == 'merge')} 个\n"
        else:
            dashboard += "记忆状态良好，无需压缩\n"
        dashboard += "\n"

        # 3. 规则优化
        dashboard += "┌─────────────────────────────────────────────────────────────┐\n"
        dashboard += "│ 规则自动优化                                                │\n"
        dashboard += "└─────────────────────────────────────────────────────────────┘\n"
        suggestions = self.rule_optimizer.analyze_rules()
        if suggestions:
            high_priority = sum(1 for s in suggestions if s.priority == "high")
            dashboard += f"发现 {len(suggestions)} 个优化点 ({high_priority} 个高优先级)\n"
        else:
            dashboard += "规则质量良好，无需优化\n"
        dashboard += "\n"

        # 4. 任务调度
        dashboard += "┌─────────────────────────────────────────────────────────────┐\n"
        dashboard += "│ 任务自动调度                                                │\n"
        dashboard += "└─────────────────────────────────────────────────────────────┘\n"
        tasks = self.task_scheduler.analyze_pending_tasks()
        if tasks:
            dashboard += f"待处理任务: {len(tasks)} 个\n"
            for task in tasks[:3]:
                dashboard += f"  • [{task.priority}] {task.description[:40]}\n"
        else:
            dashboard += "没有待调度的任务\n"
        dashboard += "\n"

        # 5. 错误恢复
        dashboard += "┌─────────────────────────────────────────────────────────────┐\n"
        dashboard += "│ 错误自动恢复                                                │\n"
        dashboard += "└─────────────────────────────────────────────────────────────┘\n"
        patterns = self.error_recovery.analyze_error_patterns()
        if patterns:
            total_errors = sum(len(errors) for errors in patterns.values())
            dashboard += f"发现 {total_errors} 个错误，{len(patterns)} 种类型\n"
            for error_type, errors in list(patterns.items())[:3]:
                dashboard += f"  • {error_type}: {len(errors)}次\n"
        else:
            dashboard += "系统运行良好，无明显错误模式\n"

        return dashboard

    def run_all_enhancements(self) -> dict:
        """运行所有增强功能"""
        results = {}

        # 1. 技能发现
        gaps = self.skill_discovery.analyze_usage_patterns()
        results["skill_gaps"] = len(gaps)

        # 2. 记忆压缩
        compression_stats = self.memory_compressor.run_compression()
        results["memory_compression"] = compression_stats

        # 3. 规则优化
        suggestions = self.rule_optimizer.analyze_rules()
        results["rule_optimizations"] = len(suggestions)

        # 4. 任务调度
        schedule = self.task_scheduler.generate_schedule()
        results["scheduled_tasks"] = len(schedule)

        # 5. 错误恢复
        patterns = self.error_recovery.analyze_error_patterns()
        results["error_patterns"] = len(patterns)

        return results

# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    dashboard = AutoEnhanceDashboard()

    if cmd == "discover":
        print(dashboard.skill_discovery.generate_discovery_report())

    elif cmd == "compress":
        stats = dashboard.memory_compressor.run_compression()
        print("═══ 记忆压缩完成 ═══")
        print(f"处理数量: {stats['total_processed']}")
        print(f"删除: {stats['deleted']}")
        print(f"归档: {stats['archived']}")
        print(f"合并: {stats['merged']}")

    elif cmd == "optimize-rules":
        print(dashboard.rule_optimizer.generate_optimization_report())

    elif cmd == "schedule":
        print(dashboard.task_scheduler.generate_schedule_report())

    elif cmd == "recover":
        print(dashboard.error_recovery.generate_recovery_report())

    elif cmd == "dashboard":
        print(dashboard.generate_dashboard())

    elif cmd == "auto":
        print("运行所有自动增强功能...\n")
        results = dashboard.run_all_enhancements()
        print("═══ 自动增强完成 ═══")
        print(f"技能缺口: {results['skill_gaps']}")
        print(f"记忆压缩: {results['memory_compression']['compressed']}")
        print(f"规则优化: {results['rule_optimizations']}")
        print(f"任务调度: {results['scheduled_tasks']}")
        print(f"错误模式: {results['error_patterns']}")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
