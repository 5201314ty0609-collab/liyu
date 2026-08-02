#!/usr/bin/env python3
"""
鲤鱼 Deep Dream — 记忆蒸馏机制
吸收自 zhayujie/CowAgent 的 Deep Dream 夜间处理

核心理念：
  - 三层记忆：会话上下文 → 日记 → 核心记忆
  - Deep Dream 定期从日记中蒸馏生成核心记忆
  - 梦境日记记录蒸馏发现的叙事日记

Usage:
  liyu-deep-dream.py run
    执行 Deep Dream 蒸馏

  liyu-deep-dream.py run --force
    强制执行（忽略配置开关）

  liyu-deep-dream.py stats
    查看蒸馏统计

  liyu-deep-dream.py reset
    重置所有状态
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import hashlib
import sys

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
DREAM_STATE_FILE = 鲤鱼_HOME / "deep-dream-state.json"
MEMORY_FILE = 鲤鱼_HOME / "MEMORY.md"
DAILY_DIR = 鲤鱼_HOME / "daily"
DREAMS_DIR = DAILY_DIR / "dreams"

# ── 配置 ──────────────────────────────────────────────────────────────
MAX_MEMORY_ITEMS = 50  # 核心记忆最大条目数
DREAM_LOOKBACK_DAYS = 7  # 回溯天数

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class DreamState:
    """蒸馏状态"""
    last_dream_at: Optional[str]
    last_dream_hash: Optional[str]
    total_dreams: int
    total_items_distilled: int
    created_at: str

# ── 蒸馏管理 ──────────────────────────────────────────────────────────────

def load_dream_state() -> dict:
    """加载蒸馏状态"""
    if DREAM_STATE_FILE.exists():
        try:
            return json.loads(DREAM_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "last_dream_at": None,
        "last_dream_hash": None,
        "total_dreams": 0,
        "total_items_distilled": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_dream_state(state: dict) -> None:
    """持久化蒸馏状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    DREAM_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def compute_content_hash(content: str) -> str:
    """计算内容哈希"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def get_daily_files(days: int = DREAM_LOOKBACK_DAYS) -> List[Path]:
    """获取最近 N 天的日记文件"""
    if not DAILY_DIR.exists():
        return []

    daily_files = []
    today = datetime.now().date()

    for i in range(days):
        date = today - __import__('datetime').timedelta(days=i)
        filename = f"{date.isoformat()}.md"
        filepath = DAILY_DIR / filename
        if filepath.exists():
            daily_files.append(filepath)

    return daily_files

def read_memory() -> str:
    """读取当前核心记忆"""
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text(encoding='utf-8')
    return ""

def write_memory(content: str) -> None:
    """写入核心记忆"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(content, encoding='utf-8')

def read_daily_files(files: List[Path]) -> str:
    """读取日记文件内容"""
    contents = []
    for f in files:
        try:
            contents.append(f"## {f.stem}\n{f.read_text(encoding='utf-8')}")
        except Exception:
            pass
    return "\n\n".join(contents)

def write_dream_log(date: str, content: str) -> None:
    """写入梦境日记"""
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    dream_file = DREAMS_DIR / f"{date}.md"
    dream_file.write_text(content, encoding='utf-8')

# ── Deep Dream 蒸馏 ──────────────────────────────────────────────────────

DREAM_PROMPT = """你是一个记忆蒸馏器。你的任务是从日记中提取关键信息，更新核心记忆。

## 当前核心记忆
{current_memory}

## 最近日记
{daily_content}

## 要求
1. 只能基于提供的材料整理，严禁编造
2. 合并语义相近条目
3. 提取新信息
4. 解决冲突
5. 清理无效/冗余条目
6. 控制在 {max_items} 条以内，每条一句话

## 输出格式
输出两个区块：

[MEMORY]
（完整的核心记忆内容，每条以 - 开头）

[DREAM]
（蒸馏发现的叙事日记，记录你如何整理和发现）
"""

