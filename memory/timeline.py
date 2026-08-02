#!/usr/bin/env python3
"""
鲤鱼 Timeline — Queryable execution traces with SQLite backend.
Absorbed from MUNDO v2.0.9 Timeline (SQLite-persisted, queryable, replayable).

Usage:
  timeline.py migrate                   Migrate story.jsonl to SQLite
  timeline.py query [--since DATE] [--type X] [--source X] [--limit N]
  timeline.py replay <session_id>       Reconstruct a session
  timeline.py stats                     Timeline statistics
  timeline.py export [--format json|csv] Export traces
  timeline.py record <type> <source> [--payload '{}']
"""

from pathlib import Path
import json
import sqlite3
import sys

鲤鱼_HOME = Path.home() / ".claude/liyu"
TIMELINE_DB = 鲤鱼_HOME / "timeline.db"
STORY_FILE = 鲤鱼_HOME / "story.jsonl"

# ── Database ──────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(str(TIMELINE_DB))
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'liyu',
            event_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            payload TEXT DEFAULT '{}',
            session_id TEXT DEFAULT '',
            summary TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_traces_date ON traces(date);
        CREATE INDEX IF NOT EXISTS idx_traces_type ON traces(event_type);
        CREATE INDEX IF NOT EXISTS idx_traces_source ON traces(source);
        CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts USING fts5(
            summary, payload, content='traces', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS traces_ai AFTER INSERT ON traces BEGIN
            INSERT INTO traces_fts(rowid, summary, payload)
            VALUES (new.id, new.summary, new.payload);
        END;
    """)
    db.commit()
    db.close()

def record(event_type: str, source: str = "liyu", payload: dict = None,
           severity: str = "info", session_id: str = "", summary: str = ""):
    init_db()
    now = datetime.now(timezone.utc)
    db = get_db()
    db.execute(
        "INSERT INTO traces (timestamp, date, source, event_type, severity, payload, session_id, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now.isoformat(), now.strftime("%Y-%m-%d"), source, event_type,
         severity, json.dumps(payload or {}), session_id, summary)
    )
    db.commit()
    db.close()

def migrate():
    """Migrate story.jsonl to timeline SQLite."""
    if not STORY_FILE.exists():
        print("No story.jsonl found.")
        return

    init_db()
    db = get_db()
    count = 0

    with open(STORY_FILE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                db.execute(
                    "INSERT INTO traces (timestamp, date, source, event_type, severity, payload, session_id, summary) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        event.get("date", ""),
                        event.get("source", "liyu"),
                        event.get("event", event.get("type", "unknown")),
                        event.get("severity", "info"),
                        json.dumps(event.get("payload", {})),
                        event.get("session_id", ""),
                        event.get("summary", event.get("description", ""))[:500],
                    )
                )
                count += 1
            except Exception:
                pass

    db.commit()
    db.close()
    print(f"Migrated {count} events from story.jsonl → timeline.db")


def query(since: str = None, until: str = None, event_type: str = None,
          source: str = None, limit: int = 50, search: str = None):
    """Query the timeline."""
    init_db()
    db = get_db()

    sql = "SELECT * FROM traces WHERE 1=1"
    params = []

    if since:
        sql += " AND date >= ?"
        params.append(since)
    if until:
        sql += " AND date <= ?"
        params.append(until)
    if event_type:
        sql += " AND event_type LIKE ?"
        params.append(event_type.replace("*", "%"))
    if source:
        sql += " AND source = ?"
        params.append(source)
    if search:
        sql += " AND id IN (SELECT rowid FROM traces_fts WHERE traces_fts MATCH ?)"
        params.append(search)

    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def replay(session_id: str):
    """Reconstruct a session from traces."""
    init_db()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM traces WHERE session_id = ? ORDER BY timestamp ASC",
        (session_id,)
    ).fetchall()
    db.close()

    if not rows:
        print(f"No traces found for session: {session_id}")
        return

    print(f"Session: {session_id}")
    print(f"Events: {len(rows)}")
    print(f"Duration: {rows[0]['timestamp'][:19]} → {rows[-1]['timestamp'][:19]}")
    print()
    for r in rows:
        icon = {"info": "ℹ️", "warn": "⚠️", "critical": "🚫"}.get(r["severity"], "·")
        print(f"  {icon} {r['timestamp'][:19]} [{r['event_type']:25s}] {r['summary'][:100]}")


def stats():
    """Timeline statistics."""
    init_db()
    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    by_source = {}
    for r in db.execute("SELECT source, COUNT(*) as c FROM traces GROUP BY source").fetchall():
        by_source[r["source"]] = r["c"]
    by_type = {}
    for r in db.execute("SELECT event_type, COUNT(*) as c FROM traces GROUP BY event_type ORDER BY c DESC LIMIT 10").fetchall():
        by_type[r["event_type"]] = r["c"]
    date_range = db.execute("SELECT MIN(date) as first, MAX(date) as last FROM traces").fetchone()

    db.close()
    return {
        "total": total,
        "by_source": by_source,
        "by_type": by_type,
        "date_range": {"first": date_range["first"], "last": date_range["last"]},
    }


def export_traces(format: str = "json", output_file: str = None):
    """Export traces to JSON or CSV."""
    init_db()
    db = get_db()
    rows = db.execute("SELECT * FROM traces ORDER BY timestamp ASC").fetchall()
    db.close()

    if format == "csv":
        import csv, io
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
        content = out.getvalue()
    else:
        content = json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)

    if output_file:
        Path(output_file).write_text(content)
        print(f"Exported {len(rows)} traces to {output_file}")
    else:
        print(content[:2000])

    return len(rows)


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "migrate":
        migrate()

    elif cmd == "record":
        etype = sys.argv[2] if len(sys.argv) > 2 else "system.event"
        src = sys.argv[3] if len(sys.argv) > 3 else "liyu"
        payload = {}
        for i, arg in enumerate(sys.argv):
            if arg == "--payload" and i + 1 < len(sys.argv):
                try:
                    payload = json.loads(sys.argv[i + 1])
                except Exception:
                    payload = {"raw": sys.argv[i + 1]}
        record(etype, src, payload)
        print(f"Recorded: {etype}")

    elif cmd == "query":
        kwargs = {}
        for i, arg in enumerate(sys.argv):
            if arg == "--since" and i + 1 < len(sys.argv):
                kwargs["since"] = sys.argv[i + 1]
            if arg == "--type" and i + 1 < len(sys.argv):
                kwargs["event_type"] = sys.argv[i + 1]
            if arg == "--source" and i + 1 < len(sys.argv):
                kwargs["source"] = sys.argv[i + 1]
            if arg == "--limit" and i + 1 < len(sys.argv):
                kwargs["limit"] = int(sys.argv[i + 1])
            if arg == "--search" and i + 1 < len(sys.argv):
                kwargs["search"] = sys.argv[i + 1]
        rows = query(**kwargs)
        for r in rows:
            print(f"  {r['timestamp'][:19]} [{r['source']:10s}] {r['event_type']:30s} {r['summary'][:80]}")

    elif cmd == "replay":
        session_id = sys.argv[2] if len(sys.argv) > 2 else ""
        replay(session_id)

    elif cmd == "stats":
        s = stats()
        print(f"Traces: {s['total']}")
        print(f"Sources: {s['by_source']}")
        print(f"Top types: {dict(list(s['by_type'].items())[:8])}")
        print(f"Date range: {s['date_range']['first']} → {s['date_range']['last']}")

    elif cmd == "export":
        fmt = "json"
        output = None
        for i, arg in enumerate(sys.argv):
            if arg == "--format" and i + 1 < len(sys.argv):
                fmt = sys.argv[i + 1]
            if arg == "--output" and i + 1 < len(sys.argv):
                output = sys.argv[i + 1]
        export_traces(fmt, output)


if __name__ == "__main__":
    main()
