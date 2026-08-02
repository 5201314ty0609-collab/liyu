#!/usr/bin/env python3
"""
鲤鱼 Observability Module — 7-Sense Agent Tracing.
=====================================================

Maps 鲤鱼 metacognitive 7-sense events to Langfuse-compatible trace format.
Supports self-hosted SQLite mode (no Langfuse dependency required).

Absorbed from: Langfuse Agent Tracing (P1#4)

Usage:
  liyu-observability.py trace <session-id>     Generate trace for a session
  liyu-observability.py dashboard              Print 7-sense radar + session scores
  liyu-observability.py ingest <sense-file>    Ingest a sense JSON snapshot
  liyu-observability.py score <session-id>     Score a session on all 7 senses
  liyu-observability.py export [--format json|lft]  Export all traces

Sense → Trace Event Mapping:
  O2 (Vitality)      → token_pressure
  Nociception (Pain)  → error_cascade
  Chronos (Time)      → session_pacing
  Spatial (Workspace) → file_churn
  Vestibular (Balance)→ tool_diversity
  Echo (Repetition)   → pattern_recurrence
  Drift (Focus)       → focus_deviation
  CTM (Thinking)      → thinking_coherence
"""

from __future__ import annotations

import atexit
import json
import sqlite3
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

鲤鱼_HOME = Path.home() / ".claude/liyu"
SENSES_DIR = 鲤鱼_HOME / "senses"
DB_PATH = 鲤鱼_HOME / "observability.db"
TRACES_DIR = 鲤鱼_HOME / "traces"

# ── Connection Pool ──────────────────────────────────────────────────────────
# Reuses a single SQLite connection per thread instead of open/init/close on
# every call.  SQLite connections are not thread-safe, so we use
# threading.local() to give each thread its own connection.  Schema
# initialization runs exactly once per connection via the _schema_initialized
# flag.

_db_local = threading.local()


def _get_db() -> sqlite3.Connection:
    """Return a per-thread SQLite connection with WAL mode and schema ready.

    The connection is cached in thread-local storage and reused across calls
    in the same thread.  Schema is initialized only on the first call per
    connection.
    """
    conn = getattr(_db_local, "connection", None)
    needs_init = not getattr(_db_local, "schema_initialized", False)

    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _db_local.connection = conn

    if needs_init:
        _init_schema(conn)
        _db_local.schema_initialized = True

    return conn


