#!/usr/bin/env python3
"""
鲤鱼 Metrics Collector — Real-time system and agent metrics.
================================================================

Collects metrics from multiple sources and writes to sense files + SQLite.
Designed to run as a background process or be invoked per-event.

Usage:
  liyu-metrics-collector.py collect              Collect all metrics once
  liyu-metrics-collector.py collect --sense o2   Collect a specific sense
  liyu-metrics-collector.py start [--interval 30] Start continuous collection
  liyu-metrics-collector.py snapshot              Write a full snapshot to DB
  liyu-metrics-collector.py trends [--hours 24]   Show metric trends
  liyu-metrics-collector.py perf                  Show performance summary
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

鲤鱼_HOME = Path.home() / ".claude/liyu"
SENSES_DIR = 鲤鱼_HOME / "senses"
DB_PATH = 鲤鱼_HOME / "observability.db"
PERF_LOG = 鲤鱼_HOME / "performance.jsonl"
STORY_PATH = 鲤鱼_HOME / "story.jsonl"

# ── Database ──────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_perf_schema(conn)
    return conn


def _init_perf_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metric_series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            tags TEXT DEFAULT '{}',
            captured_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS perf_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            span_name TEXT NOT NULL,
            span_type TEXT DEFAULT 'operation',
            duration_ms REAL,
            status TEXT DEFAULT 'ok',
            metadata TEXT DEFAULT '{}',
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_type TEXT NOT NULL,
            error_message TEXT,
            stack_trace TEXT,
            context TEXT DEFAULT '{}',
            fingerprint TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            resolved INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_metric_name ON metric_series(metric_name, captured_at);
        CREATE INDEX IF NOT EXISTS idx_perf_span_name ON perf_spans(span_name, started_at);
        CREATE INDEX IF NOT EXISTS idx_error_fingerprint ON error_records(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_error_resolved ON error_records(resolved);
    """)


# ── Metric Writers ────────────────────────────────────────────────────────────

