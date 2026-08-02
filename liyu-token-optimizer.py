#!/usr/bin/env python3
"""
鲤鱼 Token Optimizer — Token 优化
吸收自 Mibayy/token-savior 的 Token 优化机制

核心理念：
  - 结构化代码导航
  - 持久化记忆引擎
  - 减少 80% 活跃 token

Usage:
  liyu-token-optimizer.py optimize <text>
    优化文本 token

  liyu-token-optimizer.py analyze <file>
    分析文件 token 使用

  liyu-token-optimizer.py stats
    查看统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import re
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
TOKEN_OPTIMIZER_STATE_FILE = 鲤鱼_HOME / "token-optimizer-state.json"

# ── Token 估算 ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """估算 token 数（中文约 1.5 字/token，英文约 4 字符/token）"""
    cn_chars = len(re.findall(r'[一-鿿]', text))
    en_chars = len(text) - cn_chars
    return int(cn_chars / 1.5 + en_chars / 4)

# ── 优化策略 ──────────────────────────────────────────────────────────────

def optimize_text(text: str, level: str = "medium") -> Dict[str, Any]:
    """优化文本 token

    Args:
        text: 原始文本
        level: 优化级别 (light/medium/aggressive)

    Returns:
        优化结果
    """
    original_tokens = estimate_tokens(text)

    # 应用优化策略
    optimized = text

    # 1. 移除多余空白
    optimized = re.sub(r'\n\s*\n\s*\n+', '\n\n', optimized)

    # 2. 压缩重复字符
    if level in ("medium", "aggressive"):
        optimized = re.sub(r'(.)\1{3,}', r'\1\1\1', optimized)

    # 3. 移除注释（aggressive）
    if level == "aggressive":
        optimized = re.sub(r'#.*$', '', optimized, flags=re.MULTILINE)
        optimized = re.sub(r'//.*$', '', optimized, flags=re.MULTILINE)

    # 4. 压缩长字符串
    if level in ("medium", "aggressive"):
        optimized = re.sub(r'"([^"]{50,})"', lambda m: '"' + m.group(1)[:30] + '..."', optimized)
        optimized = re.sub(r"'([^']{50,})'", lambda m: "'" + m.group(1)[:30] + "...'", optimized)

    # 5. 移除空行
    optimized = re.sub(r'\n\s*\n', '\n', optimized)

    optimized_tokens = estimate_tokens(optimized)

    return {
        "original_text": text,
        "optimized_text": optimized,
        "original_tokens": original_tokens,
        "optimized_tokens": optimized_tokens,
        "tokens_saved": original_tokens - optimized_tokens,
        "savings_percent": (1 - optimized_tokens / original_tokens) * 100 if original_tokens > 0 else 0,
    }

# ── 文件分析 ──────────────────────────────────────────────────────────────

def analyze_file(file_path: str) -> Dict[str, Any]:
    """分析文件 token 使用"""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}

    total_tokens = estimate_tokens(content)

    # 按行分析
    lines = content.split('\n')
    line_tokens = []
    for i, line in enumerate(lines):
        tokens = estimate_tokens(line)
        if tokens > 0:
            line_tokens.append({
                "line": i + 1,
                "content": line[:50],
                "tokens": tokens,
            })

    # 排序找出最重的行
    heavy_lines = sorted(line_tokens, key=lambda x: x["tokens"], reverse=True)[:10]

    return {
        "file": str(path),
        "total_tokens": total_tokens,
        "total_lines": len(lines),
        "heavy_lines": heavy_lines,
    }

# ── 状态管理 ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    """加载状态"""
    if TOKEN_OPTIMIZER_STATE_FILE.exists():
        try:
            return json.loads(TOKEN_OPTIMIZER_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_optimizations": 0,
        "total_tokens_saved": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_state(state: dict) -> None:
    """持久化状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    TOKEN_OPTIMIZER_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "optimize":
        if len(sys.argv) < 3:
            print("Usage: liyu-token-optimizer.py optimize '<text>' [--level light|medium|aggressive]", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        level = "medium"

        if "--level" in sys.argv:
            idx = sys.argv.index("--level")
            if idx + 1 < len(sys.argv):
                level = sys.argv[idx + 1]

        result = optimize_text(text, level)

        # 更新统计
        state = load_state()
        state["total_optimizations"] += 1
        state["total_tokens_saved"] += result["tokens_saved"]
        save_state(state)

        print(f"📊 Token 优化结果:")
        print(f"  原始: {result['original_tokens']} tokens")
        print(f"  优化: {result['optimized_tokens']} tokens")
        print(f"  节省: {result['tokens_saved']} tokens ({result['savings_percent']:.1f}%)")
        print()
        print(f"优化后内容:")
        print(result["optimized_text"][:500])

    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("Usage: liyu-token-optimizer.py analyze <file>", file=sys.stderr)
            sys.exit(1)

        file_path = sys.argv[2]
        result = analyze_file(file_path)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"═══ 文件 Token 分析 ═══")
        print(f"  文件: {result['file']}")
        print(f"  总计: {result['total_tokens']} tokens")
        print(f"  行数: {result['total_lines']}")
        print()
        print(f"  最重的行:")
        for line in result["heavy_lines"]:
            print(f"    Line {line['line']}: {line['tokens']} tokens — {line['content'][:40]}...")

    elif cmd == "stats":
        state = load_state()

        print("═══ 鲤鱼 Token Optimizer Statistics ═══")
        print(f"  总计优化: {state.get('total_optimizations', 0)}")
        print(f"  节省 tokens: {state.get('total_tokens_saved', 0)}")

    elif cmd == "reset":
        save_state({
            "total_optimizations": 0,
            "total_tokens_saved": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Token Optimizer 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