def run_deep_dream(force: bool = False) -> dict:
    """执行 Deep Dream 蒸馏"""
    state = load_dream_state()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # 检查是否已经蒸馏过
    if not force and state.get("last_dream_hash"):
        # 读取当前日记
        daily_files = get_daily_files()
        daily_content = read_daily_files(daily_files)
        current_hash = compute_content_hash(daily_content)

        if current_hash == state["last_dream_hash"]:
            return {
                "status": "skipped",
                "reason": "No new daily content to distill",
            }

    # 读取当前记忆和日记
    current_memory = read_memory()
    daily_files = get_daily_files()
    daily_content = read_daily_files(daily_files)

    if not daily_content.strip():
        return {
            "status": "skipped",
            "reason": "No daily content found",
        }

    # 构建蒸馏提示
    prompt = DREAM_PROMPT.format(
        current_memory=current_memory,
        daily_content=daily_content,
        max_items=MAX_MEMORY_ITEMS,
    )

    # 记录蒸馏哈希
    daily_hash = compute_content_hash(daily_content)

    # 模拟 LLM 输出（实际应调用 LLM）
    # 这里使用规则提取
    memory_items = extract_memory_items(current_memory, daily_content)
    dream_narrative = generate_dream_narrative(daily_content)

    # 限制条目数
    if len(memory_items) > MAX_MEMORY_ITEMS:
        memory_items = memory_items[:MAX_MEMORY_ITEMS]

    # 构建新的核心记忆
    new_memory = "\n".join(f"- {item}" for item in memory_items)

    # 写入核心记忆
    write_memory(new_memory)

    # 写入梦境日记
    write_dream_log(today, dream_narrative)

    # 更新状态
    state["last_dream_at"] = now.isoformat()
    state["last_dream_hash"] = daily_hash
    state["total_dreams"] += 1
    state["total_items_distilled"] += len(memory_items)
    save_dream_state(state)

    return {
        "status": "completed",
        "items_distilled": len(memory_items),
        "dream_file": str(DREAMS_DIR / f"{today}.md"),
    }

def extract_memory_items(current_memory: str, daily_content: str) -> List[str]:
    """从日记中提取记忆条目（简化版）"""
    items = []

    # 保留当前记忆中的条目
    for line in current_memory.split('\n'):
        line = line.strip()
        if line.startswith('- ') and len(line) > 2:
            items.append(line[2:])

    # 从日记中提取新条目
    for line in daily_content.split('\n'):
        line = line.strip()
        if line.startswith('- ') and len(line) > 2:
            item = line[2:]
            # 检查是否已存在
            if not any(item in existing for existing in items):
                items.append(item)

    return items

def generate_dream_narrative(daily_content: str) -> str:
    """生成梦境叙事（简化版）"""
    lines = daily_content.split('\n')
    narrative_lines = []

    for line in lines:
        line = line.strip()
        if line.startswith('- ') and len(line) > 2:
            narrative_lines.append(f"蒸馏发现: {line[2:]}")

    if not narrative_lines:
        return "今日无新发现。"

    return "\n".join(narrative_lines[:10])  # 最多 10 条

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "run":
        force = "--force" in sys.argv
        result = run_deep_dream(force)

        if result["status"] == "completed":
            print(f"🌙 Deep Dream 蒸馏完成:")
            print(f"  蒸馏条目: {result['items_distilled']}")
            print(f"  梦境文件: {result['dream_file']}")
        else:
            print(f"⏭️ 跳过: {result['reason']}")

    elif cmd == "stats":
        state = load_dream_state()

        print("═══ 鲤鱼 Deep Dream Statistics ═══")
        print(f"  总计蒸馏:     {state.get('total_dreams', 0)}")
        print(f"  蒸馏条目:     {state.get('total_items_distilled', 0)}")
        print(f"  上次蒸馏:     {state.get('last_dream_at', 'never')}")

        # 检查核心记忆
        memory = read_memory()
        memory_items = [l for l in memory.split('\n') if l.strip().startswith('- ')]
        print(f"  当前记忆条目: {len(memory_items)}")

        # 检查日记文件
        daily_files = get_daily_files()
        print(f"  日记文件数:   {len(daily_files)}")

        # 检查梦境日记
        if DREAMS_DIR.exists():
            dream_files = list(DREAMS_DIR.glob("*.md"))
            print(f"  梦境日记数:   {len(dream_files)}")

    elif cmd == "reset":
        save_dream_state({
            "last_dream_at": None,
            "last_dream_hash": None,
            "total_dreams": 0,
            "total_items_distilled": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Deep Dream 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
