#!/usr/bin/env python3
"""
鲤鱼 Framework Promoter — 自动评估和晋升框架
吸收自 Claude Soul v0.2.5 的 framework evolution + evidence tiers

证据层系统:
  Hypothesis → Observed → Validated → Hardened

晋升规则:
  - 1+ observations → Observed
  - 10+ observations, >60% confidence, >70% success → Validated
  - 50+ observations, >90% confidence, ZERO contradictions → Hardened

自生成证据权重: 0.5x (防止自我强化)

Usage:
  liyu-framework-promoter.py evaluate
    评估所有 active frameworks

  liyu-framework-promoter.py promote <framework_id> <new_stage>
    手动晋升框架

  liyu-framework-promoter.py stats
    查看框架统计

  liyu-framework-promoter.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import sys
import shutil

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
FRAMEWORKS_DIR = 鲤鱼_HOME / "frameworks"
ACTIVE_DIR = FRAMEWORKS_DIR / "active"
OBSERVED_DIR = FRAMEWORKS_DIR / "observed"
VALIDATED_DIR = FRAMEWORKS_DIR / "validated"
HARDENED_DIR = FRAMEWORKS_DIR / "hardened"
PROMOTER_STATE_FILE = 鲤鱼_HOME / "framework-promoter-state.json"
PROMOTER_LOG_FILE = 鲤鱼_HOME / "framework-promoter-log.jsonl"

# ── 晋升阈值 ──────────────────────────────────────────────────────────────

THRESHOLDS = {
    "active_to_observed": {
        "min_observations": 1,
        "min_confidence": 0.0,
        "min_success_rate": 0.0,
    },
    "observed_to_validated": {
        "min_observations": 10,
        "min_confidence": 0.6,
        "min_success_rate": 0.7,
    },
    "validated_to_hardened": {
        "min_observations": 50,
        "min_confidence": 0.9,
        "min_success_rate": 1.0,  # ZERO contradictions
    },
}

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class Framework:
    """框架数据"""
    id: str
    trigger: str
    action: str
    tool_signature: str
    confidence: float
    stage: str
    enforcement_level: int
    observations: int
    successes: int
    failures: int
    domains: list
    evolved_from: list
    created_at: str
    promoted_at: dict
    amendments: list

# ── 框架加载 ──────────────────────────────────────────────────────────────

def load_frameworks() -> dict[str, Framework]:
    """加载所有框架"""
    frameworks = {}

    for stage_dir in [ACTIVE_DIR, OBSERVED_DIR, VALIDATED_DIR, HARDENED_DIR]:
        if not stage_dir.exists():
            continue
        for f in stage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                frameworks[data["id"]] = Framework(**data)
            except Exception as e:
                print(f"[framework-promoter] Warning: failed to load {f}: {e}", file=sys.stderr)

    return frameworks


def save_framework(framework: Framework) -> None:
    """保存框架到对应目录"""
    stage_dir = {
        "active": ACTIVE_DIR,
        "observed": OBSERVED_DIR,
        "validated": VALIDATED_DIR,
        "hardened": HARDENED_DIR,
    }.get(framework.stage, ACTIVE_DIR)

    stage_dir.mkdir(parents=True, exist_ok=True)
    filepath = stage_dir / f"{framework.id}.json"
    filepath.write_text(json.dumps(vars(framework), ensure_ascii=False, indent=2))


def move_framework(framework: Framework, new_stage: str) -> None:
    """移动框架到新目录"""
    old_dir = {
        "active": ACTIVE_DIR,
        "observed": OBSERVED_DIR,
        "validated": VALIDATED_DIR,
        "hardened": HARDENED_DIR,
    }.get(framework.stage, ACTIVE_DIR)

    new_dir = {
        "active": ACTIVE_DIR,
        "observed": OBSERVED_DIR,
        "validated": VALIDATED_DIR,
        "hardened": HARDENED_DIR,
    }.get(new_stage, ACTIVE_DIR)

    old_file = old_dir / f"{framework.id}.json"
    new_file = new_dir / f"{framework.id}.json"

    if old_file.exists():
        old_file.unlink()

    framework.stage = new_stage
    framework.promoted_at[new_stage] = datetime.now(timezone.utc).isoformat()
    save_framework(framework)

# ── 晋升评估 ──────────────────────────────────────────────────────────────

def evaluate_framework(framework: Framework) -> tuple[bool, str, str]:
    """评估框架是否可以晋升

    Returns:
        (can_promote, new_stage, reason)
    """
    observations = framework.observations
    confidence = framework.confidence
    success_rate = framework.successes / max(observations, 1)

    # Active → Observed
    if framework.stage == "active":
        t = THRESHOLDS["active_to_observed"]
        if observations >= t["min_observations"]:
            return True, "observed", f"First observation ({observations} observations)"

    # Observed → Validated
    elif framework.stage == "observed":
        t = THRESHOLDS["observed_to_validated"]
        if (observations >= t["min_observations"] and
            confidence >= t["min_confidence"] and
            success_rate >= t["min_success_rate"]):
            return True, "validated", f"Met validation criteria ({observations} obs, {confidence:.2f} conf, {success_rate:.0%} success)"

    # Validated → Hardened
    elif framework.stage == "validated":
        t = THRESHOLDS["validated_to_hardened"]
        if (observations >= t["min_observations"] and
            confidence >= t["min_confidence"] and
            success_rate >= t["min_success_rate"]):
            return True, "hardened", f"Met hardening criteria ({observations} obs, {confidence:.2f} conf, {success_rate:.0%} success, 0 contradictions)"

    return False, framework.stage, "Not ready for promotion"


def evaluate_all() -> list[tuple[Framework, str, str]]:
    """评估所有框架"""
    frameworks = load_frameworks()
    promotions = []

    for fid, framework in frameworks.items():
        can_promote, new_stage, reason = evaluate_framework(framework)
        if can_promote:
            promotions.append((framework, new_stage, reason))

    return promotions

# ── State Management ──────────────────────────────────────────────────────

def load_state() -> dict:
    """加载 promoter 状态"""
    if PROMOTER_STATE_FILE.exists():
        try:
            return json.loads(PROMOTER_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_evaluations": 0,
        "total_promotions": 0,
        "promotions_by_stage": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_state(state: dict) -> None:
    """持久化 promoter 状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROMOTER_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def log_promotion(framework_id: str, old_stage: str, new_stage: str, reason: str) -> None:
    """记录晋升到日志"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "framework_id": framework_id,
        "old_stage": old_stage,
        "new_stage": new_stage,
        "reason": reason,
    }
    try:
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        with open(PROMOTER_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[framework-promoter] Warning: log write failed: {e}", file=sys.stderr)

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "evaluate":
        promotions = evaluate_all()
        state = load_state()
        state["total_evaluations"] += 1

        if promotions:
            print("🔄 FRAMEWORKS READY FOR PROMOTION:")
            for framework, new_stage, reason in promotions:
                print(f"  {framework.id}: {framework.stage} → {new_stage}")
                print(f"    Reason: {reason}")
                print(f"    Observations: {framework.observations} | Confidence: {framework.confidence:.2f}")
                print()

            # 自动晋升
            for framework, new_stage, reason in promotions:
                old_stage = framework.stage
                move_framework(framework, new_stage)
                log_promotion(framework.id, old_stage, new_stage, reason)
                state["total_promotions"] += 1
                state["promotions_by_stage"][f"{old_stage}_to_{new_stage}"] = \
                    state["promotions_by_stage"].get(f"{old_stage}_to_{new_stage}", 0) + 1
                print(f"  ✅ Promoted {framework.id}: {old_stage} → {new_stage}")

            save_state(state)
        else:
            save_state(state)
            print("No frameworks ready for promotion")

    elif cmd == "promote":
        if len(sys.argv) < 4:
            print("Usage: liyu-framework-promoter.py promote <framework_id> <new_stage>", file=sys.stderr)
            sys.exit(1)

        framework_id = sys.argv[2]
        new_stage = sys.argv[3]

        if new_stage not in ["active", "observed", "validated", "hardened"]:
            print(f"Invalid stage: {new_stage}. Must be one of: active, observed, validated, hardened", file=sys.stderr)
            sys.exit(1)

        frameworks = load_frameworks()
        if framework_id not in frameworks:
            print(f"Framework not found: {framework_id}", file=sys.stderr)
            sys.exit(1)

        framework = frameworks[framework_id]
        old_stage = framework.stage
        move_framework(framework, new_stage)
        log_promotion(framework_id, old_stage, new_stage, "Manual promotion")

        state = load_state()
        state["total_promotions"] += 1
        state["promotions_by_stage"][f"{old_stage}_to_{new_stage}"] = \
            state["promotions_by_stage"].get(f"{old_stage}_to_{new_stage}", 0) + 1
        save_state(state)

        print(f"✅ Promoted {framework_id}: {old_stage} → {new_stage}")

    elif cmd == "stats":
        frameworks = load_frameworks()
        state = load_state()

        print("═══ 鲤鱼 Framework Promoter Statistics ═══")
        print(f"  总计评估:     {state.get('total_evaluations', 0)}")
        print(f"  总计晋升:     {state.get('total_promotions', 0)}")
        print()
        print("  晋升记录:")
        for transition, count in state.get("promotions_by_stage", {}).items():
            if count > 0:
                print(f"    {transition}: {count}")
        print()
        print("  当前框架分布:")
        stage_counts = {"active": 0, "observed": 0, "validated": 0, "hardened": 0}
        for f in frameworks.values():
            stage_counts[f.stage] = stage_counts.get(f.stage, 0) + 1
        for stage, count in stage_counts.items():
            stage_icon = {
                "active": "🆕",
                "observed": "👁️",
                "validated": "✅",
                "hardened": "🔒",
            }
            print(f"    {stage_icon.get(stage, '❓')} {stage}: {count}")

    elif cmd == "reset":
        save_state({
            "total_evaluations": 0,
            "total_promotions": 0,
            "promotions_by_stage": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Framework Promoter 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