def write_metric(name: str, value: float, tags: Optional[Dict] = None) -> None:
    """Write a single metric data point."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO metric_series(metric_name, metric_value, tags, captured_at) VALUES (?, ?, ?, ?)",
        (name, value, json.dumps(tags or {}), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def write_metrics_batch(metrics: List[Dict[str, Any]]) -> int:
    """Write multiple metric data points in one transaction."""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for m in metrics:
        conn.execute(
            "INSERT INTO metric_series(metric_name, metric_value, tags, captured_at) VALUES (?, ?, ?, ?)",
            (m["name"], m["value"], json.dumps(m.get("tags", {})), now),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def start_span(name: str, span_type: str = "operation", metadata: Optional[Dict] = None) -> int:
    """Start a performance span, return span ID."""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO perf_spans(span_name, span_type, status, metadata, started_at) VALUES (?, ?, 'running', ?, ?)",
        (name, span_type, json.dumps(metadata or {}), now),
    )
    span_id = cur.lastrowid
    conn.commit()
    conn.close()
    return span_id


def finish_span(span_id: int, status: str = "ok", metadata: Optional[Dict] = None) -> Optional[float]:
    """Finish a performance span, return duration in ms."""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT started_at FROM perf_spans WHERE id = ?", (span_id,)).fetchone()
    if not row:
        conn.close()
        return None

    started = datetime.fromisoformat(row["started_at"])
    finished = datetime.fromisoformat(now)
    duration_ms = (finished - started).total_seconds() * 1000

    existing_meta = json.loads(conn.execute("SELECT metadata FROM perf_spans WHERE id = ?", (span_id,)).fetchone()["metadata"] or "{}")
    if metadata:
        existing_meta.update(metadata)

    conn.execute(
        "UPDATE perf_spans SET finished_at = ?, duration_ms = ?, status = ?, metadata = ? WHERE id = ?",
        (now, duration_ms, status, json.dumps(existing_meta), span_id),
    )
    conn.commit()
    conn.close()
    return duration_ms


def record_error(
    error_type: str,
    error_message: str,
    stack_trace: str = "",
    context: Optional[Dict] = None,
) -> str:
    """Record an error with deduplication by fingerprint."""
    fingerprint = f"{error_type}:{error_message[:200]}"
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id, occurrence_count FROM error_records WHERE fingerprint = ? AND resolved = 0",
        (fingerprint,),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE error_records SET occurrence_count = occurrence_count + 1, last_seen = ?, context = ? WHERE id = ?",
            (now, json.dumps(context or {}), existing["id"]),
        )
        record_id = existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO error_records(error_type, error_message, stack_trace, context, fingerprint, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (error_type, error_message, stack_trace, json.dumps(context or {}), fingerprint, now, now),
        )
        record_id = cur.lastrowid

    conn.commit()
    conn.close()
    return fingerprint


# ── Sense Collectors ──────────────────────────────────────────────────────────

def collect_o2() -> Dict:
    """Collect O2 (context pressure) metrics from story.jsonl and session state."""
    metrics = {
        "estimated_tokens": 0,
        "context_limit": 1_000_000,
        "usage_percent": 0.0,
        "message_count": 0,
    }

    # Count messages from story.jsonl
    if STORY_PATH.exists():
        try:
            with open(STORY_PATH) as f:
                lines = f.readlines()
            metrics["message_count"] = len(lines)
            # Rough token estimate: ~4 chars per token
            total_chars = sum(len(line) for line in lines)
            metrics["estimated_tokens"] = total_chars // 4
            metrics["usage_percent"] = round(
                (metrics["estimated_tokens"] / metrics["context_limit"]) * 100, 1
            )
        except Exception:
            pass

    # Write to sense file
    sense_data = {
        "trace_event": "token_pressure",
        "status": "normal",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    if metrics["usage_percent"] >= 85:
        sense_data["status"] = "critical"
        sense_data["recommendation"] = "compress"
        sense_data["warnings"].append("Context usage critical")
    elif metrics["usage_percent"] >= 70:
        sense_data["status"] = "warning"
        sense_data["recommendation"] = "consider_compression"

    _write_sense("o2", sense_data)
    write_metrics_batch([
        {"name": "o2.usage_percent", "value": metrics["usage_percent"]},
        {"name": "o2.estimated_tokens", "value": metrics["estimated_tokens"]},
        {"name": "o2.message_count", "value": metrics["message_count"]},
    ])
    return sense_data


def collect_nociception() -> Dict:
    """Collect Nociception (error cascade) metrics from error_records."""
    conn = _get_db()
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM error_records WHERE last_seen > ? AND resolved = 0", (five_min_ago,)
    ).fetchone()
    error_count = row["cnt"]
    conn.close()

    metrics = {
        "error_count": error_count,
        "window_minutes": 5,
        "errors_per_window": error_count,
    }
    status = "normal"
    if error_count >= 5:
        status = "critical"
    elif error_count >= 3:
        status = "warning"

    sense_data = {
        "trace_event": "error_cascade",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("nociception", sense_data)
    write_metric("nociception.errors_per_window", error_count)
    return sense_data


def collect_chronos() -> Dict:
    """Collect Chronos (session pacing) metrics."""
    session_file = 鲤鱼_HOME / "session-state.json"
    idle_seconds = 0
    session_duration = 0
    active_turns = 0

    # Check if there's an active session by looking at recent story.jsonl activity
    recent_activity = False
    if STORY_PATH.exists():
        try:
            # Check if story.jsonl was modified in the last 10 minutes
            mtime = STORY_PATH.stat().st_mtime
            age_seconds = time.time() - mtime
            if age_seconds < 600:  # 10 minutes
                recent_activity = True
                idle_seconds = age_seconds
        except Exception:
            pass

    if not recent_activity and session_file.exists():
        try:
            with open(session_file) as f:
                state = json.load(f)
            updated = state.get("updated_at", state.get("current", {}).get("updated_at"))
            if updated:
                last_update = datetime.fromisoformat(updated)
                if last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)
                gap = (datetime.now(timezone.utc) - last_update).total_seconds()
                # Only report idle if gap is reasonable (< 1 hour), otherwise session is stale
                if gap < 3600:
                    idle_seconds = gap
                else:
                    idle_seconds = 0  # Session is stale, not actively idle
            active_turns = state.get("current", {}).get("session_count", 0)
        except Exception:
            pass

    metrics = {
        "idle_seconds": round(idle_seconds, 1),
        "session_duration_minutes": round(session_duration, 1),
        "active_turns": active_turns,
    }
    status = "normal"
    if idle_seconds >= 600:
        status = "critical"
    elif idle_seconds >= 300:
        status = "warning"

    sense_data = {
        "trace_event": "session_pacing",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("chronos", sense_data)
    write_metric("chronos.idle_seconds", idle_seconds)
    return sense_data


def collect_spatial() -> Dict:
    """Collect Spatial (file churn) metrics from recent perf spans."""
    conn = _get_db()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM perf_spans WHERE span_type = 'file_op' AND started_at > ?", (recent,)
    ).fetchone()
    file_ops = rows["cnt"]
    # Estimate calls from trace events
    calls = max(1, conn.execute(
        "SELECT COUNT(*) FROM trace_events WHERE start_time > ?", (recent,)
    ).fetchone()[0])
    conn.close()

    files_per_call = round(file_ops / calls, 2) if calls > 0 else 0
    metrics = {
        "files_modified": file_ops,
        "files_per_call": files_per_call,
        "total_operations": calls,
    }
    status = "normal"
    if files_per_call >= 10:
        status = "critical"
    elif files_per_call >= 5:
        status = "warning"

    sense_data = {
        "trace_event": "file_churn",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("spatial", sense_data)
    write_metric("spatial.files_per_call", files_per_call)
    return sense_data


def collect_vestibular() -> Dict:
    """Collect Vestibular (tool diversity) metrics from trace events."""
    conn = _get_db()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    rows = conn.execute(
        "SELECT trace_event, COUNT(*) as cnt FROM trace_events WHERE start_time > ? GROUP BY trace_event",
        (recent,),
    ).fetchall()
    conn.close()

    tool_counts = {}
    total = 0
    for row in rows:
        name = row["trace_event"]
        cnt = row["cnt"]
        tool_counts[name] = cnt
        total += cnt

    # Fall back to existing sense file data if DB has no recent events
    if not tool_counts:
        sense_path = SENSES_DIR / "vestibular.json"
        if sense_path.exists():
            try:
                with open(sense_path) as f:
                    existing = json.load(f)
                existing_counts = existing.get("metrics", {}).get("tool_counts", {})
                if existing_counts:
                    tool_counts = existing_counts
                    total = sum(tool_counts.values())
            except Exception:
                pass

    dominant_tool = max(tool_counts, key=tool_counts.get) if tool_counts else "none"
    dominant_pct = round((tool_counts.get(dominant_tool, 0) / total * 100), 1) if total > 0 else 0

    metrics = {
        "tool_counts": tool_counts,
        "dominant_tool": dominant_tool,
        "dominant_percent": dominant_pct,
        "total_calls": total,
    }
    status = "normal"
    if dominant_pct >= 80:
        status = "critical"
    elif dominant_pct >= 70:
        status = "warning"

    sense_data = {
        "trace_event": "tool_diversity",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("vestibular", sense_data)
    write_metric("vestibular.dominant_percent", dominant_pct)
    return sense_data


def collect_echo() -> Dict:
    """Collect Echo (pattern recurrence) metrics from error fingerprints."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT fingerprint, occurrence_count FROM error_records WHERE resolved = 0 AND occurrence_count > 1"
    ).fetchall()
    conn.close()

    repeated = len(rows)
    total_patterns = conn.execute("SELECT COUNT(*) FROM error_records WHERE resolved = 0").fetchone()[0] if rows else 0
    # Re-open for total count
    conn2 = _get_db()
    total_patterns = conn2.execute("SELECT COUNT(*) FROM error_records WHERE resolved = 0").fetchone()[0]
    conn2.close()

    diversity = round(1.0 - (repeated / max(total_patterns, 1)), 2)
    metrics = {
        "repeated_signatures": repeated,
        "unique_patterns": total_patterns,
        "pattern_diversity": diversity,
    }
    status = "normal"
    if repeated >= 3:
        status = "critical"
    elif repeated >= 2:
        status = "warning"

    sense_data = {
        "trace_event": "pattern_recurrence",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("echo", sense_data)
    write_metric("echo.repeated_signatures", repeated)
    return sense_data


def collect_drift() -> Dict:
    """Collect Drift (focus deviation) metrics from recent session topics."""
    # Analyze recent story.jsonl entries for topic coherence
    coherence = 0.95
    deviation = 5.0
    current_topic = "active-session"

    if STORY_PATH.exists():
        try:
            with open(STORY_PATH) as f:
                lines = f.readlines()
            recent_lines = lines[-20:] if len(lines) > 20 else lines
            # Simple heuristic: check if recent entries share keywords
            if len(recent_lines) >= 2:
                words_sets = []
                for line in recent_lines:
                    try:
                        entry = json.loads(line)
                        text = str(entry.get("content", entry.get("message", "")))
                        words = set(text.lower().split())
                        words_sets.append(words)
                    except (json.JSONDecodeError, AttributeError):
                        continue

                if len(words_sets) >= 2:
                    # Compute pairwise overlap
                    overlaps = []
                    for i in range(len(words_sets) - 1):
                        a, b = words_sets[i], words_sets[i + 1]
                        if a and b:
                            overlap = len(a & b) / max(len(a | b), 1)
                            overlaps.append(overlap)
                    if overlaps:
                        coherence = round(sum(overlaps) / len(overlaps), 2)
                        deviation = round((1.0 - coherence) * 100, 1)
        except Exception:
            pass

    metrics = {
        "topic_coherence": coherence,
        "deviation_percent": deviation,
        "current_topic": current_topic,
    }
    status = "normal"
    if deviation >= 30:
        status = "critical"
    elif deviation >= 25:
        status = "warning"

    sense_data = {
        "trace_event": "focus_deviation",
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "warnings": [],
        "recommendation": "continue",
    }
    _write_sense("drift", sense_data)
    write_metric("drift.deviation_percent", deviation)
    return sense_data


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_sense(sense_id: str, data: Dict) -> None:
    SENSES_DIR.mkdir(parents=True, exist_ok=True)
    path = SENSES_DIR / f"{sense_id}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


COLLECTORS = {
    "o2": collect_o2,
    "nociception": collect_nociception,
    "chronos": collect_chronos,
    "spatial": collect_spatial,
    "vestibular": collect_vestibular,
    "echo": collect_echo,
    "drift": collect_drift,
}


def collect_all() -> Dict[str, Dict]:
    """Collect all 7 senses."""
    results = {}
    for sense_id, collector in COLLECTORS.items():
        try:
            results[sense_id] = collector()
        except Exception as e:
            results[sense_id] = {"error": str(e)}
    return results


# ── Trends ────────────────────────────────────────────────────────────────────

def get_trends(metric_name: str, hours: int = 24) -> List[Dict]:
    """Get metric trend data."""
    conn = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT metric_value, captured_at FROM metric_series WHERE metric_name = ? AND captured_at > ? ORDER BY captured_at",
        (metric_name, cutoff),
    ).fetchall()
    conn.close()
    return [{"value": r["metric_value"], "time": r["captured_at"]} for r in rows]


