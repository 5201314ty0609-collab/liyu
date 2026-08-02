#!/usr/bin/env python3
"""
鲤鱼 Web Server — 独立 HTTP API + 仪表盘
Pure Python stdlib. Zero dependencies.

Endpoints:
  GET  /                  鲤鱼 Dashboard (基础视图)
  GET  /viz               鲤鱼 Visualization Dashboard (可视化控制台)
  GET  /api/status        System health + 8-Sense + drift
  GET  /api/stats         Comprehensive real-time statistics
  GET  /api/ctm           CTM thinking engine real-time status
  GET  /api/modules       Module inventory
  GET  /api/timeline      Recent timeline (query: ?limit=20&type=X)
  GET  /api/persona       NexSandglass persona
  GET  /api/tool-guard    Tool guard alerts + history
  GET  /api/skills        Skill registry stats
  GET  /api/events        Event bus stats

Usage:
  python3 server.py [--port 8765] [--host 0.0.0.0]
"""

from datetime import datetime, timezone
import json
import sys
import time

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器——支持 SSE 长连接不阻塞其他请求"""
    daemon_threads = True
from pathlib import Path
from urllib.parse import urlparse, parse_qs

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
sys.path.insert(0, str(鲤鱼_HOME))
DASHBOARD_FILE = 鲤鱼_HOME / "dashboard.html"
DASHBOARD_VIZ_FILE = 鲤鱼_HOME / "dashboard-viz.html"
INDEX_FILE = 鲤鱼_HOME / "index.html"
PORT = 8765
HOST = "127.0.0.1"


# ── API Handlers ───────────────────────────────────────────────────────────

def api_status() -> dict:
    """系统综合状态"""
    data = {
        "liyu": {
            "version": "1.3.0",
            "uptime": "since session start",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

    # 8-Sense states
    senses = {}
    senses_dir = 鲤鱼_HOME / "senses"
    if senses_dir.exists():
        for f in sorted(senses_dir.glob("*.json")):
            try:
                sense = json.loads(f.read_text())
                senses[f.stem] = {
                    "state": sense.get("status", sense.get("state", "unknown")),
                    "trend": sense.get("trend", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass
    data["senses"] = senses

    # Drift velocity (NexSandglass)
    drift_file = 鲤鱼_HOME / "nexsandglass" / "drift.json"
    if drift_file.exists():
        try:
            drift = json.loads(drift_file.read_text())
            data["drift"] = {
                "direction": drift.get("current_direction", "unknown"),
                "stability": drift.get("stability", "unknown"),
                "trend_slope": drift.get("trend_slope", 0),
            }
        except (json.JSONDecodeError, OSError):
            pass

    # Framework evolution
    frameworks_dir = 鲤鱼_HOME / "frameworks" / "active"
    if frameworks_dir.exists():
        data["evolution"] = {
            "active_frameworks": len(list(frameworks_dir.glob("*.json"))),
        }

    return data


def api_modules() -> dict:
    """模块清单与统计"""
    modules = {}
    for py_file in sorted(鲤鱼_HOME.glob("*.py")):
        if py_file.name.startswith("test_"):
            continue
        try:
            content = py_file.read_text()
            lines = len(content.split("\n"))
            # Extract docstring summary
            summary = ""
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith('"""') and not line == '"""':
                    summary = line.replace('"""', "").strip()[:100]
                    break
            modules[py_file.stem] = {
                "lines": lines,
                "summary": summary,
                "size_kb": round(py_file.stat().st_size / 1024, 1),
            }
        except OSError:
            pass

    total_lines = sum(m["lines"] for m in modules.values())
    return {
        "modules": modules,
        "total_modules": len(modules),
        "total_lines": total_lines,
    }


