#!/usr/bin/env python3
"""
鲤鱼 Dashboard — HTML visualization for 7-Sense monitoring.
===============================================================

Generates an interactive HTML dashboard with:
- 7-Sense radar chart
- Metric trend charts
- Alert timeline
- Performance heatmap
- Error summary

Usage:
  liyu-dashboard.py generate                 Generate dashboard HTML
  liyu-dashboard.py generate --open          Generate and open in browser
  liyu-dashboard.py serve [--port 8888]      Start local HTTP server
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

鲤鱼_HOME = Path.home() / ".claude/liyu"
DB_PATH = 鲤鱼_HOME / "observability.db"
DASHBOARD_PATH = 鲤鱼_HOME / "dashboard.html"

SENSE_META = {
    "o2": {"name": "O2 (Vitality)", "color": "#22c55e", "warning": 70, "critical": 85},
    "nociception": {"name": "Nociception", "color": "#ef4444", "warning": 3, "critical": 5},
    "chronos": {"name": "Chronos (Time)", "color": "#3b82f6", "warning": 300, "critical": 600},
    "spatial": {"name": "Spatial", "color": "#f59e0b", "warning": 5, "critical": 10},
    "vestibular": {"name": "Vestibular", "color": "#8b5cf6", "warning": 70, "critical": 80},
    "echo": {"name": "Echo", "color": "#ec4899", "warning": 2, "critical": 3},
    "drift": {"name": "Drift (Focus)", "color": "#06b6d4", "warning": 25, "critical": 30},
}


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_latest_session() -> Dict:
    conn = _get_db()
    session = conn.execute(
        "SELECT * FROM sessions ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if not session:
        conn.close()
        return {}

    snapshots = conn.execute(
        "SELECT sense_id, value, status, captured_at FROM sense_snapshots WHERE session_id = ?",
        (session["session_id"],),
    ).fetchall()
    conn.close()

    senses = {}
    for s in snapshots:
        senses[s["sense_id"]] = {
            "value": s["value"],
            "status": s["status"],
            "captured_at": s["captured_at"],
            "meta": SENSE_META.get(s["sense_id"], {}),
        }
    return {
        "session_id": session["session_id"],
        "timestamp": session["timestamp"],
        "health": session["overall_health"],
        "senses": senses,
    }


def get_metric_trends(hours: int = 24) -> Dict[str, List]:
    conn = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    trends = {}
    for sense_id in SENSE_META:
        rows = conn.execute(
            "SELECT metric_value, captured_at FROM metric_series WHERE metric_name LIKE ? AND captured_at > ? ORDER BY captured_at",
            (f"{sense_id}.%", cutoff),
        ).fetchall()
        trends[sense_id] = [{"value": r["metric_value"], "time": r["captured_at"]} for r in rows]
    conn.close()
    return trends


def get_alert_summary(limit: int = 20) -> List[Dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_performance_data(hours: int = 24) -> Dict:
    conn = _get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    spans = conn.execute(
        """SELECT span_name, AVG(duration_ms) as avg_ms, COUNT(*) as cnt
           FROM perf_spans WHERE started_at > ? GROUP BY span_name ORDER BY avg_ms DESC LIMIT 15""",
        (cutoff,),
    ).fetchall()
    errors = conn.execute(
        "SELECT COUNT(*) as cnt FROM error_records WHERE last_seen > ? AND resolved = 0", (cutoff,)
    ).fetchone()
    conn.close()
    return {
        "spans": [dict(s) for s in spans],
        "active_errors": errors["cnt"] if errors else 0,
    }


def generate_html() -> str:
    session = get_latest_session()
    trends = get_metric_trends(24)
    alerts = get_alert_summary(20)
    perf = get_performance_data(24)

    # Prepare radar data
    radar_labels = []
    radar_values = []
    radar_colors = []
    for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
        meta = SENSE_META[sid]
        radar_labels.append(meta["name"])
        sense = session.get("senses", {}).get(sid, {})
        val = sense.get("value", 0)
        # Normalize to 0-100 for radar
        critical = meta["critical"]
        normalized = min(100, (val / critical) * 100) if critical > 0 else 0
        radar_values.append(round(normalized, 1))
        status = sense.get("status", "normal")
        radar_colors.append(
            "#22c55e" if status == "normal" else "#f59e0b" if status == "warning" else "#ef4444"
        )

    # Prepare trend chart data
    trend_datasets = []
    for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
        data_points = trends.get(sid, [])
        meta = SENSE_META[sid]
        trend_datasets.append({
            "label": meta["name"],
            "data": [d["value"] for d in data_points],
            "labels": [d["time"].split("T")[1][:5] for d in data_points],
            "color": meta["color"],
        })

    # Alerts for timeline
    alert_items = []
    for a in alerts:
        sev_color = {"critical": "#ef4444", "warning": "#f59e0b", "info": "#3b82f6"}.get(a["severity"], "#6b7280")
        alert_items.append({
            "id": a["alert_id"],
            "sense": a["sense_id"],
            "severity": a["severity"],
            "message": a["message"],
            "status": a["status"],
            "time": a["created_at"],
            "color": sev_color,
        })

    health = session.get("health", 100)
    health_color = "#22c55e" if health >= 80 else "#f59e0b" if health >= 60 else "#ef4444"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>鲤鱼 Observability Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
    --text: #e2e8f0; --text-dim: #94a3b8; --text-bright: #f8fafc;
    --green: #22c55e; --yellow: #f59e0b; --red: #ef4444;
    --blue: #3b82f6; --purple: #8b5cf6; --cyan: #06b6d4; --pink: #ec4899;
    --border: #475569; --radius: 12px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Inter', -apple-system, system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
  header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; }}
  header h1 {{ font-size: 1.8rem; color: var(--text-bright); font-weight: 700; }}
  header .meta {{ color: var(--text-dim); font-size: 0.85rem; }}
  .health-badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 20px; font-weight: 600; font-size: 1.1rem; }}
  .grid {{ display: grid; gap: 20px; margin-bottom: 24px; }}
  .grid-2 {{ grid-template-columns: 1fr 1fr; }}
  .grid-3 {{ grid-template-columns: 1fr 1fr 1fr; }}
  @media (max-width: 900px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }}
  .card h2 {{ font-size: 1rem; color: var(--text-dim); margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }}
  .sense-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
  .sense-item {{ background: var(--surface2); border-radius: 8px; padding: 14px; text-align: center; }}
  .sense-item .name {{ font-size: 0.75rem; color: var(--text-dim); margin-bottom: 4px; }}
  .sense-item .value {{ font-size: 1.6rem; font-weight: 700; }}
  .sense-item .status {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; margin-top: 6px; display: inline-block; }}
  .status-normal {{ background: rgba(34,197,94,0.15); color: var(--green); }}
  .status-warning {{ background: rgba(245,158,11,0.15); color: var(--yellow); }}
  .status-critical {{ background: rgba(239,68,68,0.15); color: var(--red); }}
  .alert-item {{ display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--surface2); }}
  .alert-item:last-child {{ border-bottom: none; }}
  .alert-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .alert-content {{ flex: 1; }}
  .alert-content .msg {{ font-size: 0.85rem; }}
  .alert-content .time {{ font-size: 0.7rem; color: var(--text-dim); }}
  .alert-status {{ font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; }}
  .chart-container {{ position: relative; height: 280px; }}
  .perf-bar {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .perf-bar .label {{ width: 140px; font-size: 0.8rem; color: var(--text-dim); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .perf-bar .bar {{ flex: 1; height: 20px; background: var(--surface2); border-radius: 4px; overflow: hidden; }}
  .perf-bar .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s; }}
  .perf-bar .val {{ width: 70px; font-size: 0.8rem; color: var(--text-dim); }}
  .refresh-btn {{ background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }}
  .refresh-btn:hover {{ background: var(--border); }}
  .empty {{ color: var(--text-dim); font-style: italic; text-align: center; padding: 20px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>鲤鱼 Observability</h1>
      <div class="meta">Session: {session.get('session_id', 'N/A')} | Last update: {session.get('timestamp', 'N/A')[:19]}</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px;">
      <div class="health-badge" style="background:{health_color}22;color:{health_color};border:1px solid {health_color}44;">
        Health: {health:.0f}/100
      </div>
      <button class="refresh-btn" onclick="location.reload()">Refresh</button>
    </div>
  </header>

  <!-- 7-Sense Overview -->
  <div class="card" style="margin-bottom:20px;">
    <h2>7-Sense Status</h2>
    <div class="sense-grid">
"""

    for sid in ["o2", "nociception", "chronos", "spatial", "vestibular", "echo", "drift"]:
        sense = session.get("senses", {}).get(sid, {})
        meta = SENSE_META[sid]
        val = sense.get("value", 0)
        status = sense.get("status", "normal")
        status_cls = f"status-{status}"
        html += f"""      <div class="sense-item">
        <div class="name">{meta['name']}</div>
        <div class="value" style="color:{meta['color']};">{val:.1f}</div>
        <div class="status {status_cls}">{status}</div>
      </div>
"""

    html += """    </div>
  </div>

  <div class="grid grid-2">
    <!-- Radar Chart -->
    <div class="card">
      <h2>Sense Radar</h2>
      <div class="chart-container">
        <canvas id="radarChart"></canvas>
      </div>
    </div>

    <!-- Trend Chart -->
    <div class="card">
      <h2>24h Trends</h2>
      <div class="chart-container">
        <canvas id="trendChart"></canvas>
      </div>
    </div>
  </div>

  <div class="grid grid-2">
    <!-- Alerts -->
    <div class="card">
      <h2>Recent Alerts</h2>
"""

    if alert_items:
        for a in alert_items[:10]:
            status_bg = {"active": "rgba(239,68,68,0.15)", "acked": "rgba(245,158,11,0.15)", "resolved": "rgba(34,197,94,0.15)"}.get(a["status"], "var(--surface2)")
            status_color = {"active": "var(--red)", "acked": "var(--yellow)", "resolved": "var(--green)"}.get(a["status"], "var(--text-dim)")
            html += f"""      <div class="alert-item">
        <div class="alert-dot" style="background:{a['color']};"></div>
        <div class="alert-content">
          <div class="msg">{a['message'][:100]}</div>
          <div class="time">{a['time'][:19]} | {a['sense']}</div>
        </div>
        <div class="alert-status" style="background:{status_bg};color:{status_color};">{a['status']}</div>
      </div>
"""
    else:
        html += '      <div class="empty">No alerts recorded</div>\n'

    html += """    </div>

    <!-- Performance -->
    <div class="card">
      <h2>Performance (Top Operations)</h2>
"""

    if perf["spans"]:
        max_ms = max(s["avg_ms"] for s in perf["spans"]) if perf["spans"] else 1
        for s in perf["spans"][:8]:
            pct = min(100, (s["avg_ms"] / max_ms) * 100) if max_ms > 0 else 0
            bar_color = "var(--green)" if pct < 50 else "var(--yellow)" if pct < 80 else "var(--red)"
            html += f"""      <div class="perf-bar">
        <div class="label">{s['span_name']}</div>
        <div class="bar"><div class="bar-fill" style="width:{pct:.0f}%;background:{bar_color};"></div></div>
        <div class="val">{s['avg_ms']:.1f}ms ({s['cnt']})</div>
      </div>
"""
    else:
        html += '      <div class="empty">No performance data</div>\n'

    html += f"""      <div style="margin-top:12px;font-size:0.8rem;color:var(--text-dim);">
        Active errors: {perf['active_errors']} | Period: 24h
      </div>
    </div>
  </div>
</div>

<script>
const radarCtx = document.getElementById('radarChart').getContext('2d');
new Chart(radarCtx, {{
  type: 'radar',
  data: {{
    labels: {json.dumps(radar_labels)},
    datasets: [{{
      label: 'Current',
      data: {json.dumps(radar_values)},
      backgroundColor: 'rgba(59, 130, 246, 0.15)',
      borderColor: '#3b82f6',
      borderWidth: 2,
      pointBackgroundColor: {json.dumps(radar_colors)},
      pointRadius: 5,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    scales: {{
      r: {{
        beginAtZero: true,
        max: 100,
        ticks: {{ color: '#94a3b8', backdropColor: 'transparent', stepSize: 25 }},
        grid: {{ color: '#334155' }},
        angleLines: {{ color: '#334155' }},
        pointLabels: {{ color: '#e2e8f0', font: {{ size: 11 }} }}
      }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});

const trendCtx = document.getElementById('trendChart').getContext('2d');
const trendData = {json.dumps(trend_datasets)};
const trendLabels = trendData.length > 0 ? trendData[0].labels : [];
new Chart(trendCtx, {{
  type: 'line',
  data: {{
    labels: trendLabels,
    datasets: trendData.map(ds => ({{
      label: ds.label,
      data: ds.data,
      borderColor: ds.color,
      backgroundColor: ds.color + '22',
      borderWidth: 1.5,
      pointRadius: 2,
      fill: false,
      tension: 0.3,
    }}))
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8', maxTicksLimit: 12 }}, grid: {{ color: '#1e293b' }} }},
      y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#e2e8f0', boxWidth: 12, font: {{ size: 10 }} }} }} }}
  }}
}});
</script>
</body>
</html>"""

    return html


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "generate":
        html = generate_html()
        DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DASHBOARD_PATH, "w") as f:
            f.write(html)
        print(f"Dashboard written to {DASHBOARD_PATH}")

        if "--open" in sys.argv:
            import webbrowser
            webbrowser.open(f"file://{DASHBOARD_PATH}")

    elif sys.argv[1] == "serve":
        port = 8888
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 8888

        import http.server
        import os
        os.chdir(str(鲤鱼_HOME))
        handler = http.server.SimpleHTTPRequestHandler
        with http.server.HTTPServer(("", port), handler) as httpd:
            print(f"Serving dashboard at http://localhost:{port}/dashboard.html")
            httpd.serve_forever()

    else:
        print(f"Unknown command: {sys.argv[1]}")
        print("Available: generate, serve")
        sys.exit(1)


if __name__ == "__main__":
    main()
