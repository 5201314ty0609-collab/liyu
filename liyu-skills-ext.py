#!/usr/bin/env python3
"""
鲤鱼 Skills Extensibility Enhancer
自动技能发现、版本管理、依赖管理
"""

import os
import sys
import json
import sqlite3
import hashlib
import importlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

鲤鱼_HOME = Path.home() / ".claude" / "liyu"
SKILLS_DIR = 鲤鱼_HOME / "skills"
DB_PATH = 鲤鱼_HOME / "skills-registry.db"


class SkillMetadata:
    """技能元数据"""
    
    def __init__(self, skill_id: str, name: str, version: str = "1.0.0",
                 description: str = "", dependencies: List[str] = None,
                 tags: List[str] = None, author: str = "鲤鱼"):
        self.skill_id = skill_id
        self.name = name
        self.version = version
        self.description = description
        self.dependencies = dependencies or []
        self.tags = tags or []
        self.author = author
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
    
    def to_dict(self) -> Dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'SkillMetadata':
        meta = cls(
            skill_id=data["skill_id"],
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
            author=data.get("author", "鲤鱼")
        )
        meta.created_at = data.get("created_at", meta.created_at)
        meta.updated_at = data.get("updated_at", meta.updated_at)
        return meta


class SkillsRegistry:
    """技能注册表"""
    
    def __init__(self):
        self.db_path = DB_PATH
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT,
                dependencies TEXT,
                tags TEXT,
                author TEXT,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                use_count INTEGER DEFAULT 0,
                last_used TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_id TEXT NOT NULL,
                version TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                changes TEXT,
                PRIMARY KEY (skill_id, version)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_dependencies (
                skill_id TEXT NOT NULL,
                dependency TEXT NOT NULL,
                dependency_type TEXT DEFAULT 'skill',
                PRIMARY KEY (skill_id, dependency)
            )
        """)
        conn.commit()
        conn.close()
    
    def register_skill(self, metadata: SkillMetadata, file_path: str) -> bool:
        """注册技能"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 检查是否已存在
            existing = conn.execute(
                "SELECT skill_id FROM skills WHERE skill_id = ?",
                (metadata.skill_id,)
            ).fetchone()
            
            if existing:
                # 更新
                conn.execute("""
                    UPDATE skills 
                    SET name = ?, version = ?, description = ?,
                        dependencies = ?, tags = ?, author = ?,
                        file_path = ?, updated_at = ?
                    WHERE skill_id = ?
                """, (
                    metadata.name, metadata.version, metadata.description,
                    json.dumps(metadata.dependencies), json.dumps(metadata.tags),
                    metadata.author, file_path, metadata.updated_at,
                    metadata.skill_id
                ))
            else:
                # 插入
                conn.execute("""
                    INSERT INTO skills 
                    (skill_id, name, version, description, dependencies, tags,
                     author, file_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    metadata.skill_id, metadata.name, metadata.version,
                    metadata.description, json.dumps(metadata.dependencies),
                    json.dumps(metadata.tags), metadata.author, file_path,
                    metadata.created_at, metadata.updated_at
                ))
            
            # 记录版本
            conn.execute("""
                INSERT OR REPLACE INTO skill_versions 
                (skill_id, version, file_path, created_at)
                VALUES (?, ?, ?, ?)
            """, (metadata.skill_id, metadata.version, file_path, metadata.created_at))
            
            # 记录依赖
            for dep in metadata.dependencies:
                conn.execute("""
                    INSERT OR IGNORE INTO skill_dependencies 
                    (skill_id, dependency)
                    VALUES (?, ?)
                """, (metadata.skill_id, dep))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"注册失败: {e}")
            return False
    
    def get_skill(self, skill_id: str) -> Optional[Dict]:
        """获取技能"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        row = conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?",
            (skill_id,)
        ).fetchone()
        
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    def list_skills(self, status: str = "active") -> List[Dict]:
        """列出技能"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        if status:
            cursor = conn.execute(
                "SELECT * FROM skills WHERE status = ? ORDER BY name",
                (status,)
            )
        else:
            cursor = conn.execute("SELECT * FROM skills ORDER BY name")
        
        skills = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return skills
    
    def update_usage(self, skill_id: str):
        """更新使用统计"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE skills 
            SET use_count = use_count + 1, last_used = ?
            WHERE skill_id = ?
        """, (datetime.now().isoformat(), skill_id))
        conn.commit()
        conn.close()
    
    def get_dependencies(self, skill_id: str) -> List[str]:
        """获取技能依赖"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT dependency FROM skill_dependencies WHERE skill_id = ?",
            (skill_id,)
        )
        deps = [row[0] for row in cursor.fetchall()]
        conn.close()
        return deps
    
    def check_dependencies(self, skill_id: str) -> Dict:
        """检查依赖是否满足"""
        deps = self.get_dependencies(skill_id)
        missing = []
        
        for dep in deps:
            skill = self.get_skill(dep)
            if not skill:
                missing.append(dep)
        
        return {
            "satisfied": len(missing) == 0,
            "missing": missing
        }


