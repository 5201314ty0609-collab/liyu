#!/usr/bin/env python3
"""
鲤鱼 Event Integration — Unified nervous system connecting all 鲤鱼 subsystems.
====================================================================================

Bridges event-bus, observability, alerting, memory, automation, metrics, and
reflection into a single coherent event flow with handler registration,
pattern-based routing, and SQLite-backed persistence.

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │                    Event Sources                             │
  │  bus.py  observability  alert-engine  metrics  automation   │
  └──────────┬──────────────────────────────────────────────────┘
             │  emit / ingest
  ┌──────────▼──────────────────────────────────────────────────┐
  │              liyu-event-integration.py                    │
  │  ┌───────────┐ ┌───────────┐ ┌────────────┐ ┌───────────┐  │
  │  │  Handler   │ │  Router   │ │  Filter    │ │  Store    │  │
  │  │  Registry  │ │  (pattern │ │  Chain     │ │  (SQLite) │  │
  │  │            │ │  match)   │ │            │ │           │  │
  │  └───────────┘ └───────────┘ └────────────┘ └───────────┘  │
  └──────────┬──────────────────────────────────────────────────┘
             │  dispatch
  ┌──────────▼──────────────────────────────────────────────────┐
  │                   Event Sinks                                │
  │  observability  alert-engine  auto-memory  reflection       │
  │  dashboard      story.jsonl   heartbeat    knowledge-base   │
  └─────────────────────────────────────────────────────────────┘

Usage:
  liyu-event-integration.py emit <type> <source> [--payload '{}']
  liyu-event-integration.py handler list
  liyu-event-integration.py handler register <name> <pattern> [--priority N]
  liyu-event-integration.py handler unregister <name>
  liyu-event-integration.py route [--type X] [--source X] [--since ISO] [--limit N]
  liyu-event-integration.py stats
  liyu-event-integration.py replay <event-id>
  liyu-event-integration.py bridge --all | --source <name>
  liyu-event-integration.py prune [--days N]
  liyu-event-integration.py health
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Paths ──────────────────────────────────────────────────────────────────────

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
EVENT_BUS_DIR = 鲤鱼_HOME / "event-bus"
EVENTS_FILE = EVENT_BUS_DIR / "events.jsonl"
DB_PATH = 鲤鱼_HOME / "event-integration.db"
HANDLERS_FILE = 鲤鱼_HOME / "event-handlers.json"
ROUTING_RULES_FILE = 鲤鱼_HOME / "event-routing-rules.json"
INTEGRATION_LOG = 鲤鱼_HOME / "event-integration.log"

# Subsystem paths
STORY_FILE = 鲤鱼_HOME / "story.jsonl"
REFLECTIONS_FILE = 鲤鱼_HOME / "reflections.jsonl"
SENSES_DIR = 鲤鱼_HOME / "senses"
OBSERVABILITY_DB = 鲤鱼_HOME / "observability.db"
ALERTS_LOG = 鲤鱼_HOME / "alerts.jsonl"
HEARTBEATS_DIR = 鲤鱼_HOME / "heartbeats"

# ── Event Types (superset of bus.py + integration-specific) ───────────────────

EVENT_TYPES = {
    # Session lifecycle
    "session.start":         {"severity": "info",     "category": "session"},
    "session.end":           {"severity": "info",     "category": "session"},
    "session.heartbeat":     {"severity": "info",     "category": "session"},

    # Sense alerts
    "sense.alert":           {"severity": "warn",     "category": "sense"},
    "sense.o2":              {"severity": "warn",     "category": "sense"},
    "sense.nociception":     {"severity": "critical", "category": "sense"},
    "sense.chronos":         {"severity": "warn",     "category": "sense"},
    "sense.spatial":         {"severity": "warn",     "category": "sense"},
    "sense.vestibular":      {"severity": "warn",     "category": "sense"},
    "sense.echo":            {"severity": "warn",     "category": "sense"},
    "sense.drift":           {"severity": "warn",     "category": "sense"},
    "sense.aesthetic":       {"severity": "info",     "category": "sense"},

    # Knowledge graph
    "knowledge.add":         {"severity": "info",     "category": "knowledge"},
    "knowledge.edge":        {"severity": "info",     "category": "knowledge"},
    "knowledge.extract":     {"severity": "info",     "category": "knowledge"},
    "knowledge.search":      {"severity": "info",     "category": "knowledge"},

    # Self-healing
    "heal.observe":          {"severity": "info",     "category": "heal"},
    "heal.warn":             {"severity": "warn",     "category": "heal"},
    "heal.act":              {"severity": "warn",     "category": "heal"},
    "heal.escalate":         {"severity": "warn",     "category": "heal"},
    "heal.resolve":          {"severity": "info",     "category": "heal"},

    # Tool calls
    "tool.call":             {"severity": "info",     "category": "tool"},
    "tool.error":            {"severity": "warn",     "category": "tool"},
    "tool.timeout":          {"severity": "warn",     "category": "tool"},

    # Agent orchestration
    "agent.delegate":        {"severity": "info",     "category": "agent"},
    "agent.complete":        {"severity": "info",     "category": "agent"},
    "agent.error":           {"severity": "warn",     "category": "agent"},

    # Evolution
    "evolution.cycle":       {"severity": "info",     "category": "evolution"},
    "evolution.promote":     {"severity": "info",     "category": "evolution"},
    "evolution.demote":      {"severity": "warn",     "category": "evolution"},

    # Automation
    "automation.task":       {"severity": "info",     "category": "automation"},
    "automation.complete":   {"severity": "info",     "category": "automation"},
    "automation.error":      {"severity": "warn",     "category": "automation"},
    "automation.compress":   {"severity": "info",     "category": "automation"},

    # Memory
    "memory.capture":        {"severity": "info",     "category": "memory"},
    "memory.recall":         {"severity": "info",     "category": "memory"},
    "memory.decay":          {"severity": "info",     "category": "memory"},

    # Reflection
    "reflection.start":      {"severity": "info",     "category": "reflection"},
    "reflection.checkpoint": {"severity": "info",     "category": "reflection"},
    "reflection.finish":     {"severity": "info",     "category": "reflection"},

    # Metrics
    "metrics.collect":       {"severity": "info",     "category": "metrics"},
    "metrics.anomaly":       {"severity": "warn",     "category": "metrics"},

    # Alert
    "alert.fire":            {"severity": "warn",     "category": "alert"},
    "alert.ack":             {"severity": "info",     "category": "alert"},
    "alert.resolve":         {"severity": "info",     "category": "alert"},
    "alert.escalate":        {"severity": "critical", "category": "alert"},

    # Integration (self-referential)
    "integration.handler.register":   {"severity": "info",  "category": "integration"},
    "integration.handler.error":      {"severity": "warn",  "category": "integration"},
    "integration.route.match":        {"severity": "info",  "category": "integration"},
    "integration.route.miss":         {"severity": "info",  "category": "integration"},
    "integration.bridge.sync":        {"severity": "info",  "category": "integration"},

    # System
    "system.startup":        {"severity": "info",     "category": "system"},
    "system.shutdown":       {"severity": "info",     "category": "system"},
    "system.error":          {"severity": "critical", "category": "system"},
    "system.config":         {"severity": "info",     "category": "system"},
}

# Category → color for dashboard rendering
CATEGORY_COLORS = {
    "session":     "#3b82f6",
    "sense":       "#ef4444",
    "knowledge":   "#22c55e",
    "heal":        "#f59e0b",
    "tool":        "#8b5cf6",
    "agent":       "#06b6d4",
    "evolution":   "#ec4899",
    "automation":  "#14b8a6",
    "memory":      "#a855f7",
    "reflection":  "#f97316",
    "metrics":     "#6366f1",
    "alert":       "#dc2626",
    "integration": "#64748b",
    "system":      "#78716c",
}


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Immutable event record."""
    id: str
    timestamp: str
    type: str
    source: str
    severity: str
    category: str
    payload: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    handler_results: List[Dict] = field(default_factory=list)
    routed_to: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