def api_timeline(params: dict) -> dict:
    """时间线查询"""
    limit = min(int(params.get("limit", [20])[0]), 100)
    event_type = params.get("type", [None])[0]

    try:
        from timeline import query
        rows = query(limit=limit, event_type=event_type)
        return {"entries": rows, "total": len(rows)}
    except ImportError:
        # Fallback: read story.jsonl directly
        story_file = 鲤鱼_HOME / "story.jsonl"
        entries = []
        if story_file.exists():
            with open(story_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return {"entries": entries[-limit:], "total": len(entries), "source": "story.jsonl"}


def api_persona() -> dict:
    """NexSandglass 画像"""
    persona_file = 鲤鱼_HOME / "nexsandglass" / "persona.md"
    drift_file = 鲤鱼_HOME / "nexsandglass" / "drift.json"
    sand_db = 鲤鱼_HOME / "nexsandglass" / "sand.db"

    result = {"available": False}

    if persona_file.exists():
        try:
            result["persona"] = persona_file.read_text()[:3000]
            result["available"] = True
        except OSError:
            pass

    if drift_file.exists():
        try:
            drift = json.loads(drift_file.read_text())
            result["drift"] = {
                "direction": drift.get("current_direction"),
                "stability": drift.get("stability"),
                "history": drift.get("history", [])[-7:],
            }
        except (json.JSONDecodeError, OSError):
            pass

    # Grain count
    if sand_db.exists():
        import sqlite3
        try:
            db = sqlite3.connect(str(sand_db))
            count = db.execute("SELECT COUNT(*) FROM sand").fetchone()[0]
            db.close()
            result["total_grains"] = count
        except Exception:
            pass

    return result


def api_persona_all(params: dict = None) -> dict:
    """获取所有用户画像（创始人可查看所有，其他用户只能查看自己）

    Query params:
        user_id: 当前用户 ID（用于权限检查）
        tier: 当前用户层级（用于权限检查）
    """
    params = params or {}
    current_user_id = params.get("user_id", ["holyty-founder"])[0]
    current_tier = params.get("tier", ["user"])[0]

    # Load users using UserManager
    try:
        from user_manager import UserManager
        mgr = UserManager()
        users_list = mgr.list_all()
    except Exception:
        users_list = []

    # Determine what the current user can see
    is_founder = current_tier == "founder" or current_user_id == "holyty-founder"

    result = {
        "current_user_id": current_user_id,
        "current_tier": current_tier,
        "can_view_all": is_founder,
        "personas": {}
    }

    # For each user, load their persona if allowed
    for user in users_list:
        user_id = user.get("id", "")

        # Check if we can view this user's persona
        if not is_founder and user_id != current_user_id:
            # Non-founders can only see themselves
            result["personas"][user_id] = {
                "available": False,
                "privacy_blocked": True,
                "message": "仅创始人可查看其他成员画像"
            }
            continue

        # Load persona for this user
        # For now, we only have one persona file (for holyty-founder)
        # In a multi-user system, each user would have their own persona file
        if user_id == "holyty-founder":
            persona_file = 鲤鱼_HOME / "nexsandglass" / "persona.md"
            drift_file = 鲤鱼_HOME / "nexsandglass" / "drift.json"
            sand_db = 鲤鱼_HOME / "nexsandglass" / "sand.db"

            persona_data = {"available": False}

            if persona_file.exists():
                try:
                    persona_data["persona"] = persona_file.read_text()[:3000]
                    persona_data["available"] = True
                except OSError:
                    pass

            if drift_file.exists():
                try:
                    drift = json.loads(drift_file.read_text())
                    persona_data["drift"] = {
                        "direction": drift.get("current_direction"),
                        "stability": drift.get("stability"),
                        "history": drift.get("history", [])[-7:],
                    }
                except (json.JSONDecodeError, OSError):
                    pass

            # Grain count
            if sand_db.exists():
                import sqlite3
                try:
                    db = sqlite3.connect(str(sand_db))
                    count = db.execute("SELECT COUNT(*) FROM sand").fetchone()[0]
                    db.close()
                    persona_data["total_grains"] = count
                except Exception:
                    pass

            result["personas"][user_id] = persona_data
        else:
            # For other users, check if they have a persona file
            user_persona_file = 鲤鱼_HOME / "nexsandglass" / f"persona-{user_id}.md"
            if user_persona_file.exists():
                try:
                    result["personas"][user_id] = {
                        "available": True,
                        "persona": user_persona_file.read_text()[:3000]
                    }
                except OSError:
                    result["personas"][user_id] = {"available": False}
            else:
                result["personas"][user_id] = {"available": False}

    return result


def api_tool_guard() -> dict:
    """工具防护状态"""
    state_file = 鲤鱼_HOME / "tool-guard-state.json"
    config_file = 鲤鱼_HOME / "tool-guard-config.json"
    history_file = 鲤鱼_HOME / "tool-guard-history.jsonl"

    result = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            result["alerts"] = {
                "exact_failures": {
                    k: v for k, v in state.get("exact_failures", {}).items() if v >= 2
                },
                "tool_failures": {
                    k: v for k, v in state.get("tool_failures", {}).items() if v >= 2
                },
                "no_progress": state.get("no_progress", {}),
            }
            result["summary"] = {
                "total_observed": state.get("total_observed", 0),
                "total_warned": state.get("total_warned", 0),
                "total_blocked": state.get("total_blocked", 0),
                "total_halted": state.get("total_halted", 0),
            }
        except (json.JSONDecodeError, OSError):
            pass

    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            result["thresholds"] = config.get("thresholds", {})
        except (json.JSONDecodeError, OSError):
            pass

    # Load history for visualization
    if history_file.exists():
        try:
            history = []
            with open(history_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            history.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            result["history"] = history[-500:]  # Last 500 entries
        except OSError:
            pass

    return result


def api_skills() -> dict:
    """技能注册表统计"""
    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.exists():
        return {"total": 0}

    # Count skills with skill.json
    total = 0
    with_deps = 0
    with_conflicts = 0
    for d in skills_dir.iterdir():
        if d.is_dir() and (d / "SKILL.md").exists():
            total += 1
            if (d / "skill.json").exists():
                try:
                    meta = json.loads((d / "skill.json").read_text())
                    if meta.get("dependencies"):
                        with_deps += 1
                    if meta.get("conflicts"):
                        with_conflicts += 1
                except (json.JSONDecodeError, OSError):
                    pass

    return {
        "total": total,
        "with_dependencies": with_deps,
        "with_conflicts": with_conflicts,
        "orphans": total - with_deps - with_conflicts,
    }


def api_events() -> dict:
    """事件总线统计"""
    events_file = 鲤鱼_HOME / "event-bus" / "events.jsonl"
    if not events_file.exists():
        return {"total": 0}

    total = 0
    by_type = {}
    by_source = {}
    with open(events_file) as f:
        for line in f:
            if line.strip():
                try:
                    evt = json.loads(line)
                    total += 1
                    t = evt.get("type", "unknown")
                    s = evt.get("source", "unknown")
                    by_type[t] = by_type.get(t, 0) + 1
                    by_source[s] = by_source.get(s, 0) + 1
                except json.JSONDecodeError:
                    pass

    return {
        "total": total,
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])[:10]),
        "by_source": by_source,
    }


