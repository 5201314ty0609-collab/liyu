#!/usr/bin/env python3
"""
鲤鱼 Dashboard Data Sync — 同步实时数据到 FALLBACK
定期更新 dashboard-viz.html 中的 FALLBACK 数据，确保离线也能显示最新状态

用法：
  python3 sync-dashboard-data.py           # 同步一次
  python3 sync-dashboard-data.py --watch   # 持续监听（每 30 秒）
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DASHBOARD_FILE = 鲤鱼_HOME / "dashboard-viz.html"
INDEX_FILE = 鲤鱼_HOME / "index.html"
DASHBOARD_BASIC_FILE = 鲤鱼_HOME / "dashboard.html"
OBSERVABILITY_DB = 鲤鱼_HOME / "observability.db"
SENSES_DIR = 鲤鱼_HOME / "senses"


def get_current_stats():
    """从 server API 获取当前统计数据（最准确）"""
    stats = {
        "modules": 0,
        "rules": 0,
        "senses": 7,
        "rule_health": 93,
        "cost_health": 72,
        "cost_grade": "C",
        "toolCalls": 0,
        "blocked": 0,
        "frameworks_active": 0,
        "hooks": 0
    }

    # 优先从 server API 获取数据（最准确）
    try:
        import urllib.request
        req = urllib.request.Request('http://127.0.0.1:8765/api/stats')
        with urllib.request.urlopen(req, timeout=3) as response:
            api_data = json.loads(response.read())
            stats["modules"] = api_data.get("modules", stats["modules"])
            stats["rules"] = api_data.get("rules", stats["rules"])
            stats["senses"] = api_data.get("senses", stats["senses"])
            stats["toolCalls"] = api_data.get("tool_calls", stats["toolCalls"])
            stats["blocked"] = api_data.get("blocked", stats["blocked"])
            stats["rule_health"] = api_data.get("rule_health", stats["rule_health"])
            stats["cost_health"] = api_data.get("cost_health", stats["cost_health"])
            stats["cost_grade"] = api_data.get("cost_grade", stats["cost_grade"])
            stats["frameworks_active"] = api_data.get("frameworks_active", stats["frameworks_active"])
            stats["hooks"] = api_data.get("hooks", stats["hooks"])
            print("  ✓ 从 server API 获取数据")
            return stats
    except Exception as e:
        print(f"  ⚠ Server API 不可用: {e}")

    # 备用：从本地文件收集数据
    # 计算模块数
    modules = list(鲤鱼_HOME.glob("*.py"))
    stats["modules"] = len([m for m in modules if not m.name.startswith("test_")])

    # 计算规则数
    rules_dir = Path.home() / ".claude" / "rules"
    if rules_dir.exists():
        stats["rules"] = len(list(rules_dir.rglob("*.md")))

    # 计算活跃框架数
    frameworks_dir = 鲤鱼_HOME / "frameworks" / "active"
    if frameworks_dir.exists():
        stats["frameworks_active"] = len(list(frameworks_dir.glob("*.json")))

    # 计算 hook 数
    settings_file = Path.home() / ".claude" / "settings.json"
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
            hooks = settings.get("hooks", {})
            stats["hooks"] = sum(len(v) for v in hooks.values())
        except Exception:
            pass

    # 从 tool-guard 获取工具调用统计
    tool_guard_log = 鲤鱼_HOME / "tool-guard-stats.json"
    if tool_guard_log.exists():
        try:
            tg_stats = json.loads(tool_guard_log.read_text())
            stats["toolCalls"] = tg_stats.get("total_calls", 0)
            stats["blocked"] = tg_stats.get("blocked", 0)
        except Exception:
            pass

    return stats


def update_dashboard_fallback(stats):
    """更新 dashboard-viz.html 中的 FALLBACK 数据"""
    if not DASHBOARD_FILE.exists():
        print(f"Error: Dashboard file not found: {DASHBOARD_FILE}")
        return False

    content = DASHBOARD_FILE.read_text()
    original = content

    # 更新各个字段（使用精确匹配，避免破坏格式）
    # 更新 modules
    content = re.sub(r'modules:\d+', f'modules:{stats["modules"]}', content)
    # 更新 rules
    content = re.sub(r'rules:\d+', f'rules:{stats["rules"]}', content)
    # 更新 cost_health
    content = re.sub(r'cost_health:\d+', f'cost_health:{stats["cost_health"]}', content)
    # 更新 toolCalls
    content = re.sub(r'toolCalls:\d+', f'toolCalls:{stats["toolCalls"]}', content)
    # 更新 blocked
    content = re.sub(r'blocked:\d+', f'blocked:{stats["blocked"]}', content)
    # 更新 frameworks_active
    content = re.sub(r'frameworks_active:\d+', f'frameworks_active:{stats["frameworks_active"]}', content)
    # 更新 hooks
    content = re.sub(r'hooks:\d+', f'hooks:{stats["hooks"]}', content)

    if content == original:
        # 检查是否真的是无变化
        if f'cost_health:{stats["cost_health"]}' in content:
            print(f"  ℹ dashboard-viz.html FALLBACK 已是最新")
            return False
        print("Warning: No changes made to FALLBACK")
        return False

    DASHBOARD_FILE.write_text(content)
    return True


def update_index_static(stats):
    """更新 index.html 中的静态数据"""
    if not INDEX_FILE.exists():
        print(f"Warning: Index file not found: {INDEX_FILE}")
        return False

    content = INDEX_FILE.read_text()
    original = content

    # 更新模块数（在 hero-meta 中）
    content = re.sub(
        r'<span>\d+ 引擎模块</span>',
        f'<span>{stats["modules"]} 引擎模块</span>',
        content
    )

    # 更新统计数字（使用更精确的匹配）
    content = re.sub(
        r'id="stat-modules">\d+</div>',
        f'id="stat-modules">{stats["modules"]}</div>',
        content
    )
    content = re.sub(
        r'id="stat-rules">\d+</div>',
        f'id="stat-rules">{stats["rules"]}</div>',
        content
    )
    content = re.sub(
        r'id="stat-rule-health">\d+%</div>',
        f'id="stat-rule-health">{stats["rule_health"]}%</div>',
        content
    )
    content = re.sub(
        r'id="stat-cost-health">\d+</div>',
        f'id="stat-cost-health">{stats["cost_health"]}</div>',
        content
    )

    if content == original:
        # 检查是否真的是无变化，还是已经是最新
        if f'id="stat-cost-health">{stats["cost_health"]}</div>' in content:
            print(f"  ℹ index.html 已是最新数据")
            return False
        print("Warning: No changes made to index.html")
        return False

    INDEX_FILE.write_text(content)
    return True


def update_dashboard_basic_static(stats):
    """更新 dashboard.html 中的静态数据"""
    if not DASHBOARD_BASIC_FILE.exists():
        print(f"Warning: Dashboard basic file not found: {DASHBOARD_BASIC_FILE}")
        return False

    content = DASHBOARD_BASIC_FILE.read_text()
    original = content

    # 更新页面描述
    content = re.sub(
        r'鲤鱼 v1\.3\.0 · \d+ 模块 · \d+ 规则 · 规则健康 \d+% · 成本健康 \d+/100 · \d{4}-\d{2}-\d{2}',
        f'鲤鱼 v1.3.0 · {stats["modules"]} 模块 · {stats["rules"]} 规则 · 规则健康 {stats["rule_health"]}% · 成本健康 {stats["cost_health"]}/100 · {datetime.now().strftime("%Y-%m-%d")}',
        content
    )

    # 更新 KPI 显示
    content = re.sub(
        r'id="kpi-rule-health">\d+%',
        f'id="kpi-rule-health">{stats["rule_health"]}%',
        content
    )
    content = re.sub(
        r'id="kpi-cost-health">\d+ / 100',
        f'id="kpi-cost-health">{stats["cost_health"]} / 100',
        content
    )

    if content == original:
        print("Warning: No changes made to dashboard.html")
        return False

    DASHBOARD_BASIC_FILE.write_text(content)
    return True


def sync_once():
    """执行一次同步"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 同步 dashboard 数据...")

    stats = get_current_stats()
    print(f"  成本健康: {stats['cost_health']}% ({stats['cost_grade']}级)")
    print(f"  工具调用: {stats['toolCalls']}")
    print(f"  模块数: {stats['modules']}")

    results = []

    # 更新 dashboard-viz.html
    if update_dashboard_fallback(stats):
        print(f"  ✓ dashboard-viz.html FALLBACK 已更新")
        results.append(True)
    else:
        print(f"  ⚠ dashboard-viz.html 无变化")
        results.append(False)

    # 更新 index.html
    if update_index_static(stats):
        print(f"  ✓ index.html 静态数据已更新")
        results.append(True)
    else:
        print(f"  ⚠ index.html 无变化")
        results.append(False)

    # 更新 dashboard.html
    if update_dashboard_basic_static(stats):
        print(f"  ✓ dashboard.html 静态数据已更新")
        results.append(True)
    else:
        print(f"  ⚠ dashboard.html 无变化")
        results.append(False)

    return any(results)


def watch_mode(interval=30):
    """持续监听模式"""
    print(f"启动持续监听模式 (每 {interval} 秒同步)")
    print("按 Ctrl+C 停止\n")

    while True:
        try:
            sync_once()
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n停止监听")
            break


def main():
    if "--watch" in sys.argv:
        watch_mode()
    else:
        sync_once()


if __name__ == "__main__":
    main()
