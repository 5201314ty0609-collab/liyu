#!/usr/bin/env python3
"""
鲤鱼 Hooks Robustness Enhancer
统一错误处理、依赖管理、执行状态追踪
"""

import os
import sys
import json
import time
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Callable
from contextlib import contextmanager

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
HOOKS_DIR = 鲤鱼_HOME / "hooks"
HEALTH_DB = 鲤鱼_HOME / "hooks-health.db"


class HookBase:
    """Hook 基类 - 统一错误处理和状态追踪"""
    
    def __init__(self, name: str, dependencies: List[str] = None):
        self.name = name
        self.dependencies = dependencies or []
        self.execution_log = []
    
    def execute(self, context: Dict) -> Dict:
        """统一执行入口"""
        start_time = time.time()
        
        try:
            # 验证依赖
            self._validate_dependencies(context)
            
            # 执行具体逻辑
            result = self._run(context)
            
            # 记录成功
            duration = (time.time() - start_time) * 1000
            self._log_success(result, duration)
            
            return {"success": True, "result": result, "duration_ms": duration}
        except Exception as e:
            # 记录失败
            duration = (time.time() - start_time) * 1000
            self._log_failure(e, duration)
            
            return {"success": False, "error": str(e), "duration_ms": duration}
    
    def _validate_dependencies(self, context: Dict):
        """验证依赖"""
        for dep in self.dependencies:
            if dep not in context:
                raise ValueError(f"缺少依赖: {dep}")
    
    def _run(self, context: Dict) -> Dict:
        """子类实现的具体逻辑"""
        raise NotImplementedError
    
    def _log_success(self, result: Dict, duration: float):
        """记录成功"""
        self.execution_log.append({
            "time": datetime.now().isoformat(),
            "status": "success",
            "duration_ms": duration
        })
    
    def _log_failure(self, error: Exception, duration: float):
        """记录失败"""
        self.execution_log.append({
            "time": datetime.now().isoformat(),
            "status": "failed",
            "error": str(error),
            "duration_ms": duration
        })


