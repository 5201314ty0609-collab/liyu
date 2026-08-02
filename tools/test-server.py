#!/usr/bin/env python3
"""鲤鱼 Server API 测试"""

from pathlib import Path
import json
import sys
import time
import urllib.error
import urllib.request

from threading import Thread

sys.path.insert(0, str(Path.home() / ".claude" / "liyu"))

PASS, FAIL = 0, 0

# Start server in background thread
import http.server
server = http.server.HTTPServer(("127.0.0.1", 8767), PhoenixHandler)
t = Thread(target=server.serve_forever, daemon=True)
t.start()
time.sleep(0.3)

BASE = "http://127.0.0.1:8767"

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}

# ── Tests ──
print("═══ Health Check ═══")
code, data = get("/health")
if code == 200 and data.get("status") == "ok":
    PASS += 1; print("  ✅ /health")
else:
    FAIL += 1; print(f"  ❌ /health: {data}")

print("\n═══ API Endpoints ═══")
for path, key in [("/api/status", "liyu"), ("/api/modules", "modules"),
                   ("/api/timeline", "entries"), ("/api/persona", "available"),
                   ("/api/tool-guard", "summary"), ("/api/skills", "total"),
                   ("/api/events", "total")]:
    code, data = get(path)
    if code == 200 and key in data:
        PASS += 1; print(f"  ✅ {path}")
    else:
        FAIL += 1; print(f"  ❌ {path}: status={code} keys={list(data.keys())[:3]}")

print("\n═══ Dashboard HTML ═══")
try:
    with urllib.request.urlopen(BASE + "/", timeout=5) as r:
        html = r.read().decode()
        if "鲤鱼" in html and "</html>" in html:
            PASS += 1; print("  ✅ Dashboard HTML valid")
        else:
            FAIL += 1; print("  ❌ Dashboard HTML incomplete")
except Exception as e:
    FAIL += 1; print(f"  ❌ Dashboard: {e}")

print("\n═══ CORS Headers ═══")
try:
    req = urllib.request.Request(BASE + "/api/status", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=5) as r:
        cors = r.headers.get("Access-Control-Allow-Origin", "")
        if cors == "*":
            PASS += 1; print("  ✅ CORS enabled")
        else:
            FAIL += 1; print(f"  ❌ CORS: {cors}")
except Exception as e:
    FAIL += 1; print(f"  ❌ OPTIONS: {e}")

print("\n═══ Error Handling ═══")
code, data = get("/api/nonexistent")
if code == 404:
    PASS += 1; print("  ✅ 404 for unknown route")
else:
    FAIL += 1; print(f"  ❌ Expected 404, got {code}")

print("\n═══ Data Consistency ═══")
_, mods = get("/api/modules")
_, skills = get("/api/skills")
if mods.get("total_modules", 0) >= 8:
    PASS += 1; print(f"  ✅ {mods['total_modules']} modules, {mods['total_lines']} lines")
else:
    FAIL += 1; print(f"  ❌ Too few modules: {mods.get('total_modules', 0)}")

if skills.get("total", 0) == 62:
    PASS += 1; print("  ✅ 62 skills (matches skill-registry)")
else:
    FAIL += 1; print(f"  ❌ Skills count: {skills.get('total', 0)}")

# Cleanup
server.shutdown()

print(f"\n{'═'*50}")
print(f"  通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
print(f"  通过率: {PASS / (PASS + FAIL) * 100:.0f}%")
print(f"{'═'*50}")
sys.exit(0 if FAIL == 0 else 1)
