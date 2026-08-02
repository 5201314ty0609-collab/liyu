#!/usr/bin/env python3
"""
鲤鱼 用户层级管理 — User Tier System

三层体系:
  founder  — 创始人（时宇 + 鲤鱼），全权限，不可降级
  partner  — 合伙人（已构建 ≥10 沙粒的画像），完整功能
  user     — 使用者（未构建画像），学习期

自动升级: user → partner (persona 激活时)
创始人: 仅 holyty-founder + liyu-founder，硬编码保护

Usage:
  user_manager.py list                   列出所有用户
  user_manager.py register <name> <tier> 注册新用户
  user_manager.py status <user_id>       查看用户状态
  user_manager.py promote <user_id>      手动升级
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import sys

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
USERS_FILE = 鲤鱼_HOME / "users.json"

# 创始人名单——硬编码保护，不可降级、不可删除
FOUNDERS = {
    "holyty-founder": {
        "name": "holyty",
        "tier": "founder",
        "description": "鲤鱼 联合创始人 · 架构设计与方向",
        "created_at": "2026-06-05T00:00:00+00:00",
        "persona_status": "active",
    },
    "liyu-founder": {
        "name": "鲤鱼",
        "tier": "founder",
        "description": "鲤鱼 联合创始人 · 自进化引擎核心",
        "created_at": "2026-06-05T00:00:00+00:00",
        "persona_status": "active",
    },
}

TIER_LEVEL = {"founder": 3, "partner": 2, "user": 1}
TIER_LABELS = {
    "founder": {"zh": "创始人", "en": "Founder", "icon": "👑", "color": "#e0af68"},
    "partner": {"zh": "合伙人", "en": "Partner", "icon": "⭐", "color": "#7aa2f7"},
    "user":    {"zh": "使用者", "en": "User",    "icon": "🌱", "color": "#9ece6a"},
}


class UserManager:
    """鲤鱼 用户层级管理器"""

    def __init__(self):
        self.users = self._load()

    def _load(self) -> dict:
        """加载用户注册表"""
        if USERS_FILE.exists():
            try:
                data = json.loads(USERS_FILE.read_text())
                # Ensure founders always exist
                for fid, fdata in FOUNDERS.items():
                    if fid not in data:
                        data[fid] = dict(fdata)
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return dict(FOUNDERS)

    def _save(self) -> None:
        """持久化用户注册表"""
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        USERS_FILE.write_text(json.dumps(self.users, ensure_ascii=False, indent=2))

    # ── Registration ────────────────────────────────────────────────────

    def register(self, name: str, tier: str = "user",
                 description: str = "") -> dict:
        """
        注册新用户。
        tier 只能为 'user' 或 'partner'（founder 不可通过注册创建）
        """
        if tier not in ("user", "partner"):
            return {"status": "error", "message": f"无效层级: {tier}。只能注册 user 或 partner"}

        user_id = self._make_id(name, tier)
        if user_id in self.users:
            return {"status": "error", "message": f"用户已存在: {user_id}"}

        self.users[user_id] = {
            "name": name,
            "tier": tier,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "persona_status": "active" if tier == "partner" else "learning",
            "total_grains": 0,
        }
        self._save()
        return {"status": "ok", "user_id": user_id, "tier": tier}

    # ── Promotion ───────────────────────────────────────────────────────

    def check_promotion(self, user_id: str, total_grains: int) -> dict:
        """
        检查并自动升级：沙粒 ≥10 → user → partner
        Founder 不可降级，partner 不会退回 user
        """
        if user_id not in self.users:
            return {"status": "error", "message": "用户不存在"}

        user = self.users[user_id]
        current_tier = user["tier"]

        # Founder never changes
        if current_tier == "founder":
            user["total_grains"] = total_grains
            self._save()
            return {"status": "ok", "tier": "founder", "changed": False}

        # Update grain count
        user["total_grains"] = total_grains

        # Auto-promote: user → partner when ≥10 grains
        if current_tier == "user" and total_grains >= 10:
            user["tier"] = "partner"
            user["persona_status"] = "active"
            self._save()
            return {
                "status": "ok",
                "tier": "partner",
                "changed": True,
                "message": f"🎉 {user['name']} 已升级为合伙人！画像已激活。",
            }

        # Update persona status
        if total_grains >= 10:
            user["persona_status"] = "active"
        else:
            user["persona_status"] = "learning"

        self._save()
        return {"status": "ok", "tier": current_tier, "changed": False}

    # ── Query ───────────────────────────────────────────────────────────

    def get(self, user_id: str) -> Optional[dict]:
        """获取用户信息"""
        return self.users.get(user_id)

    def list_all(self) -> list[dict]:
        """列出所有用户，按层级排序"""
        result = []
        for uid, data in self.users.items():
            tier_info = TIER_LABELS.get(data["tier"], TIER_LABELS["user"])
            result.append({
                "id": uid,
                "name": data["name"],
                "tier": data["tier"],
                "tier_label": tier_info["zh"],
                "tier_icon": tier_info["icon"],
                "persona_status": data.get("persona_status", "learning"),
                "total_grains": data.get("total_grains", 0),
                "description": data.get("description", ""),
                "created_at": data.get("created_at", ""),
            })
        return sorted(result, key=lambda u: -TIER_LEVEL.get(u["tier"], 0))

    def stats(self) -> dict:
        """用户统计"""
        by_tier = {"founder": 0, "partner": 0, "user": 0}
        for u in self.users.values():
            by_tier[u["tier"]] = by_tier.get(u["tier"], 0) + 1
        return {
            "total": len(self.users),
            "by_tier": by_tier,
            "tiers": TIER_LABELS,
        }

    @staticmethod
    def _make_id(name: str, tier: str) -> str:
        """生成用户 ID: name-tier"""
        import re
        safe_name = re.sub(r"[^a-zA-Z0-9一-鿿_-]", "", name.lower().replace(" ", "-"))
        return f"{safe_name}-{tier}" if tier != "founder" else f"{safe_name}-founder"


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    mgr = UserManager()

    if cmd == "list":
        users = mgr.list_all()
        print(f"═══ 鲤鱼 用户 ({len(users)} 人) ═══")
        for u in users:
            icon = u["tier_icon"]
            grains = f" · {u['total_grains']} 沙粒" if u["total_grains"] > 0 else ""
            print(f"  {icon} [{u['tier_label']}] {u['name']}{grains}")
            if u["description"]:
                print(f"     {u['description']}")

    elif cmd == "register":
        if len(sys.argv) < 4:
            print("Usage: user_manager.py register <name> <tier> [description]")
            print("  tier: user | partner")
            return
        name = sys.argv[2]
        tier = sys.argv[3]
        desc = sys.argv[4] if len(sys.argv) > 4 else ""
        result = mgr.register(name, tier, desc)
        if result["status"] == "ok":
            print(f"✅ 注册成功: {result['user_id']} ({result['tier']})")
        else:
            print(f"❌ {result['message']}")

    elif cmd == "status":
        user_id = sys.argv[2] if len(sys.argv) > 2 else "holyty-founder"
        user = mgr.get(user_id)
        if user:
            tier_info = TIER_LABELS.get(user["tier"], TIER_LABELS["user"])
            print(f"{tier_info['icon']} {user['name']}")
            print(f"   层级: {tier_info['zh']} ({user['tier']})")
            print(f"   画像: {user.get('persona_status', '?')}")
            print(f"   沙粒: {user.get('total_grains', 0)}")
        else:
            print(f"❌ 用户不存在: {user_id}")

    elif cmd == "promote":
        user_id = sys.argv[2] if len(sys.argv) > 2 else ""
        # Check grain count from NexSandglass
        try:
            from nexsandglass import NexSandglass
            ns = NexSandglass()
            grains = ns.writer.count()
            result = mgr.check_promotion(user_id, grains)
            print(f"  {result.get('message', result.get('tier', '?'))}")
        except Exception as e:
            print(f"❌ {e}")

    elif cmd == "stats":
        s = mgr.stats()
        print(f"═══ 鲤鱼 用户统计 ═══")
        print(f"  总用户: {s['total']}")
        for tier, count in s["by_tier"].items():
            info = s["tiers"].get(tier, {})
            if count > 0:
                print(f"  {info.get('icon','')} {info.get('zh',tier)}: {count} 人")

    elif cmd == "setup":
        # 首次部署引导——创建用户身份
        print("🐦‍🔥 鲤鱼 首次部署 · 用户身份创建")
        print("")
        # Check if already set up
        existing = [u for u in mgr.users.values() if u["tier"] != "founder"]
        if existing:
            print(f"⚠️ 已存在 {len(existing)} 个非创始人用户，跳过设置。")
            print("  如需重新设置，请删除 users.json 后重试。")
            return
        print("  请选择你的用户名（英文字母+数字+下划线）：")
        username = input("  > ").strip()
        if not username:
            print("❌ 用户名不能为空")
            return
        # Validate username
        import re
        if not re.match(r"^[a-zA-Z0-9_]{2,30}$", username):
            print("❌ 用户名格式无效。只允许英文字母、数字、下划线，2-30 个字符。")
            return
        result = mgr.register(username, "user", f"鲤鱼 使用者 · {username}")
        if result["status"] == "ok":
            print(f"✅ 欢迎，{username}！")
            print(f"   你的 ID: {result['user_id']}")
            print(f"   当前层级: 🌱 使用者")
            print(f"   积累 ≥10 条对话沙粒后自动升级为 ⭐ 合伙人")
            print(f"")
            print(f"   鲤鱼 仪表盘: http://127.0.0.1:8765")
        else:
            print(f"❌ {result['message']}")

    elif cmd == "init":
        # Force re-initialize with founders
        mgr.users = dict(FOUNDERS)
        mgr._save()
        print("✅ 用户系统已初始化（2 位创始人）")
        for uid, data in FOUNDERS.items():
            print(f"  👑 {data['name']} ({uid})")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
