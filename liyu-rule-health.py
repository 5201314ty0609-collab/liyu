#!/usr/bin/env python3
"""
鲤鱼 Rule Health — DSPy-style Rule Optimization.
吸收自 DSPy 的 prompt-as-optimization-target 模式。

将 鲤鱼 rules 视为可优化目标，分析其有效性：
- 使用频率
- 成功率关联
- Token 成本
- 衰减/过时检测

Usage:
  python3 liyu-rule-health.py report            生成规则健康报告
  python3 liyu-rule-health.py score <rule-id>   查看单个规则评分
  python3 liyu-rule-health.py optimize           标记需要优化的规则
  python3 liyu-rule-health.py dashboard          规则健康仪表盘（文本）
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import math
import re
import sys

# ── 路径 ─────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
RULES_DIR = Path.home() / ".claude" / "rules" / "liyu"
STORY_FILE = 鲤鱼_HOME / "story.jsonl"
FRAMEWORKS_DIR = 鲤鱼_HOME / "frameworks"

# ── 数据类 ───────────────────────────────────────────────────────────────

@dataclass
class RuleHealth:
    """规则健康状态"""
    rule_id: str
    rule_name: str
    file_path: str
    stage: str                      # active/observed/validated/hardened
    enforcement_level: int          # 1-7
    usage_count: int = 0             # 被引用的次数
    success_correlation: float = 0.0 # 与成功会话的相关性
    estimated_token_cost: int = 0    # 估算 token 成本
    last_used: str = ""              # 最后使用时间
    days_since_last_use: int = 999
    optimization_score: float = 0.0  # 综合优化分数
    needs_attention: bool = False    # 是否需要关注
    flags: List[str] = field(default_factory=list)


# ── 分析引擎 ─────────────────────────────────────────────────────────────

def analyze_story_references() -> Dict[str, Dict]:
    """
    分析 story.jsonl 中每条记录对各规则的引用情况。
    返回 {rule_id: {usage_count, success_count, last_used}}
    """
    rule_patterns = _load_rule_patterns()
    stats = defaultdict(lambda: {
        "usage_count": 0,
        "success_count": 0,
        "total_sessions": 0,
        "last_used": "",
        "sessions_with_rule": set(),
    })

    if not STORY_FILE.exists():
        return stats

    lines = STORY_FILE.read_text().strip().split("\n")

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue

        timestamp = entry.get("timestamp", "")
        summary = entry.get("summary", "")
        event = entry.get("event", "")

        # 检查每条规则的引用
        for rule_id, patterns in rule_patterns.items():
            referenced = False
            for pattern in patterns:
                if pattern.lower() in summary.lower():
                    referenced = True
                    break

            if referenced:
                stats[rule_id]["usage_count"] += 1
                stats[rule_id]["sessions_with_rule"].add(timestamp[:10])
                if timestamp > stats[rule_id]["last_used"]:
                    stats[rule_id]["last_used"] = timestamp

        # 检查是否成功会话
        if event == "session_end":
            for rule_id in rule_patterns:
                stats[rule_id]["total_sessions"] += 1

    # 清理 set
    result = {}
    for rule_id, data in stats.items():
        result[rule_id] = {
            "usage_count": data["usage_count"],
            "success_correlation": _calc_correlation(
                data["usage_count"],
                data["total_sessions"],
            ),
            "last_used": data["last_used"],
            "days_since_last_use": _days_since(data["last_used"]),
        }

    return result


def _load_rule_patterns() -> Dict[str, List[str]]:
    """从 rule 文件中提取关键短语作为匹配模式"""
    patterns = {}

    if not RULES_DIR.exists():
        return patterns

    for rule_file in RULES_DIR.glob("*.md"):
        if rule_file.name == "README.md":
            continue

        rule_id = rule_file.stem
        content = rule_file.read_text(encoding="utf-8")

        # 提取规则中的关键概念
        phrases = []

        # 从 Trigger 部分提取
        trigger_match = re.search(r'## Trigger\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
        if trigger_match:
            phrases.extend(_extract_key_phrases(trigger_match.group(1)))

        # 从标题提取
        title_match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
        if title_match:
            phrases.extend(_extract_key_phrases(title_match.group(1)))

        # 从 Domains 提取
        domains_match = re.search(r'## Domains\s*\n(.+?)(?:\n##|\Z)', content, re.DOTALL)
        if domains_match:
            phrases.extend(_extract_key_phrases(domains_match.group(1)))

        patterns[rule_id] = list(set(phrases))

    return patterns


def _extract_key_phrases(text: str) -> List[str]:
    """提取关键短语"""
    phrases = []
    # 提取英文单词序列（3+ 字符）
    words = re.findall(r'\b[a-zA-Z][a-zA-Z-]{2,}\b', text)
    for i in range(len(words)):
        if len(words[i]) >= 4:
            phrases.append(words[i])
        if i < len(words) - 1:
            phrases.append(f"{words[i]} {words[i+1]}")
    return phrases


def _calc_correlation(usage: int, total: int) -> float:
    """简单相关性：使用频率 / 总会话数的比值"""
    if total == 0:
        return 0.0
    return round(min(usage / max(total, 1), 1.0), 3)


def _days_since(timestamp: str) -> int:
    """计算距离最后使用日期的天数"""
    if not timestamp:
        return 999
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except Exception:
        return 999


# ── Token 成本估算 ───────────────────────────────────────────────────────

def estimate_token_cost(rule_file: Path) -> int:
    """估算规则文件的 token 成本"""
    if not rule_file.exists():
        return 0

    content = rule_file.read_text(encoding="utf-8")
    # 粗略估算：~1.3 tokens per word for Chinese, ~0.75 for English
    chinese_chars = len(re.findall(r'[一-鿿]', content))
    english_words = len(re.findall(r'[a-zA-Z]+', content))
    tokens = int(chinese_chars * 1.3 + english_words * 0.75)
    return tokens


# ── 优化评分 ─────────────────────────────────────────────────────────────

def compute_optimization_score(usage_count: int, success_corr: float,
                                token_cost: int, days_since: int,
                                stage: str) -> Tuple[float, List[str]]:
    """
    DSPy-style 优化评分。
    effectiveness = times_used × success_rate / token_cost
    值越低 → 越需要优化或淘汰

    返回 (score, flags)
    """
    flags = []

    # 基础有效性
    if token_cost > 0:
        effectiveness = (usage_count + 1) * (success_corr + 0.1) / (token_cost / 100)
    else:
        effectiveness = (usage_count + 1) * (success_corr + 0.1)

    # 时效衰减
    if days_since > 60:
        effectiveness *= 0.5
        flags.append("STALE: >60 days unused")
    elif days_since > 30:
        effectiveness *= 0.7
        flags.append("AGING: >30 days unused")

    # 从未使用的规则
    if usage_count == 0:
        effectiveness *= 0.3
        flags.append("UNUSED: never referenced")

    # Stage 惩罚（active 阶段不成熟）
    if stage == "active":
        effectiveness *= 0.6
        flags.append("IMMATURE: still in active stage")

    # Token 成本过高
    if token_cost > 3000:
        effectiveness *= 0.7
        flags.append(f"HEAVY: {token_cost} tokens estimated")

    score = round(effectiveness, 4)
    return score, flags


# ── 报告生成 ─────────────────────────────────────────────────────────────

def generate_report():
    """生成完整的规则健康报告"""
    story_stats = analyze_story_references()

    if not RULES_DIR.exists():
        print("No 鲤鱼 rules directory found.")
        return

    rules = []
    for rule_file in sorted(RULES_DIR.glob("*.md")):
        if rule_file.name == "README.md":
            continue

        rule_id = rule_file.stem
        content = rule_file.read_text(encoding="utf-8")

        # 提取元数据
        name = _extract_title(content) or rule_id
        stage = _extract_stage(content) or "active"
        enforcement = _extract_enforcement(content) or 1

        # 获取统计数据
        stats = story_stats.get(rule_id, {
            "usage_count": 0,
            "success_correlation": 0.0,
            "last_used": "",
            "days_since_last_use": 999,
        })

        token_cost = estimate_token_cost(rule_file)

        # 计算优化分数
        opt_score, flags = compute_optimization_score(
            usage_count=stats["usage_count"],
            success_corr=stats["success_correlation"],
            token_cost=token_cost,
            days_since=stats["days_since_last_use"],
            stage=stage,
        )

        rules.append(RuleHealth(
            rule_id=rule_id,
            rule_name=name,
            file_path=str(rule_file),
            stage=stage,
            enforcement_level=enforcement,
            usage_count=stats["usage_count"],
            success_correlation=stats["success_correlation"],
            estimated_token_cost=token_cost,
            last_used=stats["last_used"],
            days_since_last_use=stats["days_since_last_use"],
            optimization_score=opt_score,
            needs_attention=opt_score < 0.5 or len(flags) >= 2,
            flags=flags,
        ))

    # 按优化分数排序（越低越需要关注）
    rules.sort(key=lambda r: r.optimization_score)

    # ── 输出报告 ──────────────────────────────────────────────────────────
    print("=" * 72)
    print("  鲤鱼 Rule Health Dashboard")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Rules analyzed: {len(rules)}")
    print("=" * 72)
    print()

    # 摘要
    healthy = sum(1 for r in rules if r.optimization_score >= 0.5 and not r.needs_attention)
    warning = sum(1 for r in rules if r.needs_attention and r.optimization_score >= 0.2)
    critical = sum(1 for r in rules if r.optimization_score < 0.2)

    print(f"Summary: {healthy} healthy | {warning} need review | {critical} critical")
    print()

    # 需要关注的规则
    attention_rules = [r for r in rules if r.needs_attention]
    if attention_rules:
        print("── Rules Needing Attention ──")
        print(f"{'Score':>7}  {'Rule':30s}  {'Stage':12s}  Flags")
        print("-" * 72)
        for r in attention_rules:
            stage_icon = {"hardened": "✦", "validated": "●", "observed": "○", "active": "·"}.get(r.stage, "?")
            print(f"{r.optimization_score:7.4f}  {r.rule_id:30s}  {stage_icon} {r.stage:10s}  {', '.join(r.flags[:2])}")
        print()

    # 全部规则排名
    print("── Full Rule Ranking ──")
    print(f"{'Rank':>4}  {'Score':>7}  {'Rule':30s}  {'Use':>5}  {'Stage':10s}  {'Tokens':>6}  {'Days':>5}")
    print("-" * 85)
    for i, r in enumerate(rules, 1):
        status = "⚠" if r.needs_attention else "✓"
        print(f"{i:4d}  {r.optimization_score:7.4f}  {r.rule_id:30s}  {r.usage_count:5d}  {r.stage:10s}  {r.estimated_token_cost:6d}  {r.days_since_last_use:5d}  {status}")

    print()
    print("── Legend ──")
    print("Score = (usage × success_rate) / token_cost  → 越高越好")
    print("Stage: ✦ hardened  ● validated  ○ observed  · active")
    print("✓ healthy  ⚠ needs review")

    return rules


def _extract_title(content: str) -> str:
    """从 Markdown 提取标题"""
    match = re.search(r'^#\s+(.+?)(?:\s*—|$)', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_stage(content: str) -> str:
    """从 Markdown 提取 stage"""
    match = re.search(r'Stage:\s*(\w+)', content)
    if match:
        return match.group(1)
    return ""


def _extract_enforcement(content: str) -> int:
    """从 Markdown 提取 enforcement level"""
    match = re.search(r'Enforcement:\s*rule file \(Level (\d)\)', content)
    if match:
        return int(match.group(1))
    return 1


# ── 单项评分 ─────────────────────────────────────────────────────────────

def score_single(rule_id: str):
    """查看单个规则的详细评分"""
    rule_file = RULES_DIR / f"{rule_id}.md"
    if not rule_file.exists():
        print(f"Rule not found: {rule_id}")
        return

    story_stats = analyze_story_references()
    content = rule_file.read_text(encoding="utf-8")
    stats = story_stats.get(rule_id, {
        "usage_count": 0,
        "success_correlation": 0.0,
        "last_used": "",
        "days_since_last_use": 999,
    })

    token_cost = estimate_token_cost(rule_file)
    stage = _extract_stage(content) or "active"

    opt_score, flags = compute_optimization_score(
        usage_count=stats["usage_count"],
        success_corr=stats["success_correlation"],
        token_cost=token_cost,
        days_since=stats["days_since_last_use"],
        stage=stage,
    )

    print(f"Rule: {rule_id}")
    print(f"Name: {_extract_title(content) or rule_id}")
    print(f"Stage: {stage}")
    print(f"Enforcement: Level {_extract_enforcement(content) or 1}")
    print()
    print(f"Usage count: {stats['usage_count']}")
    print(f"Success correlation: {stats['success_correlation']}")
    print(f"Estimated token cost: {token_cost}")
    print(f"Last used: {stats['last_used'] or 'never'}")
    print(f"Days since last use: {stats['days_since_last_use']}")
    print()
    print(f"Optimization Score: {opt_score:.4f}")
    if flags:
        print(f"Flags: {', '.join(flags)}")
    else:
        print("Flags: none — rule is healthy")


# ── 优化建议 ─────────────────────────────────────────────────────────────

def optimize():
    """标记需要优化的规则并给出建议"""
    rules = generate_report()

    attention = [r for r in rules if r.needs_attention]
    if not attention:
        print("\nAll rules are healthy. No optimization needed.")
        return

    print(f"\n{'=' * 72}")
    print("  Optimization Recommendations")
    print(f"{'=' * 72}")
    print()

    for r in attention:
        print(f"[{r.rule_id}]")
        recommendations = []

        if "UNUSED" in str(r.flags):
            recommendations.append("Consider retiring or testing in a shadow session")
        if "STALE" in str(r.flags):
            recommendations.append("Verify rule is still relevant to current workflow")
        if "HEAVY" in str(r.flags):
            recommendations.append("Consider simplifying rule text to reduce token cost")
        if "IMMATURE" in str(r.flags):
            recommendations.append("Needs more observations to validate — run shadow tests")

        for rec in recommendations:
            print(f"  → {rec}")
        print(f"  Score: {r.optimization_score:.4f} | Tokens: {r.estimated_token_cost} | Last used: {r.days_since_last_use}d ago")
        print()


# ── 框架健康 ─────────────────────────────────────────────────────────────

def analyze_frameworks():
    """分析 active frameworks 的健康状态"""
    active_dir = FRAMEWORKS_DIR / "active"
    if not active_dir.exists():
        print("No active frameworks found.")
        return []

    results = []
    for fw_file in sorted(active_dir.glob("*.json")):
        try:
            fw = json.loads(fw_file.read_text())
        except Exception:
            continue

        fw_id = fw.get("id", fw_file.stem)
        observations = fw.get("observations", 0)
        successes = fw.get("successes", 0)
        failures = fw.get("failures", 0)
        confidence = fw.get("confidence", 0.0)
        stage = fw.get("stage", "active")

        # 成功率
        total = successes + failures
        success_rate = successes / max(total, 1)

        # 框架健康分
        health = (observations / 10) * (success_rate) * (confidence + 0.1)

        results.append({
            "id": fw_id,
            "stage": stage,
            "observations": observations,
            "success_rate": round(success_rate, 3),
            "confidence": confidence,
            "health": round(health, 4),
        })

    results.sort(key=lambda r: r["health"])

    print("\n── Active Framework Health ──")
    print(f"{'Health':>7}  {'ID':35s}  {'Obs':>5}  {'Rate':>6}  {'Conf':>5}")
    print("-" * 70)
    for r in results:
        status = "⚠" if r["health"] < 0.3 else "·"
        print(f"{r['health']:7.4f}  {r['id']:35s}  {r['observations']:5d}  {r['success_rate']:6.3f}  {r['confidence']:5.2f}  {status}")

    return results


# ── 仪表盘 ───────────────────────────────────────────────────────────────

def dashboard():
    """综合仪表盘"""
    print("=" * 72)
    print("  鲤鱼 Rule Health Dashboard")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 72)

    # 规则健康
    rules = generate_report()
    print()

    # 框架健康
    analyze_frameworks()
    print()

    # 整体指标
    total_rules = len(rules)
    healthy = sum(1 for r in rules if not r.needs_attention)
    total_usage = sum(r.usage_count for r in rules)
    total_tokens = sum(r.estimated_token_cost for r in rules)
    avg_score = sum(r.optimization_score for r in rules) / max(total_rules, 1)

    print("── Overall Metrics ──")
    print(f"Total rules:      {total_rules}")
    print(f"Healthy:          {healthy}/{total_rules} ({100*healthy//max(total_rules,1)}%)")
    print(f"Total usage:      {total_usage}")
    print(f"Total tokens:     {total_tokens:,}")
    print(f"Average score:    {avg_score:.4f}")
    print(f"System health:    {'GOOD' if healthy/total_rules > 0.6 else 'NEEDS ATTENTION'}")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "report":
        generate_report()

    elif cmd == "score":
        if len(sys.argv) < 3:
            print("Usage: python3 liyu-rule-health.py score <rule-id>")
            return
        score_single(sys.argv[2])

    elif cmd == "optimize":
        optimize()

    elif cmd == "dashboard":
        dashboard()

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