class HooksManager:
    """Hooks 管理器 - 统一管理所有 Hooks"""
    
    def __init__(self):
        self.hooks: Dict[str, HookBase] = {}
        self.health_db = HEALTH_DB
        self._init_db()
    
    def _init_db(self):
        """初始化健康检查数据库"""
        conn = sqlite3.connect(self.health_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hook_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_name TEXT NOT NULL,
                execution_time TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL,
                error TEXT,
                context_keys TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hook_dependencies (
                hook_name TEXT NOT NULL,
                dependency TEXT NOT NULL,
                PRIMARY KEY (hook_name, dependency)
            )
        """)
        conn.commit()
        conn.close()
    
    def register_hook(self, hook: HookBase):
        """注册 Hook"""
        self.hooks[hook.name] = hook
        
        # 保存依赖关系
        conn = sqlite3.connect(self.health_db)
        for dep in hook.dependencies:
            conn.execute(
                "INSERT OR IGNORE INTO hook_dependencies (hook_name, dependency) VALUES (?, ?)",
                (hook.name, dep)
            )
        conn.commit()
        conn.close()
    
    def execute_hook(self, hook_name: str, context: Dict) -> Dict:
        """执行 Hook"""
        if hook_name not in self.hooks:
            return {"success": False, "error": f"Hook 不存在: {hook_name}"}
        
        hook = self.hooks[hook_name]
        start_time = time.time()
        
        try:
            result = hook.execute(context)
            duration = (time.time() - start_time) * 1000
            
            # 记录执行
            self._log_execution(hook_name, result["success"], duration, 
                              result.get("error"), list(context.keys()))
            
            return result
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self._log_execution(hook_name, False, duration, str(e), list(context.keys()))
            
            return {"success": False, "error": str(e), "duration_ms": duration}
    
    def execute_chain(self, hook_names: List[str], context: Dict) -> List[Dict]:
        """执行 Hook 链"""
        results = []
        
        for hook_name in hook_names:
            result = self.execute_hook(hook_name, context)
            results.append({
                "hook": hook_name,
                **result
            })
            
            # 如果失败，停止执行
            if not result["success"]:
                break
        
        return results
    
    def _log_execution(self, hook_name: str, success: bool, duration: float,
                      error: str = None, context_keys: List[str] = None):
        """记录执行"""
        conn = sqlite3.connect(self.health_db)
        conn.execute(
            """INSERT INTO hook_executions 
               (hook_name, execution_time, status, duration_ms, error, context_keys)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (hook_name, datetime.now().isoformat(),
             "success" if success else "failed",
             duration, error, json.dumps(context_keys or []))
        )
        conn.commit()
        conn.close()
    
    def get_hook_stats(self, hook_name: str = None) -> Dict:
        """获取 Hook 统计"""
        conn = sqlite3.connect(self.health_db)
        
        if hook_name:
            cursor = conn.execute(
                """SELECT status, COUNT(*), AVG(duration_ms)
                   FROM hook_executions 
                   WHERE hook_name = ?
                   GROUP BY status""",
                (hook_name,)
            )
        else:
            cursor = conn.execute(
                """SELECT hook_name, status, COUNT(*), AVG(duration_ms)
                   FROM hook_executions 
                   GROUP BY hook_name, status"""
            )
        
        stats = {}
        for row in cursor.fetchall():
            if hook_name:
                stats[row[0]] = {
                    "count": row[1],
                    "avg_duration_ms": row[2]
                }
            else:
                if row[0] not in stats:
                    stats[row[0]] = {}
                stats[row[0]][row[1]] = {
                    "count": row[2],
                    "avg_duration_ms": row[3]
                }
        
        conn.close()
        return stats
    
    def check_dependencies(self) -> Dict:
        """检查依赖关系"""
        issues = []
        
        for hook_name, hook in self.hooks.items():
            for dep in hook.dependencies:
                # 检查依赖是否存在
                if dep not in self.hooks:
                    # 检查是否是上下文依赖
                    if not dep.startswith("context:"):
                        issues.append({
                            "hook": hook_name,
                            "dependency": dep,
                            "type": "missing"
                        })
        
        return {
            "status": "healthy" if len(issues) == 0 else "warning",
            "issues": issues
        }
    
    def get_health_report(self) -> Dict:
        """获取健康报告"""
        report = {
            "status": "healthy",
            "hooks_count": len(self.hooks),
            "issues": [],
            "warnings": []
        }
        
        # 检查依赖
        dep_check = self.check_dependencies()
        if dep_check["status"] != "healthy":
            report["issues"].extend(dep_check["issues"])
        
        # 检查执行历史
        stats = self.get_hook_stats()
        for hook_name, hook_stats in stats.items():
            if "failed" in hook_stats:
                fail_count = hook_stats["failed"]["count"]
                if fail_count > 5:
                    report["warnings"].append({
                        "hook": hook_name,
                        "issue": f"失败次数过多: {fail_count}"
                    })
        
        if report["issues"]:
            report["status"] = "error"
        elif report["warnings"]:
            report["status"] = "warning"
        
        return report


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("用法: liyu-hooks-robust.py <command>")
        print("命令:")
        print("  stats <hook_name>  - 查看 Hook 统计")
        print("  deps               - 检查依赖关系")
        print("  health             - 健康报告")
        return
    
    manager = HooksManager()
    command = sys.argv[1]
    
    if command == "stats":
        hook_name = sys.argv[2] if len(sys.argv) > 2 else None
        stats = manager.get_hook_stats(hook_name)
        print(json.dumps(stats, indent=2))
    
    elif command == "deps":
        result = manager.check_dependencies()
        print(f"依赖检查: {result['status']}")
        if result["issues"]:
            for issue in result["issues"]:
                print(f"  ✗ {issue['hook']}: {issue['dependency']}")
    
    elif command == "health":
        report = manager.get_health_report()
        print(f"健康状态: {report['status']}")
        print(f"Hook 数量: {report['hooks_count']}")
        if report["issues"]:
            print("问题:")
            for issue in report["issues"]:
                print(f"  ✗ {issue}")
        if report["warnings"]:
            print("警告:")
            for warning in report["warnings"]:
                print(f"  ⚠ {warning['hook']}: {warning['issue']}")
    
    else:
        print(f"未知命令: {command}")


if __name__ == "__main__":
    main()
