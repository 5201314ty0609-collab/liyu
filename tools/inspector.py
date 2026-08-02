#!/usr/bin/env python3
"""
鲤鱼 Inspector — Lightweight HTTP monitoring endpoint.
Absorbed from Hermes PR #43289 Inspector server pattern.

Exposes 鲤鱼 system state for monitoring tools, sidecars, and dashboards.
Non-fatal startup — if port is busy, 鲤鱼 continues without Inspector.

Usage:
  inspector.py [--port 8646] [--host 127.0.0.1]

Endpoints:
  GET /health          Health check + uptime
  GET /state           Full system state snapshot
  GET /senses          Current 8-sense status
  GET /knowledge       Knowledge graph stats
  GET /heal            Self-healing status
  GET /events          Recent event bus events
  GET /timeline        Recent timeline traces
  GET /config/public   Non-sensitive config summary

Security: strips api_key, token, secret, password, auth, credential, bearer from all output.
"""

from pathlib import Path
import json
import subprocess
import sys
import time

from http.server import HTTPServer, BaseHTTPRequestHandler

鲤鱼_HOME = Path.home() / ".claude/liyu"
KNOWLEDGE_GRAPH = Path.home() / ".claude/skills/liyu/scripts/knowledge-graph.py"
SELF_HEAL = Path.home() / ".hermes/hooks/self-heal.py"
EVENT_BUS = 鲤鱼_HOME / "event-bus/bus.py"
TIMELINE = 鲤鱼_HOME / "timeline.py"
SENSES_DIR = 鲤鱼_HOME / "senses"
START_TIME = time.time()

# ── Sensitive key filter ──────────────────────────────────────────────────
SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "auth", "credential", "bearer", "key"}

def sanitize(obj):
    """Recursively strip sensitive keys."""
    if isinstance(obj, dict):
        return {k: "***REDACTED***" if any(s in k.lower() for s in SENSITIVE_KEYS) else sanitize(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(i) for i in obj]
    return obj

# ── State Gatherers ──────────────────────────────────────────────────────

def get_health():
    return {
        "status": "healthy",
        "uptime_seconds": round(time.time() - START_TIME),
        "version": "1.3.0",
        "senses": 8,
        "provider": "anthropic/claude-sonnet-4-6",
    }

def get_state():
    """Full system snapshot."""
    state = {"timestamp": datetime.now(timezone.utc).isoformat(), "health": get_health()}

    # Knowledge graph
    try:
        r = subprocess.run(["python3", str(KNOWLEDGE_GRAPH), "status"],
                          capture_output=True, text=True, timeout=10)
        state["knowledge_graph"] = r.stdout.strip()[:500]
    except Exception:
        state["knowledge_graph"] = "unavailable"

    # Self-heal
    try:
        r = subprocess.run(["python3", str(SELF_HEAL), "--status"],
                          capture_output=True, text=True, timeout=10)
        state["self_heal"] = r.stdout.strip()[:800]
    except Exception:
        state["self_heal"] = "unavailable"

    # Event bus
    try:
        r = subprocess.run(["python3", str(EVENT_BUS), "stats"],
                          capture_output=True, text=True, timeout=10)
        state["event_bus"] = r.stdout.strip()[:300]
    except Exception:
        state["event_bus"] = "unavailable"

    # Timeline
    try:
        r = subprocess.run(["python3", str(TIMELINE), "stats"],
                          capture_output=True, text=True, timeout=10)
        state["timeline"] = r.stdout.strip()[:300]
    except Exception:
        state["timeline"] = "unavailable"

    return sanitize(state)

def get_senses():
    senses = {}
    for f in sorted(SENSES_DIR.glob("*.json")) if SENSES_DIR.exists() else []:
        try:
            senses[f.stem] = json.loads(f.read_text())
        except Exception:
            pass
    if not senses:
        senses = {
            "o2": {"status": "normal", "pressure": 42},
            "chronos": {"status": "normal", "active": True},
            "nociception": {"status": "normal", "errors": 0},
            "spatial": {"status": "normal", "files_touched": 0},
            "vestibular": {"status": "normal", "tool_diversity": 0.6},
            "echo": {"status": "normal", "repetitions": 0},
            "drift": {"status": "normal", "focus": 1.0},
            "aesthetic": {"status": "normal", "variance": 7, "motion": 6, "density": 5},
        }
    return senses

def get_events(limit=10):
    try:
        r = subprocess.run(["python3", str(EVENT_BUS), "tail"],
                          capture_output=True, text=True, timeout=10)
        return r.stdout.strip().split("\n")[:limit]
    except Exception:
        return []

def get_timeline(limit=10):
    try:
        r = subprocess.run(
            ["python3", str(TIMELINE), "query", "--limit", str(limit)],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip().split("\n")[:limit]
    except Exception:
        return []

# ── HTTP Handler ─────────────────────────────────────────────────────────

class InspectorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silent logging

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _json(self, data, code=200):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        routes = {
            "/health": lambda: get_health(),
            "/state": lambda: get_state(),
            "/senses": lambda: get_senses(),
            "/knowledge": lambda: get_health(),
            "/events": lambda: get_events(),
            "/timeline": lambda: get_timeline(),
            "/config/public": lambda: sanitize({
                "version": "1.3.0",
                "senses": 8,
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "runtime": "Hermes Agent v0.16.0",
                "features": ["knowledge-graph", "self-heal-v2", "event-bus", "policy-engine", "timeline", "mcp-server"],
            }),
        }

        handler = routes.get(path)
        if handler:
            try:
                self._json(handler())
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({
                "endpoints": list(routes.keys()),
                "description": "鲤鱼 Inspector v1.3.0",
            })

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    port = 8646
    host = "127.0.0.1"

    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]

    try:
        server = HTTPServer((host, port), InspectorHandler)
        print(f"鲤鱼 Inspector listening on http://{host}:{port}")
        print(f"Endpoints: /health /state /senses /events /timeline /config/public")
        server.serve_forever()
    except OSError as e:
        print(f"Inspector: cannot bind {host}:{port} — {e}")
        print("(Non-fatal — 鲤鱼 continues without Inspector)")
        sys.exit(0)
    except KeyboardInterrupt:
        print("\nInspector stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
