#!/usr/bin/env python3
"""
鲤鱼 Skills — 统一入口。

轻量级 Skill 系统，常用操作快速触发。

Usage:
  liyu-skill.py <skill_name> [args...]    执行指定 Skill
  liyu-skill.py list                       列出所有可用 Skill
  liyu-skill.py help <skill_name>          显示 Skill 帮助
"""

from pathlib import Path
import importlib.util
import sys

SKILLS_DIR = Path(__file__).parent / "skills"

# Skill 注册表
SKILLS = {
    "code-tidy": {
        "description": "代码洁癖级整理 — 清理未使用 import、注释代码、排序",
        "module": "code-tidy",
        "usage": "code-tidy.py <file_or_dir> [--dry-run]",
    },
    "verify": {
        "description": "完成前强制验证 — 语法检查、常见问题检测",
        "module": "verify-completion",
        "usage": "verify-completion.py <file_or_dir> [--strict]",
    },
    "debug": {
        "description": "4 阶段根因调试 — 重现、缩小范围、假设、验证",
        "module": "systematic-debug",
        "usage": "systematic-debug.py start <problem>",
    },
    "dispatch": {
        "description": "并行任务分派 — 分析任务可并行性，创建执行计划",
        "module": "dispatch-parallel",
        "usage": "dispatch-parallel.py analyze <task>",
    },
    "knowledge-sync": {
        "description": "知识库同步 — 同步 memory/ 到 SQLite 知识库",
        "module": "knowledge-sync",
        "usage": "knowledge-sync.py sync",
    },
    "mutation-gate": {
        "description": "变体门控 — TDD 变体验证步骤，检查测试是否真正捕获缺陷",
        "module": None,  # standalone script at liyu root
        "usage": "liyu-mutation-gate.py run <source> <test> [--threshold 80]",
        "script": str(Path(__file__).parent / "liyu-mutation-gate.py"),
    },
}


def load_skill(module_name: str):
    """动态加载 Skill 模块"""
    skill_path = SKILLS_DIR / f"{module_name}.py"
    if not skill_path.exists():
        print(f"Skill module not found: {skill_path}")
        return None

    spec = importlib.util.spec_from_file_location(module_name, skill_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def list_skills():
    """列出所有可用 Skill"""
    print("鲤鱼 Skills")
    print("=" * 60)
    print()

    for name, info in sorted(SKILLS.items()):
        print(f"  {name}")
        print(f"    {info['description']}")
        print(f"    Usage: {info['usage']}")
        print()


def show_help(skill_name: str):
    """显示 Skill 帮助"""
    if skill_name not in SKILLS:
        print(f"Unknown skill: {skill_name}")
        print("Available skills:", ", ".join(SKILLS.keys()))
        return

    info = SKILLS[skill_name]
    print(f"Skill: {skill_name}")
    print(f"Description: {info['description']}")
    print(f"Usage: {info['usage']}")

    # 加载模块并显示 __doc__（如果有module）
    if info.get("module"):
        module = load_skill(info["module"])
        if module and module.__doc__:
            print()
            print(module.__doc__)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "list":
        list_skills()

    elif cmd == "help":
        if len(sys.argv) < 3:
            print("Usage: liyu-skill.py help <skill_name>")
            return
        show_help(sys.argv[2])

    elif cmd in SKILLS:
        # 执行 Skill
        info = SKILLS[cmd]

        # Standalone script (no module, direct invocation)
        if info.get("script"):
            script_path = info["script"]
            import subprocess
            result = subprocess.run(
                [sys.executable, script_path] + sys.argv[2:],
            )
            sys.exit(result.returncode)

        module = load_skill(info["module"])

        if module:
            # 将参数传递给 Skill 的 main()
            sys.argv = [f"{SKILLS_DIR}/{info['module']}.py"] + sys.argv[2:]
            module.main()

    else:
        # 尝试作为 Skill 名称
        skill_name = cmd
        if skill_name in SKILLS:
            info = SKILLS[skill_name]
            module = load_skill(info["module"])
            if module:
                sys.argv = [f"{SKILLS_DIR}/{info['module']}.py"] + sys.argv[2:]
                module.main()
        else:
            print(f"Unknown skill: {skill_name}")
            print("Available skills:", ", ".join(SKILLS.keys()))
            print()
            print("Use 'liyu-skill.py list' to see all skills")


if __name__ == "__main__":
    main()
