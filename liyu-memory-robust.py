#!/usr/bin/env python3
"""
鲤鱼 Memory Robustness Enhancer
错误恢复、数据一致性、并发安全、性能优化
"""

import os
import sys
import json
import sqlite3
import hashlib
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from contextlib import contextmanager
from collections import defaultdict

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DB_PATH = 鲤鱼_HOME / "knowledge-base.db"
HEALTH_DB = 鲤鱼_HOME / "memory-health.db"


class MemoryRobustness:
    """Memory 健壮性增强器"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self.health_db = HEALTH_DB
        self._lock = threading.Lock()
        self._init_health_db()
    
    def _init_health_db(self):
        """初始化健康检查数据库"""
        conn = sqlite3.connect(self.health_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_time TEXT NOT NULL,
                check_type TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT,
                duration_ms REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recovery_time TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                details TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    @contextmanager
    def get_db(self, db_path: str = None):
        """安全的数据库连接上下文管理器"""
        path = db_path or self.db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    # === 数据一致性检查 ===
    
    def check_consistency(self) -> Dict:
        """检查数据一致性"""
        results = {
            "status": "healthy",
            "issues": [],
            "fixed": 0
        }
        
        start_time = time.time()
        
        try:
            with self.get_db() as conn:
                # 检查 auto_memories 表
                orphaned = conn.execute("""
                    SELECT COUNT(*) FROM auto_memories 
                    WHERE is_active = 1 AND content IS NULL
                """).fetchone()[0]
                
                if orphaned > 0:
                    results["issues"].append({
                        "type": "orphaned_records",
                        "table": "auto_memories",
                        "count": orphaned
                    })
                    # 修复：标记为空内容
                    conn.execute("""
                        UPDATE auto_memories 
                        SET is_active = 0 
                        WHERE is_active = 1 AND content IS NULL
                    """)
                    results["fixed"] += orphaned
                
                # 检查 memory_links 表
                broken_links = conn.execute("""
                    SELECT COUNT(*) FROM memory_links 
                    WHERE source_id NOT IN (SELECT id FROM auto_memories)
                    OR target_id NOT IN (SELECT id FROM auto_memories)
                """).fetchone()[0]
                
                if broken_links > 0:
                    results["issues"].append({
                        "type": "broken_links",
                        "table": "memory_links",
                        "count": broken_links
                    })
                    # 修复：删除无效链接
                    conn.execute("""
                        DELETE FROM memory_links 
                        WHERE source_id NOT IN (SELECT id FROM auto_memories)
                        OR target_id NOT IN (SELECT id FROM auto_memories)
                    """)
                    results["fixed"] += broken_links
                
                # 检查 FTS 索引
                try:
                    conn.execute("SELECT * FROM auto_memories_fts LIMIT 1")
                except Exception:
                    results["issues"].append({
                        "type": "fts_missing",
                        "table": "auto_memories_fts"
                    })
                    # 重建 FTS
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS auto_memories_fts 
                        USING fts5(content, name, description)
                    """)
                    results["fixed"] += 1
        
        except Exception as e:
            results["status"] = "error"
            results["issues"].append({
                "type": "check_error",
                "error": str(e)
            })
        
        duration = (time.time() - start_time) * 1000
        
        # 记录健康检查
        self._log_health_check("consistency", results["status"], results, duration)
        
        return results
    
    # === 错误恢复 ===
    
    def recover_from_error(self, error_type: str) -> Dict:
        """从错误中恢复"""
        recovery_actions = {
            "corrupted_db": self._recover_corrupted_db,
            "missing_tables": self._recover_missing_tables,
            "fts_error": self._recover_fts_error,
            "index_error": self._recover_index_error
        }
        
        if error_type not in recovery_actions:
            return {"success": False, "error": f"未知错误类型: {error_type}"}
        
        start_time = time.time()
        
        try:
            result = recovery_actions[error_type]()
            duration = (time.time() - start_time) * 1000
            
            self._log_recovery(error_type, "auto_recovery", result["success"], result)
            
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _recover_corrupted_db(self) -> Dict:
        """恢复损坏的数据库"""
        try:
            with self.get_db() as conn:
                # 检查数据库完整性
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] == "ok":
                    return {"success": True, "message": "数据库完整"}
                
                # 备份并重建
                backup_path = self.db_path.with_suffix(".db.backup")
                import shutil
                shutil.copy2(self.db_path, backup_path)
                
                # 重建表
                conn.execute("DROP TABLE IF EXISTS auto_memories")
                conn.execute("""
                    CREATE TABLE auto_memories (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        name TEXT,
                        description TEXT,
                        memory_type TEXT DEFAULT 'semantic',
                        importance REAL DEFAULT 0.5,
                        confidence REAL DEFAULT 0.5,
                        decay_strength REAL DEFAULT 1.0,
                        recall_count INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        last_recalled TEXT,
                        is_active INTEGER DEFAULT 1
                    )
                """)
                
                return {"success": True, "message": "数据库已重建", "backup": str(backup_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _recover_missing_tables(self) -> Dict:
        """恢复缺失的表"""
        try:
            with self.get_db() as conn:
                # 检查并创建缺失的表
                tables_to_check = [
                    ("auto_memories", """
                        CREATE TABLE IF NOT EXISTS auto_memories (
                            id TEXT PRIMARY KEY,
                            content TEXT NOT NULL,
                            name TEXT,
                            description TEXT,
                            memory_type TEXT DEFAULT 'semantic',
                            importance REAL DEFAULT 0.5,
                            confidence REAL DEFAULT 0.5,
                            decay_strength REAL DEFAULT 1.0,
                            recall_count INTEGER DEFAULT 0,
                            created_at TEXT NOT NULL,
                            last_recalled TEXT,
                            is_active INTEGER DEFAULT 1
                        )
                    """),
                    ("memory_links", """
                        CREATE TABLE IF NOT EXISTS memory_links (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_store TEXT NOT NULL,
                            source_id TEXT NOT NULL,
                            target_store TEXT NOT NULL,
                            target_id TEXT NOT NULL,
                            link_type TEXT DEFAULT 'related_to',
                            weight REAL DEFAULT 1.0,
                            reason TEXT DEFAULT '',
                            created_at TEXT NOT NULL,
                            UNIQUE(source_store, source_id, target_store, target_id, link_type)
                        )
                    """),
                    ("auto_memories_fts", """
                        CREATE VIRTUAL TABLE IF NOT EXISTS auto_memories_fts 
                        USING fts5(content, name, description)
                    """)
                ]
                
                created = 0
                for table_name, create_sql in tables_to_check:
                    try:
                        conn.execute(f"SELECT * FROM {table_name} LIMIT 1")
                    except Exception:
                        conn.execute(create_sql)
                        created += 1
                
                return {"success": True, "created_tables": created}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _recover_fts_error(self) -> Dict:
        """恢复 FTS 错误"""
        try:
            with self.get_db() as conn:
                # 重建 FTS 表
                conn.execute("DROP TABLE IF EXISTS auto_memories_fts")
                conn.execute("""
                    CREATE VIRTUAL TABLE auto_memories_fts 
                    USING fts5(content, name, description)
                """)
                
                # 重新填充
                conn.execute("""
                    INSERT INTO auto_memories_fts(rowid, content, name, description)
                    SELECT rowid, content, name, description FROM auto_memories
                """)
                
                return {"success": True, "message": "FTS 已重建"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _recover_index_error(self) -> Dict:
        """恢复索引错误"""
        try:
            with self.get_db() as conn:
                # 重建索引
                conn.execute("DROP INDEX IF EXISTS idx_auto_memories_active")
                conn.execute("DROP INDEX IF EXISTS idx_auto_memories_type")
                conn.execute("DROP INDEX IF EXISTS idx_memory_links_source")
                conn.execute("DROP INDEX IF EXISTS idx_memory_links_target")
                
                conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_memories_active ON auto_memories(is_active)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_memories_type ON auto_memories(memory_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_store, source_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_store, target_id)")
                
                return {"success": True, "message": "索引已重建"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # === 并发安全 ===
    
    def safe_execute(self, func, *args, **kwargs):
        """线程安全执行"""
        with self._lock:
            return func(*args, **kwargs)
    
    def safe_insert(self, table: str, data: Dict) -> bool:
        """安全插入"""
        with self._lock:
            try:
                with self.get_db() as conn:
                    columns = ", ".join(data.keys())
                    placeholders = ", ".join(["?" for _ in data])
                    conn.execute(
                        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                        list(data.values())
                    )
                return True
            except Exception as e:
                print(f"插入失败: {e}")
                return False
    
    def safe_update(self, table: str, data: Dict, where: str, params: List) -> bool:
        """安全更新"""
        with self._lock:
            try:
                with self.get_db() as conn:
                    set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
                    conn.execute(
                        f"UPDATE {table} SET {set_clause} WHERE {where}",
                        list(data.values()) + params
                    )
                return True
            except Exception as e:
                print(f"更新失败: {e}")
                return False
    
    # === 性能优化 ===
    
    def optimize_database(self) -> Dict:
        """优化数据库"""
        results = {"status": "success", "actions": []}
        
        try:
            with self.get_db() as conn:
                # 分析表
                conn.execute("ANALYZE")
                results["actions"].append("ANALYZE")
                
                # 清理空间
                conn.execute("VACUUM")
                results["actions"].append("VACUUM")
                
                # 重建索引
                conn.execute("REINDEX")
                results["actions"].append("REINDEX")
                
                # 更新统计
                conn.execute("PRAGMA optimize")
                results["actions"].append("PRAGMA optimize")
            
            return results
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def get_performance_stats(self) -> Dict:
        """获取性能统计"""
        try:
            with self.get_db() as conn:
                # 表大小
                tables = {}
                for table in ["auto_memories", "memory_links", "session_captures"]:
                    try:
                        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        tables[table] = count
                    except Exception:
                        tables[table] = 0
                
                # 索引使用情况
                indexes = conn.execute("""
                    SELECT name, tbl_name FROM sqlite_master 
                    WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
                """).fetchall()
                
                return {
                    "tables": tables,
                    "indexes": len(indexes),
                    "db_size": os.path.getsize(self.db_path) if self.db_path.exists() else 0
                }
        except Exception as e:
            return {"error": str(e)}
    
    # === 监控告警 ===
    
    def check_health(self) -> Dict:
        """健康检查"""
        results = {
            "status": "healthy",
            "checks": [],
            "warnings": [],
            "errors": []
        }
        
        # 检查数据库存在
        if not self.db_path.exists():
            results["errors"].append("数据库不存在")
            results["status"] = "error"
            return results
        
        # 检查表存在
        try:
            with self.get_db() as conn:
                tables = conn.execute("""
                    SELECT name FROM sqlite_master WHERE type = 'table'
                """).fetchall()
                table_names = [t[0] for t in tables]
                
                required_tables = ["auto_memories", "memory_links"]
                for table in required_tables:
                    if table not in table_names:
                        results["errors"].append(f"缺失表: {table}")
                        results["status"] = "error"
        except Exception as e:
            results["errors"].append(f"数据库连接失败: {e}")
            results["status"] = "error"
        
        # 检查数据量
        try:
            with self.get_db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM auto_memories WHERE is_active = 1").fetchone()[0]
                if count > 10000:
                    results["warnings"].append(f"活跃记忆过多: {count}")
                results["checks"].append(f"活跃记忆: {count}")
        except Exception:
            pass
        
        # 检查数据库大小
        db_size = os.path.getsize(self.db_path) / (1024 * 1024)  # MB
        if db_size > 100:
            results["warnings"].append(f"数据库过大: {db_size:.1f}MB")
        results["checks"].append(f"数据库大小: {db_size:.1f}MB")
        
        return results
    
    def _log_health_check(self, check_type: str, status: str, details: Dict, duration: float):
        """记录健康检查"""
        try:
            with sqlite3.connect(self.health_db) as conn:
                conn.execute(
                    """INSERT INTO health_checks 
                       (check_time, check_type, status, details, duration_ms)
                       VALUES (?, ?, ?, ?, ?)""",
                    (datetime.now().isoformat(), check_type, status,
                     json.dumps(details), duration)
                )
        except Exception:
            pass
    
    def _log_recovery(self, issue_type: str, action: str, success: bool, details: Dict):
        """记录恢复操作"""
        try:
            with sqlite3.connect(self.health_db) as conn:
                conn.execute(
                    """INSERT INTO recovery_log 
                       (recovery_time, issue_type, action, success, details)
                       VALUES (?, ?, ?, ?, ?)""",
                    (datetime.now().isoformat(), issue_type, action,
                     1 if success else 0, json.dumps(details))
                )
        except Exception:
            pass


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("用法: liyu-memory-robust.py <command>")
        print("命令:")
        print("  check     - 一致性检查")
        print("  recover   - 错误恢复")
        print("  optimize  - 性能优化")
        print("  health    - 健康检查")
        print("  stats     - 性能统计")
        return
    
    robust = MemoryRobustness()
    command = sys.argv[1]
    
    if command == "check":
        result = robust.check_consistency()
        print(f"一致性检查: {result['status']}")
        if result["issues"]:
            print(f"  发现 {len(result['issues'])} 个问题")
            for issue in result["issues"]:
                print(f"    - {issue['type']}: {issue.get('count', 'N/A')}")
        if result["fixed"] > 0:
            print(f"  已修复: {result['fixed']} 个")
    
    elif command == "recover":
        if len(sys.argv) < 3:
            print("用法: liyu-memory-robust.py recover <error_type>")
            print("错误类型: corrupted_db, missing_tables, fts_error, index_error")
            return
        error_type = sys.argv[2]
        result = robust.recover_from_error(error_type)
        if result["success"]:
            print(f"✓ 恢复成功: {result.get('message', '')}")
        else:
            print(f"✗ 恢复失败: {result.get('error', '')}")
    
    elif command == "optimize":
        result = robust.optimize_database()
        print(f"优化状态: {result['status']}")
        if result.get("actions"):
            print(f"  执行操作: {', '.join(result['actions'])}")
    
    elif command == "health":
        result = robust.check_health()
        print(f"健康状态: {result['status']}")
        if result["checks"]:
            for check in result["checks"]:
                print(f"  ✓ {check}")
        if result["warnings"]:
            for warning in result["warnings"]:
                print(f"  ⚠ {warning}")
        if result["errors"]:
            for error in result["errors"]:
                print(f"  ✗ {error}")
    
    elif command == "stats":
        result = robust.get_performance_stats()
        if "error" in result:
            print(f"✗ 错误: {result['error']}")
        else:
            print("性能统计:")
            print(f"  数据库大小: {result['db_size'] / 1024 / 1024:.1f}MB")
            print(f"  索引数量: {result['indexes']}")
            print("  表统计:")
            for table, count in result["tables"].items():
                print(f"    {table}: {count} 条")
    
    else:
        print(f"未知命令: {command}")


if __name__ == "__main__":
    main()
