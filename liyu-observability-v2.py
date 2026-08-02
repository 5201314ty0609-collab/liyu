#!/usr/bin/env python3
"""
鲤鱼 Observability v2 — Unified monitoring orchestrator.
============================================================

Ties together all observability modules:
- Metrics Collector (real-time data)
- Alert Engine (threshold-based alerting)
- Dashboard (HTML visualization)
- Original Observability (tracing + scoring)

Usage:
  liyu-observability-v2.py status               Quick status of all 7 senses
  liyu-observability-v2.py cycle                 Run a full collect → alert → score cycle
  liyu-observability-v2.py dashboard [--open]    Generate HTML dashboard
  liyu-observability-v2.py watch [--interval 30] Continuous monitoring loop
  liyu-observability-v2.py report                Full text report
  liyu-observability-v2.py health                Health score with recommendations
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

鲤鱼_HOME = Path.home() / ".claude/liyu"
PYTHON = sys.executable


def _run_module(script: str, args: List[str], capture: bool = True) -> Optional[str]:
    """Run a 鲤鱼 module and return stdout."""
    cmd = [PYTHON, str(鲤鱼_HOME / script)] + args
    try:
        result = subprocess.run(cmd, capture_output=capture, text=True, timeout=30)
        return result.stdout if capture else None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"


def run_cycle() -> Dict:
    """Run a full observability cycle: collect → alert → score."""
    results = {"timestamp": datetime.now(timezone.utc).isoformat()}

    # 1. Collect metrics
    output = _run_module("liyu-metrics-collector.py", ["collect"])
    results["collect"] = output.strip() if output else "failed"

    # 2. Check alerts
    output = _run_module("liyu-alert-engine.py", ["check"])
    results["alerts"] = output.strip() if output else "failed"

    # 3. Ingest into observability DB
    output = _run_module("liyu-observability.py", ["ingest", "all"])
    results["ingest"] = output.strip() if output else "failed"

    return results


def get_status() -> Dict:
    """Get quick status of all senses."""
    senses = {}
    for sense_id in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
        path = 鲤鱼_HOME / "senses" / f"{sense_id}.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            senses[sense_id] = {
                "status": data.get("status", "unknown"),
                "metrics": data.get("metrics", {}),
                "updated": data.get("last_updated", ""),
            }
        else:
            senses[sense_id] = {"status": "no_data", "metrics": {}, "updated": ""}

    # Active alerts
    output = _run_module("liyu-alert-engine.py", ["active"])
    alerts_text = output.strip() if output else ""

    return {"senses": senses, "alerts_summary": alerts_text}


def generate_report() -> str:
    """Generate a comprehensive text report."""
    status = get_status()
    lines = [
        "=" * 60,
        "  鲤鱼 Observability Report",
        f"  Generated: {datetime.now(timezone.utc).isoformat()[:19]}",
        "=" * 60,
        "",
        "  7-SENSE STATUS",
        "  " + "-" * 50,
    ]

    status_icons = {"normal": "[OK]", "warning": "[!!]", "critical": "[XX]", "no_data": "[--]", "unknown": "[?]"}

    for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
        sense = status["senses"].get(sid, {})
        icon = status_icons.get(sense.get("status", "unknown"), "[?]")
        metrics = sense.get("metrics", {})
        # Pick a scalar primary value (skip dicts/lists)
        primary_val = "N/A"
        for v in metrics.values():
            if isinstance(v, (int, float)):
                primary_val = f"{v:.1f}" if isinstance(v, float) else str(v)
                break
        lines.append(f"  {icon} {sid:<14} primary={primary_val}")

    lines.extend([
        "",
        "  ALERTS",
        "  " + "-" * 50,
    ])
    if status["alerts_summary"]:
        for line in status["alerts_summary"].split("\n"):
            lines.append(f"  {line}")
    else:
        lines.append("  No active alerts.")

    # Performance summary
    output = _run_module("liyu-metrics-collector.py", ["perf"])
    if output:
        lines.extend(["", "  PERFORMANCE", "  " + "-" * 50])
        for line in output.strip().split("\n"):
            lines.append(f"  {line}")

    lines.append("")
    return "\n".join(lines)


def get_health() -> Dict:
    """Get health score with actionable recommendations."""
    status = get_status()
    recommendations = []

    sense_health = {}
    for sid, sense in status["senses"].items():
        s = sense.get("status", "unknown")
        sense_health[sid] = s
        if s == "critical":
            recommendations.append(f"CRITICAL: {sid} needs immediate attention")
        elif s == "warning":
            recommendations.append(f"WARNING: {sid} approaching threshold")

    normal_count = sum(1 for v in sense_health.values() if v == "normal")
    total = len(sense_health)
    health_score = round((normal_count / max(total, 1)) * 100, 1)

    return {
        "health_score": health_score,
        "sense_status": sense_health,
        "recommendations": recommendations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def watch_loop(interval: int = 30):
    """Continuous monitoring loop."""
    print(f"鲤鱼 Watch Mode — collecting every {interval}s (Ctrl+C to stop)")
    print("-" * 60)

    cycle_count = 0
    try:
        while True:
            cycle_count += 1
            results = run_cycle()
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # Parse collect output for quick display
            collect_lines = results.get("collect", "").split("\n")
            statuses = []
            for line in collect_lines:
                if ":" in line and ("normal" in line or "warning" in line or "critical" in line):
                    parts = line.strip().split(":")
                    if len(parts) >= 2:
                        sense = parts[0].strip()
                        status_part = parts[1].strip().split("(")[0].strip()
                        statuses.append(f"{sense}:{status_part}")

            status_str = " | ".join(statuses) if statuses else "collecting..."
            alert_str = ""
            if "No new alerts" not in results.get("alerts", ""):
                alert_str = f" ALERTS: {results['alerts'].split(chr(10))[0]}"

            print(f"[{now}] #{cycle_count} {status_str}{alert_str}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {cycle_count} cycles.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        status = get_status()
        status_icons = {"normal": "[OK]", "warning": "[!!]", "critical": "[XX]", "no_data": "[--]", "unknown": "[?]"}
        for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
            sense = status["senses"].get(sid, {})
            icon = status_icons.get(sense.get("status", "unknown"), "[?]")
            metrics = sense.get("metrics", {})
            val = "N/A"
            for v in metrics.values():
                if isinstance(v, (int, float)):
                    val = f"{v:.1f}" if isinstance(v, float) else str(v)
                    break
            print(f"{icon} {sid:<14} {val}")

    elif cmd == "cycle":
        results = run_cycle()
        print(f"Cycle complete at {results['timestamp']}")
        print(f"  Collect: {results['collect'][:100]}")
        print(f"  Alerts: {results['alerts'][:100]}")
        print(f"  Ingest: {results['ingest'][:100]}")

    elif cmd == "dashboard":
        args = ["generate"]
        if "--open" in sys.argv:
            args.append("--open")
        output = _run_module("liyu-dashboard.py", args, capture=False)

    elif cmd == "watch":
        interval = 30
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            interval = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
        watch_loop(interval)

    elif cmd == "report":
        print(generate_report())

    elif cmd == "health":
        health = get_health()
        print(f"Health Score: {health['health_score']}/100")
        for sid, st in health["sense_status"].items():
            print(f"  {sid}: {st}")
        if health["recommendations"]:
            print("\nRecommendations:")
            for r in health["recommendations"]:
                print(f"  - {r}")

    else:
        print(f"Unknown command: {cmd}")
        print("Available: status, cycle, dashboard, watch, report, health")
        sys.exit(1)


if __name__ == "__main__":
    main()
