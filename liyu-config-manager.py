#!/usr/bin/env python3
"""
鲤鱼 Config Manager
统一配置管理、验证、热重载、版本控制
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
import sqlite3

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
CONFIGS_DIR = 鲤鱼_HOME / "configs"
DB_PATH = 鲤鱼_HOME / "config-manager.db"

# 默认配置
DEFAULT_CONFIGS = {
    "alert": {
        "enabled": True,
        "cooldown_minutes": 5,
        "max_per_hour": 20,
        "thresholds": {
            "o2": {"warning": 70, "critical": 85},
            "nociception": {"warning": 3, "critical": 5},
            "chronos": {"warning": 300, "critical": 600}
        }
    },
    "automation": {
        "skill_discovery": True,
        "memory_compression": True,
        "compression_days": 30,
        "task_scheduling": True,
        "error_recovery": True
    },
    "observability": {
        "metrics_collection": True,
        "collection_interval": 30,
        "dashboard_enabled": True,
        "alerting_enabled": True
    },
    "memory": {
        "unified_search": True,
        "auto_capture": True,
        "consolidation": True,
        "max_memories": 10000
    },
    "rules": {
        "dynamic_loading": True,
        "conflict_detection": True,
        "priority_system": True,
        "auto_migration": False
    },
    "skills": {
        "auto_discovery": True,
        "pipeline_enabled": True,
        "learning_enabled": True
    },
    "graph": {
        "parallel_execution": True,
        "subgraph_support": True,
        "visualization": True,
        "history_tracking": True
    }
}


class PhoenixConfigManager:
    """鲤鱼 统一配置管理器"""
    
    def __init__(self):
        self.configs_dir = CONFIGS_DIR
        self.configs_dir.mkdir(exist_ok=True)
        self.db_path = DB_PATH
        self._init_db()
        self._ensure_configs()
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT DEFAULT 'system',
                comment TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_name TEXT NOT NULL,
                key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL,
                changed_by TEXT DEFAULT 'user'
            )
        """)
        conn.commit()
        conn.close()
    
    def _ensure_configs(self):
        """确保所有配置文件存在"""
        for name, default in DEFAULT_CONFIGS.items():
            config_file = self.configs_dir / f"{name}.json"
            if not config_file.exists():
                self.save_config(name, default, comment="初始化默认配置")
    
    def get_config(self, name: str) -> Optional[Dict]:
        """获取配置"""
        config_file = self.configs_dir / f"{name}.json"
        if config_file.exists():
            return json.loads(config_file.read_text())
        return None
    
    def save_config(self, name: str, config: Dict, comment: str = None):
        """保存配置"""
        config_file = self.configs_dir / f"{name}.json"
        
        # 保存文件
        config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        
        # 记录版本
        content = json.dumps(config, sort_keys=True)
        hash_val = hashlib.md5(content.encode()).hexdigest()
        
        conn = sqlite3.connect(self.db_path)
        
        # 获取当前版本
        cursor = conn.execute(
            "SELECT MAX(version) FROM config_versions WHERE config_name = ?",
            (name,)
        )
        max_version = cursor.fetchone()[0] or 0
        
        conn.execute(
            """INSERT INTO config_versions 
               (config_name, version, content, hash, created_at, comment)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, max_version + 1, content, hash_val,
             datetime.now().isoformat(), comment)
        )
        conn.commit()
        conn.close()
    
    def update_config(self, name: str, key: str, value: Any, changed_by: str = "user"):
        """更新配置项"""
        config = self.get_config(name)
        if not config:
            return False
        
        old_value = config.get(key)
        config[key] = value
        
        self.save_config(name, config, comment=f"更新 {key}")
        
        # 记录变更
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO config_changes 
               (config_name, key, old_value, new_value, changed_at, changed_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, key, json.dumps(old_value), json.dumps(value),
             datetime.now().isoformat(), changed_by)
        )
        conn.commit()
        conn.close()
        
        return True
    
    def get_config_history(self, name: str, limit: int = 10) -> List[Dict]:
        """获取配置历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT version, hash, created_at, comment
               FROM config_versions 
               WHERE config_name = ?
               ORDER BY version DESC
               LIMIT ?""",
            (name, limit)
        )
        history = []
        for row in cursor.fetchall():
            history.append({
                "version": row[0],
                "hash": row[1],
                "created_at": row[2],
                "comment": row[3]
            })
        conn.close()
        return history
    
    def get_changes(self, name: str = None, limit: int = 20) -> List[Dict]:
        """获取配置变更记录"""
        conn = sqlite3.connect(self.db_path)
        
        if name:
            cursor = conn.execute(
                """SELECT config_name, key, old_value, new_value, changed_at, changed_by
                   FROM config_changes 
                   WHERE config_name = ?
                   ORDER BY changed_at DESC
                   LIMIT ?""",
                (name, limit)
            )
        else:
            cursor = conn.execute(
                """SELECT config_name, key, old_value, new_value, changed_at, changed_by
                   FROM config_changes 
                   ORDER BY changed_at DESC
                   LIMIT ?""",
                (limit,)
            )
        
        changes = []
        for row in cursor.fetchall():
            changes.append({
                "config": row[0],
                "key": row[1],
                "old_value": row[2],
                "new_value": row[3],
                "changed_at": row[4],
                "changed_by": row[5]
            })
        conn.close()
        return changes
    
    def validate_config(self, name: str) -> Dict:
        """验证配置"""
        config = self.get_config(name)
        if not config:
            return {"valid": False, "error": "配置不存在"}
        
        errors = []
        
        # 基本验证
        if not isinstance(config, dict):
            errors.append("配置必须是字典类型")
        
        # 特定配置验证
        if name == "alert":
            if "thresholds" not in config:
                errors.append("缺少 thresholds 配置")
            elif not isinstance(config["thresholds"], dict):
                errors.append("thresholds 必须是字典类型")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    def get_all_configs(self) -> Dict[str, Dict]:
        """获取所有配置"""
        configs = {}
        for config_file in self.configs_dir.glob("*.json"):
            name = config_file.stem
            configs[name] = json.loads(config_file.read_text())
        return configs
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        conn = sqlite3.connect(self.db_path)
        
        configs_count = len(list(self.configs_dir.glob("*.json")))
        
        versions_count = conn.execute(
            "SELECT COUNT(*) FROM config_versions"
        ).fetchone()[0]
        
        changes_count = conn.execute(
            "SELECT COUNT(*) FROM config_changes"
        ).fetchone()[0]
        
        conn.close()
        
        return {
            "configs": configs_count,
            "versions": versions_count,
            "changes": changes_count
        }


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("用法: liyu-config-manager.py <command>")
        print("命令:")
        print("  list           - 列出所有配置")
        print("  get <name>     - 获取配置")
        print("  set <name> <key> <value> - 设置配置项")
        print("  history <name> - 查看配置历史")
        print("  changes        - 查看变更记录")
        print("  validate <name> - 验证配置")
        print("  stats          - 查看统计")
        return
    
    manager = PhoenixConfigManager()
    command = sys.argv[1]
    
    if command == "list":
        configs = manager.get_all_configs()
        print(f"配置列表 ({len(configs)} 个):")
        for name in configs:
            print(f"  - {name}")
    
    elif command == "get":
        if len(sys.argv) < 3:
            print("用法: liyu-config-manager.py get <name>")
            return
        name = sys.argv[2]
        config = manager.get_config(name)
        if config:
            print(json.dumps(config, indent=2, ensure_ascii=False))
        else:
            print(f"配置 {name} 不存在")
    
    elif command == "set":
        if len(sys.argv) < 5:
            print("用法: liyu-config-manager.py set <name> <key> <value>")
            return
        name, key, value = sys.argv[2], sys.argv[3], sys.argv[4]
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        if manager.update_config(name, key, value):
            print(f"✓ 已更新 {name}.{key} = {value}")
        else:
            print(f"✗ 更新失败")
    
    elif command == "history":
        if len(sys.argv) < 3:
            print("用法: liyu-config-manager.py history <name>")
            return
        name = sys.argv[2]
        history = manager.get_config_history(name)
        if history:
            print(f"配置 {name} 历史:")
            for h in history:
                print(f"  v{h['version']} - {h['created_at']} - {h['comment'] or '无注释'}")
        else:
            print(f"配置 {name} 无历史记录")
    
    elif command == "changes":
        changes = manager.get_changes()
        if changes:
            print("最近变更:")
            for c in changes[:10]:
                print(f"  {c['config']}.{c['key']}: {c['old_value']} -> {c['new_value']}")
        else:
            print("无变更记录")
    
    elif command == "validate":
        if len(sys.argv) < 3:
            print("用法: liyu-config-manager.py validate <name>")
            return
        name = sys.argv[2]
        result = manager.validate_config(name)
        if result["valid"]:
            print(f"✓ 配置 {name} 有效")
        else:
            print(f"✗ 配置 {name} 无效:")
            for error in result["errors"]:
                print(f"  - {error}")
    
    elif command == "stats":
        stats = manager.get_stats()
        print("配置管理统计:")
        print(f"  配置文件: {stats['configs']}")
        print(f"  版本记录: {stats['versions']}")
        print(f"  变更记录: {stats['changes']}")
    
    else:
        print(f"未知命令: {command}")


if __name__ == "__main__":
    main()
