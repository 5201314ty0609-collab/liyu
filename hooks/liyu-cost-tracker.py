#!/usr/bin/env python3
"""
鲤鱼 Cost Tracker — CLI and report formatter for token cost attribution.

Usage:
  python3 liyu-cost-tracker.py report        # Full cost health report
  python3 liyu-cost-tracker.py optimize      # Waste scan + fix recommendations
  python3 liyu-cost-tracker.py session <id>  # Single session deep-dive
  python3 liyu-cost-tracker.py o2-check      # Quick O2 vitality check (JSON)
  python3 liyu-cost-tracker.py pre-compact   # PreCompact hook data
  python3 liyu-cost-tracker.py sessions      # List all sessions
  python3 liyu-cost-tracker.py json          # Full report as JSON

Depends on liyu_cost_core.py for data parsing and analysis.
Inspired by CodeBurn (github.com/coder/codeburn) architecture.
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone
from collections import defaultdict, Counter

# Add liyu dir to path so we can import the core module
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from liyu_cost_core import (
    parse_all_sessions,
    analyze_config_overhead,
    detect_waste,
    calculate_health,
    o2_check,
    pre_compact_check,
    HEALTH_GRADES,
    TOOL_CATEGORIES,
    MODEL_PRICING,
    SessionTokens,
    ConfigOverhead,
    WasteFinding,
    CostReport,
)


# ─── Report Generator ──────────────────────────────────────────────────────────

def generate_report(sessions, overheads, waste) -> CostReport:
    """Assemble a complete cost report."""
    score, grade = calculate_health(sessions, waste, overheads)

    start_date = min(
        (s.start_time or "?") for s in sessions
    ) if sessions else "?"
    end_date = max(
        (s.end_time or "?") for s in sessions
    ) if sessions else "?"

    return CostReport(
        sessions=sessions,
        config_overheads=overheads,
        waste_findings=waste,
        health_score=score,
        health_grade=grade,
        total_sessions=len(sessions),
        date_range=(start_date[:10], end_date[:10]),
        report_time=datetime.now(timezone.utc).isoformat(),
    )


def format_report(report: CostReport) -> str:
    """Format a complete cost health report for terminal display."""
    s = report.sessions
    o = report.config_overheads
    w = report.waste_findings

    lines = []
    sep = "=" * 72
    sub = "-" * 72

    lines.append(sep)
    lines.append("  鲤鱼 COST TRACKER — Token Health Report")
    lines.append(sep)
    lines.append(f"  Report time:  {report.report_time[:19]}")
    lines.append(f"  Date range:   {report.date_range[0]}  >>>  {report.date_range[1]}")
    lines.append(f"  Sessions:     {report.total_sessions}")
    lines.append(f"  Health Grade: {report.health_grade} ({report.health_score}/100)")
    grade_desc = dict(HEALTH_GRADES).get(report.health_grade, ("", "", "?"))[2]
    lines.append(f"  Status:       {grade_desc}")
    lines.append("")

    # ── Cost Summary ──
    lines.append(sub)
    lines.append("  COST SUMMARY")
    lines.append(sub)

    total_input = report.total_input
    total_output = report.total_output
    total_cache_r = report.total_cache_read
    total_cache_w = sum(s.cache_write_tokens for s in s)
    total_cost = report.total_cost
    config_overhead = report.config_overhead_tokens

    lines.append(f"  Input tokens:       {total_input:>12,}")
    lines.append(f"  Output tokens:      {total_output:>12,}")
    lines.append(f"  Cache read (saved): {total_cache_r:>12,}")
    lines.append(f"  Cache write:        {total_cache_w:>12,}")
    lines.append(f"  Config overhead:    {config_overhead:>12,}  (loaded every session)")
    lines.append(f"  {'─' * 33}")
    lines.append(f"  Estimated cost:     ${total_cost:>11.2f}")
    lines.append("")

    # ── Model Breakdown ──
    lines.append(sub)
    lines.append("  MODEL BREAKDOWN")
    lines.append(sub)
    model_stats = defaultdict(lambda: {"count": 0, "input": 0, "output": 0})
    for session in s:
        m = session.model
        model_stats[m]["count"] += 1
        model_stats[m]["input"] += session.input_tokens
        model_stats[m]["output"] += session.output_tokens

    for model, stats in sorted(model_stats.items(), key=lambda x: -x[1]["input"]):
        pricing = MODEL_PRICING.get(model, {})
        est_cost = (
            stats["input"] / 1_000_000 * pricing.get("input", 0)
            + stats["output"] / 1_000_000 * pricing.get("output", 0)
        )
        lines.append(f"  {model}:")
        lines.append(
            f"    Sessions: {stats['count']}, "
            f"Input: {stats['input']:,}, Output: {stats['output']:,}"
        )
        lines.append(f"    Est. cost: ${est_cost:.2f}")
    lines.append("")

    # ── Tool Usage ──
    lines.append(sub)
    lines.append("  TOOL USAGE (across all sessions)")
    lines.append(sub)
    all_tools = Counter()
    for session in s:
        all_tools.update(session.tool_calls)
    total_tool_calls = sum(all_tools.values())
    for tool, count in all_tools.most_common(15):
        pct = count / max(total_tool_calls, 1) * 100
        cat = next(
            (c for c, tools in TOOL_CATEGORIES.items() if tool in tools),
            "uncategorized",
        )
        lines.append(f"  {tool:<20} {count:>5d}  ({pct:5.1f}%)  [{cat}]")
    lines.append("")

    # ── Session Overview ──
    lines.append(sub)
    lines.append("  SESSION OVERVIEW")
    lines.append(sub)
    header = f"  {'Session':<10} {'Input':>10} {'Output':>10} {'Ratio':>7} {'Tools':>6} {'Cost':>8}"
    lines.append(header)
    lines.append(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*7} {'-'*6} {'-'*8}")
    for session in sorted(s, key=lambda x: x.start_time or "", reverse=True)[:20]:
        sid = session.session_id[:8]
        cost = session.estimate_cost()["total"]
        tools = sum(session.tool_calls.values())
        lines.append(
            f"  {sid:<10} {session.input_tokens:>10,} {session.output_tokens:>10,} "
            f"{session.output_ratio:>6.1%} {tools:>5d} ${cost:>7.2f}"
        )
    lines.append("")

    # ── Waste Analysis ──
    if w:
        lines.append(sub)
        lines.append(f"  WASTE ANALYSIS ({len(w)} findings)")
        lines.append(sub)
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sev_markers = {"critical": "!!", "high": "! ", "medium": "- ", "low": "  "}
        for finding in sorted(w, key=lambda x: sev_order.get(x.severity, 4)):
            marker = sev_markers.get(finding.severity, "? ")
            lines.append(
                f"  [{marker} {finding.severity.upper():<8}] {finding.category}"
            )
            lines.append(f"    {finding.description}")
            lines.append(
                f"    Waste: ~{finding.estimated_token_waste:,} tokens "
                f"(~${finding.estimated_cost_waste:.2f})"
            )
            lines.append(f"    Fix:   {finding.fix_command[:100]}")
            lines.append("")
    else:
        lines.append("  No waste patterns detected. Excellent!")

    # ── Config Overhead Breakdown ──
    lines.append(sub)
    lines.append("  CONFIG OVERHEAD BREAKDOWN")
    lines.append(sub)
    for oh in sorted(o, key=lambda x: x.est_tokens, reverse=True)[:10]:
        pct = oh.est_tokens / max(config_overhead, 1) * 100
        lines.append(
            f"  {oh.description:<45} ~{oh.est_tokens:>8,}t ({pct:5.1f}%)"
        )
    lines.append("")

    # ── Health Score Breakdown ──
    lines.append(sub)
    lines.append("  HEALTH SCORE BREAKDOWN (0-100)")
    lines.append(sub)

    total_cache = report.total_cache_read
    if total_input > 0:
        cache_ratio = total_cache / (total_input + total_cache)
        cache_score = int(min(cache_ratio * 50, 30))
        lines.append(
            f"  Cache efficiency:    {cache_score:>3}/30  "
            f"(cache serves {cache_ratio:.1%} of input)"
        )

    ratios = [s.output_ratio for s in s if s.input_tokens > 1000]
    if ratios:
        avg = sum(ratios) / len(ratios)
        out_score = int(min(avg * 60, 20))
        lines.append(
            f"  Output/input ratio:  {out_score:>3}/20  "
            f"(avg {avg:.1%} output per input token)"
        )

    tool_score = min(len(all_tools), 15)
    lines.append(
        f"  Tool diversity:       {tool_score:>3}/15  "
        f"({len(all_tools)} unique tools used)"
    )

    sev_penalties = {"critical": 8, "high": 5, "medium": 2, "low": 1}
    waste_penalty = sum(sev_penalties.get(f.severity, 0) for f in w)
    lines.append(
        f"  Waste penalty:       -{waste_penalty:>3}     "
        f"({len(w)} findings)"
    )

    # Percentage-based thresholds matching calculate_health() in cost_core
    overhead_pct = config_overhead / 1_000_000  # 1M context window (mimo-v2.5-pro)
    if overhead_pct < 0.05:
        oh_display = "15/15  (compact config)"
    elif overhead_pct < 0.10:
        oh_display = "12/15  (lean config)"
    elif overhead_pct < 0.20:
        oh_display = " 9/15  (moderate config)"
    elif overhead_pct < 0.35:
        oh_display = " 6/15  (fair config)"
    elif overhead_pct < 0.50:
        oh_display = " 3/15  (heavy config)"
    else:
        oh_display = " 0/15  (bloated config)"
    lines.append(f"  Config overhead:     {oh_display}")

    total_reads = sum(s.tool_calls.get("Read", 0) for s in s)
    total_edits = sum(
        s.tool_calls.get("Edit", 0) + s.tool_calls.get("Write", 0) for s in s
    )
    if total_edits > 0:
        re_ratio = total_reads / total_edits
        if re_ratio < 3:
            re_display = "10/10  (efficient reads)"
        elif re_ratio < 7:
            re_display = " 5/10  (moderate reads)"
        else:
            re_display = " 2/10  (excessive reads per edit)"
        lines.append(f"  Read:Edit ratio:     {re_display} ({re_ratio:.1f}:1)")
    else:
        lines.append(f"  Read:Edit ratio:      0/10  (no edits!)")

    lines.append(f"  {'─'*40}")
    lines.append(f"  FINAL GRADE:          {report.health_grade} ({report.health_score}/100)")
    lines.append("")
    lines.append(sep)

    return "\n".join(lines)


# ─── Optimize Command ──────────────────────────────────────────────────────────

def generate_optimizations(sessions, overheads) -> str:
    """Generate actionable optimization recommendations with fix commands."""
    waste = detect_waste(sessions, overheads)
    _, grade = calculate_health(sessions, waste, overheads)

    lines = []
    sep = "=" * 72
    sub = "-" * 72

    lines.append(sep)
    lines.append("  鲤鱼 COST TRACKER — Optimization Recommendations")
    lines.append(sep)
    lines.append(f"  Current grade: {grade}")
    lines.append("")

    if not waste:
        lines.append("  No optimizations needed. Configuration is efficient!")
        return "\n".join(lines)

    by_category = defaultdict(list)
    for w in waste:
        by_category[w.category].append(w)

    category_descriptions = {
        "repeated_context": "REPEATED CONTEXT — Files read multiple times",
        "low_ratio": "LOW OUTPUT RATIO — Sessions with poor input/output balance",
        "bloated_config": "BLOATED CONFIG — Rules for unused languages/features",
        "ghost_skills": "GHOST SKILLS — Skills listed but never invoked",
        "ghost_agents": "GHOST AGENTS — Agents defined but rarely spawned",
        "cache_inefficiency": "CACHE INEFFICIENCY — Low prompt cache hit rates",
        "tool_imbalance": "TOOL IMBALANCE — Over-reliance on single tool type",
    }

    total_savings = 0
    total_tokens = 0

    for category, findings in sorted(by_category.items()):
        cat_savings = sum(f.estimated_cost_waste for f in findings)
        cat_tokens = sum(f.estimated_token_waste for f in findings)
        total_savings += cat_savings
        total_tokens += cat_tokens

        lines.append(sub)
        desc = category_descriptions.get(category, category.upper())
        lines.append(f"  {desc}")
        lines.append(
            f"  {len(findings)} finding(s) | ~${cat_savings:.2f} potential savings "
            f"(~{cat_tokens:,} tokens)"
        )
        lines.append(sub)

        for f in findings[:3]:
            lines.append(f"  [{f.severity.upper()}] {f.description}")
            lines.append(f"  Fix: {f.fix_command}")
            if f.evidence:
                lines.append(f"  Evidence: {f.evidence}")
            lines.append("")

    lines.append(sep)
    lines.append(
        f"  TOTAL Potential Savings: ~${total_savings:.2f} "
        f"(~{total_tokens:,} tokens)"
    )
    lines.append("")
    lines.append("  PRIORITY ORDER (apply in this sequence):")
    lines.append("  1. Remove unused language rules (immediate, persistent savings)")
    lines.append("  2. Trim ghost skills/agents (reduces every session's overhead)")
    lines.append("  3. Fix repeated file reads (per-session savings)")
    lines.append("  4. Improve low-ratio sessions (behavior change)")
    lines.append("")
    lines.append(
        "  Run 'python3 liyu-cost-tracker.py report' after each change"
    )
    lines.append("  to see the impact on your health grade.")
    lines.append(sep)

    return "\n".join(lines)


# ─── Single Session Deep-Dive ──────────────────────────────────────────────────

def session_deep_dive(sessions, session_id: str) -> str:
    """Generate a detailed report for a single session."""
    target = None
    for s in sessions:
        if s.session_id.startswith(session_id):
            target = s
            break
    if not target:
        for s in sessions:
            if session_id in s.session_id:
                target = s
                break

    if not target:
        ids = "\n".join(
            f"  {s.session_id[:8]}  {s.start_time or '?':<20}  "
            f"{s.input_tokens:>10,} in / {s.output_tokens:>10,} out"
            for s in sessions[:10]
        )
        return f"Session '{session_id}' not found. Available sessions:\n{ids}"

    s = target
    cost = s.estimate_cost()
    sep = "=" * 72
    sub = "-" * 72

    lines = []
    lines.append(sep)
    lines.append(f"  SESSION DEEP-DIVE: {s.session_id}")
    lines.append(sep)
    lines.append(f"  Model:        {s.model}")
    lines.append(f"  Time:         {s.start_time or '?'}  >>>  {s.end_time or '?'}")
    lines.append(f"  Duration:     {s.duration_ms / 1000:.1f}s")
    lines.append(f"  Turns:        {s.turn_count}")
    lines.append(f"  User msgs:    {s.user_messages}")
    lines.append(f"  Asst msgs:    {s.assistant_messages}")
    lines.append("")

    lines.append(sub)
    lines.append("  TOKEN BREAKDOWN")
    lines.append(sub)
    lines.append(f"  Input:        {s.input_tokens:>12,} tokens")
    lines.append(f"  Output:       {s.output_tokens:>12,} tokens")
    lines.append(f"  Cache read:   {s.cache_read_tokens:>12,} tokens")
    lines.append(f"  Cache write:  {s.cache_write_tokens:>12,} tokens")
    lines.append(f"  Thinking:     {s.thinking_tokens:>12,} tokens (est.)")
    lines.append(f"  {'─'*29}")
    lines.append(f"  Ratio:        {s.output_ratio:>11.2%}  (output per input)")
    lines.append(f"  Cache hit:    {s.cache_hit_rate:>11.2%}  (of processed tokens)")
    lines.append("")

    lines.append(sub)
    lines.append("  COST ESTIMATE")
    lines.append(sub)
    for k, v in cost.items():
        lines.append(f"  {k:<14} ${v:>10.4f}")
    lines.append("")

    lines.append(sub)
    lines.append("  TOOL USAGE")
    lines.append(sub)
    for tool, count in sorted(s.tool_calls.items(), key=lambda x: -x[1]):
        cat = next(
            (c for c, tools in TOOL_CATEGORIES.items() if tool in tools), "?"
        )
        lines.append(f"  {tool:<20} {count:>4d}  [{cat}]")
    lines.append("")

    if s.repeated_files:
        lines.append(sub)
        lines.append("  REPEATED FILE READS (potential waste)")
        lines.append(sub)
        top = sorted(s.repeated_files.items(), key=lambda x: -x[1])[:10]
        for fp, count in top:
            if count > 1:
                lines.append(f"  {count}x  {fp}")

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        description="鲤鱼 Cost Tracker — Token cost attribution & optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command", nargs="?", default="report",
        choices=[
            "report", "optimize", "session", "o2-check",
            "pre-compact", "sessions", "json",
        ],
    )
    parser.add_argument("args", nargs="*", help="Additional arguments (e.g. session ID)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Parse data once
    sessions = parse_all_sessions()
    overheads = analyze_config_overhead()
    waste = detect_waste(sessions, overheads)

    if args.command == "report":
        report = generate_report(sessions, overheads, waste)
        print(format_report(report))

    elif args.command == "optimize":
        print(generate_optimizations(sessions, overheads))

    elif args.command == "session":
        sid = args.args[0] if args.args else ""
        if not sid:
            print(
                "Usage: python3 liyu-cost-tracker.py session <session-id-prefix>"
            )
            print("\nAvailable sessions:")
            for s in sessions[:20]:
                print(
                    f"  {s.session_id[:8]}  {s.start_time or '?':<20}  "
                    f"{s.input_tokens:>10,} in / {s.output_tokens:>10,} out  "
                    f"  {s.model}"
                )
            sys.exit(1)
        print(session_deep_dive(sessions, sid))

    elif args.command == "o2-check":
        result = o2_check(sessions)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "pre-compact":
        result = pre_compact_check(sessions, overheads)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif result["warnings"]:
            print("\n  鲤鱼 PreCompact Warning:")
            for w in result["warnings"]:
                print(f"    {w}")
            print(
                f"\n  Config overhead: {result['total_config_overhead']:,} "
                f"tokens every session"
            )
            if result["potential_savings_by_trimming_unused"] > 0:
                print(
                    f"  Trim unused rules to save: "
                    f"~{result['potential_savings_by_trimming_unused']:,} tokens/session"
                )
            compact = "YES" if result["recommend_compact"] else "Not urgent"
            print(f"\n  Recommended: {compact}")
        else:
            print("  Config looks lean. No pre-compact warnings.")

    elif args.command == "sessions":
        header = (
            f"{'Session ID':<10} {'Date':<12} {'Model':<20} "
            f"{'Input':>10} {'Output':>10} {'Ratio':>7} {'Tools':>6} {'Cost':>8}"
        )
        print(header)
        print("-" * 85)
        for s in sorted(sessions, key=lambda x: x.start_time or "", reverse=True)[:30]:
            cost = s.estimate_cost()["total"]
            date = (s.start_time or "?")[:10]
            tools = sum(s.tool_calls.values())
            print(
                f"{s.session_id[:8]:<10} {date:<12} {s.model:<20} "
                f"{s.input_tokens:>10,} {s.output_tokens:>10,} "
                f"{s.output_ratio:>6.1%} {tools:>5d} ${cost:>7.2f}"
            )

    elif args.command == "json":
        report = generate_report(sessions, overheads, waste)
        output = {
            "health_grade": report.health_grade,
            "health_score": report.health_score,
            "total_sessions": report.total_sessions,
            "date_range": report.date_range,
            "tokens": {
                "total_input": report.total_input,
                "total_output": report.total_output,
                "total_cache_read": report.total_cache_read,
                "total_cost_usd": round(report.total_cost, 2),
            },
            "waste_findings": [
                {
                    "id": f.id,
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "est_token_waste": f.estimated_token_waste,
                    "est_cost_waste": round(f.estimated_cost_waste, 4),
                    "fix": f.fix_command,
                }
                for f in sorted(
                    waste,
                    key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                        x.severity, 4
                    ),
                )
            ],
            "o2_vitality": o2_check(sessions),
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
