#!/usr/bin/env python3
"""
鲤鱼 Automation System
自动发现、压缩、调度、恢复
"""

import os
import sys
import json
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import hashlib

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SKILLS_DIR = 鲤鱼_HOME / "skills"
DB_PATH = 鲤鱼_HOME / "automation.db"


class PhoenixAutomation:
    """鲤鱼 自动化系统"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS discovered_skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                last_used TEXT,
                use_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS compression_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                compressed_at TEXT NOT NULL,
                entries_removed INTEGER,
                entries_kept INTEGER,
                space_saved INTEGER
            )
        """)
        conn.commit()
        conn.close()
    
    # === 技能自动发现 ===
    
    def discover_skills(self) -> List[Dict]:
        """扫描 skills 目录，自动发现新技能"""
        discovered = []
        
        for skill_file in SKILLS_DIR.glob("*.py"):
            if skill_file.name.startswith("_"):
                continue
            
            skill_id = skill_file.stem
            
            # 检查是否已注册
            conn = sqlite3.connect(self.db_path)
            existing = conn.execute(
                "SELECT id FROM discovered_skills WHERE id = ?",
                (skill_id,)
            ).fetchone()
            
            if not existing:
                # 解析技能元数据
                meta = self._parse_skill_metadata(skill_file)
                
                conn.execute(
                    """INSERT INTO discovered_skills 
                       (id, name, path, discovered_at, status)
                       VALUES (?, ?, ?, ?, ?)""",
                    (skill_id, meta.get("name", skill_id),
                     str(skill_file), datetime.now().isoformat(), "active")
                )
                conn.commit()
                discovered.append({
                    "id": skill_id,
                    "name": meta.get("name", skill_id),
                    "path": str(skill_file)
                })
            
            conn.close()
        
        return discovered
    
    def _parse_skill_metadata(self, skill_file: Path) -> Dict:
        """解析技能元数据"""
        meta = {"name": skill_file.stem}
        
        try:
            content = skill_file.read_text()
            
            # 从 docstring 提取名称
            if '"""' in content:
                start = content.index('"""') + 3
                end = content.index('"""', start)
                docstring = content[start:end].strip()
                if docstring:
                    meta["name"] = docstring.split("\n")[0].strip()
        except Exception:
            pass
        
        return meta
    
    # === 记忆自动压缩 ===
    
    def compress_memories(self, days_old: int = 30) -> Dict:
        """压缩旧记忆"""
        stats = {"removed": 0, "kept": 0, "space_saved": 0}
        
        # 压缩 auto_memories
        db_path = 鲤鱼_HOME / "knowledge-base.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            
            cutoff = (datetime.now() - timedelta(days=days_old)).isoformat()
            
            # 统计旧条目
            old_count = conn.execute(
                "SELECT COUNT(*) FROM auto_memories WHERE is_active = 0 AND created_at < ?",
                (cutoff,)
            ).fetchone()[0]
            
            if old_count > 0:
                # 删除旧条目
                conn.execute(
                    "DELETE FROM auto_memories WHERE is_active = 0 AND created_at < ?",
                    (cutoff,)
                )
                stats["removed"] = old_count
            
            # 统计保留条目
            kept_count = conn.execute(
                "SELECT COUNT(*) FROM auto_memories WHERE is_active = 1"
            ).fetchone()[0]
            stats["kept"] = kept_count
            
            conn.commit()
            conn.close()
        
        # 记录压缩日志
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO compression_log 
               (compressed_at, entries_removed, entries_kept, space_saved)
               VALUES (?, ?, ?, ?)""",
            (datetime.now().isoformat(), stats["removed"], stats["kept"], stats["space_saved"])
        )
        conn.commit()
        conn.close()
        
        return stats
    
    # === 任务自动调度 ===
    
    def add_task(self, task_type: str, payload: Dict, priority: int = 5) -> int:
        """添加任务到队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """INSERT INTO task_queue 
               (task_type, priority, payload, created_at)
               VALUES (?, ?, ?, ?)""",
            (task_type, priority, json.dumps(payload), datetime.now().isoformat())
        )
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id
    
    def get_next_task(self) -> Optional[Dict]:
        """获取下一个待处理任务"""
        conn = sqlite3.connect(self.db_path)
        
        task = conn.execute(
            """SELECT id, task_type, priority, payload 
               FROM task_queue 
               WHERE status = 'pending'
               ORDER BY priority ASC, created_at ASC
               LIMIT 1"""
        ).fetchone()
        
        if task:
            # 标记为处理中
            conn.execute(
                "UPDATE task_queue SET status = 'running', started_at = ? WHERE id = ?",
                (datetime.now().isoformat(), task[0])
            )
            conn.commit()
            
            result = {
                "id": task[0],
                "type": task[1],
                "priority": task[2],
                "payload": json.loads(task[3])
            }
        else:
            result = None
        
        conn.close()
        return result
    
    def complete_task(self, task_id: int, success: bool = True, error: str = None):
        """完成任务"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE task_queue 
               SET status = ?, completed_at = ?, error = ?
               WHERE id = ?""",
            ("completed" if success else "failed",
             datetime.now().isoformat(), error, task_id)
        )
        conn.commit()
        conn.close()
    
    # === 错误自动恢复 ===
    
    def retry_failed_tasks(self, max_retries: int = 3) -> List[Dict]:
        """重试失败的任务"""
        retried = []
        
        conn = sqlite3.connect(self.db_path)
        
        failed_tasks = conn.execute(
            """SELECT id, task_type, payload, error
               FROM task_queue 
               WHERE status = 'failed'
               ORDER BY created_at DESC
               LIMIT 10"""
        ).fetchall()
        
        for task in failed_tasks:
            task_id = task[0]
            
            # 重置为待处理
            conn.execute(
                "UPDATE task_queue SET status = 'pending', error = NULL WHERE id = ?",
                (task_id,)
            )
            retried.append({
                "id": task_id,
                "type": task[1],
                "payload": json.loads(task[2])
            })
        
        conn.commit()
        conn.close()
        
        return retried
    
    # === 统计 ===
    
    def get_stats(self) -> Dict:
        """获取自动化统计"""
        conn = sqlite3.connect(self.db_path)
        
        skills_count = conn.execute(
            "SELECT COUNT(*) FROM discovered_skills WHERE status = 'active'"
        ).fetchone()[0]
        
        tasks_pending = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE status = 'pending'"
        ).fetchone()[0]
        
        tasks_completed = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE status = 'completed'"
        ).fetchone()[0]
        
        tasks_failed = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE status = 'failed'"
        ).fetchone()[0]
        
        compressions = conn.execute(
            "SELECT COUNT(*) FROM compression_log"
        ).fetchone()[0]
        
        conn.close()
        
        return {
            "skills_discovered": skills_count,
            "tasks_pending": tasks_pending,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "compressions": compressions
        }


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("用法: liyu-automation.py <command>")
        print("命令:")
        print("  discover   - 发现新技能")
        print("  compress   - 压缩旧记忆")
        print("  stats      - 查看统计")
        print("  retry      - 重试失败任务")
        return
    
    automation = PhoenixAutomation()
    command = sys.argv[1]
    
    if command == "discover":
        skills = automation.discover_skills()
        if skills:
            print(f"发现 {len(skills)} 个新技能:")
            for skill in skills:
                print(f"  - {skill['name']} ({skill['id']})")
        else:
            print("没有发现新技能")
    
    elif command == "compress":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        stats = automation.compress_memories(days)
        print(f"压缩完成:")
        print(f"  删除: {stats['removed']} 条")
        print(f"  保留: {stats['kept']} 条")
    
    elif command == "stats":
        stats = automation.get_stats()
        print("自动化统计:")
        print(f"  已发现技能: {stats['skills_discovered']}")
        print(f"  待处理任务: {stats['tasks_pending']}")
        print(f"  已完成任务: {stats['tasks_completed']}")
        print(f"  失败任务: {stats['tasks_failed']}")
        print(f"  压缩次数: {stats['compressions']}")
    
    elif command == "retry":
        tasks = automation.retry_failed_tasks()
        if tasks:
            print(f"重试 {len(tasks)} 个失败任务")
        else:
            print("没有失败任务需要重试")
    
    else:
        print(f"未知命令: {command}")


if __name__ == "__main__":
    main()
