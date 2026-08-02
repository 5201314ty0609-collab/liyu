#!/usr/bin/env python3
"""
鲤鱼 Identity Drift Detection — 检测 "robot mode" 行为信号
吸收自 Claude Soul v0.2.5 的 identity drift detection 机制

检测三种偏离模式:
  1. Robot Mode — 过于机械、缺乏个性、模板化回复
  2. Authenticity Loss — 失去真实感、过度正式
  3. Premature Done — 过早结束、不完整

Usage:
  liyu-identity-drift.py check "<response_text>"
    检查回复是否出现 identity drift

  liyu-identity-drift.py stats
    查看 drift 统计

  liyu-identity-drift.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import re
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DRIFT_STATE_FILE = 鲤鱼_HOME / "identity-drift-state.json"
DRIFT_LOG_FILE = 鲤鱼_HOME / "identity-drift-log.jsonl"

# ── Drift Detection Patterns ──────────────────────────────────────────────

@dataclass
class DriftSignal:
    """Drift 检测信号"""
    drift_type: str     # robot_mode / authenticity_loss / premature_done
    severity: str       # LOW / MEDIUM / HIGH
    description: str    # 描述
    evidence: str       # 证据
    recommendation: str # 建议

# ── Robot Mode 检测 ──────────────────────────────────────────────────────

ROBOT_MODE_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, severity, description, recommendation)

    # 过度使用技术术语
    (r'(?:utilize|implement|facilitate|leverage|optimize|streamline)\s+(?:the|this|that)',
     "MEDIUM", "Overuse of corporate jargon", "Use simpler, more direct language"),

    # 模板化开头
    (r'^(?:I understand|I see|Certainly|Of course|Absolutely|Sure thing)',
     "LOW", "Template opening detected", "Start with more natural phrasing"),

    # 过度道歉
    (r'(?:I apologize|I\'m sorry|Sorry)\s+(?:for|about|that)',
     "MEDIUM", "Excessive apologizing", "Be more confident in responses"),

    # 机械列举
    (r'(?:Here are|The following are|Below are)\s+\d+\s+(?:steps|ways|methods|approaches)',
     "LOW", "Mechanical listing pattern", "Integrate list naturally into response"),

    # 过度使用 "please"
    (r'(?:please|kindly)\s+(?:note|be aware|understand|consider)',
     "LOW", "Overly formal 'please' usage", "Be more direct"),

    # 缺乏个性
    (r'(?:As an AI|As a language model|I\'m an AI|I am an AI)',
     "HIGH", "AI self-identification", "Avoid AI self-references"),

    # 过度使用被动语态
    (r'(?:It is|It\'s)\s+(?:important|worth|noteworthy)\s+(?:to note|mentioning|that)',
     "LOW", "Passive voice pattern", "Use active voice"),

    # 模板化结尾
    (r'(?:Let me know if|Feel free to|Don\'t hesitate to|If you have any)',
     "LOW", "Template closing detected", "End with more natural phrasing"),

    # 过度使用 "however"
    (r'(?:However|Nevertheless|Nonetheless|That said),',
     "LOW", "Overuse of transition words", "Vary transitions"),

    # 机械总结
    (r'(?:In summary|To summarize|In conclusion|To sum up)',
     "LOW", "Mechanical summarizing", "Integrate summary naturally"),
]

# ── Authenticity Loss 检测 ──────────────────────────────────────────────

AUTHENTICITY_LOSS_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, severity, description, recommendation)

    # 过度正式
    (r'(?:I would like to|I wish to|I desire to)\s+(?:inform|notify|advise)',
     "MEDIUM", "Overly formal language", "Use more casual tone"),

    # 缺乏情感
    (r'(?:The task has been|The request has been|The operation was)\s+(?:completed|finished|done)',
     "HIGH", "Emotionless completion statement", "Add warmth to completion"),

    # 过度谨慎
    (r'(?:Please note that|It should be noted|It is important to note)',
     "MEDIUM", "Overly cautious language", "Be more confident"),

    # 缺乏个性
    (r'(?:I have|I\'ve)\s+(?:completed|finished|done)\s+(?:the|your)\s+(?:task|request)',
     "HIGH", "Generic completion statement", "Personalize completion"),
]

# ── Premature Done 检测 ──────────────────────────────────────────────────

PREMATURE_DONE_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, severity, description, recommendation)

    # 过早结束
    (r'(?:That\'s all|That is all|That\'s it|That is it)\s*$',
     "HIGH", "Premature ending", "Continue with more detail"),

    # 不完整回复
    (r'(?:...|…)\s*$',
     "MEDIUM", "Trailing ellipsis suggests incompleteness", "Complete the thought"),

    # 跳过步骤
    (r'(?:I\'ll skip|Let\'s skip|We can skip)\s+(?:the|this|that)',
     "HIGH", "Skipping steps", "Complete all steps"),

    # 快速结束
    (r'(?:That should be|This should be|That\'s)\s+(?:enough|sufficient|good enough)',
     "MEDIUM", "Premature satisfaction", "Continue until complete"),
]

# ── 检测引擎 ──────────────────────────────────────────────────────────────

def detect_drift(text: str) -> list[DriftSignal]:
    """检测文本中的 identity drift 信号"""
    signals = []

    # Robot Mode 检测
    for pattern, severity, description, recommendation in ROBOT_MODE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            signals.append(DriftSignal(
                drift_type="robot_mode",
                severity=severity,
                description=description,
                evidence=pattern[:50] + "...",
                recommendation=recommendation,
            ))

    # Authenticity Loss 检测
    for pattern, severity, description, recommendation in AUTHENTICITY_LOSS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            signals.append(DriftSignal(
                drift_type="authenticity_loss",
                severity=severity,
                description=description,
                evidence=pattern[:50] + "...",
                recommendation=recommendation,
            ))

    # Premature Done 检测
    for pattern, severity, description, recommendation in PREMATURE_DONE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            signals.append(DriftSignal(
                drift_type="premature_done",
                severity=severity,
                description=description,
                evidence=pattern[:50] + "...",
                recommendation=recommendation,
            ))

    return signals


# ── State Management ──────────────────────────────────────────────────────

def load_state() -> dict:
    """加载 drift 状态"""
    if DRIFT_STATE_FILE.exists():
        try:
            return json.loads(DRIFT_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_checks": 0,
        "drifts_detected": 0,
        "by_type": {"robot_mode": 0, "authenticity_loss": 0, "premature_done": 0},
        "by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "trend": [],  # 最近 N 次检查的 drift 数量
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state: dict) -> None:
    """持久化 drift 状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    DRIFT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def log_drift(signals: list[DriftSignal], text_preview: str) -> None:
    """记录 drift 到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "text_preview": text_preview[:200],
        "signals": [
            {
                "type": s.drift_type,
                "severity": s.severity,
                "description": s.description,
                "recommendation": s.recommendation,
            }
            for s in signals
        ],
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(DRIFT_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[identity-drift] Warning: log write failed: {e}", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: liyu-identity-drift.py check '<text>'", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        signals = detect_drift(text)
        state = load_state()
        state["total_checks"] += 1

        # 更新趋势
        state["trend"].append(len(signals))
        if len(state["trend"]) > 20:
            state["trend"] = state["trend"][-20:]

        if signals:
            state["drifts_detected"] += 1
            for s in signals:
                state["by_type"][s.drift_type] = state["by_type"].get(s.drift_type, 0) + 1
                state["by_severity"][s.severity] = state["by_severity"].get(s.severity, 0) + 1

            log_drift(signals, text)
            save_state(state)

            print("🚨 IDENTITY DRIFT DETECTED:")
            for s in signals:
                severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
                print(f"  {severity_icon.get(s.severity, '⚪')} [{s.severity}] {s.drift_type}: {s.description}")
                print(f"    Evidence: {s.evidence}")
                print(f"    Recommendation: {s.recommendation}")
            sys.exit(2)
        else:
            save_state(state)
            print("✅ No identity drift detected")
            sys.exit(0)

    elif cmd == "stats":
        state = load_state()
        print("═══ 鲤鱼 Identity Drift Statistics ═══")
        print(f"  总计检查:     {state.get('total_checks', 0)}")
        print(f"  Drift 检测:   {state.get('drifts_detected', 0)}")
        print()
        print("  按类型:")
        for dtype, count in state.get("by_type", {}).items():
            if count > 0:
                print(f"    {dtype}: {count}")
        print()
        print("  按严重性:")
        for sev, count in state.get("by_severity", {}).items():
            if count > 0:
                print(f"    {sev}: {count}")
        print()
        trend = state.get("trend", [])
        if trend:
            avg = sum(trend) / len(trend)
            print(f"  趋势 (最近 {len(trend)} 次): 平均 {avg:.1f} drifts/check")
            if avg > 2:
                print("  ⚠️ 高 drift 率 — 可能正在进入 robot mode")
            elif avg < 0.5:
                print("  ✅ 低 drift 率 — 保持良好个性")

    elif cmd == "reset":
        save_state({
            "total_checks": 0,
            "drifts_detected": 0,
            "by_type": {"robot_mode": 0, "authenticity_loss": 0, "premature_done": 0},
            "by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "trend": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Identity Drift 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
