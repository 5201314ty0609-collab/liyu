#!/usr/bin/env python3
"""
鲤鱼 Skill Registry — 技能依赖与冲突管理系统
Absorbed from MUNDO v2.0.9 skills.py (SkillRegistry with dependency resolution)

核心能力：
  1. 增量元数据 — skill.json 可选，缺失时默认为无依赖
  2. 依赖链解析 — DFS 拓扑排序，确保加载顺序
  3. 冲突检测 — A 和 B 互斥时警告
  4. 循环依赖检测 — 防止死锁
  5. 完整性验证 — 缺失依赖、孤立技能、权限缺口

Usage:
  skill-registry.py list [--origin X] [--tag X]    列出所有技能
  skill-registry.py deps <name>                      显示依赖树
  skill-registry.py chain <name>                     加载顺序（拓扑排序）
  skill-registry.py validate                         全量验证
  skill-registry.py conflicts                        显示所有冲突对
  skill-registry.py orphans                          无关系的孤立技能
  skill-registry.py stats                            统计概览
  skill-registry.py init <name>                      为技能生成 skill.json 模板
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
SKILLS_DIR = Path.home() / ".claude" / "skills"

# ── Data Model ─────────────────────────────────────────────────────────────

@dataclass
class SkillMeta:
    """技能的完整元数据——来自 SKILL.md frontmatter + skill.json"""
    name: str
    description: str = ""
    version: str = "1.0.0"
    origin: str = ""
    dependencies: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: int = 100
    enabled: bool = True
    path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "origin": self.origin,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
            "required_tools": self.required_tools,
            "required_permissions": self.required_permissions,
            "tags": self.tags,
            "priority": self.priority,
            "enabled": self.enabled,
        }


# ── Registry ───────────────────────────────────────────────────────────────

class SkillRegistry:
    """技能注册表——发现、解析、验证"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills: dict[str, SkillMeta] = {}
        self._skills_dir = skills_dir or SKILLS_DIR
        self._errors: list[str] = []
        self._warnings: list[str] = []

    # ── Discover ────────────────────────────────────────────────────────

    def discover(self) -> int:
        """扫描 skills 目录，加载所有技能元数据"""
        self._skills.clear()
        self._errors.clear()
        self._warnings.clear()

        if not self._skills_dir.exists():
            self._errors.append(f"Skills 目录不存在: {self._skills_dir}")
            return 0

        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            meta = self._load_skill(skill_dir, skill_md)
            if meta:
                self._skills[meta.name] = meta

        return len(self._skills)

    def _load_skill(self, skill_dir: Path, skill_md: Path) -> Optional[SkillMeta]:
        """加载单个技能的元数据：SKILL.md frontmatter + 可选 skill.json"""
        # 1. Parse SKILL.md frontmatter
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            self._errors.append(f"{skill_dir.name}: 无法读取 SKILL.md")
            return None

        meta = self._parse_frontmatter(content)
        # Fallback: use directory name when frontmatter is missing a name
        if not meta.name or meta.name == "unknown":
            meta.name = skill_dir.name
            self._warnings.append(f"{skill_dir.name}: SKILL.md 缺少 name 字段，使用目录名")

        # 2. Overlay skill.json if exists
        skill_json = skill_dir / "skill.json"
        if skill_json.exists():
            try:
                overlay = json.loads(skill_json.read_text(encoding="utf-8"))
                self._apply_overlay(meta, overlay)
            except (json.JSONDecodeError, OSError) as e:
                self._warnings.append(f"{skill_dir.name}: skill.json 解析失败 — {e}")

        meta.path = str(skill_dir)
        return meta

    def _parse_frontmatter(self, content: str) -> SkillMeta:
        """从 SKILL.md 的 YAML frontmatter 提取基础元数据"""
        meta = SkillMeta(name="unknown", description="", origin="")

        if not content.startswith("---"):
            return meta

        try:
            end = content.index("---", 3)
            fm = content[3:end].strip()
        except ValueError:
            return meta

        for line in fm.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip().strip('"').strip("'")

            if key == "name":
                meta.name = val
            elif key == "description":
                meta.description = val
            elif key == "origin":
                meta.origin = val
            elif key == "version":
                meta.version = val
            elif key == "priority":
                try:
                    meta.priority = int(val)
                except ValueError:
                    pass
            elif key == "tags":
                meta.tags = [t.strip() for t in val.split(",") if t.strip()]

        return meta

    def _apply_overlay(self, meta: SkillMeta, overlay: dict) -> None:
        """将 skill.json 的字段合并到 meta（覆盖 frontmatter 中的同名字段）"""
        for field_name in ["dependencies", "conflicts", "required_tools",
                           "required_permissions", "tags"]:
            if field_name in overlay:
                setattr(meta, field_name, overlay[field_name])
        for field_name in ["version", "description", "origin", "priority"]:
            if field_name in overlay:
                setattr(meta, field_name, overlay[field_name])
        if "enabled" in overlay:
            meta.enabled = overlay["enabled"]

    # ── Query ───────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[SkillMeta]:
        """获取单个技能"""
        return self._skills.get(name)

    def list_all(self, origin: str = "", tag: str = "",
                 enabled_only: bool = False) -> list[SkillMeta]:
        """列出技能，支持过滤"""
        skills = list(self._skills.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        if origin:
            skills = [s for s in skills if s.origin == origin]
        if tag:
            skills = [s for s in skills if tag in s.tags]
        return sorted(skills, key=lambda s: s.priority)

    def names(self) -> list[str]:
        """所有技能名称"""
        return sorted(self._skills.keys())

    # ── Dependency Resolution ───────────────────────────────────────────

    def dependency_tree(self, name: str, depth: int = 0,
                        visited: Optional[set] = None) -> list[str]:
        """递归展示依赖树"""
        if visited is None:
            visited = set()

        skill = self._skills.get(name)
        if not skill:
            return [f"{'  ' * depth}❌ {name} (不存在)"]

        if name in visited:
            return [f"{'  ' * depth}🔄 {name} (循环依赖!)"]

        visited.add(name)
        prefix = "📦" if depth == 0 else "├─"
        deps_str = f" v{skill.version}" if skill.version else ""
        lines = [f"{'  ' * depth}{prefix} {name}{deps_str}"]

        for dep in skill.dependencies:
            lines.extend(self.dependency_tree(dep, depth + 1, visited.copy()))

        return lines

    def load_order(self, name: str) -> list[str]:
        """拓扑排序——返回加载顺序（依赖在前）"""
        order: list[str] = []
        visiting: set[str] = set()
        permanent: set[str] = set()

        def _visit(n: str) -> bool:
            if n in permanent:
                return True
            if n in visiting:
                self._errors.append(f"循环依赖: {' → '.join(visiting)} → {n}")
                return False
            if n not in self._skills:
                self._errors.append(f"缺失依赖: {n}")
                return False

            visiting.add(n)
            skill = self._skills[n]
            for dep in skill.dependencies:
                if not _visit(dep):
                    return False
            visiting.discard(n)
            permanent.add(n)
            order.append(n)
            return True

        if _visit(name):
            return order
        return []

    # ── Validation ──────────────────────────────────────────────────────

    def validate(self) -> dict:
        """全量验证：依赖完整性 + 冲突 + 循环"""
        results = {
            "total": len(self._skills),
            "errors": [],
            "warnings": [],
            "cycles": [],
            "conflicts_found": [],
            "orphans": [],
            "missing_deps": [],
        }

        # Check each skill
        seen_pairs: set[tuple] = set()
        for name, skill in self._skills.items():
            if not skill.enabled:
                continue

            # Missing dependencies
            for dep in skill.dependencies:
                if dep not in self._skills:
                    results["missing_deps"].append(f"{name} → {dep} (不存在)")
                elif not self._skills[dep].enabled:
                    results["warnings"].append(f"{name} → {dep} (已禁用)")

            # Conflicts
            for conflict in skill.conflicts:
                pair = tuple(sorted([name, conflict]))
                if pair not in seen_pairs and conflict in self._skills and self._skills[conflict].enabled:
                    seen_pairs.add(pair)
                    results["conflicts_found"].append(f"{name} ⚔️ {conflict}")

        # Circular dependency detection (single DFS pass)
        cycles_set: set[str] = set()
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._skills}
        stack: list[str] = []

        def _dfs(n: str):
            color[n] = GRAY
            stack.append(n)
            skill = self._skills.get(n)
            if skill:
                for dep in skill.dependencies:
                    c = color.get(dep)
                    if c == GRAY:
                        # Found cycle — extract the sub-path
                        start = stack.index(dep)
                        cycle = " → ".join(stack[start:] + [dep])
                        # Normalize: rotate to start with alphabetically smallest
                        nodes = stack[start:] + [dep]
                        pivot = min(range(len(nodes) - 1), key=lambda i: nodes[i])
                        normalized = " → ".join(nodes[pivot:] + nodes[1:pivot + 1])
                        cycles_set.add(normalized)
                    elif c == WHITE:
                        _dfs(dep)
            stack.pop()
            color[n] = BLACK

        for name in self._skills:
            if color[name] == WHITE:
                _dfs(name)

        results["cycles"] = sorted(cycles_set)

        # Orphans: no deps, nobody depends on them
        all_deps = set()
        for s in self._skills.values():
            all_deps.update(s.dependencies)
        for name in self._skills:
            if name not in all_deps and not self._skills[name].dependencies:
                results["orphans"].append(name)

        # Merge instance-level errors
        results["errors"].extend(self._errors)

        return results

    def conflicts_report(self) -> list[dict]:
        """列出所有已启用的冲突对"""
        conflicts = []
        seen = set()
        for name, skill in self._skills.items():
            if not skill.enabled:
                continue
            for conflict in skill.conflicts:
                pair = tuple(sorted([name, conflict]))
                if pair in seen:
                    continue
                seen.add(pair)
                if conflict in self._skills and self._skills[conflict].enabled:
                    conflicts.append({
                        "skill_a": name,
                        "skill_b": conflict,
                        "a_desc": skill.description[:80],
                        "b_desc": self._skills[conflict].description[:80],
                    })
        return conflicts

    # ── Stats ───────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """统计概览"""
        total = len(self._skills)
        enabled = sum(1 for s in self._skills.values() if s.enabled)
        with_deps = sum(1 for s in self._skills.values() if s.dependencies)
        with_conflicts = sum(1 for s in self._skills.values() if s.conflicts)
        origins: dict[str, int] = {}
        for s in self._skills.values():
            o = s.origin or "unknown"
            origins[o] = origins.get(o, 0) + 1
        max_deps = max((len(s.dependencies) for s in self._skills.values()), default=0)

        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "with_dependencies": with_deps,
            "with_conflicts": with_conflicts,
            "max_dependency_depth": max_deps,
            "by_origin": origins,
        }

    # ── Init Template ───────────────────────────────────────────────────

    def generate_template(self, name: str) -> Optional[str]:
        """为指定技能生成 skill.json 模板"""
        skill = self._skills.get(name)
        if not skill:
            return None

        template = {
            "_comment": f"鲤鱼 Skill 元数据 — {name}",
            "version": skill.version or "1.0.0",
            "dependencies": [],
            "conflicts": [],
            "required_tools": [],
            "required_permissions": [],
            "tags": skill.tags,
            "priority": skill.priority,
            "enabled": True,
        }
        return json.dumps(template, ensure_ascii=False, indent=2)


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    registry = SkillRegistry()

    if cmd == "init":
        # Generate skill.json template
        if len(sys.argv) < 3:
            print("Usage: skill-registry.py init <skill_name>")
            return

        # Discover first to populate registry
        registry.discover()
        template = registry.generate_template(sys.argv[2])
        if template:
            print(template)
        else:
            print(f"❌ 技能不存在: {sys.argv[2]}")
            sys.exit(1)

    else:
        # All other commands need discover first
        count = registry.discover()
        if count == 0:
            print("⚠️ 未发现任何技能")
            return

        if cmd == "list":
            origin = ""
            tag = ""
            for i, arg in enumerate(sys.argv):
                if arg == "--origin" and i + 1 < len(sys.argv):
                    origin = sys.argv[i + 1]
                if arg == "--tag" and i + 1 < len(sys.argv):
                    tag = sys.argv[i + 1]

            skills = registry.list_all(origin=origin, tag=tag)
            for s in skills:
                icon = "🟢" if s.enabled else "🔴"
                deps = f" → [{', '.join(s.dependencies)}]" if s.dependencies else ""
                conflicts = f" ⚔️ [{', '.join(s.conflicts)}]" if s.conflicts else ""
                print(f"  {icon} {s.name}{deps}{conflicts}")
                if s.description:
                    print(f"     {s.description[:100]}")
            print(f"\n  {len(skills)} skills")

        elif cmd == "deps":
            if len(sys.argv) < 3:
                print("Usage: skill-registry.py deps <name>")
                return
            name = sys.argv[2]
            tree = registry.dependency_tree(name)
            for line in tree:
                print(line)

        elif cmd == "chain":
            if len(sys.argv) < 3:
                print("Usage: skill-registry.py chain <name>")
                return
            name = sys.argv[2]
            order = registry.load_order(name)
            if order:
                print(f"加载顺序 ({len(order)} 步):")
                for i, s in enumerate(order):
                    skill = registry.get(s)
                    ver = f" v{skill.version}" if skill and skill.version else ""
                    print(f"  {i + 1}. {s}{ver}")
            else:
                for err in registry._errors:
                    print(f"  ❌ {err}")
                if not registry._errors:
                    print(f"❌ 无法解析 {name} 的依赖链")
                sys.exit(1)

        elif cmd == "validate":
            result = registry.validate()
            print(f"═══ 鲤鱼 Skill 验证 ───")
            print(f"  总技能: {result['total']}")
            print()

            if result["errors"]:
                print(f"  ❌ 错误 ({len(result['errors'])}):")
                for e in result["errors"]:
                    print(f"     • {e}")
                print()

            if result["warnings"]:
                print(f"  ⚠️ 警告 ({len(result['warnings'])}):")
                for w in result["warnings"]:
                    print(f"     • {w}")
                print()

            if result["cycles"]:
                print(f"  🔄 循环依赖 ({len(result['cycles'])}):")
                for c in result["cycles"]:
                    print(f"     • {c}")
                print()

            if result["conflicts_found"]:
                print(f"  ⚔️ 冲突 ({len(result['conflicts_found'])}):")
                for c in result["conflicts_found"]:
                    print(f"     • {c}")
                print()

            if result["orphans"]:
                print(f"  🏝️ 孤立技能 ({len(result['orphans'])}):")
                for o in result["orphans"]:
                    print(f"     • {o}")
                print()

            if result["missing_deps"]:
                print(f"  🔗 缺失依赖 ({len(result['missing_deps'])}):")
                for m in result["missing_deps"]:
                    print(f"     • {m}")
                print()

            has_issues = any([
                result["errors"], result["warnings"], result["cycles"],
                result["conflicts_found"], result["missing_deps"],
            ])
            if not has_issues:
                print("  ✅ 全部通过")
            sys.exit(1 if result["errors"] else 0)

        elif cmd == "conflicts":
            conflicts = registry.conflicts_report()
            if conflicts:
                print(f"⚔️ {len(conflicts)} 个冲突对:")
                for c in conflicts:
                    print(f"  {c['skill_a']} ⚔️ {c['skill_b']}")
                    print(f"    A: {c['a_desc']}")
                    print(f"    B: {c['b_desc']}")
            else:
                print("✅ 无冲突")

        elif cmd == "orphans":
            result = registry.validate()
            if result["orphans"]:
                print(f"🏝️ {len(result['orphans'])} 个孤立技能 (无依赖关系):")
                for o in result["orphans"]:
                    s = registry.get(o)
                    if s:
                        print(f"  • {o} [{s.origin}] — {s.description[:80]}")
            else:
                print("✅ 无孤立技能")

        elif cmd == "stats":
            s = registry.stats()
            print(f"═══ 鲤鱼 Skill 统计 ───")
            print(f"  总计: {s['total']}")
            print(f"  启用: {s['enabled']}  禁用: {s['disabled']}")
            print(f"  有依赖关系: {s['with_dependencies']}")
            print(f"  有冲突声明: {s['with_conflicts']}")
            print(f"  最大依赖深度: {s['max_dependency_depth']}")
            print(f"  来源分布:")
            for o, c in sorted(s["by_origin"].items(), key=lambda x: -x[1]):
                print(f"    {o}: {c}")

        else:
            print(f"未知命令: {cmd}")
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    main()