def api_stats() -> dict:
    """综合实时统计 — 仪表盘专用"""
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "modules": 0,
        "rules": 0,
        "senses": 0,
        "frameworks_active": 0,
        "hooks": 0,
        "tool_calls": 0,
        "tool_usage": {},
        "blocked": 0,
        "health_score": 0,
    }

    # Count Python modules (excluding test files)
    py_files = [f for f in 鲤鱼_HOME.glob("*.py") if not f.name.startswith("test_") and not f.name.startswith("test-")]
    result["modules"] = len(py_files)
    result["module_list"] = sorted([f.stem for f in py_files])

    # Count rules
    rules_dir = Path.home() / ".claude" / "rules" / "liyu"
    if rules_dir.exists():
        result["rules"] = len(list(rules_dir.glob("*.md")))

    # Count senses
    senses_dir = 鲤鱼_HOME / "senses"
    if senses_dir.exists():
        result["senses"] = len(list(senses_dir.glob("*.json")))

    # Count active frameworks
    fw_dir = 鲤鱼_HOME / "frameworks" / "active"
    if fw_dir.exists():
        result["frameworks_active"] = len(list(fw_dir.glob("*.json")))

    # Count hooks
    hooks_dir = 鲤鱼_HOME / "hooks"
    if hooks_dir.exists():
        result["hooks"] = len([f for f in hooks_dir.iterdir() if f.suffix in ('.sh', '.py')])

    # Tool usage from tool-guard history
    history_file = 鲤鱼_HOME / "tool-guard-history.jsonl"
    if history_file.exists():
        tool_counts = {}
        blocked_count = 0
        try:
            with open(history_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            tool = entry.get("tool", "unknown")
                            tool_counts[tool] = tool_counts.get(tool, 0) + 1
                            if entry.get("decision") in ("block", "halt"):
                                blocked_count += 1
                        except json.JSONDecodeError:
                            pass
            result["tool_usage"] = dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:10])
            result["tool_calls"] = sum(tool_counts.values())
            result["blocked"] = blocked_count
        except OSError:
            pass

    # Rule health score (规则健康度)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rule_health", 鲤鱼_HOME / "liyu-rule-health.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "generate_report"):
            rules = mod.generate_report()
            healthy = sum(1 for r in rules if not r.needs_attention)
            total = len(rules)
            result["rule_health"] = round(healthy / max(total, 1) * 100) if total else 0
    except Exception:
        result["rule_health"] = 0

    # Cost health score (成本健康度) from cost tracker
    try:
        import importlib.util
        spec3 = importlib.util.spec_from_file_location(
            "cost_tracker", 鲤鱼_HOME / "liyu-cost-tracker.py"
        )
        mod3 = importlib.util.module_from_spec(spec3)
        spec3.loader.exec_module(mod3)
        sessions = mod3.parse_all_sessions()
        overheads = mod3.analyze_config_overhead()
        waste = mod3.detect_waste(sessions, overheads)
        report = mod3.generate_report(sessions, overheads, waste)
        result["cost_health"] = report.health_score
        result["cost_grade"] = report.health_grade
    except Exception:
        result["cost_health"] = 0
        result["cost_grade"] = "F"

    # Combined score: average of both
    result["health_score"] = round((result.get("rule_health", 0) + result.get("cost_health", 0)) / 2)

    # Waste breakdown analysis
    waste = {}
    total_calls = result.get("tool_calls", 0)
    tool_usage = result.get("tool_usage", {})

    # 1. Ghost skills: skills loaded but rarely/never invoked
    try:
        skills_dir = Path.home() / ".claude" / "skills"
        if skills_dir.exists():
            total_skills = len([d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])
            # Skills referenced in tool usage are "active"
            active_skills = sum(1 for k in tool_usage if k not in ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Agent", "TaskCreate", "TaskUpdate", "StructuredOutput"))
            ghost = max(total_skills - active_skills, 0)
            waste["ghost_skills"] = ghost
    except Exception:
        waste["ghost_skills"] = 0

    # 2. Repeated context: Read calls that might be redundant
    read_calls = tool_usage.get("Read", 0)
    write_calls = tool_usage.get("Write", 0) + tool_usage.get("Edit", 0)
    if read_calls > 0 and write_calls > 0:
        # High read-to-write ratio suggests potential repeated reads
        ratio = read_calls / max(write_calls, 1)
        waste["repeated_context"] = max(int((ratio - 1.5) * 10), 0) if ratio > 1.5 else 0
    else:
        waste["repeated_context"] = 0

    # 3. Tool imbalance: one tool dominates
    if tool_usage:
        max_tool = max(tool_usage.values())
        dominant_pct = max_tool / max(total_calls, 1) * 100
        waste["tool_imbalance"] = max(int(dominant_pct - 40), 0) if dominant_pct > 50 else 0
    else:
        waste["tool_imbalance"] = 0

    # 4. Blocked attempts = direct waste
    waste["blocked_attempts"] = result.get("blocked", 0)

    # 5. Other waste (heuristic)
    waste["other"] = max(total_calls // 100, 1) if total_calls > 0 else 0

    result["waste"] = waste

    return result


def api_ctm() -> dict:
    """CTM 思维引擎实时状态"""
    result = {
        "active": 0,
        "total_created": 0,
        "coherence": 1.0,
        "efficiency": 0,
        "compute_strategy": "standard",
        "streams": [],
        "depth_dist": {"d0": 0, "d1": 0, "d2": 0, "d3": 0, "d4p": 0},
        "oscillator_stats": {},
        "complexity_avg": 0
    }

    try:
        from ctm.ctm_core import CTMCore, CTMConfig
        ctm = CTMCore(CTMConfig())

        # 获取思维引擎状态
        if ctm.thinking_engine:
            streams = ctm.thinking_engine.streams
            result["active"] = len(streams)
            result["total_created"] = ctm.total_streams_created

            # 流详情
            for sid, stream in list(streams.items())[-5:]:
                stream_info = {
                    "stream_id": sid,
                    "state": stream.current_state.value if hasattr(stream.current_state, 'value') else str(stream.current_state),
                    "max_depth": stream.max_depth,
                    "nodes_count": len(stream.nodes),
                    "total_tokens": stream.total_tokens,
                    "query": stream.query[:40] if stream.query else "",
                    "elapsed": round(time.time() - stream.start_time, 1) if stream.start_time else 0
                }
                result["streams"].append(stream_info)

                # 深度分布
                for node in stream.nodes:
                    d = node.depth
                    if d == 0:
                        result["depth_dist"]["d0"] += 1
                    elif d == 1:
                        result["depth_dist"]["d1"] += 1
                    elif d == 2:
                        result["depth_dist"]["d2"] += 1
                    elif d == 3:
                        result["depth_dist"]["d3"] += 1
                    else:
                        result["depth_dist"]["d4p"] += 1

        # 振荡器同步状态
        if ctm.oscillator:
            osc = ctm.oscillator
            result["oscillator_stats"] = {
                "modules": len(osc.oscillators),
                "coherence": osc.compute_phase_coherence() if hasattr(osc, 'compute_phase_coherence') else 1.0,
                "last_sync": osc.sync_history[-1].to_dict() if osc.sync_history else None
            }
            result["coherence"] = result["oscillator_stats"]["coherence"]

        # 自适应计算统计
        if ctm.compute_timer:
            result["compute_strategy"] = "adaptive"
            result["efficiency"] = 0.85  # Placeholder

    except ImportError:
        pass
    except Exception as e:
        result["error"] = str(e)

    # 从 CTM 状态文件读取（如果存在）
    ctm_state_file = 鲤鱼_HOME / "ctm" / "state.json"
    if ctm_state_file.exists():
        try:
            state = json.loads(ctm_state_file.read_text())
            result["active"] = state.get("active_streams", result["active"])
            result["total_created"] = state.get("total_streams_created", result["total_created"])
            result["coherence"] = state.get("coherence", result["coherence"])
        except (json.JSONDecodeError, OSError):
            pass

    return result


def api_activity() -> dict:
    """最近活动 — 从 git log 获取"""
    import subprocess
    entries = []
    try:
        result = subprocess.run(
            ["git", "log", "--since=7 days ago", "--format=%ai|%s", "--no-merges"],
            capture_output=True, text=True, cwd=str(鲤鱼_HOME), timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[:20]:
                if "|" in line:
                    time_str, desc = line.split("|", 1)
                    # Format time: "2026-06-17 04:22:16 +0800" → "2026-06-17 04:22"
                    date_part = time_str[:16].strip()
                    entries.append({"time": date_part, "type": "commit", "desc": desc.strip()})
    except Exception:
        pass

    # Also include story.jsonl events
    story_file = 鲤鱼_HOME / "story.jsonl"
    if story_file.exists():
        try:
            with open(story_file) as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            if entry.get("event") != "session_end":
                                entries.append({
                                    "time": entry.get("date", ""),
                                    "type": entry.get("event", "event"),
                                    "desc": entry.get("summary", "")[:80]
                                })
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass

    # Sort by time descending, limit to 15
    entries.sort(key=lambda x: x.get("time", ""), reverse=True)
    return {"entries": entries[:15]}


# ── Action APIs (POST) ─────────────────────────────────────────────────────

def api_evolve(params: dict = None) -> dict:
    """触发进化分析"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "policy_engine", 鲤鱼_HOME / "policy-engine.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        engine = mod.PolicyEngine()
        stats = engine.stats()
        return {"status": "ok", "action": "evolve", "rules": stats["total_rules"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_tool_guard_reset(params: dict = None) -> dict:
    """重置工具防护状态"""
    tool = (params or {}).get("tool", ["all"])
    if isinstance(tool, list): tool = tool[0]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tool_guard", 鲤鱼_HOME / "tool-guard.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        guard = mod.ToolGuard()
        result = guard.reset(tool)
        return {"status": "ok", "action": "reset", "detail": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_persona_rebuild(params: dict = None) -> dict:
    """强制重建画像（full scan）"""
    try:
        from nexsandglass import NexSandglass
        ns = NexSandglass()
        persona = ns.persona.build(full=True)
        guide = ns.interaction_guide()
        return {
            "status": "ok",
            "action": "persona_rebuild",
            "grains": persona.get("total_grains", 0),
            "guide_preview": guide[:200],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_drift_recalc(params: dict = None) -> dict:
    """重新计算偏移率"""
    try:
        from nexsandglass import NexSandglass
        ns = NexSandglass()
        drift = ns.drift.compute_range(30)
        return {"status": "ok", "action": "drift_recalc", "drift": drift}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def api_timeline_detail(params: dict) -> dict:
    """增强时间线查询——支持搜索、过滤、分页"""
    limit = min(int(params.get("limit", [50])[0]), 200)
    event_type = params.get("type", [None])[0]
    search = params.get("search", [None])[0]
    source = params.get("source", [None])[0]

    try:
        from timeline import query
        rows = query(limit=limit, event_type=event_type, source=source, search=search)
        return {"entries": rows, "total": len(rows), "filters": {
            "type": event_type, "search": search, "source": source,
        }}
    except ImportError:
        return api_timeline(params)


def api_users() -> dict:
    """用户层级列表"""
    try:
        from user_manager import UserManager
        mgr = UserManager()
        return {
            "users": mgr.list_all(),
            "stats": mgr.stats(),
        }
    except Exception as e:
        return {"error": str(e)}


def api_sense_detail(params: dict) -> dict:
    """单个 sense 详情"""
    sense_name = params.get("name", ["o2"])[0]
    sense_file = 鲤鱼_HOME / "senses" / f"{sense_name}.json"
    if sense_file.exists():
        try:
            return json.loads(sense_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"error": "sense not found"}


def api_models(params: dict = None) -> dict:
    """模型配置信息"""
    settings_file = Path.home() / ".claude" / "settings.json"

    result = {
        "current": {},
        "available": [],
        "env_vars": {}
    }

    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
            env = settings.get("env", {})

            result["current"] = {
                "model": env.get("ANTHROPIC_MODEL", ""),
                "haiku": env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", ""),
                "sonnet": env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ""),
                "opus": env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", ""),
                "base_url": env.get("ANTHROPIC_BASE_URL", ""),
                "effort_level": env.get("CLAUDE_CODE_EFFORT_LEVEL", "default")
            }

            result["env_vars"] = env

            # 检查代理状态
            base_url = env.get("ANTHROPIC_BASE_URL", "")
            if "127.0.0.1" in base_url or "localhost" in base_url:
                result["proxy"] = {
                    "enabled": True,
                    "url": base_url,
                    "status": "local"
                }
            else:
                result["proxy"] = {
                    "enabled": False,
                    "url": base_url,
                    "status": "direct"
                }
        except (json.JSONDecodeError, OSError):
            pass

    # 加载可用预设
    result["presets"] = {
        "mimo": {"name": "MiMo (小米)", "model": "mimo-v2.5-pro"},
        "mimo-1m": {"name": "MiMo 1M (大上下文)", "model": "mimo-v2.5-pro[1M]"},
        "deepseek": {"name": "DeepSeek V4 Pro", "model": "deepseek-v4-pro"},
        "claude": {"name": "Claude (Anthropic)", "model": "claude-sonnet-4-6"},
        "proxy": {"name": "Local Proxy (Token 追踪)", "model": "proxy"}
    }

    return result


def api_model_list(params: dict = None) -> dict:
    """列出可用模型（只读）"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "liyu_model", 鲤鱼_HOME / "liyu-model.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        manager = mod.ModelManager()

        return {
            "current": manager.get_current(),
            "models": manager.list_models(),
            "presets": mod.PRESETS
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── SSE (Server-Sent Events) ─────────────────────────────────────────────

# SSE 客户端列表：每个客户端是一个 queue
_sse_clients: list = []


def api_stream(params: dict = None):
    """SSE 事件流——Generator，不返回 dict"""
    import queue
    q = queue.Queue()
    _sse_clients.append(q)
    try:
        # 发送初始连接事件
        yield f"event: connected\ndata: {json.dumps({'status':'connected','clients':len(_sse_clients)})}\n\n"
        while True:
            try:
                event = q.get(timeout=30)
                yield f"event: {event.get('event','update')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"event: heartbeat\ndata: {json.dumps({'timestamp':datetime.now(timezone.utc).isoformat()})}\n\n"
    except GeneratorExit:
        pass
    finally:
        if q in _sse_clients:
            _sse_clients.remove(q)


def _broadcast_sse(event: dict):
    """向所有 SSE 客户端广播事件"""
    dead = []
    for q in _sse_clients:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        if q in _sse_clients:
            _sse_clients.remove(q)


def _poll_sse_events():
    """检查 sse-events.jsonl 是否有新事件，有则广播"""
    sse_file = 鲤鱼_HOME / "nexsandglass" / "sse-events.jsonl"
    if not sse_file.exists():
        return
    # Track last read position
    state_file = 鲤鱼_HOME / "nexsandglass" / ".sse-position"
    last_pos = 0
    if state_file.exists():
        try:
            last_pos = int(state_file.read_text().strip())
        except (ValueError, OSError):
            pass
    current_size = sse_file.stat().st_size
    if current_size > last_pos:
        with open(sse_file) as f:
            f.seek(last_pos)
            for line in f:
                if line.strip():
                    try:
                        event = json.loads(line)
                        _broadcast_sse(event)
                    except json.JSONDecodeError:
                        pass
        state_file.write_text(str(current_size))


# ── HTTP Server ────────────────────────────────────────────────────────────

GET_ROUTES = {
    "/api/status": api_status,
    "/api/stats": api_stats,
    "/api/activity": api_activity,
    "/api/ctm": api_ctm,
    "/api/modules": api_modules,
    "/api/timeline": api_timeline,
    "/api/persona": api_persona,
    "/api/persona/all": api_persona_all,
    "/api/tool-guard": api_tool_guard,
    "/api/skills": api_skills,
    "/api/events": api_events,
    "/api/timeline/detail": api_timeline_detail,
    "/api/sense/detail": api_sense_detail,
    "/api/stream": api_stream,
    "/api/users": api_users,
    "/api/models": api_models,
    "/api/model/list": api_model_list,
}

POST_ROUTES = {
    "/api/evolve": api_evolve,
    "/api/tool-guard/reset": api_tool_guard_reset,
    "/api/persona/rebuild": api_persona_rebuild,
    "/api/drift/recalc": api_drift_recalc,
}


class PhoenixHandler(BaseHTTPRequestHandler):
    """鲤鱼 HTTP Request Handler"""

    def log_message(self, format, *args):
        """Silence default logging — use our own."""
        pass

    def _cors_headers(self):
        """Add CORS headers for cross-origin requests."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, generator):
        """发送 SSE 事件流"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors_headers()
        self.end_headers()
        try:
            for chunk in generator:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, msg: str, status: int = 500):
        self._send_json({"error": msg}, status)

    def _route_get(self, path, params):
        """Handle GET routes. Returns (result, is_sse)"""
        if path in GET_ROUTES:
            handler = GET_ROUTES[path]
            is_sse = (path == "/api/stream")
            if path in ("/api/timeline", "/api/timeline/detail", "/api/sense/detail", "/api/persona/all", "/api/models"):
                return handler(params), is_sse
            return handler(), is_sse
        return None, False

    def do_GET(self):
        # Poll SSE events on any request (keeps broadcasts flowing)
        _poll_sse_events()

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Landing page
        if path == "/":
            for html_file in [INDEX_FILE, DASHBOARD_FILE]:
                if html_file.exists():
                    try:
                        html = html_file.read_text()
                        self._send_html(html)
                        return
                    except OSError:
                        pass
            self._send_html("<h1>鲤鱼</h1><p>v1.3.0 — Self-Evolving Agent Harness</p>")
            return

        # Simple Dashboard
        if path == "/dashboard":
            if DASHBOARD_FILE.exists():
                try:
                    html = DASHBOARD_FILE.read_text()
                    self._send_html(html)
                    return
                except OSError:
                    pass
            self._send_html("<h1>鲤鱼 Dashboard</h1><p>Dashboard file not found.</p>")
            return

        # Visualization Dashboard
        if path == "/viz" or path == "/dashboard-viz":
            if DASHBOARD_VIZ_FILE.exists():
                try:
                    html = DASHBOARD_VIZ_FILE.read_text()
                    self._send_html(html)
                    return
                except OSError:
                    pass
            self._send_html("<h1>鲤鱼 Visualization Dashboard</h1><p>Dashboard file not found.</p>")
            return

        # Test Dashboard
        test_file = 鲤鱼_HOME / "test-dashboard.html"
        if path == "/test" or path == "/test-dashboard.html":
            if test_file.exists():
                try:
                    html = test_file.read_text()
                    self._send_html(html)
                    return
                except OSError:
                    pass
            self._send_html("<h1>Test Dashboard</h1><p>File not found.</p>")
            return

        # Generic HTML file serving
        if path.endswith(".html"):
            html_file = 鲤鱼_HOME / path.lstrip("/")
            if html_file.exists():
                try:
                    html = html_file.read_text()
                    self._send_html(html)
                    return
                except OSError:
                    pass
            self._send_error(f"File not found: {path}", 404)
            return

        # API routes
        try:
            result, is_sse = self._route_get(path, params)
            if result is not None:
                if is_sse:
                    self._send_sse(result)
                else:
                    self._send_json(result)
                return
        except Exception as e:
            self._send_error(str(e))
            return

        # Health check
        if path == "/health":
            self._send_json({"status": "ok", "service": "鲤鱼"})
            return

        # 404
        self._send_error("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # Read body for JSON params
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length)
                body_params = json.loads(body)
                if isinstance(body_params, dict):
                    # Merge body params into query params
                    for k, v in body_params.items():
                        params[k] = [v] if not isinstance(v, list) else v
        except (json.JSONDecodeError, ValueError):
            pass

        if path in POST_ROUTES:
            try:
                handler = POST_ROUTES[path]
                result = handler(params)
                self._send_json(result)
            except Exception as e:
                self._send_error(str(e))
            return

        self._send_error("Not found", 404)

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    global PORT, HOST

    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            PORT = int(sys.argv[i + 1])
        if arg == "--host" and i + 1 < len(sys.argv):
            HOST = sys.argv[i + 1]

    server = ThreadingHTTPServer((HOST, PORT), PhoenixHandler)
    print(f"🐦‍🔥 鲤鱼 Server")
    print(f"   http://{HOST}:{PORT}")
    print(f"   API: http://{HOST}:{PORT}/api/status")
    print(f"   Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 鲤鱼 Server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
