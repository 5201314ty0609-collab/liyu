#!/usr/bin/env python3
"""
鲤鱼 Alert Engine — Threshold-based alerting with cooldown and escalation.
=============================================================================

Monitors 7-Sense metrics and triggers alerts when thresholds are breached.
Supports cooldown periods, escalation chains, and notification delivery.

Usage:
  liyu-alert-engine.py check                  Check all senses and fire alerts
  liyu-alert-engine.py check --sense o2       Check a specific sense
  liyu-alert-engine.py history [--limit 20]   Show alert history
  liyu-alert-engine.py active                  Show active (unresolved) alerts
  liyu-alert-engine.py ack <alert-id>          Acknowledge an alert
  liyu-alert-engine.py config                  Show current alert config
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

鲤鱼_HOME = Path.home() / ".claude/liyu"
SENSES_DIR = 鲤鱼_HOME / "senses"
DB_PATH = 鲤鱼_HOME / "observability.db"
ALERTS_LOG = 鲤鱼_HOME / "alerts.jsonl"
ALERT_CONFIG_PATH = 鲤鱼_HOME / "alert-config.json"

# ── Default Alert Configuration ───────────────────────────────────────────────

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "cooldown_minutes": 5,
    "escalation_after_minutes": 15,
    "max_alerts_per_hour": 20,
    "auto_resolve_after_minutes": 30,
    "notification_channels": ["file", "stdout"],
    "severity_rules": {
        "critical": {
            "notify_immediately": True,
            "repeat_interval_minutes": 5,
            "requires_ack": True,
        },
        "warning": {
            "notify_immediately": True,
            "repeat_interval_minutes": 15,
            "requires_ack": False,
        },
        "info": {
            "notify_immediately": False,
            "repeat_interval_minutes": 60,
            "requires_ack": False,
        },
    },
    "sense_overrides": {},
}


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class Alert:
    alert_id: str
    sense_id: str
    severity: str  # critical / warning / info
    message: str
    value: float
    threshold: float
    status: str  # active / acked / resolved
    created_at: str
    updated_at: str
    acked_at: Optional[str] = None
    resolved_at: Optional[str] = None
    escalation_level: int = 0
    notify_count: int = 0
    metadata: Dict = field(default_factory=dict)


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> Dict:
    if ALERT_CONFIG_PATH.exists():
        with open(ALERT_CONFIG_PATH) as f:
            user_cfg = json.load(f)
        merged = {**DEFAULT_CONFIG, **user_cfg}
        merged["severity_rules"] = {**DEFAULT_CONFIG["severity_rules"], **user_cfg.get("severity_rules", {})}
        return merged
    return DEFAULT_CONFIG.copy()


def save_config(config: Dict) -> None:
    ALERT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ── Database ──────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_alert_schema(conn)
    return conn


def _init_alert_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY,
            sense_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            value REAL,
            threshold REAL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            acked_at TEXT,
            resolved_at TEXT,
            escalation_level INTEGER DEFAULT 0,
            notify_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
        CREATE INDEX IF NOT EXISTS idx_alerts_sense ON alerts(sense_id);
        CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
        CREATE INDEX IF NOT EXISTS idx_alert_events_id ON alert_events(alert_id);
    """)


# ── Sense Loading ─────────────────────────────────────────────────────────────

SENSE_META = {
    "o2": {"name": "O2 (Vitality)", "warning": 70, "critical": 85, "unit": "%", "metric_key": "usage_percent"},
    "nociception": {"name": "Nociception (Pain)", "warning": 3, "critical": 5, "unit": "errors", "metric_key": "errors_per_window"},
    "chronos": {"name": "Chronos (Time)", "warning": 300, "critical": 600, "unit": "s", "metric_key": "idle_seconds"},
    "spatial": {"name": "Spatial (Workspace)", "warning": 5, "critical": 10, "unit": "files/call", "metric_key": "files_per_call"},
    "vestibular": {"name": "Vestibular (Balance)", "warning": 70, "critical": 80, "unit": "%", "metric_key": "dominant_percent"},
    "echo": {"name": "Echo (Repetition)", "warning": 2, "critical": 3, "unit": "sigs", "metric_key": "repeated_signatures"},
    "drift": {"name": "Drift (Focus)", "warning": 25, "critical": 30, "unit": "%", "metric_key": "deviation_percent"},
}


def load_sense(sense_id: str) -> Optional[Dict]:
    path = SENSES_DIR / f"{sense_id}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def extract_metric(sense_id: str, data: Dict) -> float:
    metrics = data.get("metrics", {})
    key = SENSE_META.get(sense_id, {}).get("metric_key", "")
    return float(metrics.get(key, 0))


# ── Cooldown & Dedup ─────────────────────────────────────────────────────────

