#!/usr/bin/env python3
"""
鲤鱼 — 统一入口脚本
整合所有鲤鱼模块，提供统一的 CLI 接口

Usage:
  liyu.py <module> <command> [args...]

Modules:
  security    — 安全层 (5层防御)
  memory      — 记忆系统 (渐进式披露)
  budget      — 迭代预算控制
  drift       — Identity Drift 检测
  correction  — 纠正模式生命周期
  framework   — 框架进化引擎
  circuit     — 熔断器
  bash-guard  — Bash 安全防护
  compress    — 上下文压缩
  reflect     — 反思引擎
  stats       — 全局统计
  reset       — 重置所有状态
"""

import sys
import os
import subprocess
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path(__file__).parent
PYTHON = "python3"

# ── 模块映射 ──────────────────────────────────────────────────────────────
MODULES = {
    "security": "liyu-security-layer.py",
    "memory": "liyu-memory-v2.py",
    "budget": "liyu-iteration-budget.py",
    "drift": "liyu-identity-drift.py",
    "correction": "liyu-correction-lifecycle.py",
    "framework": "liyu-framework-promoter.py",
    "circuit": "liyu-circuit-breaker.py",
    "bash-guard": "liyu-bash-guard.py",
    "compress": "liyu-context-compressor.py",
    "reflect": "reflection-engine.py",
}

# ── 帮助信息 ──────────────────────────────────────────────────────────────

def show_help():
    """显示帮助信息"""
    print(__doc__)
    print("\n可用模块:")
    for name, script in MODULES.items():
        print(f"  {name:15} — {script}")
    print("\n示例:")
    print("  liyu.py security scan-input 'test input'")
    print("  liyu.py memory search '鲤鱼'")
    print("  liyu.py budget check main-agent")
    print("  liyu.py stats")
    print("  liyu.py reset")

def show_stats():
    """显示全局统计"""
    print("═══ 鲤鱼 全局统计 ═══\n")

    for name, script in MODULES.items():
        script_path = 鲤鱼_HOME / script
        if script_path.exists():
            try:
                result = subprocess.run(
                    [PYTHON, str(script_path), "stats"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    # 只显示前几行
                    lines = result.stdout.strip().split('\n')
                    print(f"📊 {name}:")
                    for line in lines[:3]:
                        print(f"  {line}")
                    print()
            except Exception:
                print(f"⚠️ {name}: 无法获取统计\n")

def reset_all():
    """重置所有状态"""
    print("🔄 重置所有鲤鱼状态...\n")

    for name, script in MODULES.items():
        script_path = 鲤鱼_HOME / script
        if script_path.exists():
            try:
                result = subprocess.run(
                    [PYTHON, str(script_path), "reset"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    print(f"✅ {name}: 已重置")
                else:
                    print(f"⚠️ {name}: 重置失败")
            except Exception:
                print(f"⚠️ {name}: 无法重置")

    print("\n✅ 所有状态已重置")

def run_module(module_name, args):
    """运行指定模块"""
    if module_name not in MODULES:
        print(f"❌ 未知模块: {module_name}")
        print(f"可用模块: {', '.join(MODULES.keys())}")
        sys.exit(1)

    script = MODULES[module_name]
    script_path = 鲤鱼_HOME / script

    if not script_path.exists():
        print(f"❌ 脚本不存在: {script}")
        sys.exit(1)

    # 构建命令
    cmd = [PYTHON, str(script_path)] + args

    # 执行
    try:
        result = subprocess.run(cmd)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n⚠️ 中断")
        sys.exit(130)
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        sys.exit(1)

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        show_help()
        sys.exit(0)

    command = sys.argv[1]

    # 特殊命令
    if command == "help" or command == "--help" or command == "-h":
        show_help()
        sys.exit(0)

    if command == "stats":
        show_stats()
        sys.exit(0)

    if command == "reset":
        reset_all()
        sys.exit(0)

    # 模块命令
    if len(sys.argv) < 3:
        print(f"❌ 用法: liyu.py <module> <command> [args...]")
        print(f"运行 'liyu.py help' 查看帮助")
        sys.exit(1)

    module_name = command
    module_args = sys.argv[2:]

    run_module(module_name, module_args)

if __name__ == "__main__":
    main()
