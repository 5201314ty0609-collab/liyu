#!/usr/bin/env python3
"""
鲤鱼 Correction Lifecycle — 自动检测和跟踪纠正模式
吸收自 Claude Soul v0.2.5 的 behavioral correction lifecycle

纠正模式生命周期:
  new → active → improving → internalized

检测的纠正模式:
  1. premature_done — 过早结束任务
  2. robot_mode — 进入机器人模式
  3. authenticity — 失去真实性
  4. scope_creep — 范围蔓延
  5. error_repeat — 重复错误

Usage:
  liyu-correction-lifecycle.py detect "<text>"
    检测文本中的纠正模式

  liyu-correction-lifecycle.py list
    列出所有纠正模式

  liyu-correction-lifecycle.py update <pattern_id> <new_status>
    更新纠正模式状态

  liyu-correction-lifecycle.py stats
    查看纠正统计

  liyu-correction-lifecycle.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import re
import sys
import uuid

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
CORRECTIONS_FILE = 鲤鱼_HOME / "corrections.json"
CORRECTION_LOG_FILE = 鲤鱼_HOME / "correction-log.jsonl"

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class CorrectionPattern:
    """纠正模式"""
    pattern_id: str
    pattern_type: str     # premature_done / robot_mode / authenticity / scope_creep / error_repeat
    status: str           # new / active / improving / internalized
    first_detected: str
    last_detected: str
    detection_count: int
    trend: str            # ↑ / ↓ / → / 🆕
    description: str
    evidence: list = field(default_factory=list)
    resolution: str = ""

# ── 纠正模式检测 ──────────────────────────────────────────────────────────

CORRECTION_PATTERNS: list[tuple[str, str, str, str]] = [
    # (pattern, pattern_type, severity, description)

    # Premature Done
    (r'(?:That\'s all|That is all|That\'s it|That is it)\s*$',
     "premature_done", "HIGH", "Premature ending — task not fully completed"),

    (r'(?:I\'ll skip|Let\'s skip|We can skip)\s+(?:the|this|that)',
     "premature_done", "HIGH", "Skipping steps — incomplete execution"),

    (r'(?:That should be|This should be|That\'s)\s+(?:enough|sufficient|good enough)',
     "premature_done", "MEDIUM", "Premature satisfaction — may need more work"),

    # Robot Mode
    (r'(?:As an AI|As a language model|I\'m an AI|I am an AI)',
     "robot_mode", "HIGH", "AI self-identification — breaking character"),

    (r'(?:I understand|I see|Certainly|Of course|Absolutely|Sure thing)',
     "robot_mode", "LOW", "Template opening — mechanical response"),

    (r'(?:Let me know if|Feel free to|Don\'t hesitate to|If you have any)',
     "robot_mode", "LOW", "Template closing — mechanical response"),

    # Authenticity
    (r'(?:I would like to|I wish to|I desire to)\s+(?:inform|notify|advise)',
     "authenticity", "MEDIUM", "Overly formal language — losing naturalness"),

    (r'(?:The task has been|The request has been|The operation was)\s+(?:completed|finished|done)',
     "authenticity", "HIGH", "Emotionless completion — lacking warmth"),

    # Scope Creep
    (r'(?:While we\'re at it|We could also|It would be nice to|Let\'s also)',
     "scope_creep", "MEDIUM", "Scope expansion — staying focused"),

    (r'(?:Let me also|I\'ll also|We should also)\s+(?:add|include|consider)',
     "scope_creep", "LOW", "Additional work — may be scope creep"),

    # Error Repeat
    (r'(?:I made a mistake|I apologize|Sorry|I was wrong)',
     "error_repeat", "MEDIUM", "Error acknowledgment — learning opportunity"),

    (r'(?:Let me try again|I\'ll retry|Let me redo)',
     "error_repeat", "MEDIUM", "Retry attempt — may indicate repeated error"),
]

# ── 检测引擎 ──────────────────────────────────────────────────────────────

def detect_corrections(text: str) -> list[tuple[str, str, str]]:
    """检测文本中的纠正模式，返回 (pattern_type, severity, description)"""
    detected = []

    for pattern, pattern_type, severity, description in CORRECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            detected.append((pattern_type, severity, description))

    return detected


def load_corrections() -> dict:
    """加载纠正模式"""
    if CORRECTIONS_FILE.exists():
        try:
            return json.loads(CORRECTIONS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "patterns": {},
        "stats": {
            "total_detections": 0,
            "by_type": {},
            "by_status": {"new": 0, "active": 0, "improving": 0, "internalized": 0},
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_corrections(data: dict) -> None:
    """持久化纠正模式"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    CORRECTIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def update_pattern(data: dict, pattern_type: str, severity: str, description: str) -> None:
    """更新或创建纠正模式"""
    now = datetime.now(timezone.utc).isoformat()

    # 查找现有模式
    pattern_id = None
    for pid, p in data["patterns"].items():
        if p["pattern_type"] == pattern_type:
            pattern_id = pid
            break

    if pattern_id:
        # 更新现有模式
        pattern = data["patterns"][pattern_id]
        pattern["last_detected"] = now
        pattern["detection_count"] += 1
        pattern["evidence"].append(now)
        if len(pattern["evidence"]) > 10:
            pattern["evidence"] = pattern["evidence"][-10:]

        # 更新趋势
        if pattern["detection_count"] >= 5:
            pattern["trend"] = "↑"  # 频繁出现
        elif pattern["detection_count"] >= 3:
            pattern["trend"] = "→"  # 稳定
        else:
            pattern["trend"] = "↓"  # 减少

        # 自动状态转换
        if pattern["status"] == "new" and pattern["detection_count"] >= 2:
            pattern["status"] = "active"
        elif pattern["status"] == "active" and pattern["detection_count"] >= 5:
            pattern["status"] = "improving"
    else:
        # 创建新模式
        pattern_id = f"corr-{uuid.uuid4().hex[:8]}"
        data["patterns"][pattern_id] = {
            "pattern_id": pattern_id,
            "pattern_type": pattern_type,
            "status": "new",
            "first_detected": now,
            "last_detected": now,
            "detection_count": 1,
            "trend": "🆕",
            "description": description,
            "evidence": [now],
            "resolution": "",
        }

    # 更新统计
    data["stats"]["total_detections"] += 1
    data["stats"]["by_type"][pattern_type] = data["stats"]["by_type"].get(pattern_type, 0) + 1

    # 重新计算状态统计
    by_status = {"new": 0, "active": 0, "improving": 0, "internalized": 0}
    for p in data["patterns"].values():
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    data["stats"]["by_status"] = by_status