def _is_in_cooldown(conn: sqlite3.Connection, sense_id: str, severity: str, cooldown_minutes: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
    row = conn.execute(
        "SELECT created_at FROM alerts WHERE sense_id = ? AND severity = ? AND created_at > ? ORDER BY created_at DESC LIMIT 1",
        (sense_id, severity, cutoff),
    ).fetchone()
    return row is not None


def _count_recent_alerts(conn: sqlite3.Connection, hours: int = 1) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute("SELECT COUNT(*) FROM alerts WHERE created_at > ?", (cutoff,)).fetchone()
    return row[0]


def _has_active_alert(conn: sqlite3.Connection, sense_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT alert_id FROM alerts WHERE sense_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (sense_id,),
    ).fetchone()
    return row["alert_id"] if row else None


# ── Alert Operations ──────────────────────────────────────────────────────────

def fire_alert(sense_id: str, severity: str, value: float, threshold: float, config: Dict) -> Optional[Alert]:
    """Create and record a new alert if not in cooldown."""
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Rate limit
    if _count_recent_alerts(conn) >= config.get("max_alerts_per_hour", 20):
        conn.close()
        return None

    # Cooldown check
    cooldown = config.get("cooldown_minutes", 5)
    if _is_in_cooldown(conn, sense_id, severity, cooldown):
        conn.close()
        return None

    # Check for existing active alert on this sense
    existing_id = _has_active_alert(conn, sense_id)
    if existing_id:
        # Escalate if severity increased
        existing = conn.execute("SELECT severity FROM alerts WHERE alert_id = ?", (existing_id,)).fetchone()
        sev_order = {"info": 0, "warning": 1, "critical": 2}
        if sev_order.get(severity, 0) > sev_order.get(existing["severity"], 0):
            conn.execute(
                "UPDATE alerts SET severity = ?, value = ?, threshold = ?, updated_at = ?, escalation_level = escalation_level + 1 WHERE alert_id = ?",
                (severity, value, threshold, now, existing_id),
            )
            conn.execute(
                "INSERT INTO alert_events(alert_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
                (existing_id, "escalated", f"Escalated to {severity}: value={value}, threshold={threshold}", now),
            )
            conn.commit()
        conn.close()
        return None

    meta = SENSE_META.get(sense_id, {})
    message = f"[{severity.upper()}] {meta.get('name', sense_id)}: value={value:.1f}{meta.get('unit', '')} exceeds threshold={threshold:.1f}"

    alert = Alert(
        alert_id=f"alert-{uuid.uuid4().hex[:12]}",
        sense_id=sense_id,
        severity=severity,
        message=message,
        value=value,
        threshold=threshold,
        status="active",
        created_at=now,
        updated_at=now,
    )

    conn.execute(
        """INSERT INTO alerts(alert_id, sense_id, severity, message, value, threshold, status,
           created_at, updated_at, escalation_level, notify_count, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (alert.alert_id, alert.sense_id, alert.severity, alert.message,
         alert.value, alert.threshold, alert.status, alert.created_at, alert.updated_at,
         alert.escalation_level, alert.notify_count, json.dumps(alert.metadata)),
    )
    conn.execute(
        "INSERT INTO alert_events(alert_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
        (alert.alert_id, "fired", message, now),
    )
    conn.commit()
    conn.close()

    # Append to JSONL log
    ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_LOG, "a") as f:
        f.write(json.dumps(asdict(alert), ensure_ascii=False) + "\n")

    return alert


def ack_alert(alert_id: str) -> bool:
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT status FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    if not row or row["status"] != "active":
        conn.close()
        return False
    conn.execute(
        "UPDATE alerts SET status = 'acked', acked_at = ?, updated_at = ? WHERE alert_id = ?",
        (now, now, alert_id),
    )
    conn.execute(
        "INSERT INTO alert_events(alert_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
        (alert_id, "acked", "Alert acknowledged by user/system", now),
    )
    conn.commit()
    conn.close()
    return True


def resolve_alert(alert_id: str, reason: str = "auto-resolved") -> bool:
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT status FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    if not row or row["status"] == "resolved":
        conn.close()
        return False
    conn.execute(
        "UPDATE alerts SET status = 'resolved', resolved_at = ?, updated_at = ? WHERE alert_id = ?",
        (now, now, alert_id),
    )
    conn.execute(
        "INSERT INTO alert_events(alert_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
        (alert_id, "resolved", reason, now),
    )
    conn.commit()
    conn.close()
    return True


def auto_resolve_stale(config: Dict) -> int:
    """Auto-resolve alerts that have been active too long."""
    conn = _get_db()
    now = datetime.now(timezone.utc)
    auto_minutes = config.get("auto_resolve_after_minutes", 30)
    cutoff = (now - timedelta(minutes=auto_minutes)).isoformat()
    rows = conn.execute(
        "SELECT alert_id FROM alerts WHERE status IN ('active', 'acked') AND updated_at < ?", (cutoff,)
    ).fetchall()
    conn.close()
    count = 0
    for row in rows:
        if resolve_alert(row["alert_id"], f"auto-resolved after {auto_minutes}min stale"):
            count += 1
    return count


# ── Check Logic ───────────────────────────────────────────────────────────────

def check_sense(sense_id: str, config: Dict) -> List[Alert]:
    """Check a single sense and fire alerts if needed."""
    data = load_sense(sense_id)
    if data is None:
        return []

    meta = SENSE_META.get(sense_id, {})
    value = extract_metric(sense_id, data)
    alerts = []

    # Apply overrides
    overrides = config.get("sense_overrides", {}).get(sense_id, {})
    crit_thresh = overrides.get("critical", meta.get("critical", 100))
    warn_thresh = overrides.get("warning", meta.get("warning", 70))

    if value >= crit_thresh:
        alert = fire_alert(sense_id, "critical", value, crit_thresh, config)
        if alert:
            alerts.append(alert)
    elif value >= warn_thresh:
        alert = fire_alert(sense_id, "warning", value, warn_thresh, config)
        if alert:
            alerts.append(alert)
    else:
        # Value is normal -- resolve any active alerts for this sense
        conn = _get_db()
        active = conn.execute(
            "SELECT alert_id FROM alerts WHERE sense_id = ? AND status IN ('active', 'acked')", (sense_id,)
        ).fetchall()
        conn.close()
        for row in active:
            resolve_alert(row["alert_id"], f"value={value:.1f} returned to normal")

    return alerts


def check_all_senses(config: Optional[Dict] = None) -> List[Alert]:
    """Check all 7 senses and fire/resolve alerts."""
    if config is None:
        config = load_config()

    all_alerts = []
    for sense_id in SENSE_META:
        alerts = check_sense(sense_id, config)
        all_alerts.extend(alerts)

    # Auto-resolve stale
    auto_resolve_stale(config)

    return all_alerts


# ── Query ─────────────────────────────────────────────────────────────────────

def get_active_alerts() -> List[Dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE status IN ('active', 'acked') ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_alert_history(limit: int = 20) -> List[Dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_alert_events(alert_id: str) -> List[Dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM alert_events WHERE alert_id = ? ORDER BY created_at", (alert_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    config = load_config()

    if cmd == "check":
        sense_filter = None
        if "--sense" in sys.argv:
            idx = sys.argv.index("--sense")
            sense_filter = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

        if sense_filter:
            alerts = check_sense(sense_filter, config)
        else:
            alerts = check_all_senses(config)

        if alerts:
            print(f"New alerts fired: {len(alerts)}")
            for a in alerts:
                print(f"  [{a.severity.upper()}] {a.message}")
        else:
            print("No new alerts. All senses within thresholds.")

        active = get_active_alerts()
        if active:
            print(f"\nActive alerts: {len(active)}")
            for a in active:
                print(f"  {a['alert_id']} [{a['severity'].upper()}] {a['sense_id']}: {a['message']}")

    elif cmd == "history":
        limit = 20
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            limit = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 20
        history = get_alert_history(limit)
        if not history:
            print("No alert history.")
        else:
            print(f"Alert history (last {limit}):")
            for a in history:
                status_icon = {"active": "[!]", "acked": "[~]", "resolved": "[ok]"}.get(a["status"], "[?]")
                print(f"  {status_icon} {a['alert_id']} | {a['severity']:>8} | {a['sense_id']:<14} | {a['created_at']}")

    elif cmd == "active":
        active = get_active_alerts()
        if not active:
            print("No active alerts.")
        else:
            print(f"Active alerts: {len(active)}")
            for a in active:
                print(f"  {a['alert_id']} [{a['severity'].upper()}] {a['sense_id']}")
                print(f"    {a['message']}")
                print(f"    Created: {a['created_at']}  Escalations: {a['escalation_level']}")

    elif cmd == "ack":
        if len(sys.argv) < 3:
            print("Usage: liyu-alert-engine.py ack <alert-id>")
            sys.exit(1)
        alert_id = sys.argv[2]
        if ack_alert(alert_id):
            print(f"Acknowledged: {alert_id}")
        else:
            print(f"Cannot ack {alert_id} (not found or not active)")
            sys.exit(1)

    elif cmd == "config":
        print(json.dumps(config, indent=2, ensure_ascii=False))

    elif cmd == "init-config":
        if not ALERT_CONFIG_PATH.exists():
            save_config(DEFAULT_CONFIG)
            print(f"Default config written to {ALERT_CONFIG_PATH}")
        else:
            print(f"Config already exists at {ALERT_CONFIG_PATH}")

    else:
        print(f"Unknown command: {cmd}")
        print("Available: check, history, active, ack, config, init-config")
        sys.exit(1)


if __name__ == "__main__":
    main()