class SkillDiscovery:
    """技能自动发现"""
    
    def __init__(self, skills_dir: Path = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self.registry = SkillsRegistry()
    
    def discover(self) -> List[SkillMetadata]:
        """扫描目录，自动发现技能"""
        discovered = []
        
        for skill_file in self.skills_dir.glob("*.py"):
            if skill_file.name.startswith("_"):
                continue
            
            # 解析元数据
            metadata = self._parse_metadata(skill_file)
            
            # 注册到注册表
            if self.registry.register_skill(metadata, str(skill_file)):
                discovered.append(metadata)
        
        return discovered
    
    def _parse_metadata(self, skill_file: Path) -> SkillMetadata:
        """解析技能元数据"""
        skill_id = skill_file.stem
        
        # 默认元数据
        metadata = SkillMetadata(
            skill_id=skill_id,
            name=skill_id.replace("-", " ").title(),
            version="1.0.0",
            description="",
            dependencies=[],
            tags=[]
        )
        
        try:
            content = skill_file.read_text()
            
            # 从 docstring 提取描述
            if '"""' in content:
                start = content.index('"""') + 3
                end = content.index('"""', start)
                docstring = content[start:end].strip()
                if docstring:
                    lines = docstring.split("\n")
                    metadata.description = lines[0].strip()
                    if len(lines) > 1:
                        metadata.name = lines[0].strip()
            
            # 从文件头提取版本
            if "# Version:" in content:
                version_line = [l for l in content.split("\n") if "# Version:" in l]
                if version_line:
                    metadata.version = version_line[0].split("# Version:")[1].strip()
            
            # 从文件头提取标签
            if "# Tags:" in content:
                tags_line = [l for l in content.split("\n") if "# Tags:" in l]
                if tags_line:
                    tags = tags_line[0].split("# Tags:")[1].strip()
                    metadata.tags = [t.strip() for t in tags.split(",")]
        
        except Exception:
            pass
        
        return metadata
    
    def get_discovery_report(self) -> Dict:
        """获取发现报告"""
        discovered = self.discover()
        
        return {
            "discovered": len(discovered),
            "skills": [m.to_dict() for m in discovered]
        }


class SkillVersionManager:
    """技能版本管理"""
    
    def __init__(self):
        self.registry = SkillsRegistry()
    
    def get_versions(self, skill_id: str) -> List[Dict]:
        """获取技能版本历史"""
        conn = sqlite3.connect(self.registry.db_path)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute(
            "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY created_at DESC",
            (skill_id,)
        )
        
        versions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return versions
    
    def get_latest_version(self, skill_id: str) -> Optional[str]:
        """获取最新版本"""
        conn = sqlite3.connect(self.registry.db_path)
        
        cursor = conn.execute(
            "SELECT version FROM skill_versions WHERE skill_id = ? ORDER BY created_at DESC LIMIT 1",
            (skill_id,)
        )
        
        row = cursor.fetchone()
        conn.close()
        
        return row[0] if row else None


def main():
    """CLI 入口"""
    if len(sys.argv) < 2:
        print("用法: liyu-skills-ext.py <command>")
        print("命令:")
        print("  discover        - 发现新技能")
        print("  list            - 列出所有技能")
        print("  get <skill_id>  - 获取技能详情")
        print("  deps <skill_id> - 检查依赖")
        print("  versions <skill_id> - 查看版本历史")
        return
    
    discovery = SkillDiscovery()
    registry = SkillsRegistry()
    version_mgr = SkillVersionManager()
    
    command = sys.argv[1]
    
    if command == "discover":
        report = discovery.get_discovery_report()
        print(f"发现 {report['discovered']} 个技能:")
        for skill in report["skills"]:
            print(f"  - {skill['name']} ({skill['skill_id']}) v{skill['version']}")
    
    elif command == "list":
        skills = registry.list_skills()
        print(f"技能列表 ({len(skills)} 个):")
        for skill in skills:
            print(f"  - {skill['name']} ({skill['skill_id']}) v{skill['version']} [{skill['status']}]")
    
    elif command == "get":
        if len(sys.argv) < 3:
            print("用法: liyu-skills-ext.py get <skill_id>")
            return
        skill_id = sys.argv[2]
        skill = registry.get_skill(skill_id)
        if skill:
            print(json.dumps(skill, indent=2))
        else:
            print(f"技能 {skill_id} 不存在")
    
    elif command == "deps":
        if len(sys.argv) < 3:
            print("用法: liyu-skills-ext.py deps <skill_id>")
            return
        skill_id = sys.argv[2]
        result = registry.check_dependencies(skill_id)
        if result["satisfied"]:
            print(f"✓ 依赖满足")
        else:
            print(f"✗ 缺少依赖: {', '.join(result['missing'])}")
    
    elif command == "versions":
        if len(sys.argv) < 3:
            print("用法: liyu-skills-ext.py versions <skill_id>")
            return
        skill_id = sys.argv[2]
        versions = version_mgr.get_versions(skill_id)
        if versions:
            print(f"版本历史:")
            for v in versions:
                print(f"  v{v['version']} - {v['created_at']}")
        else:
            print(f"无版本历史")
    
    else:
        print(f"未知命令: {command}")


if __name__ == "__main__":
    main()
