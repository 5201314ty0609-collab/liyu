#!/usr/bin/env python3
"""
鲤鱼 Iteration Budget — 迭代预算控制
吸收自 hermes-agent 的 IterationBudget 设计

核心理念：
  - 线程安全的迭代计数器，防止 Agent 无限循环
  - 父子 Agent 独立预算
  - 退款机制允许特殊操作不计入预算

Usage:
  liyu-iteration-budget.py check <agent_id>
    检查 agent 是否还有预算

  liyu-iteration-budget.py consume <agent_id>
    消耗一次迭代

  liyu-iteration-budget.py refund <agent_id>
    退还一次迭代

  liyu-iteration-budget.py stats
    查看预算统计

  liyu-iteration-budget.py reset
    重置所有计数器
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict
import json
import sys
import threading

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
BUDGET_STATE_FILE = 鲤鱼_HOME / "iteration-budget-state.json"

# ── 默认配置 ──────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "parent_max_iterations": 90,
    "child_max_iterations": 50,
    "special_operations": ["execute_code", "web_search", "file_read"],
}

# ── IterationBudget 类 ──────────────────────────────────────────────────────

class IterationBudget:
    """线程安全的迭代计数器"""

    def __init__(self, agent_id: str, max_iterations: int):
        self.agent_id = agent_id
        self.max_iterations = max_iterations
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """尝试消耗一次迭代，返回是否允许"""
        with self._lock:
            if self._used >= self.max_iterations:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """退还一次迭代"""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    def remaining(self) -> int:
        """返回剩余迭代次数"""
        with self._lock:
            return self.max_iterations - self._used

    def used(self) -> int:
        """返回已使用迭代次数"""
        with self._lock:
            return self._used

    def is_exhausted(self) -> bool:
        """检查是否已耗尽"""
        with self._lock:
            return self._used >= self.max_iterations

# ── Budget Manager ──────────────────────────────────────────────────────────

class BudgetManager:
    """预算管理器"""

    def __init__(self):
        self.budgets: Dict[str, IterationBudget] = {}
        self._load_state()

    def _load_state(self):
        """加载状态"""
        if BUDGET_STATE_FILE.exists():
            try:
                data = json.loads(BUDGET_STATE_FILE.read_text())
                for agent_id, info in data.get("agents", {}).items():
                    budget = IterationBudget(agent_id, info["max_iterations"])
                    budget._used = info["used"]
                    self.budgets[agent_id] = budget
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self):
        """持久化状态"""
        鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
        data = {
            "agents": {
                agent_id: {
                    "max_iterations": budget.max_iterations,
                    "used": budget.used(),
                    "remaining": budget.remaining(),
                }
                for agent_id, budget in self.budgets.items()
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        BUDGET_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def get_or_create(self, agent_id: str, is_child: bool = False) -> IterationBudget:
        """获取或创建预算"""
        if agent_id not in self.budgets:
            max_iter = DEFAULT_CONFIG["child_max_iterations"] if is_child else DEFAULT_CONFIG["parent_max_iterations"]
            self.budgets[agent_id] = IterationBudget(agent_id, max_iter)
            self._save_state()
        return self.budgets[agent_id]

    def check(self, agent_id: str) -> dict:
        """检查预算状态"""
        budget = self.get_or_create(agent_id)
        return {
            "agent_id": agent_id,
            "max_iterations": budget.max_iterations,
            "used": budget.used(),
            "remaining": budget.remaining(),
            "is_exhausted": budget.is_exhausted(),
        }

    def consume(self, agent_id: str) -> dict:
        """消耗一次迭代"""
        budget = self.get_or_create(agent_id)
        allowed = budget.consume()
        self._save_state()
        return {
            "agent_id": agent_id,
            "allowed": allowed,
            "used": budget.used(),
            "remaining": budget.remaining(),
        }

    def refund(self, agent_id: str) -> dict:
        """退还一次迭代"""
        budget = self.get_or_create(agent_id)
        budget.refund()
        self._save_state()
        return {
            "agent_id": agent_id,
            "used": budget.used(),
            "remaining": budget.remaining(),
        }

    def stats(self) -> dict:
        """统计信息"""
        return {
            "total_agents": len(self.budgets),
            "agents": {
                agent_id: {
                    "max_iterations": budget.max_iterations,
                    "used": budget.used(),
                    "remaining": budget.remaining(),
                    "is_exhausted": budget.is_exhausted(),
                }
                for agent_id, budget in self.budgets.items()
            },
        }

    def reset(self):
        """重置所有预算"""
        self.budgets = {}
        self._save_state()

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    manager = BudgetManager()

    if cmd == "check":
        if len(sys.argv) < 3:
            print("Usage: liyu-iteration-budget.py check <agent_id>", file=sys.stderr)
            sys.exit(1)

        agent_id = sys.argv[2]
        result = manager.check(agent_id)

        status_icon = "🟢" if not result["is_exhausted"] else "🔴"
        print(f"{status_icon} Agent: {agent_id}")
        print(f"  Max: {result['max_iterations']}")
        print(f"  Used: {result['used']}")
        print(f"  Remaining: {result['remaining']}")

        if result["is_exhausted"]:
            sys.exit(2)

    elif cmd == "consume":
        if len(sys.argv) < 3:
            print("Usage: liyu-iteration-budget.py consume <agent_id>", file=sys.stderr)
            sys.exit(1)

        agent_id = sys.argv[2]
        result = manager.consume(agent_id)

        if result["allowed"]:
            print(f"✅ Consumed: {agent_id} ({result['remaining']} remaining)")
        else:
            print(f"❌ Exhausted: {agent_id}")
            sys.exit(2)

    elif cmd == "refund":
        if len(sys.argv) < 3:
            print("Usage: liyu-iteration-budget.py refund <agent_id>", file=sys.stderr)
            sys.exit(1)

        agent_id = sys.argv[2]
        result = manager.refund(agent_id)
        print(f"✅ Refunded: {agent_id} ({result['remaining']} remaining)")

    elif cmd == "stats":
        result = manager.stats()
        print("═══ 鲤鱼 Iteration Budget Statistics ═══")
        print(f"  Agents: {result['total_agents']}")
        print()
        for agent_id, info in result["agents"].items():
            status_icon = "🟢" if not info["is_exhausted"] else "🔴"
            print(f"  {status_icon} {agent_id}:")
            print(f"    Max: {info['max_iterations']} | Used: {info['used']} | Remaining: {info['remaining']}")

    elif cmd == "reset":
        manager.reset()
        print("✅ Iteration Budget 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