@dataclass
class HandlerRegistration:
    """A registered event handler."""
    name: str
    pattern: str
    description: str = ""
    priority: int = 50
    enabled: bool = True
    handler_type: str = "file"  # file | command | function
    target: str = ""            # file path or command string
    max_retries: int = 2
    timeout_seconds: int = 30
    created_at: str = ""
    last_fired: str = ""
    fire_count: int = 0
    error_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RoutingRule:
    """A routing rule that directs events to specific sinks."""
    name: str
    pattern: str
    sink: str              # observability | alert | memory | reflection | story | custom
    sink_config: Dict = field(default_factory=dict)
    enabled: bool = True
    priority: int = 50
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Database Layer ────────────────────────────────────────────────────────────

class EventStore:
    """SQLite-backed event persistence with retention policies."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                category TEXT NOT NULL DEFAULT 'system',
                payload TEXT DEFAULT '{}',
                correlation_id TEXT DEFAULT '',
                handler_results TEXT DEFAULT '[]',
                routed_to TEXT DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
            CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
            CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
            CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);

            CREATE TABLE IF NOT EXISTS handler_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                handler_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                duration_ms REAL DEFAULT 0,
                error_message TEXT DEFAULT '',
                fired_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events(id)
            );

            CREATE INDEX IF NOT EXISTS idx_handler_log_event ON handler_log(event_id);
            CREATE INDEX IF NOT EXISTS idx_handler_log_handler ON handler_log(handler_name);

            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                handler_name TEXT NOT NULL,
                error_message TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
        """)
        conn.commit()
        return conn

    def store_event(self, event: Event) -> None:
        """Persist an event to the store."""
        self.conn.execute(
            """INSERT OR REPLACE INTO events
               (id, timestamp, type, source, severity, category,
                payload, correlation_id, handler_results, routed_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, event.timestamp, event.type, event.source,
                event.severity, event.category,
                json.dumps(event.payload, ensure_ascii=False),
                event.correlation_id,
                json.dumps(event.handler_results, ensure_ascii=False),
                json.dumps(event.routed_to, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def store_handler_result(
        self, event_id: str, handler_name: str, status: str,
        duration_ms: float = 0, error_message: str = "",
    ) -> None:
        """Record a handler execution result."""
        self.conn.execute(
            """INSERT INTO handler_log
               (event_id, handler_name, status, duration_ms, error_message, fired_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id, handler_name, status, duration_ms, error_message,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def add_to_dead_letter(
        self, event_id: str, handler_name: str, error_message: str, retry_count: int = 0,
    ) -> None:
        """Add a failed event to the dead letter queue."""
        self.conn.execute(
            """INSERT INTO dead_letter
               (event_id, handler_name, error_message, retry_count, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                event_id, handler_name, error_message, retry_count,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def query_events(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        correlation_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Event]:
        """Query events with filters."""
        clauses = []
        params = []

        if event_type:
            if "*" in event_type:
                clauses.append("type GLOB ?")
                params.append(event_type)
            else:
                clauses.append("type = ?")
                params.append(event_type)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)

        where = " AND ".join(clauses) if clauses else "1=1"
        query = f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> Optional[Event]:
        """Get a single event by ID."""
        row = self.conn.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive event statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        by_type = {}
        for row in self.conn.execute(
            "SELECT type, COUNT(*) as cnt FROM events GROUP BY type ORDER BY cnt DESC"
        ):
            by_type[row["type"]] = row["cnt"]

        by_source = {}
        for row in self.conn.execute(
            "SELECT source, COUNT(*) as cnt FROM events GROUP BY source ORDER BY cnt DESC"
        ):
            by_source[row["source"]] = row["cnt"]

        by_severity = {}
        for row in self.conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM events GROUP BY severity"
        ):
            by_severity[row["severity"]] = row["cnt"]

        by_category = {}
        for row in self.conn.execute(
            "SELECT category, COUNT(*) as cnt FROM events GROUP BY category ORDER BY cnt DESC"
        ):
            by_category[row["category"]] = row["cnt"]

        handler_stats = {}
        for row in self.conn.execute(
            """SELECT handler_name, status, COUNT(*) as cnt
               FROM handler_log GROUP BY handler_name, status"""
        ):
            name = row["handler_name"]
            if name not in handler_stats:
                handler_stats[name] = {}
            handler_stats[name][row["status"]] = row["cnt"]

        dead_count = self.conn.execute(
            "SELECT COUNT(*) FROM dead_letter WHERE resolved_at IS NULL"
        ).fetchone()[0]

        oldest = self.conn.execute(
            "SELECT MIN(timestamp) FROM events"
        ).fetchone()[0]
        newest = self.conn.execute(
            "SELECT MAX(timestamp) FROM events"
        ).fetchone()[0]

        return {
            "total_events": total,
            "by_type": by_type,
            "by_source": by_source,
            "by_severity": by_severity,
            "by_category": by_category,
            "handler_stats": handler_stats,
            "dead_letter_count": dead_count,
            "oldest_event": oldest,
            "newest_event": newest,
        }

    def prune(self, days: int = 30) -> int:
        """Remove events older than N days. Returns count of removed events."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Delete handler_log entries for events about to be pruned (FK constraint)
        self.conn.execute(
            """DELETE FROM handler_log WHERE event_id IN
               (SELECT id FROM events WHERE timestamp < ?)""",
            (cutoff,),
        )

        # Delete events
        cursor = self.conn.execute(
            "DELETE FROM events WHERE timestamp < ?", (cutoff,)
        )
        self.conn.commit()
        removed = cursor.rowcount

        # Also prune resolved dead letters older than cutoff
        self.conn.execute(
            "DELETE FROM dead_letter WHERE resolved_at IS NOT NULL AND resolved_at < ?",
            (cutoff,),
        )
        self.conn.commit()

        return removed

    def get_dead_letter(self, limit: int = 20) -> List[Dict]:
        """Get unresolved dead letter entries."""
        rows = self.conn.execute(
            """SELECT * FROM dead_letter
               WHERE resolved_at IS NULL
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_dead_letter(self, dl_id: int) -> None:
        """Mark a dead letter entry as resolved."""
        self.conn.execute(
            "UPDATE dead_letter SET resolved_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), dl_id),
        )
        self.conn.commit()

    def _row_to_event(self, row) -> Event:
        """Convert a DB row to an Event dataclass."""
        return Event(
            id=row["id"],
            timestamp=row["timestamp"],
            type=row["type"],
            source=row["source"],
            severity=row["severity"],
            category=row["category"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            correlation_id=row["correlation_id"] or "",
            handler_results=json.loads(row["handler_results"]) if row["handler_results"] else [],
            routed_to=json.loads(row["routed_to"]) if row["routed_to"] else [],
        )

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()


# ── Pattern Matching Engine ───────────────────────────────────────────────────

class PatternMatcher:
    """
    Pattern matching for event routing and handler dispatch.

    Pattern syntax:
      field:value          Exact match
      field:prefix*        Glob prefix match
      field:*suffix        Glob suffix match
      field:a|b|c          OR match (any of a, b, c)
      !field:value         Negation

    Supported fields: type, source, severity, category
    Compound patterns: space-separated (AND logic)
    """

    @staticmethod
    def match(event: Event, pattern: str) -> bool:
        """Check if an event matches a compound pattern."""
        if not pattern or not pattern.strip():
            return True

        conditions = pattern.strip().split()
        for cond in conditions:
            if not PatternMatcher._match_condition(event, cond):
                return False
        return True

    @staticmethod
    def _match_condition(event: Event, condition: str) -> bool:
        """Match a single condition against an event."""
        negate = False
        if condition.startswith("!"):
            negate = True
            condition = condition[1:]

        if ":" not in condition:
            return not negate

        field_name, value = condition.split(":", 1)
        event_value = getattr(event, field_name, "")

        if value == "*":
            result = bool(event_value)
        elif "*" in value:
            # Glob matching: convert to regex
            regex = re.escape(value).replace(r"\*", ".*")
            result = bool(re.match(f"^{regex}$", event_value))
        elif "|" in value:
            options = value.split("|")
            result = event_value in options
        else:
            result = event_value == value

        return not result if negate else result

    @staticmethod
    def find_matching_handlers(
        event: Event, handlers: List[HandlerRegistration],
    ) -> List[HandlerRegistration]:
        """Find all enabled handlers whose pattern matches the event."""
        matched = []
        for handler in handlers:
            if not handler.enabled:
                continue
            if PatternMatcher.match(event, handler.pattern):
                matched.append(handler)
        # Sort by priority (lower number = higher priority)
        matched.sort(key=lambda h: h.priority)
        return matched

    @staticmethod
    def find_matching_routes(
        event: Event, routes: List[RoutingRule],
    ) -> List[RoutingRule]:
        """Find all enabled routing rules that match the event."""
        matched = []
        for route in routes:
            if not route.enabled:
                continue
            if PatternMatcher.match(event, route.pattern):
                matched.append(route)
        matched.sort(key=lambda r: r.priority)
        return matched


# ── Handler Registry ─────────────────────────────────────────────────────────

class HandlerRegistry:
    """Manages event handler registrations with persistence."""

    def __init__(self, handlers_file: Path = HANDLERS_FILE):
        self.handlers_file = handlers_file
        self.handlers: List[HandlerRegistration] = []
        self._load()

    def _load(self) -> None:
        """Load handlers from disk."""
        if not self.handlers_file.exists():
            self.handlers = self._default_handlers()
            self._save()
            return

        try:
            data = json.loads(self.handlers_file.read_text())
            self.handlers = [HandlerRegistration(**h) for h in data.get("handlers", [])]
        except Exception:
            self.handlers = self._default_handlers()
            self._save()

    def _save(self) -> None:
        """Persist handlers to disk."""
        data = {
            "version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "handlers": [h.to_dict() for h in self.handlers],
        }
        self.handlers_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def _default_handlers(self) -> List[HandlerRegistration]:
        """Return the default set of built-in handlers."""
        now = datetime.now(timezone.utc).isoformat()
        return [
            HandlerRegistration(
                name="observability-ingest",
                pattern="sense.*",
                description="Forward sense events to observability DB",
                priority=10,
                handler_type="function",
                target="observability",
                created_at=now,
            ),
            HandlerRegistration(
                name="alert-engine-trigger",
                pattern="severity:critical",
                description="Trigger alert engine on critical events",
                priority=20,
                handler_type="function",
                target="alert",
                created_at=now,
            ),
            HandlerRegistration(
                name="memory-capture",
                pattern="category:knowledge|category:evolution|category:reflection",
                description="Auto-capture knowledge/evolution/reflection events to memory",
                priority=30,
                handler_type="function",
                target="memory",
                created_at=now,
            ),
            HandlerRegistration(
                name="story-logger",
                pattern="",
                description="Log all events to story.jsonl (catch-all)",
                priority=90,
                handler_type="function",
                target="story",
                created_at=now,
            ),
            HandlerRegistration(
                name="heartbeat-updater",
                pattern="type:session.heartbeat",
                description="Update heartbeat file on heartbeat events",
                priority=15,
                handler_type="function",
                target="heartbeat",
                created_at=now,
            ),
            HandlerRegistration(
                name="dead-letter-retry",
                pattern="category:alert",
                description="Retry failed alert events via dead letter queue",
                priority=25,
                handler_type="function",
                target="dead_letter",
                created_at=now,
            ),
        ]

    def register(self, handler: HandlerRegistration) -> str:
        """Register a new handler. Returns handler name."""
        # Check for duplicate names
        existing = [h for h in self.handlers if h.name == handler.name]
        if existing:
            # Update existing
            idx = self.handlers.index(existing[0])
            self.handlers[idx] = handler
        else:
            self.handlers.append(handler)

        self._save()
        return handler.name

    def unregister(self, name: str) -> bool:
        """Disable a handler by name. Returns True if found."""
        for h in self.handlers:
            if h.name == name:
                h.enabled = False
                self._save()
                return True
        return False

    def remove(self, name: str) -> bool:
        """Permanently remove a handler by name."""
        before = len(self.handlers)
        self.handlers = [h for h in self.handlers if h.name != name]
        if len(self.handlers) < before:
            self._save()
            return True
        return False

    def get(self, name: str) -> Optional[HandlerRegistration]:
        """Get a handler by name."""
        for h in self.handlers:
            if h.name == name:
                return h
        return None

    def list_all(self, include_disabled: bool = False) -> List[HandlerRegistration]:
        """List all handlers."""
        if include_disabled:
            return list(self.handlers)
        return [h for h in self.handlers if h.enabled]


# ── Routing Engine ────────────────────────────────────────────────────────────

class RoutingEngine:
    """Routes events to sinks based on routing rules."""

    def __init__(self, rules_file: Path = ROUTING_RULES_FILE):
        self.rules_file = rules_file
        self.rules: List[RoutingRule] = []
        self._load()

    def _load(self) -> None:
        """Load routing rules from disk."""
        if not self.rules_file.exists():
            self.rules = self._default_rules()
            self._save()
            return

        try:
            data = json.loads(self.rules_file.read_text())
            self.rules = [RoutingRule(**r) for r in data.get("rules", [])]
        except Exception:
            self.rules = self._default_rules()
            self._save()

    def _save(self) -> None:
        """Persist routing rules to disk."""
        data = {
            "version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "rules": [r.to_dict() for r in self.rules],
        }
        self.rules_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def _default_rules(self) -> List[RoutingRule]:
        """Default routing rules connecting all subsystems."""
        now = datetime.now(timezone.utc).isoformat()
        return [
            RoutingRule(
                name="sense-to-observability",
                pattern="category:sense",
                sink="observability",
                priority=10,
                created_at=now,
            ),
            RoutingRule(
                name="critical-to-alert",
                pattern="severity:critical",
                sink="alert",
                priority=10,
                created_at=now,
            ),
            RoutingRule(
                name="knowledge-to-memory",
                pattern="category:knowledge",
                sink="memory",
                priority=20,
                created_at=now,
            ),
            RoutingRule(
                name="reflection-to-memory",
                pattern="category:reflection",
                sink="memory",
                priority=20,
                created_at=now,
            ),
            RoutingRule(
                name="evolution-to-memory",
                pattern="category:evolution",
                sink="memory",
                priority=20,
                created_at=now,
            ),
            RoutingRule(
                name="all-to-story",
                pattern="",
                sink="story",
                priority=100,
                created_at=now,
            ),
            RoutingRule(
                name="alerts-to-heartbeat",
                pattern="type:session.heartbeat",
                sink="heartbeat",
                priority=15,
                created_at=now,
            ),
        ]

    def route(self, event: Event) -> List[str]:
        """Route an event to matching sinks. Returns list of sink names reached."""
        matched_rules = PatternMatcher.find_matching_routes(event, self.rules)
        sinks_reached = []

        for rule in matched_rules:
            try:
                self._dispatch_to_sink(event, rule)
                sinks_reached.append(rule.sink)
            except Exception as exc:
                _log(f"Route error [{rule.name}] → {rule.sink}: {exc}")

        return sinks_reached

    def _dispatch_to_sink(self, event: Event, rule: RoutingRule) -> None:
        """Dispatch an event to a specific sink."""
        sink = rule.sink
        config = rule.sink_config

        if sink == "story":
            self._sink_story(event)
        elif sink == "observability":
            self._sink_observability(event)
        elif sink == "alert":
            self._sink_alert(event)
        elif sink == "memory":
            self._sink_memory(event)
        elif sink == "heartbeat":
            self._sink_heartbeat(event)
        elif sink == "reflection":
            self._sink_reflection(event)
        elif sink == "dead_letter":
            pass  # handled by handler layer
        elif sink == "custom":
            self._sink_custom(event, config)

    def _sink_story(self, event: Event) -> None:
        """Write event to story.jsonl."""
        entry = {
            "timestamp": event.timestamp,
            "type": "event",
            "source": event.source,
            "event_type": event.type,
            "severity": event.severity,
            "payload": event.payload,
        }
        with open(STORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _sink_observability(self, event: Event) -> None:
        """Forward sense events to observability database."""
        if not OBSERVABILITY_DB.exists():
            return
        try:
            conn = sqlite3.connect(str(OBSERVABILITY_DB))
            conn.execute("PRAGMA journal_mode=WAL")
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
            """)
            sense_id = event.type.replace("sense.", "")
            value = event.payload.get("value", 0)
            session_id = f"integration-{event.source}"
            conn.execute(
                """INSERT OR IGNORE INTO sessions (session_id, timestamp)
                   VALUES (?, ?)""",
                (session_id, event.timestamp),
            )
            conn.execute(
                """INSERT INTO sense_snapshots
                   (session_id, sense_id, value, status, raw_metrics, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    sense_id,
                    value,
                    "warning" if event.severity == "warn" else event.severity,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.timestamp,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            _log(f"Observability sink error: {exc}")

    def _sink_alert(self, event: Event) -> None:
        """Forward critical events to alert engine."""
        entry = {
            "timestamp": event.timestamp,
            "event_id": event.id,
            "type": event.type,
            "source": event.source,
            "severity": event.severity,
            "payload": event.payload,
        }
        with open(ALERTS_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _sink_memory(self, event: Event) -> None:
        """Auto-capture events to auto-memory system."""
        entry = {
            "timestamp": event.timestamp,
            "source": event.source,
            "type": event.type,
            "payload": event.payload,
            "auto_captured": True,
        }
        cache_file = 鲤鱼_HOME / "event-memory-buffer.jsonl"
        with open(cache_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _sink_heartbeat(self, event: Event) -> None:
        """Update heartbeat file."""
        HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)
        source = event.source or "main"
        hb_file = HEARTBEATS_DIR / f"{source}.heartbeat"
        data = {
            "agent_id": source,
            "timestamp": event.timestamp,
            "status": event.payload.get("status", "alive"),
            "event_id": event.id,
        }
        hb_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _sink_reflection(self, event: Event) -> None:
        """Forward reflection events to reflection engine buffer."""
        entry = {
            "timestamp": event.timestamp,
            "event_type": event.type,
            "payload": event.payload,
        }
        buffer_file = 鲤鱼_HOME / "event-reflection-buffer.jsonl"
        with open(buffer_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _sink_custom(self, event: Event, config: Dict) -> None:
        """Dispatch to a custom command sink."""
        cmd = config.get("command", "")
        if not cmd:
            return
        try:
            env = os.environ.copy()
            env["鲤鱼_EVENT_ID"] = event.id
            env["鲤鱼_EVENT_TYPE"] = event.type
            env["鲤鱼_EVENT_SOURCE"] = event.source
            env["鲤鱼_EVENT_JSON"] = json.dumps(event.to_dict(), ensure_ascii=False)
            subprocess.run(
                cmd, shell=True, env=env, timeout=30,
                capture_output=True, text=True,
            )
        except Exception as exc:
            _log(f"Custom sink error: {exc}")

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a routing rule."""
        existing = [r for r in self.rules if r.name == rule.name]
        if existing:
            idx = self.rules.index(existing[0])
            self.rules[idx] = rule
        else:
            self.rules.append(rule)
        self._save()

    def remove_rule(self, name: str) -> bool:
        """Remove a routing rule by name."""
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.name != name]
        if len(self.rules) < before:
            self._save()
            return True
        return False

    def list_rules(self) -> List[RoutingRule]:
        """List all routing rules."""
        return list(self.rules)


# ── Event Handler Executor ────────────────────────────────────────────────────

class HandlerExecutor:
    """Executes registered handlers for events."""

    def __init__(self, registry: HandlerRegistry, store: EventStore):
        self.registry = registry
        self.store = store

    def execute(self, event: Event) -> List[Dict]:
        """Execute all matching handlers for an event. Returns results."""
        handlers = PatternMatcher.find_matching_handlers(
            event, self.registry.list_all()
        )
        results = []

        for handler in handlers:
            result = self._execute_single(handler, event)
            results.append(result)

            # Update handler stats
            handler.last_fired = datetime.now(timezone.utc).isoformat()
            handler.fire_count += 1
            if result["status"] == "error":
                handler.error_count += 1
                # Add to dead letter if retries exhausted
                self.store.add_to_dead_letter(
                    event.id, handler.name, result.get("error", ""),
                    retry_count=handler.max_retries,
                )

        # Persist handler stats
        self.registry._save()

        return results

    def _execute_single(
        self, handler: HandlerRegistration, event: Event,
    ) -> Dict:
        """Execute a single handler for an event."""
        start = time.monotonic()

        try:
            if handler.handler_type == "function":
                result = self._execute_builtin(handler, event)
            elif handler.handler_type == "command":
                result = self._execute_command(handler, event)
            elif handler.handler_type == "file":
                result = self._execute_file(handler, event)
            else:
                result = {"status": "skip", "message": f"Unknown type: {handler.handler_type}"}

            duration_ms = (time.monotonic() - start) * 1000
            result["duration_ms"] = round(duration_ms, 2)
            result["handler"] = handler.name

            self.store.store_handler_result(
                event.id, handler.name, result["status"],
                duration_ms, result.get("error", ""),
            )

            return result

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            error_msg = str(exc)
            self.store.store_handler_result(
                event.id, handler.name, "error", duration_ms, error_msg,
            )
            return {
                "status": "error", "handler": handler.name,
                "error": error_msg, "duration_ms": round(duration_ms, 2),
            }

    def _execute_builtin(self, handler: HandlerRegistration, event: Event) -> Dict:
        """Execute a built-in function handler."""
        target = handler.target

        if target == "observability":
            return self._builtin_observability(event)
        elif target == "alert":
            return self._builtin_alert(event)
        elif target == "memory":
            return self._builtin_memory(event)
        elif target == "story":
            return self._builtin_story(event)
        elif target == "heartbeat":
            return self._builtin_heartbeat(event)
        elif target == "dead_letter":
            return self._builtin_dead_letter(event)
        else:
            return {"status": "skip", "message": f"Unknown builtin: {target}"}

    def _builtin_observability(self, event: Event) -> Dict:
        """Forward to observability system."""
        if not OBSERVABILITY_DB.exists():
            return {"status": "skip", "reason": "no observability DB"}
        try:
            conn = sqlite3.connect(str(OBSERVABILITY_DB))
            conn.execute("PRAGMA journal_mode=WAL")
            # Match observability.py schema
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
            """)
            sense_id = event.type.replace("sense.", "")
            value = event.payload.get("value", 0)
            session_id = f"integration-{event.source}"
            # Ensure session exists
            conn.execute(
                """INSERT OR IGNORE INTO sessions (session_id, timestamp)
                   VALUES (?, ?)""",
                (session_id, event.timestamp),
            )
            conn.execute(
                """INSERT INTO sense_snapshots
                   (session_id, sense_id, value, status, raw_metrics, captured_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    sense_id,
                    value,
                    "warning" if event.severity == "warn" else event.severity,
                    json.dumps(event.payload, ensure_ascii=False),
                    event.timestamp,
                ),
            )
            conn.commit()
            conn.close()
            return {"status": "ok", "sink": "observability"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _builtin_alert(self, event: Event) -> Dict:
        """Trigger alert engine."""
        entry = {
            "timestamp": event.timestamp,
            "event_id": event.id,
            "type": event.type,
            "severity": event.severity,
            "payload": event.payload,
        }
        with open(ALERTS_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"status": "ok", "sink": "alert"}

    def _builtin_memory(self, event: Event) -> Dict:
        """Capture to memory buffer."""
        cache_file = 鲤鱼_HOME / "event-memory-buffer.jsonl"
        entry = {
            "timestamp": event.timestamp,
            "source": event.source,
            "type": event.type,
            "payload": event.payload,
        }
        with open(cache_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"status": "ok", "sink": "memory"}

    def _builtin_story(self, event: Event) -> Dict:
        """Log to story.jsonl."""
        entry = {
            "timestamp": event.timestamp,
            "type": "event",
            "source": event.source,
            "event_type": event.type,
            "severity": event.severity,
            "payload": event.payload,
        }
        with open(STORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"status": "ok", "sink": "story"}

    def _builtin_heartbeat(self, event: Event) -> Dict:
        """Update heartbeat file."""
        HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)
        source = event.source or "main"
        hb_file = HEARTBEATS_DIR / f"{source}.heartbeat"
        data = {
            "agent_id": source,
            "timestamp": event.timestamp,
            "status": event.payload.get("status", "alive"),
        }
        hb_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return {"status": "ok", "sink": "heartbeat"}

    def _builtin_dead_letter(self, event: Event) -> Dict:
        """Dead letter handler — records but does not retry."""
        return {"status": "ok", "sink": "dead_letter", "note": "recorded"}

    def _execute_command(self, handler: HandlerRegistration, event: Event) -> Dict:
        """Execute a command-line handler."""
        env = os.environ.copy()
        env["鲤鱼_EVENT_ID"] = event.id
        env["鲤鱼_EVENT_TYPE"] = event.type
        env["鲤鱼_EVENT_SOURCE"] = event.source
        env["鲤鱼_EVENT_SEVERITY"] = event.severity
        env["鲤鱼_EVENT_JSON"] = json.dumps(event.to_dict(), ensure_ascii=False)

        result = subprocess.run(
            handler.target, shell=True, env=env,
            timeout=handler.timeout_seconds,
            capture_output=True, text=True,
        )

        if result.returncode == 0:
            return {"status": "ok", "stdout": result.stdout[:500]}
        else:
            return {
                "status": "error",
                "returncode": result.returncode,
                "stderr": result.stderr[:500],
            }

    def _execute_file(self, handler: HandlerRegistration, event: Event) -> Dict:
        """Execute a file-based handler (append event to target file)."""
        target_path = Path(os.path.expanduser(handler.target))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "a") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return {"status": "ok", "file": str(target_path)}


# ── Event Bus Bridge ─────────────────────────────────────────────────────────

class EventBusBridge:
    """Bridges the existing event-bus/bus.py with the integration layer."""

    def __init__(self, store: EventStore):
        self.store = store
        self.bus_events_file = EVENTS_FILE

    def sync_from_bus(self, limit: int = 100) -> int:
        """Sync events from the event bus JSONL to the integration store."""
        if not self.bus_events_file.exists():
            return 0

        # Get the latest event timestamp in our store to avoid re-syncing
        existing = self.store.query_events(limit=1)
        last_ts = existing[0].timestamp if existing else ""

        synced = 0
        with open(self.bus_events_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if raw.get("timestamp", "") <= last_ts:
                        continue

                    event = Event(
                        id=raw.get("id", f"evt-{uuid.uuid4().hex[:10]}"),
                        timestamp=raw.get("timestamp", ""),
                        type=raw.get("type", "unknown"),
                        source=raw.get("source", "bus"),
                        severity=raw.get("severity", "info"),
                        category=EVENT_TYPES.get(
                            raw.get("type", ""), {}
                        ).get("category", "system"),
                        payload=raw.get("payload", {}),
                        correlation_id=raw.get("correlation_id", ""),
                    )
                    self.store.store_event(event)
                    synced += 1

                    if synced >= limit:
                        break
                except Exception:
                    continue

        return synced

    def emit_to_bus(self, event: Event) -> None:
        """Emit an event back to the event bus JSONL."""
        bus_event = {
            "id": event.id,
            "timestamp": event.timestamp,
            "source": event.source,
            "type": event.type,
            "severity": event.severity,
            "payload": event.payload,
            "correlation_id": event.correlation_id,
        }
        with open(self.bus_events_file, "a") as f:
            f.write(json.dumps(bus_event, ensure_ascii=False) + "\n")


# ── Integration Engine (Core Orchestrator) ────────────────────────────────────

class PhoenixEventIntegration:
    """
    Core orchestrator that ties together:
    - EventStore (persistence)
    - HandlerRegistry (handler management)
    - RoutingEngine (event routing to sinks)
    - HandlerExecutor (handler execution)
    - EventBusBridge (legacy bus sync)
    - PatternMatcher (event filtering)
    """

    def __init__(self):
        self.store = EventStore()
        self.handler_registry = HandlerRegistry()
        self.routing_engine = RoutingEngine()
        self.handler_executor = HandlerExecutor(self.handler_registry, self.store)
        self.bus_bridge = EventBusBridge(self.store)

    def emit(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict] = None,
        severity: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Event:
        """
        Emit an event through the full integration pipeline:
        1. Create Event object
        2. Persist to SQLite store
        3. Execute matching handlers
        4. Route to sinks (story, observability, memory, etc.)
        5. Echo to event bus JSONL
        """
        type_meta = EVENT_TYPES.get(event_type, {})
        event = Event(
            id=f"evt-{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=event_type,
            source=source,
            severity=severity or type_meta.get("severity", "info"),
            category=type_meta.get("category", "system"),
            payload=payload or {},
            correlation_id=correlation_id or "",
        )

        # Step 1: Persist
        self.store.store_event(event)

        # Step 2: Execute handlers
        handler_results = self.handler_executor.execute(event)
        event.handler_results = handler_results

        # Step 3: Route to sinks
        sinks_reached = self.routing_engine.route(event)
        event.routed_to = sinks_reached

        # Step 4: Update persisted event with results
        self.store.store_event(event)

        # Step 5: Echo to event bus
        self.bus_bridge.emit_to_bus(event)

        return event

    def query(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> List[Event]:
        """Query stored events."""
        return self.store.query_events(
            event_type=event_type, source=source,
            severity=severity, category=category,
            since=since, limit=limit,
        )

    def stats(self) -> Dict[str, Any]:
        """Get comprehensive integration statistics."""
        store_stats = self.store.get_stats()
        handlers = self.handler_registry.list_all(include_disabled=True)
        rules = self.routing_engine.list_rules()

        return {
            **store_stats,
            "registered_handlers": len(handlers),
            "enabled_handlers": len([h for h in handlers if h.enabled]),
            "routing_rules": len(rules),
            "handler_list": [
                {
                    "name": h.name,
                    "pattern": h.pattern,
                    "enabled": h.enabled,
                    "priority": h.priority,
                    "fire_count": h.fire_count,
                    "error_count": h.error_count,
                }
                for h in handlers
            ],
        }

    def sync_from_bus(self, limit: int = 100) -> int:
        """Sync events from the legacy event bus."""
        return self.bus_bridge.sync_from_bus(limit)

    def health(self) -> Dict[str, Any]:
        """Run a health check on all integration components."""
        checks = {}

        # Store check
        try:
            self.store.conn.execute("SELECT 1")
            checks["store"] = {"status": "ok"}
        except Exception as exc:
            checks["store"] = {"status": "error", "error": str(exc)}

        # Handlers file check
        checks["handlers_file"] = {
            "status": "ok" if HANDLERS_FILE.exists() else "missing",
            "path": str(HANDLERS_FILE),
        }

        # Routing rules check
        checks["routing_rules"] = {
            "status": "ok" if ROUTING_RULES_FILE.exists() else "missing",
            "path": str(ROUTING_RULES_FILE),
        }

        # Event bus check
        checks["event_bus"] = {
            "status": "ok" if EVENTS_FILE.exists() else "missing",
            "path": str(EVENTS_FILE),
        }

        # Story file check
        checks["story_file"] = {
            "status": "ok" if STORY_FILE.exists() else "missing",
            "path": str(STORY_FILE),
        }

        # Dead letter check
        dead_count = self.store.get_stats().get("dead_letter_count", 0)
        checks["dead_letter"] = {
            "status": "warning" if dead_count > 10 else "ok",
            "unresolved_count": dead_count,
        }

        # Overall health
        has_error = any(c.get("status") == "error" for c in checks.values())
        overall = "error" if has_error else "ok"

        return {
            "overall": overall,
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def prune(self, days: int = 30) -> int:
        """Prune old events."""
        return self.store.prune(days)

    def close(self) -> None:
        """Clean up resources."""
        self.store.close()


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(message: str) -> None:
    """Append to integration log."""
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {message}\n"
    try:
        with open(INTEGRATION_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_event(event: Event, verbose: bool = False) -> None:
    """Pretty-print an event."""
    severity_icon = {
        "info": " ",
        "warn": "!",
        "critical": "*",
    }.get(event.severity, " ")

    ts = event.timestamp[:19] if event.timestamp else "?"
    print(f"  {severity_icon} [{event.source:12s}] {event.type:30s} {ts}")
    if verbose:
        if event.payload:
            print(f"    payload: {json.dumps(event.payload, ensure_ascii=False)[:120]}")
        if event.routed_to:
            print(f"    routed:  {', '.join(event.routed_to)}")
        if event.handler_results:
            for hr in event.handler_results:
                status = hr.get("status", "?")
                name = hr.get("handler", "?")
                print(f"    handler: {name} -> {status} ({hr.get('duration_ms', 0):.1f}ms)")


def cmd_emit(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Emit an event."""
    if len(args) < 2:
        print("Usage: emit <type> <source> [--payload '{}'] [--severity X]")
        return

    event_type = args[0]
    source = args[1]
    payload = {}
    severity = None
    correlation_id = None

    i = 2
    while i < len(args):
        if args[i] == "--payload" and i + 1 < len(args):
            try:
                payload = json.loads(args[i + 1])
            except Exception:
                payload = {"raw": args[i + 1]}
            i += 2
        elif args[i] == "--severity" and i + 1 < len(args):
            severity = args[i + 1]
            i += 2
        elif args[i] == "--correlation" and i + 1 < len(args):
            correlation_id = args[i + 1]
            i += 2
        else:
            i += 1

    event = engine.emit(event_type, source, payload, severity, correlation_id)
    print(f"Emitted: {event.id}")
    print(f"  type:     {event.type}")
    print(f"  source:   {event.source}")
    print(f"  severity: {event.severity}")
    print(f"  category: {event.category}")
    if event.routed_to:
        print(f"  routed:   {', '.join(event.routed_to)}")
    if event.handler_results:
        for hr in event.handler_results:
            print(f"  handler:  {hr.get('status', '?')}")


def cmd_handler(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Manage handlers."""
    if not args:
        print("Usage: handler {list|register|unregister|info}")
        return

    subcmd = args[0]

    if subcmd == "list":
        handlers = engine.handler_registry.list_all(include_disabled=True)
        if not handlers:
            print("No handlers registered.")
            return
        print(f"Handlers ({len(handlers)} total):")
        for h in handlers:
            status = "ON " if h.enabled else "OFF"
            print(f"  [{status}] {h.name:30s} p={h.priority:3d}  "
                  f"fires={h.fire_count:5d}  errors={h.error_count:4d}  "
                  f"pattern='{h.pattern}'")
            if h.description:
                print(f"         {h.description}")

    elif subcmd == "register":
        if len(args) < 3:
            print("Usage: handler register <name> <pattern> [--priority N] [--desc TEXT] [--type TYPE] [--target TARGET]")
            return
        name = args[1]
        pattern = args[2]
        priority = 50
        desc = ""
        handler_type = "function"
        target = ""

        i = 3
        while i < len(args):
            if args[i] == "--priority" and i + 1 < len(args):
                priority = int(args[i + 1])
                i += 2
            elif args[i] == "--desc" and i + 1 < len(args):
                desc = args[i + 1]
                i += 2
            elif args[i] == "--type" and i + 1 < len(args):
                handler_type = args[i + 1]
                i += 2
            elif args[i] == "--target" and i + 1 < len(args):
                target = args[i + 1]
                i += 2
            else:
                i += 1

        handler = HandlerRegistration(
            name=name,
            pattern=pattern,
            description=desc,
            priority=priority,
            handler_type=handler_type,
            target=target,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        engine.handler_registry.register(handler)
        engine.emit("integration.handler.register", "event-integration", {
            "handler_name": name, "pattern": pattern,
        })
        print(f"Registered handler: {name}")

    elif subcmd == "unregister":
        if len(args) < 2:
            print("Usage: handler unregister <name>")
            return
        if engine.handler_registry.unregister(args[1]):
            print(f"Disabled handler: {args[1]}")
        else:
            print(f"Handler not found: {args[1]}")

    elif subcmd == "info":
        if len(args) < 2:
            print("Usage: handler info <name>")
            return
        h = engine.handler_registry.get(args[1])
        if not h:
            print(f"Handler not found: {args[1]}")
            return
        for k, v in h.to_dict().items():
            print(f"  {k}: {v}")

    else:
        print(f"Unknown handler subcommand: {subcmd}")


def cmd_route(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Query routed events."""
    event_type = None
    source = None
    severity = None
    category = None
    since = None
    limit = 20

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            event_type = args[i + 1]
            i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        elif args[i] == "--severity" and i + 1 < len(args):
            severity = args[i + 1]
            i += 2
        elif args[i] == "--category" and i + 1 < len(args):
            category = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        else:
            i += 1

    events = engine.query(
        event_type=event_type, source=source,
        severity=severity, category=category,
        since=since, limit=limit,
    )

    if not events:
        print("No events found.")
        return

    print(f"Events ({len(events)} shown):")
    for e in events:
        _print_event(e, verbose=True)


def cmd_stats(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Show integration statistics."""
    stats = engine.stats()

    print(f"=== 鲤鱼 Event Integration Stats ===")
    print(f"Total events:       {stats['total_events']}")
    print(f"Dead letter queue:  {stats['dead_letter_count']}")
    print(f"Oldest event:       {stats.get('oldest_event', 'N/A')}")
    print(f"Newest event:       {stats.get('newest_event', 'N/A')}")
    print()

    if stats.get("by_category"):
        print("By Category:")
        for cat, cnt in list(stats["by_category"].items())[:12]:
            color = CATEGORY_COLORS.get(cat, "#888")
            print(f"  {cat:15s}  {cnt:6d}")
        print()

    if stats.get("by_severity"):
        print("By Severity:")
        for sev, cnt in stats["by_severity"].items():
            print(f"  {sev:15s}  {cnt:6d}")
        print()

    if stats.get("by_source"):
        print("By Source (top 10):")
        for src, cnt in list(stats["by_source"].items())[:10]:
            print(f"  {src:15s}  {cnt:6d}")
        print()

    if stats.get("by_type"):
        print("By Type (top 15):")
        for typ, cnt in list(stats["by_type"].items())[:15]:
            print(f"  {typ:35s}  {cnt:6d}")
        print()

    print(f"Handlers: {stats['enabled_handlers']}/{stats['registered_handlers']} enabled")
    print(f"Routing rules: {stats['routing_rules']}")

    if stats.get("handler_stats"):
        print("\nHandler Execution Stats:")
        for name, status_counts in stats["handler_stats"].items():
            parts = ", ".join(f"{s}={c}" for s, c in status_counts.items())
            print(f"  {name:30s}  {parts}")


def cmd_health(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Run health check."""
    health = engine.health()
    print(f"Overall: {health['overall'].upper()}")
    for name, check in health["checks"].items():
        status = check["status"]
        icon = {"ok": "OK", "warning": "!!", "error": "XX", "missing": "--"}.get(status, "??")
        detail = ""
        if "error" in check:
            detail = f" ({check['error']})"
        elif "unresolved_count" in check:
            detail = f" (unresolved: {check['unresolved_count']})"
        print(f"  [{icon}] {name:20s}{detail}")


def cmd_prune(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Prune old events."""
    days = 30
    if args and args[0] == "--days" and len(args) > 1:
        days = int(args[1])

    removed = engine.prune(days)
    print(f"Pruned {removed} events older than {days} days.")


def cmd_bridge(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Bridge/sync events from the event bus."""
    if args and args[0] == "--all":
        synced = engine.sync_from_bus(limit=1000)
        print(f"Synced {synced} events from event bus.")
    elif args and args[0] == "--source":
        # Sync and show events from a specific source
        synced = engine.sync_from_bus(limit=500)
        print(f"Synced {synced} events from event bus.")
        source = args[1] if len(args) > 1 else None
        if source:
            events = engine.query(source=source, limit=20)
            for e in events:
                _print_event(e)
    else:
        synced = engine.sync_from_bus(limit=100)
        print(f"Synced {synced} events from event bus.")


def cmd_replay(engine: PhoenixEventIntegration, args: List[str]) -> None:
    """Replay a specific event by ID."""
    if not args:
        print("Usage: replay <event-id>")
        return

    event = engine.store.get_event(args[0])
    if not event:
        print(f"Event not found: {args[0]}")
        return

    print(f"Replaying event: {event.id}")
    _print_event(event, verbose=True)

    # Re-emit through the pipeline
    new_event = engine.emit(
        event.type, event.source, event.payload,
        event.severity, event.correlation_id,
    )
    print(f"Re-emitted as: {new_event.id}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    engine = PhoenixEventIntegration()

    try:
        if cmd == "emit":
            cmd_emit(engine, args)
        elif cmd == "handler":
            cmd_handler(engine, args)
        elif cmd == "route":
            cmd_route(engine, args)
        elif cmd == "stats":
            cmd_stats(engine, args)
        elif cmd == "health":
            cmd_health(engine, args)
        elif cmd == "prune":
            cmd_prune(engine, args)
        elif cmd == "bridge":
            cmd_bridge(engine, args)
        elif cmd == "replay":
            cmd_replay(engine, args)
        else:
            print(f"Unknown command: {cmd}")
            print("Commands: emit, handler, route, stats, health, prune, bridge, replay")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