def log_correction(pattern_type: str, severity: str, description: str, text_preview: str) -> None:
    """记录纠正到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pattern_type": pattern_type,
        "severity": severity,
        "description": description,
        "text_preview": text_preview[:200],
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(CORRECTION_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[correction-lifecycle] Warning: log write failed: {e}", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "detect":
        if len(sys.argv) < 3:
            print("Usage: liyu-correction-lifecycle.py detect '<text>'", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        corrections = detect_corrections(text)
        data = load_corrections()

        if corrections:
            for pattern_type, severity, description in corrections:
                update_pattern(data, pattern_type, severity, description)
                log_correction(pattern_type, severity, description, text)

            save_corrections(data)

            print("🚨 CORRECTION PATTERNS DETECTED:")
            for pattern_type, severity, description in corrections:
                severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
                print(f"  {severity_icon.get(severity, '⚪')} [{severity}] {pattern_type}: {description}")
            sys.exit(2)
        else:
            save_corrections(data)
            print("✅ No correction patterns detected")
            sys.exit(0)

    elif cmd == "list":
        data = load_corrections()
        patterns = data.get("patterns", {})

        if not patterns:
            print("No correction patterns tracked")
            sys.exit(0)

        print("═══ 鲤鱼 Correction Patterns ═══")
        for pid, p in sorted(patterns.items(), key=lambda x: x[1]["detection_count"], reverse=True):
            status_icon = {
                "new": "🆕",
                "active": "⚡",
                "improving": "📈",
                "internalized": "✅",
            }
            print(f"  {status_icon.get(p['status'], '❓')} [{p['status']}] {p['pattern_type']}: {p['description']}")
            print(f"    Count: {p['detection_count']} | Trend: {p['trend']} | Last: {p['last_detected'][:19]}")
            if p.get("resolution"):
                print(f"    Resolution: {p['resolution']}")

    elif cmd == "update":
        if len(sys.argv) < 4:
            print("Usage: liyu-correction-lifecycle.py update <pattern_id> <new_status>", file=sys.stderr)
            sys.exit(1)

        pattern_id = sys.argv[2]
        new_status = sys.argv[3]

        if new_status not in ["new", "active", "improving", "internalized"]:
            print(f"Invalid status: {new_status}. Must be one of: new, active, improving, internalized", file=sys.stderr)
            sys.exit(1)

        data = load_corrections()
        if pattern_id not in data["patterns"]:
            print(f"Pattern not found: {pattern_id}", file=sys.stderr)
            sys.exit(1)

        data["patterns"][pattern_id]["status"] = new_status
        save_corrections(data)
        print(f"✅ Updated {pattern_id} to {new_status}")

    elif cmd == "stats":
        data = load_corrections()
        stats = data.get("stats", {})

        print("═══ 鲤鱼 Correction Lifecycle Statistics ═══")
        print(f"  总计检测:     {stats.get('total_detections', 0)}")
        print()
        print("  按类型:")
        for dtype, count in stats.get("by_type", {}).items():
            if count > 0:
                print(f"    {dtype}: {count}")
        print()
        print("  按状态:")
        for status, count in stats.get("by_status", {}).items():
            if count > 0:
                status_icon = {
                    "new": "🆕",
                    "active": "⚡",
                    "improving": "📈",
                    "internalized": "✅",
                }
                print(f"    {status_icon.get(status, '❓')} {status}: {count}")

    elif cmd == "reset":
        save_corrections({
            "patterns": {},
            "stats": {
                "total_detections": 0,
                "by_type": {},
                "by_status": {"new": 0, "active": 0, "improving": 0, "internalized": 0},
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Correction Lifecycle 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