def _close_db() -> None:
    """Close the per-thread connection (called at process exit)."""
    conn = getattr(_db_local, "connection", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _db_local.connection = None
        _db_local.schema_initialized = False


atexit.register(_close_db)

# ── 7-Sense Definitions ──────────────────────────────────────────────────────

SENSE_META = {
    "o2": {
        "name": "O2 (Vitality)",
        "trace_event": "token_pressure",
        "description": "Token/context pressure monitoring",
        "unit": "percent",
        "warning_threshold": 70,
        "critical_threshold": 85,
        "lower_is_better": True,
    },
    "nociception": {
        "name": "Nociception (Pain)",
        "trace_event": "error_cascade",
        "description": "Error cascade detection",
        "unit": "errors_per_window",
        "warning_threshold": 3,
        "critical_threshold": 5,
        "lower_is_better": True,
    },
    "chronos": {
        "name": "Chronos (Time)",
        "trace_event": "session_pacing",
        "description": "Session pacing and idle detection",
        "unit": "idle_seconds",
        "warning_threshold": 300,
        "critical_threshold": 600,
        "lower_is_better": True,
    },
    "spatial": {
        "name": "Spatial (Workspace)",
        "trace_event": "file_churn",
        "description": "File churn rate monitoring",
        "unit": "files_per_call",
        "warning_threshold": 5,
        "critical_threshold": 10,
        "lower_is_better": True,
    },
    "vestibular": {
        "name": "Vestibular (Balance)",
        "trace_event": "tool_diversity",
        "description": "Tool diversity balance",
        "unit": "dominant_percent",
        "warning_threshold": 70,
        "critical_threshold": 80,
        "lower_is_better": True,
    },
    "echo": {
        "name": "Echo (Repetition)",
        "trace_event": "pattern_recurrence",
        "description": "Error pattern recurrence detection",
        "unit": "repeated_signatures",
        "warning_threshold": 2,
        "critical_threshold": 3,
        "lower_is_better": True,
    },
    "drift": {
        "name": "Drift (Focus)",
        "trace_event": "focus_deviation",
        "description": "Topic coherence / focus deviation",
        "unit": "deviation_percent",
        "warning_threshold": 25,
        "critical_threshold": 30,
        "lower_is_better": True,
    },
    "ctm": {
        "name": "CTM (Thinking)",
        "trace_event": "thinking_coherence",
        "description": "CTM thinking stream coherence and health",
        "unit": "coherence_ratio",
        "warning_threshold": 0.3,
        "critical_threshold": 0.1,
        "lower_is_better": False,  # Higher coherence is better
    },
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class SenseScore:
    sense_id: str
    value: float
    status: str  # normal / warning / critical / unknown
    raw_metrics: Dict = field(default_factory=dict)


@dataclass
class SessionScore:
    session_id: str
    timestamp: str
    senses: Dict[str, SenseScore] = field(default_factory=dict)
    overall_health: float = 100.0
    tags: List[str] = field(default_factory=list)

    def to_radar(self) -> Dict:
        """Generate radar chart data for the 7 senses."""
        labels = []
        values = []
        thresholds_warning = []
        thresholds_critical = []
        for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift", "ctm"]:
            meta = SENSE_META[sid]
            labels.append(meta["name"])
            if sid in self.senses:
                values.append(round(self.senses[sid].value, 1))
            else:
                values.append(0)
            thresholds_warning.append(meta["warning_threshold"])
            thresholds_critical.append(meta["critical_threshold"])
        return {
            "labels": labels,
            "values": values,
            "warningThresholds": thresholds_warning,
            "criticalThresholds": thresholds_critical,
            "overallHealth": round(self.overall_health, 1),
        }


# ── Database Layer ───────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """Get SQLite connection with schema initialized."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            overall_health REAL DEFAULT 100.0,
            tags TEXT DEFAULT '[]',
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS sense_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            sense_id TEXT NOT NULL,
            value REAL,
            status TEXT DEFAULT 'unknown',
            raw_metrics TEXT DEFAULT '{}',
            captured_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
        CREATE TABLE IF NOT EXISTS trace_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            trace_event TEXT NOT NULL,
            level TEXT DEFAULT 'DEFAULT',
            metadata TEXT DEFAULT '{}',
            start_time TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_session ON sense_snapshots(session_id);
        CREATE INDEX IF NOT EXISTS idx_events_session ON trace_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(timestamp);
    """)


# ── Sense Ingestion ──────────────────────────────────────────────────────────

def ingest_sense_file(filepath: str, session_id: Optional[str] = None) -> SessionScore:
    """Ingest a sense JSON file and write trace events to DB."""
    path = Path(filepath)
    sense_id = path.stem  # e.g. "o2", "nociception"
    if sense_id not in SENSE_META:
        raise ValueError(f"Unknown sense: {sense_id}")

    with open(path) as f:
        data = json.load(f)

    meta = SENSE_META[sense_id]
    value = _extract_value(sense_id, data)
    status = _compute_status(sense_id, value)
    metrics = data.get("metrics", {})

    if session_id is None:
        session_id = f"sess-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Upsert session
    conn.execute(
        "INSERT OR REPLACE INTO sessions(session_id, timestamp) VALUES (?, ?)",
        (session_id, now),
    )
    # Write snapshot
    conn.execute(
        """INSERT INTO sense_snapshots(session_id, sense_id, value, status, raw_metrics, captured_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, sense_id, value, status, json.dumps(metrics), now),
    )
    # Write trace event
    level = "WARNING" if status == "warning" else "ERROR" if status == "critical" else "DEFAULT"
    conn.execute(
        """INSERT INTO trace_events(session_id, trace_event, level, metadata, start_time)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, meta["trace_event"], level, json.dumps({"value": value, "status": status}), now),
    )
    conn.commit()

    score = SenseScore(sense_id=sense_id, value=value, status=status, raw_metrics=metrics)
    return SessionScore(session_id=session_id, timestamp=now, senses={sense_id: score})


def ingest_ctm_state(session_id: Optional[str] = None) -> SessionScore:
    """Ingest CTM state directly from the CTM core engine."""
    if session_id is None:
        session_id = f"sess-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    try:
        import sys
        ctm_dir = str(鲤鱼_HOME / "ctm")
        if ctm_dir not in sys.path:
            sys.path.insert(0, str(鲤鱼_HOME.parent))
        from liyu.ctm.ctm_core import get_ctm_core
        ctm = get_ctm_core()
        state = ctm.get_ctm_state()

        # Compute coherence as primary metric (0-1, higher is better)
        coherence = state.current_coherence
        metrics = {
            "coherence": coherence,
            "active_streams": state.active_streams,
            "total_streams": state.total_streams_created,
            "compute_stats": state.compute_stats,
            "oscillator_stats": state.oscillator_stats,
        }
    except Exception:
        # Fallback if CTM not available
        coherence = 1.0
        metrics = {"coherence": coherence, "active_streams": 0, "total_streams": 0}

    meta = SENSE_META["ctm"]
    status = _compute_status("ctm", coherence)

    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT OR REPLACE INTO sessions(session_id, timestamp) VALUES (?, ?)",
        (session_id, now),
    )
    conn.execute(
        """INSERT INTO sense_snapshots(session_id, sense_id, value, status, raw_metrics, captured_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, "ctm", coherence, status, json.dumps(metrics), now),
    )
    level = "WARNING" if status == "warning" else "ERROR" if status == "critical" else "DEFAULT"
    conn.execute(
        """INSERT INTO trace_events(session_id, trace_event, level, metadata, start_time)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, meta["trace_event"], level,
         json.dumps({"value": coherence, "status": status, "active_streams": metrics.get("active_streams", 0)}), now),
    )
    conn.commit()

    score = SenseScore(sense_id="ctm", value=coherence, status=status, raw_metrics=metrics)
    return SessionScore(session_id=session_id, timestamp=now, senses={"ctm": score})


def ingest_all_senses(session_id: Optional[str] = None) -> SessionScore:
    """Ingest all sense files from senses/ directory."""
    if session_id is None:
        session_id = f"sess-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    scores: Dict[str, SenseScore] = {}
    for sense_id in SENSE_META:
        if sense_id == "ctm":
            # CTM pulls from engine directly
            try:
                result = ingest_ctm_state(session_id)
                scores.update(result.senses)
            except Exception:
                pass
        else:
            sense_file = SENSES_DIR / f"{sense_id}.json"
            if sense_file.exists():
                result = ingest_sense_file(str(sense_file), session_id)
                scores.update(result.senses)

    overall = _compute_overall(scores)
    conn = _get_db()
    conn.execute("UPDATE sessions SET overall_health = ? WHERE session_id = ?", (overall, session_id))
    conn.commit()

    return SessionScore(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        senses=scores,
        overall_health=overall,
    )


# ── Value Extraction ─────────────────────────────────────────────────────────

def _extract_value(sense_id: str, data: Dict) -> float:
    """Extract the primary metric value from a sense JSON blob."""
    metrics = data.get("metrics", {})
    if sense_id == "o2":
        return float(metrics.get("usage_percent", 0))
    elif sense_id == "nociception":
        return float(metrics.get("errors_in_window", 0))
    elif sense_id == "chronos":
        return float(metrics.get("idle_seconds", 0))
    elif sense_id == "spatial":
        return float(metrics.get("files_per_call", 0))
    elif sense_id == "vestibular":
        return float(metrics.get("dominant_percentage", 0))
    elif sense_id == "echo":
        return float(metrics.get("repeated_signatures", 0))
    elif sense_id == "drift":
        return float(metrics.get("deviation_percent", 0))
    elif sense_id == "ctm":
        return float(metrics.get("coherence", 0))
    return 0.0


def _compute_status(sense_id: str, value: float) -> str:
    """Determine status from value against thresholds."""
    meta = SENSE_META[sense_id]
    if meta.get("lower_is_better", True):
        # Normal: higher value = worse (e.g. error count, pressure)
        if value >= meta["critical_threshold"]:
            return "critical"
        elif value >= meta["warning_threshold"]:
            return "warning"
        return "normal"
    else:
        # Reversed: lower value = worse (e.g. coherence, health)
        if value <= meta["critical_threshold"]:
            return "critical"
        elif value <= meta["warning_threshold"]:
            return "warning"
        return "normal"


def _compute_overall(scores: Dict[str, SenseScore]) -> float:
    """Compute overall session health from sense scores (0-100)."""
    if not scores:
        return 100.0
    penalties = 0.0
    for sid, score in scores.items():
        meta = SENSE_META[sid]
        if meta.get("lower_is_better", True):
            # Normal: higher = worse (ratio against critical)
            ratio = score.value / meta["critical_threshold"] if meta["critical_threshold"] > 0 else 0
        else:
            # Reversed (CTM): lower = worse (inverse ratio)
            ratio = (1.0 - score.value) / (1.0 - meta["critical_threshold"]) if meta["critical_threshold"] < 1.0 else 0
        # Warning zone: 0.4 penalty, Critical zone: 0.8 penalty
        if score.status == "critical":
            penalties += min(max(ratio, 0), 1.0) * (100 / len(SENSE_META)) * 0.8
        elif score.status == "warning":
            penalties += min(max(ratio, 0), 1.0) * (100 / len(SENSE_META)) * 0.4
    return max(0.0, 100.0 - penalties)


# ── Trace Generation ─────────────────────────────────────────────────────────

def generate_trace(session_id: str) -> Dict:
    """Generate a Langfuse-compatible trace JSON for a session."""
    conn = _get_db()

    session = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not session:
        raise ValueError(f"Session not found: {session_id}")

    snapshots = conn.execute(
        "SELECT * FROM sense_snapshots WHERE session_id = ? ORDER BY captured_at",
        (session_id,),
    ).fetchall()

    events = conn.execute(
        "SELECT * FROM trace_events WHERE session_id = ? ORDER BY start_time",
        (session_id,),
    ).fetchall()

    first_ts = snapshots[0]["captured_at"] if snapshots else session["timestamp"]
    last_ts = snapshots[-1]["captured_at"] if snapshots else session["timestamp"]

    observations = []
    for evt in events:
        meta = json.loads(evt["metadata"])
        sense_meta = None
        for sid, sm in SENSE_META.items():
            if sm["trace_event"] == evt["trace_event"]:
                sense_meta = sm
                break

        observations.append({
            "name": evt["trace_event"],
            "type": "EVENT",
            "startTime": evt["start_time"],
            "level": evt["level"],
            "statusMessage": f"{sense_meta['name']}: {meta.get('status', 'unknown')} (value={meta.get('value', '?')})" if sense_meta else "",
            "metadata": {
                "sense_id": sense_meta["name"] if sense_meta else evt["trace_event"],
                "value": meta.get("value"),
                "status": meta.get("status"),
                "unit": sense_meta["unit"] if sense_meta else "",
            },
        })

    trace = {
        "trace": {
            "id": session_id,
            "name": f"鲤鱼 Session {session_id}",
            "userId": "liyu-agent",
            "sessionId": session_id,
            "timestamp": first_ts,
            "metadata": {
                "overall_health": session["overall_health"],
                "sense_count": len(snapshots),
                "tags": json.loads(session["tags"]) if session["tags"] else [],
                "framework": "鲤鱼 v1.3.0",
                "observability_version": "1.0.0",
            },
            "tags": ["liyu", "7-sense", "metacognition"] + (json.loads(session["tags"]) if session["tags"] else []),
        },
        "observations": observations,
    }
    return trace


def trace_command(session_id: str, output_format: str = "json") -> str:
    """CLI: generate trace for a session."""
    try:
        trace = generate_trace(session_id)
    except ValueError:
        # Try ingest-all first, then generate
        result = ingest_all_senses(session_id)
        trace = generate_trace(session_id)

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TRACES_DIR / f"{session_id}.trace.json"
    with open(out_path, "w") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)

    if output_format == "lft":
        # Langfuse-compatible flattened format
        lft_lines = [json.dumps(trace["trace"])]
        for obs in trace["observations"]:
            lft_lines.append(json.dumps(obs))
        lft_out = "\n".join(lft_lines)
        lft_path = TRACES_DIR / f"{session_id}.trace.jsonl"
        with open(lft_path, "w") as f:
            f.write(lft_out)
        return f"Trace exported to {out_path} (JSON) and {lft_path} (JSONL/Langfuse)"

    return f"Trace written to {out_path}"


def score_command(session_id: str) -> Dict:
    """CLI: score a session on all 7 senses, return radar data."""
    conn = _get_db()
    snapshots = conn.execute(
        "SELECT * FROM sense_snapshots WHERE session_id = ?", (session_id,)
    ).fetchall()

    senses = {}
    for snap in snapshots:
        sid = snap["sense_id"]
        metrics = json.loads(snap["raw_metrics"]) if snap["raw_metrics"] else {}
        senses[sid] = SenseScore(
            sense_id=sid, value=snap["value"], status=snap["status"], raw_metrics=metrics
        )

    overall = _compute_overall(senses)
    session_score = SessionScore(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        senses=senses,
        overall_health=overall,
    )
    return session_score.to_radar()


def dashboard_command() -> str:
    """CLI: print a 7-sense dashboard for the latest session."""
    conn = _get_db()
    latest = conn.execute(
        "SELECT session_id, timestamp, overall_health FROM sessions ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    if not latest:
        return "No session data found. Run 'ingest' first."

    snapshots = conn.execute(
        "SELECT sense_id, value, status FROM sense_snapshots WHERE session_id = ?",
        (latest["session_id"],),
    ).fetchall()

    scores = {s["sense_id"]: SenseScore(sense_id=s["sense_id"], value=s["value"], status=s["status"])
              for s in snapshots}
    session = SessionScore(
        session_id=latest["session_id"],
        timestamp=latest["timestamp"],
        senses=scores,
        overall_health=latest["overall_health"],
    )
    radar = session.to_radar()

    lines = [
        f"{'='*60}",
        f"  鲤鱼 7-Sense Dashboard  |  Session: {latest['session_id']}",
        f"  Captured: {latest['timestamp']}",
        f"  Overall Health: {radar['overallHealth']:.1f}/100",
        f"{'='*60}",
        "",
        f"  {'Sense':<24} {'Value':>8}  {'Status':>10}  {'Threshold':>12}",
        f"  {'-'*58}",
    ]

    status_icons = {"normal": "O", "warning": "!", "critical": "X", "unknown": "?"}
    for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift", "ctm"]:
        meta = SENSE_META[sid]
        score = scores.get(sid)
        if score:
            icon = status_icons.get(score.status, "?")
            thresh = f"{meta['critical_threshold']}{meta['unit']}"
            lines.append(
                f"  [{icon}] {meta['name']:<20} {score.value:>8.1f}  {score.status:>10}  {thresh:>12}"
            )
        else:
            lines.append(f"  [?] {meta['name']:<20} {'N/A':>8}  {'no data':>10}")

    lines.extend([
        "",
        "  Radar Chart Data (JSON):",
        f"  {json.dumps(radar)}",
    ])

    return "\n".join(lines)


def export_command(output_format: str = "json") -> str:
    """Export all traces to files."""
    conn = _get_db()
    sessions = conn.execute("SELECT session_id FROM sessions ORDER BY timestamp DESC").fetchall()

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    exported = []
    for s in sessions:
        trace = generate_trace(s["session_id"])
        out_path = TRACES_DIR / f"{s['session_id']}.trace.json"
        with open(out_path, "w") as f:
            json.dump(trace, f, indent=2, ensure_ascii=False)
        exported.append(str(out_path))

    return f"Exported {len(exported)} traces:\n" + "\n".join(f"  {p}" for p in exported)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "trace":
        if len(sys.argv) < 3:
            print("Usage: liyu-observability.py trace <session-id> [--format json|lft]")
            sys.exit(1)
        sid = sys.argv[2]
        fmt = "json"
        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            fmt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "json"
        print(trace_command(sid, fmt))

    elif cmd == "dashboard":
        print(dashboard_command())

    elif cmd == "ingest":
        if len(sys.argv) < 3:
            # Ingest all senses
            result = ingest_all_senses()
            radar = result.to_radar()
            print(f"Ingested all 7 senses → session {result.session_id}")
            print(f"Overall health: {radar['overallHealth']:.1f}/100")
        else:
            target = sys.argv[2]
            if target == "all":
                result = ingest_all_senses()
                radar = result.to_radar()
                print(f"Ingested all 7 senses → session {result.session_id}")
                print(f"Overall health: {radar['overallHealth']:.1f}/100")
            else:
                sid = sys.argv[3] if len(sys.argv) > 3 else None
                result = ingest_sense_file(target, sid)
                print(f"Ingested {target} → session {result.session_id}")

    elif cmd == "ingest-ctm":
        result = ingest_ctm_state()
        print(f"Ingested CTM state → session {result.session_id}")
        print(f"Coherence: {result.senses['ctm'].value:.4f} ({result.senses['ctm'].status})")

    elif cmd == "score":
        if len(sys.argv) < 3:
            print("Usage: liyu-observability.py score <session-id>")
            sys.exit(1)
        radar = score_command(sys.argv[1])
        print(json.dumps(radar, indent=2, ensure_ascii=False))

    elif cmd == "export":
        fmt = "json"
        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            fmt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "json"
        print(export_command(fmt))

    elif cmd == "stats":
        conn = _get_db()
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]
        snapshot_count = conn.execute("SELECT COUNT(*) FROM sense_snapshots").fetchone()[0]
        print(f"Sessions: {session_count}  |  Snapshots: {snapshot_count}  |  Trace Events: {event_count}")

    else:
        print(f"Unknown command: {cmd}")
        print("Available: trace, dashboard, ingest, ingest-ctm, score, export, stats")
        sys.exit(1)


if __name__ == "__main__":
    main()