def get_performance_summary(hours: int = 24) -> Dict:
    """Get performance span summary."""
    conn = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    spans = conn.execute(
        """SELECT span_name, COUNT(*) as cnt,
           AVG(duration_ms) as avg_ms, MIN(duration_ms) as min_ms, MAX(duration_ms) as max_ms,
           SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
           FROM perf_spans WHERE started_at > ?
           GROUP BY span_name ORDER BY avg_ms DESC""",
        (cutoff,),
    ).fetchall()

    errors = conn.execute(
        """SELECT error_type, COUNT(*) as cnt, SUM(occurrence_count) as total_occurrences
           FROM error_records WHERE last_seen > ?
           GROUP BY error_type ORDER BY total_occurrences DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()

    return {
        "period_hours": hours,
        "spans": [dict(s) for s in spans],
        "errors": [dict(e) for e in errors],
        "total_spans": sum(s["cnt"] for s in spans) if spans else 0,
        "total_errors": sum(e["cnt"] for e in errors) if errors else 0,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "collect":
        sense_filter = None
        if "--sense" in sys.argv:
            idx = sys.argv.index("--sense")
            sense_filter = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

        if sense_filter:
            if sense_filter not in COLLECTORS:
                print(f"Unknown sense: {sense_filter}")
                print(f"Available: {', '.join(COLLECTORS.keys())}")
                sys.exit(1)
            result = COLLECTORS[sense_filter]()
            print(f"Collected {sense_filter}: {json.dumps(result.get('metrics', {}), indent=2)}")
        else:
            results = collect_all()
            print("Collected all senses:")
            for sid, data in results.items():
                if "error" in data:
                    print(f"  {sid}: ERROR - {data['error']}")
                else:
                    status = data.get("status", "unknown")
                    metrics = data.get("metrics", {})
                    primary = list(metrics.values())[0] if metrics else "N/A"
                    print(f"  {sid}: {status} (primary={primary})")

    elif cmd == "start":
        interval = 30
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            interval = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
        print(f"Starting continuous collection every {interval}s (Ctrl+C to stop)")
        try:
            while True:
                results = collect_all()
                now = datetime.now(timezone.utc).strftime("%H:%M:%S")
                statuses = [f"{sid}:{data.get('status', '?')}" for sid, data in results.items() if "error" not in data]
                print(f"[{now}] {' | '.join(statuses)}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")

    elif cmd == "snapshot":
        results = collect_all()
        # Also ingest into observability
        sys.path.insert(0, str(鲤鱼_HOME))
        try:
            from liyu_observability import ingest_all_senses
            session = ingest_all_senses()
            print(f"Snapshot complete. Session: {session.session_id}, Health: {session.overall_health:.1f}/100")
        except ImportError:
            print(f"Snapshot written to sense files. {len(results)} senses updated.")

    elif cmd == "trends":
        metric = "o2.usage_percent"
        hours = 24
        if "--metric" in sys.argv:
            idx = sys.argv.index("--metric")
            metric = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else metric
        if "--hours" in sys.argv:
            idx = sys.argv.index("--hours")
            hours = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 24

        trends = get_trends(metric, hours)
        if not trends:
            print(f"No data for {metric} in last {hours}h")
        else:
            values = [t["value"] for t in trends]
            print(f"Trend: {metric} ({len(data_points)} points, {hours}h)")
            print(f"  Min: {min(values):.1f}  Max: {max(values):.1f}  Avg: {sum(values)/len(values):.1f}")
            print(f"  Latest: {values[-1]:.1f}  First: {values[0]:.1f}")

    elif cmd == "perf":
        hours = 24
        if "--hours" in sys.argv:
            idx = sys.argv.index("--hours")
            hours = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 24

        summary = get_performance_summary(hours)
        print(f"Performance Summary ({hours}h)")
        print(f"  Total spans: {summary['total_spans']}")
        print(f"  Total errors: {summary['total_errors']}")
        if summary["spans"]:
            print("\n  Top operations by avg duration:")
            for s in summary["spans"][:10]:
                print(f"    {s['span_name']}: {s['avg_ms']:.1f}ms avg, {s['cnt']} calls, {s['errors']} errors")
        if summary["errors"]:
            print("\n  Error types:")
            for e in summary["errors"][:10]:
                print(f"    {e['error_type']}: {e['total_occurrences']} occurrences ({e['cnt']} unique)")

    elif cmd == "errors":
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM error_records WHERE resolved = 0 ORDER BY occurrence_count DESC LIMIT 20"
        ).fetchall()
        conn.close()
        if not rows:
            print("No unresolved errors.")
        else:
            print(f"Unresolved errors: {len(rows)}")
            for r in rows:
                print(f"  [{r['occurrence_count']}x] {r['error_type']}: {r['error_message'][:100]}")
                print(f"    First: {r['first_seen']}  Last: {r['last_seen']}")

    else:
        print(f"Unknown command: {cmd}")
        print("Available: collect, start, snapshot, trends, perf, errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
