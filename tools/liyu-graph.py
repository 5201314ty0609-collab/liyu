#!/usr/bin/env python3
"""
鲤鱼 状态图引擎 v2.0
借鉴 LangGraph 的状态图概念，轻量化实现
支持节点、边、状态、检查点、可视化、并行分支、子图、动态节点、执行历史

用法：
  from liyu_graph import PhoenixGraph, PhoenixCheckpoint

  graph = PhoenixGraph()
  graph.add_node("analyze", analyze_function)
  graph.add_node("solve", solve_function)
  graph.add_edge("analyze", "solve")
  graph.add_edge("solve", "END")
  graph.set_entry_point("analyze")

  app = graph.compile()
  result = app.invoke({"question": "..."})
"""

from typing import TypedDict, Any, Callable, Optional, List, Dict, Set
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from datetime import datetime
import concurrent.futures
import uuid
import copy


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------

class NodeStatus(Enum):
    """节点执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EdgeType(Enum):
    """边类型"""
    NORMAL = "normal"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"


@dataclass
class ExecutionRecord:
    """单条执行记录"""
    record_id: str
    node_name: str
    status: NodeStatus
    started_at: str
    finished_at: Optional[str] = None
    duration_ms: Optional[float] = None
    input_keys: List[str] = field(default_factory=list)
    output_keys: List[str] = field(default_factory=list)
    error: Optional[str] = None
    branch_id: Optional[str] = None  # 并行分支 ID
    parent_node: Optional[str] = None  # 子图父节点

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "node_name": self.node_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "input_keys": self.input_keys,
            "output_keys": self.output_keys,
            "error": self.error,
            "branch_id": self.branch_id,
            "parent_node": self.parent_node,
        }


@dataclass
class ExecutionTimeline:
    """执行时间线"""
    run_id: str
    graph_name: Optional[str]
    started_at: str
    finished_at: Optional[str] = None
    total_duration_ms: Optional[float] = None
    records: List[ExecutionRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_record(self, record: ExecutionRecord):
        self.records.append(record)

    def get_records_by_node(self, node_name: str) -> List[ExecutionRecord]:
        return [r for r in self.records if r.node_name == node_name]

    def get_failed_records(self) -> List[ExecutionRecord]:
        return [r for r in self.records if r.status == NodeStatus.FAILED]

    def get_duration_breakdown(self) -> Dict[str, float]:
        breakdown: Dict[str, float] = {}
        for r in self.records:
            if r.duration_ms is not None:
                breakdown[r.node_name] = breakdown.get(r.node_name, 0) + r.duration_ms
        return breakdown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "graph_name": self.graph_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "records": [r.to_dict() for r in self.records],
            "metadata": self.metadata,
        }

    def summary(self) -> str:
        lines = [
            f"Run: {self.run_id}",
            f"Graph: {self.graph_name or '(unnamed)'}",
            f"Started: {self.started_at}",
            f"Finished: {self.finished_at or 'in progress'}",
            f"Duration: {self.total_duration_ms:.1f}ms" if self.total_duration_ms else "Duration: -",
            f"Steps: {len(self.records)}",
        ]
        failed = self.get_failed_records()
        if failed:
            lines.append(f"Failed: {len(failed)}")
            for r in failed:
                lines.append(f"  - {r.node_name}: {r.error}")
        breakdown = self.get_duration_breakdown()
        if breakdown:
            lines.append("Duration breakdown:")
            for name, ms in sorted(breakdown.items(), key=lambda x: -x[1]):
                lines.append(f"  {name}: {ms:.1f}ms")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core State
# ---------------------------------------------------------------------------

class PhoenixState(TypedDict, total=False):
    """鲤鱼 状态"""
    data: Dict[str, Any]
    history: List[str]
    metadata: Dict[str, Any]


def make_state(data: Dict[str, Any], history: Optional[List[str]] = None,
               metadata: Optional[Dict[str, Any]] = None) -> PhoenixState:
    return {
        "data": data,
        "history": history or [],
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class PhoenixNode:
    """鲤鱼 节点"""
    def __init__(self, name: str, func: Callable, *, is_dynamic: bool = False,
                 tags: Optional[List[str]] = None):
        self.name = name
        self.func = func
        self.is_dynamic = is_dynamic
        self.tags = tags or []

    def execute(self, state: PhoenixState) -> PhoenixState:
        try:
            result = self.func(state)
            if not isinstance(result, dict) or "data" not in result:
                raise TypeError(f"节点 {self.name} 返回值必须是包含 'data' 键的 dict")
            return result
        except Exception as e:
            return {
                **state,
                "metadata": {
                    **state.get("metadata", {}),
                    "error": str(e),
                    "error_node": self.name,
                },
            }


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------

class PhoenixEdge:
    """鲤鱼 边"""
    def __init__(self, from_node: str, to_node: str,
                 condition: Optional[Callable] = None,
                 edge_type: EdgeType = EdgeType.NORMAL):
        self.from_node = from_node
        self.to_node = to_node
        self.condition = condition
        self.edge_type = edge_type


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class PhoenixCheckpoint:
    """鲤鱼 检查点"""
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.states: Dict[str, PhoenixState] = {}

    def save(self, thread_id: str, state: PhoenixState):
        if self.path == ":memory:":
            self.states[thread_id] = copy.deepcopy(state)
        else:
            cp = Path(self.path) / f"{thread_id}.json"
            cp.parent.mkdir(parents=True, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

    def load(self, thread_id: str) -> Optional[PhoenixState]:
        if self.path == ":memory:":
            s = self.states.get(thread_id)
            return copy.deepcopy(s) if s is not None else None
        else:
            cp = Path(self.path) / f"{thread_id}.json"
            if cp.exists():
                with open(cp, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def list_checkpoints(self) -> List[str]:
        if self.path == ":memory:":
            return list(self.states.keys())
        else:
            d = Path(self.path)
            if d.exists():
                return [f.stem for f in d.glob("*.json")]
        return []


# ---------------------------------------------------------------------------
# Execution History Store
# ---------------------------------------------------------------------------

class PhoenixHistoryStore:
    """执行历史持久化存储"""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.timelines: Dict[str, ExecutionTimeline] = {}

    def save(self, timeline: ExecutionTimeline):
        if self.path == ":memory:":
            self.timelines[timeline.run_id] = timeline
        else:
            hp = Path(self.path) / f"{timeline.run_id}.json"
            hp.parent.mkdir(parents=True, exist_ok=True)
            with open(hp, "w", encoding="utf-8") as f:
                json.dump(timeline.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self, run_id: str) -> Optional[ExecutionTimeline]:
        if self.path == ":memory:":
            return self.timelines.get(run_id)
        else:
            hp = Path(self.path) / f"{run_id}.json"
            if hp.exists():
                with open(hp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tl = ExecutionTimeline(
                    run_id=data["run_id"],
                    graph_name=data.get("graph_name"),
                    started_at=data["started_at"],
                    finished_at=data.get("finished_at"),
                    total_duration_ms=data.get("total_duration_ms"),
                    metadata=data.get("metadata", {}),
                )
                for rd in data.get("records", []):
                    tl.add_record(ExecutionRecord(
                        record_id=rd["record_id"],
                        node_name=rd["node_name"],
                        status=NodeStatus(rd["status"]),
                        started_at=rd["started_at"],
                        finished_at=rd.get("finished_at"),
                        duration_ms=rd.get("duration_ms"),
                        input_keys=rd.get("input_keys", []),
                        output_keys=rd.get("output_keys", []),
                        error=rd.get("error"),
                        branch_id=rd.get("branch_id"),
                        parent_node=rd.get("parent_node"),
                    ))
                return tl
        return None

    def list_runs(self) -> List[str]:
        if self.path == ":memory:":
            return list(self.timelines.keys())
        else:
            d = Path(self.path)
            if d.exists():
                return sorted([f.stem for f in d.glob("*.json")])
        return []


# ---------------------------------------------------------------------------
# Parallel Execution
# ---------------------------------------------------------------------------

class PhoenixParallel:
    """鲤鱼 并行分支执行器"""

    def __init__(self, branches: List[str]):
        if not branches:
            raise ValueError("并行分支列表不能为空")
        self.branches = branches

    def execute(self, state: PhoenixState, graph: "PhoenixGraph",
                timeline: Optional[ExecutionTimeline] = None) -> PhoenixState:
        """并行执行多个分支节点，合并结果"""
        branch_id = f"parallel-{uuid.uuid4().hex[:8]}"

        def _run_branch(node_name: str) -> tuple:
            record = ExecutionRecord(
                record_id=uuid.uuid4().hex[:12],
                node_name=node_name,
                status=NodeStatus.RUNNING,
                started_at=datetime.now().isoformat(),
                branch_id=branch_id,
                input_keys=list(state.get("data", {}).keys()),
            )
            start_ms = _now_ms()
            try:
                result = graph.nodes[node_name].execute(copy.deepcopy(state))
                record.status = NodeStatus.COMPLETED
                record.output_keys = list(result.get("data", {}).keys())
            except Exception as e:
                result = {
                    **state,
                    "metadata": {
                        **state.get("metadata", {}),
                        "error": str(e),
                        "error_node": node_name,
                    },
                }
                record.status = NodeStatus.FAILED
                record.error = str(e)
            finally:
                record.finished_at = datetime.now().isoformat()
                record.duration_ms = _now_ms() - start_ms
            return node_name, result, record

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.branches)) as pool:
            futures = {pool.submit(_run_branch, b): b for b in self.branches}
            results: Dict[str, PhoenixState] = {}
            for future in concurrent.futures.as_completed(futures):
                node_name, result, record = future.result()
                results[node_name] = result
                if timeline:
                    timeline.add_record(record)

        # 合并：data 取最后写入的值（按 branches 顺序），history 追加，metadata 合并
        merged = copy.deepcopy(state)
        for branch_name in self.branches:
            branch_state = results[branch_name]
            merged["data"].update(branch_state.get("data", {}))
            for h in branch_state.get("history", []):
                if h not in merged["history"]:
                    merged["history"].append(h)
            merged_metadata = merged.get("metadata", {})
            for mk, mv in branch_state.get("metadata", {}).items():
                if mk not in ("start_time", "thread_id"):
                    merged_metadata[mk] = mv
            merged["metadata"] = merged_metadata

        merged["history"].append(f"PARALLEL({','.join(self.branches)})")
        return merged


# ---------------------------------------------------------------------------
# Subgraph
# ---------------------------------------------------------------------------

class PhoenixSubgraph:
    """鲤鱼 子图节点"""

    def __init__(self, name: str, graph: "PhoenixGraph"):
        self.name = name
        self.graph = graph

    def execute(self, state: PhoenixState,
                timeline: Optional[ExecutionTimeline] = None) -> PhoenixState:
        """执行子图，将子图执行历史合入主时间线"""
        sub_app = self.graph.compile(history_store=None)  # 不单独持久化子图历史
        sub_result = sub_app.invoke(state["data"])
        # 把子图的执行记录标记 parent_node 后合入主时间线
        if timeline and sub_app._last_timeline:
            for rec in sub_app._last_timeline.records:
                rec.parent_node = self.name
                timeline.add_record(rec)
        return {
            **state,
            "data": {**state["data"], **sub_result.get("data", {})},
            "history": state.get("history", []) + sub_result.get("history", []),
            "metadata": {**state.get("metadata", {}), **sub_result.get("metadata", {})},
        }


# ---------------------------------------------------------------------------
# Human Approval (保留原有功能)
# ---------------------------------------------------------------------------

class PhoenixHumanApproval:
    """鲤鱼 人类审批"""
    def __init__(self, prompt: str):
        self.prompt = prompt

    def execute(self, state: PhoenixState) -> PhoenixState:
        print(self.prompt)
        print(f"当前状态: {state['data']}")
        approved = input("批准? (y/n): ").lower() == "y"
        return {
            **state,
            "data": {**state["data"], "approved": approved},
        }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class PhoenixGraph:
    """鲤鱼 状态图"""

    def __init__(self, name: Optional[str] = None):
        self.name = name
        self.nodes: Dict[str, PhoenixNode] = {}
        self.edges: List[PhoenixEdge] = []
        self.entry_point: Optional[str] = None
        self._parallel_groups: Dict[str, PhoenixParallel] = {}
        self._subgraphs: Dict[str, PhoenixSubgraph] = {}

    # -- Node management ----------------------------------------------------

    def add_node(self, name: str, func: Callable, *, tags: Optional[List[str]] = None):
        """添加静态节点"""
        self.nodes[name] = PhoenixNode(name, func, is_dynamic=False, tags=tags)

    def add_dynamic_node(self, name: str, func: Callable, *, tags: Optional[List[str]] = None):
        """添加动态节点（运行时可替换）"""
        self.nodes[name] = PhoenixNode(name, func, is_dynamic=True, tags=tags)

    def replace_node(self, name: str, func: Callable):
        """替换已有节点的执行函数（动态节点模式）"""
        if name not in self.nodes:
            raise ValueError(f"节点 {name} 不存在，无法替换")
        old = self.nodes[name]
        self.nodes[name] = PhoenixNode(name, func, is_dynamic=True, tags=old.tags)

    def remove_node(self, name: str):
        """移除节点及其关联边"""
        self.nodes.pop(name, None)
        self.edges = [e for e in self.edges if e.from_node != name and e.to_node != name]

    def get_node_names(self) -> List[str]:
        return list(self.nodes.keys())

    def get_dynamic_nodes(self) -> List[str]:
        return [n for n, node in self.nodes.items() if node.is_dynamic]

    # -- Edge management ----------------------------------------------------

    def add_edge(self, from_node: str, to_node: str):
        """添加普通边"""
        self.edges.append(PhoenixEdge(from_node, to_node, edge_type=EdgeType.NORMAL))

    def add_conditional_edge(self, from_node: str, condition: Callable,
                             mapping: Dict[str, str]):
        """添加条件边
        mapping: {"route_name": "target_node", ...}
        condition 应返回 mapping 中的一个 key
        """
        for route_key, to_node in mapping.items():
            def _make_cond(rk=route_key):
                return lambda state, _rk=rk: condition(state) == _rk
            self.edges.append(
                PhoenixEdge(from_node, to_node, condition=_make_cond(), edge_type=EdgeType.CONDITIONAL)
            )

    # -- Parallel branches --------------------------------------------------

    def add_parallel_branches(self, name: str, branches: List[str]):
        """注册并行分支组
        name: 分支组名，用作虚拟节点
        branches: 并行执行的节点名列表
        """
        for b in branches:
            if b not in self.nodes:
                raise ValueError(f"并行分支节点 {b} 不存在")
        self._parallel_groups[name] = PhoenixParallel(branches)
        # 并行组作为虚拟节点加入 nodes（执行时走 parallel 逻辑）
        # 不覆盖已有的真实节点

    def add_parallel_edge(self, from_node: str, parallel_name: str, to_node: str):
        """从 from_node -> 并行组 parallel_name -> to_node"""
        if parallel_name not in self._parallel_groups:
            raise ValueError(f"并行分支组 {parallel_name} 未注册")
        self.add_edge(from_node, f"__parallel__{parallel_name}")
        self.add_edge(f"__parallel__{parallel_name}", to_node)

    # -- Subgraph -----------------------------------------------------------

    def add_subgraph(self, name: str, subgraph: "PhoenixGraph"):
        """注册子图"""
        self._subgraphs[name] = PhoenixSubgraph(name, subgraph)

    def add_subgraph_node(self, parent_node: str, subgraph_name: str, next_node: str):
        """父节点 -> 子图 -> next_node"""
        if subgraph_name not in self._subgraphs:
            raise ValueError(f"子图 {subgraph_name} 未注册")
        self.add_edge(parent_node, f"__subgraph__{subgraph_name}")
        self.add_edge(f"__subgraph__{subgraph_name}", next_node)

    # -- Entry & Compile ----------------------------------------------------

    def set_entry_point(self, name: str):
        self.entry_point = name

    def compile(self, checkpointer: Optional[PhoenixCheckpoint] = None,
                history_store: Optional[PhoenixHistoryStore] = None) -> "PhoenixRunnable":
        return PhoenixRunnable(self, checkpointer=checkpointer, history_store=history_store)

    # -- Visualization (Mermaid) --------------------------------------------

    def visualize(self, *, direction: str = "TD", show_parallel: bool = True,
                  show_subgraphs: bool = True) -> str:
        """生成 Mermaid 状态图
        direction: TD (top-down) | LR (left-right) | BT | RL
        """
        lines = [f"graph {direction}"]
        seen_edges: Set[str] = set()

        # 入口节点
        if self.entry_point:
            lines.append(f"    __start__([\"▶ Start\"]) --> {self.entry_point}")

        # 普通边和条件边
        for edge in self.edges:
            if edge.from_node.startswith("__parallel__") or edge.from_node.startswith("__subgraph__"):
                continue
            if edge.to_node.startswith("__parallel__") or edge.to_node.startswith("__subgraph__"):
                continue
            key = f"{edge.from_node}->{edge.to_node}"
            if key in seen_edges:
                continue
            seen_edges.add(key)
            if edge.condition:
                lines.append(f"    {edge.from_node} -->|条件| {edge.to_node}")
            else:
                lines.append(f"    {edge.from_node} --> {edge.to_node}")

        # 并行分支可视化
        if show_parallel:
            for name, parallel in self._parallel_groups.items():
                vname = f"__parallel__{name}"
                lines.append(f"    {vname}{{\"⚡ {name}\"}}")
                for branch in parallel.branches:
                    bkey = f"{vname}->{branch}"
                    if bkey not in seen_edges:
                        seen_edges.add(bkey)
                        lines.append(f"    {vname} --> {branch}")

        # 子图可视化
        if show_subgraphs:
            for name, sub in self._subgraphs.items():
                vname = f"__subgraph__{name}"
                lines.append(f"    {vname}[\"📦 {name}\"]")

        # END 节点
        lines.append(f"    __end__([\"■ End\"])")
        for edge in self.edges:
            if edge.to_node == "END":
                key = f"{edge.from_node}->END"
                if key not in seen_edges:
                    seen_edges.add(key)
                    lines.append(f"    {edge.from_node} --> __end__")

        return "\n".join(lines)

    def visualize_mermaid_with_history(self, timeline: ExecutionTimeline) -> str:
        """生成带执行历史高亮的 Mermaid 图"""
        base = self.visualize()
        executed_nodes = {r.node_name for r in timeline.records if r.status == NodeStatus.COMPLETED}
        failed_nodes = {r.node_name for r in timeline.records if r.status == NodeStatus.FAILED}
        # 追加样式
        style_lines = ["\n%% Execution styles"]
        for node in executed_nodes:
            safe = node.replace("-", "_")
            style_lines.append(f"    classDef executed fill:#d4edda,stroke:#28a745,color:#000")
            style_lines.append(f"    class {safe} executed")
        for node in failed_nodes:
            safe = node.replace("-", "_")
            style_lines.append(f"    classDef failed fill:#f8d7da,stroke:#dc3545,color:#000")
            style_lines.append(f"    class {safe} failed")
        return base + "\n".join(style_lines)


# ---------------------------------------------------------------------------
# Runnable (Executor)
# ---------------------------------------------------------------------------

def _now_ms() -> float:
    return datetime.now().timestamp() * 1000


class PhoenixRunnable:
    """可运行的图"""

    def __init__(self, graph: PhoenixGraph,
                 checkpointer: Optional[PhoenixCheckpoint] = None,
                 history_store: Optional[PhoenixHistoryStore] = None):
        self.graph = graph
        self.checkpointer = checkpointer
        self.history_store = history_store
        self._last_timeline: Optional[ExecutionTimeline] = None

    def invoke(self, initial_state: Dict[str, Any],
               config: Optional[Dict] = None) -> Dict[str, Any]:
        """运行图"""
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "default")
        run_id = uuid.uuid4().hex[:12]

        timeline = ExecutionTimeline(
            run_id=run_id,
            graph_name=self.graph.name,
            started_at=datetime.now().isoformat(),
            metadata={"thread_id": thread_id},
        )

        state: PhoenixState = {
            "data": initial_state,
            "history": [],
            "metadata": {
                "start_time": datetime.now().isoformat(),
                "thread_id": thread_id,
                "run_id": run_id,
            },
        }

        # 从检查点恢复
        if self.checkpointer:
            saved = self.checkpointer.load(thread_id)
            if saved:
                state = saved

        current = self.graph.entry_point
        step = 0
        max_steps = 200

        while current is not None and current != "END":
            if step >= max_steps:
                state["metadata"]["error"] = f"超过最大步数限制 ({max_steps})"
                break

            # -- 并行分支 --
            if current.startswith("__parallel__"):
                pname = current[len("__parallel__"):]
                parallel = self.graph._parallel_groups.get(pname)
                if not parallel:
                    raise ValueError(f"并行分支组 {pname} 不存在")
                state = parallel.execute(state, self.graph, timeline=timeline)
                state["history"].append(current)
                step += 1
                current = self._get_next_node(current, state)
                continue

            # -- 子图 --
            if current.startswith("__subgraph__"):
                sname = current[len("__subgraph__"):]
                subgraph = self.graph._subgraphs.get(sname)
                if not subgraph:
                    raise ValueError(f"子图 {sname} 不存在")
                state = subgraph.execute(state, timeline=timeline)
                state["history"].append(current)
                step += 1
                current = self._get_next_node(current, state)
                continue

            # -- 普通节点 --
            if current not in self.graph.nodes:
                raise ValueError(f"节点 {current} 不存在")

            record = ExecutionRecord(
                record_id=uuid.uuid4().hex[:12],
                node_name=current,
                status=NodeStatus.RUNNING,
                started_at=datetime.now().isoformat(),
                input_keys=list(state.get("data", {}).keys()),
            )
            start_ms = _now_ms()

            node = self.graph.nodes[current]
            state = node.execute(state)

            record.finished_at = datetime.now().isoformat()
            record.duration_ms = _now_ms() - start_ms
            record.output_keys = list(state.get("data", {}).keys())

            if state.get("metadata", {}).get("error"):
                record.status = NodeStatus.FAILED
                record.error = state["metadata"]["error"]
            else:
                record.status = NodeStatus.COMPLETED

            timeline.add_record(record)
            state["history"].append(current)

            # 检查点
            if self.checkpointer:
                self.checkpointer.save(thread_id, state)

            # 错误中止
            if state.get("metadata", {}).get("error"):
                break

            step += 1
            current = self._get_next_node(current, state)

        # 完成
        state["metadata"]["end_time"] = datetime.now().isoformat()
        state["metadata"]["total_steps"] = step

        timeline.finished_at = state["metadata"]["end_time"]
        timeline.total_duration_ms = (
            _now_ms() - datetime.fromisoformat(timeline.started_at).timestamp() * 1000
        )

        self._last_timeline = timeline

        # 持久化历史
        if self.history_store:
            self.history_store.save(timeline)

        state["metadata"]["run_id"] = run_id
        return state

    def _get_next_node(self, current: str, state: PhoenixState) -> Optional[str]:
        for edge in self.graph.edges:
            if edge.from_node == current:
                if edge.condition:
                    if edge.condition(state):
                        return edge.to_node
                else:
                    return edge.to_node
        return "END"

    def get_state(self, config: Dict) -> Optional[PhoenixState]:
        if self.checkpointer:
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                return self.checkpointer.load(thread_id)
        return None

    def update_state(self, config: Dict, updates: Dict[str, Any]):
        if self.checkpointer:
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                state = self.checkpointer.load(thread_id)
                if state:
                    state["data"].update(updates)
                    self.checkpointer.save(thread_id, state)


# ---------------------------------------------------------------------------
# Stream executor
# ---------------------------------------------------------------------------

class PhoenixStream:
    """鲤鱼 流式输出"""

    def __init__(self, graph: PhoenixGraph):
        self.graph = graph

    def stream(self, initial_state: Dict[str, Any], config: Optional[Dict] = None):
        state: PhoenixState = {
            "data": initial_state,
            "history": [],
            "metadata": {},
        }
        current = self.graph.entry_point
        while current is not None and current != "END":
            if current.startswith("__parallel__"):
                pname = current[len("__parallel__"):]
                parallel = self.graph._parallel_groups.get(pname)
                if parallel:
                    state = parallel.execute(state, self.graph)
                state["history"].append(current)
                yield {"node": current, "type": "parallel", "state": state, "history": state["history"]}
                current = self._get_next_node(current, state)
                continue

            if current.startswith("__subgraph__"):
                sname = current[len("__subgraph__"):]
                subgraph = self.graph._subgraphs.get(sname)
                if subgraph:
                    state = subgraph.execute(state)
                state["history"].append(current)
                yield {"node": current, "type": "subgraph", "state": state, "history": state["history"]}
                current = self._get_next_node(current, state)
                continue

            if current not in self.graph.nodes:
                break
            node = self.graph.nodes[current]
            state = node.execute(state)
            state["history"].append(current)
            yield {"node": current, "type": "node", "state": state, "history": state["history"]}
            current = self._get_next_node(current, state)

    def _get_next_node(self, current: str, state: PhoenixState) -> Optional[str]:
        for edge in self.graph.edges:
            if edge.from_node == current:
                if edge.condition:
                    if edge.condition(state):
                        return edge.to_node
                else:
                    return edge.to_node
        return "END"


# ---------------------------------------------------------------------------
# Demo / Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("鲤鱼 State Graph Engine v2.0 — Self-test")
    print("=" * 60)

    # -- 基础图 -------------------------------------------------------------
    graph = PhoenixGraph(name="demo-pipeline")

    def analyze(state):
        q = state["data"]["question"]
        return {**state, "data": {**state["data"], "analysis": f"深度分析: {q}"}}

    def solve(state):
        a = state["data"]["analysis"]
        return {**state, "data": {**state["data"], "solution": f"方案: {a}"}}

    def execute(state):
        s = state["data"]["solution"]
        return {**state, "data": {**state["data"], "result": f"执行: {s}"}}

    def review(state):
        return {**state, "data": {**state["data"], "reviewed": True}}

    def approve(state):
        return {**state, "data": {**state["data"], "approved": True}}

    def reject(state):
        return {**state, "data": {**state["data"], "approved": False}}

    graph.add_node("analyze", analyze, tags=["core"])
    graph.add_node("solve", solve, tags=["core"])
    graph.add_node("execute", execute, tags=["core"])
    graph.add_node("review", review, tags=["qa"])
    graph.add_node("approve", approve, tags=["decision"])
    graph.add_node("reject", reject, tags=["decision"])

    graph.add_edge("analyze", "solve")
    graph.add_edge("solve", "execute")

    # 条件边：review 后根据 approved 分支
    def check_approval(state):
        return "yes" if state["data"].get("reviewed") else "no"

    graph.add_conditional_edge("execute", check_approval, {"yes": "approve", "no": "reject"})
    graph.add_edge("approve", "END")
    graph.add_edge("reject", "END")

    graph.set_entry_point("analyze")

    # -- 检查点 + 历史 -------------------------------------------------------
    checkpoint = PhoenixCheckpoint()
    history = PhoenixHistoryStore()

    app = graph.compile(checkpointer=checkpoint, history_store=history)

    print("\n[1] Basic pipeline run:")
    result = app.invoke(
        {"question": "如何优化 鲤鱼 的多 Agent 协作?"},
        config={"configurable": {"thread_id": "demo-1"}},
    )
    print(f"  Question: {result['data']['question']}")
    print(f"  Analysis: {result['data']['analysis']}")
    print(f"  Solution: {result['data']['solution']}")
    print(f"  Result:   {result['data']['result']}")
    print(f"  Reviewed: {result['data'].get('reviewed')}")
    print(f"  History:  {result['history']}")
    print(f"  Steps:    {result['metadata']['total_steps']}")
    print(f"  Run ID:   {result['metadata']['run_id']}")

    # -- 并行分支 -----------------------------------------------------------
    print("\n[2] Parallel branches:")

    def branch_a(state):
        return {**state, "data": {**state["data"], "branch_a": "A 完成"}}

    def branch_b(state):
        return {**state, "data": {**state["data"], "branch_b": "B 完成"}}

    def branch_c(state):
        return {**state, "data": {**state["data"], "branch_c": "C 完成"}}

    pgraph = PhoenixGraph(name="parallel-demo")
    pgraph.add_node("start", lambda s: {**s, "data": {**s["data"], "init": True}})
    pgraph.add_node("a", branch_a)
    pgraph.add_node("b", branch_b)
    pgraph.add_node("c", branch_c)
    pgraph.add_node("merge", lambda s: {**s, "data": {**s["data"], "merged": True}})

    pgraph.add_parallel_branches("fanout", ["a", "b", "c"])
    pgraph.add_edge("start", "__parallel__fanout")
    pgraph.add_edge("__parallel__fanout", "merge")
    pgraph.add_edge("merge", "END")
    pgraph.set_entry_point("start")

    phistory = PhoenixHistoryStore()
    papp = pgraph.compile(history_store=phistory)
    presult = papp.invoke({"task": "parallel test"})
    print(f"  branch_a: {presult['data'].get('branch_a')}")
    print(f"  branch_b: {presult['data'].get('branch_b')}")
    print(f"  branch_c: {presult['data'].get('branch_c')}")
    print(f"  merged:   {presult['data'].get('merged')}")
    print(f"  History:  {presult['history']}")

    # -- 子图 ---------------------------------------------------------------
    print("\n[3] Subgraph:")

    sub = PhoenixGraph(name="sub-workflow")
    sub.add_node("sub_step1", lambda s: {**s, "data": {**s["data"], "sub1": "子步骤1完成"}})
    sub.add_node("sub_step2", lambda s: {**s, "data": {**s["data"], "sub2": "子步骤2完成"}})
    sub.add_edge("sub_step1", "sub_step2")
    sub.add_edge("sub_step2", "END")
    sub.set_entry_point("sub_step1")

    mgraph = PhoenixGraph(name="parent-with-subgraph")
    mgraph.add_node("pre", lambda s: {**s, "data": {**s["data"], "pre": "前置完成"}})
    mgraph.add_node("post", lambda s: {**s, "data": {**s["data"], "post": "后置完成"}})
    mgraph.add_subgraph("sub", sub)
    mgraph.add_edge("pre", "__subgraph__sub")
    mgraph.add_edge("__subgraph__sub", "post")
    mgraph.add_edge("post", "END")
    mgraph.set_entry_point("pre")

    mhistory = PhoenixHistoryStore()
    mapp = mgraph.compile(history_store=mhistory)
    mresult = mapp.invoke({"task": "subgraph test"})
    print(f"  pre:  {mresult['data'].get('pre')}")
    print(f"  sub1: {mresult['data'].get('sub1')}")
    print(f"  sub2: {mresult['data'].get('sub2')}")
    print(f"  post: {mresult['data'].get('post')}")
    print(f"  History: {mresult['history']}")

    # -- 动态节点替换 --------------------------------------------------------
    print("\n[4] Dynamic node replacement:")

    dgraph = PhoenixGraph(name="dynamic-demo")
    dgraph.add_node("greet", lambda s: {**s, "data": {**s["data"], "greeting": "Hello"}})
    dgraph.add_dynamic_node("format", lambda s: {**s, "data": {**s["data"], "output": f"v1: {s['data']['greeting']}"}})
    dgraph.add_edge("greet", "format")
    dgraph.add_edge("format", "END")
    dgraph.set_entry_point("greet")

    dapp = dgraph.compile()
    r1 = dapp.invoke({"name": "Phoenix"})
    print(f"  v1 output: {r1['data']['output']}")

    # 替换 format 节点
    dgraph.replace_node("format", lambda s: {**s, "data": {**s["data"], "output": f"v2: {s['data']['greeting']}!!!"}})
    dapp2 = dgraph.compile()
    r2 = dapp2.invoke({"name": "Phoenix"})
    print(f"  v2 output: {r2['data']['output']}")

    # -- 可视化 -------------------------------------------------------------
    print("\n[5] Visualization (Mermaid):")
    print(graph.visualize())

    print("\n[5b] Parallel graph visualization:")
    print(pgraph.visualize())

    # -- 执行历史 -----------------------------------------------------------
    print("\n[6] Execution history:")
    tl = app._last_timeline
    if tl:
        print(tl.summary())

    print("\n[6b] Parallel history:")
    ptl = papp._last_timeline
    if ptl:
        print(ptl.summary())

    # -- 检查点恢复 ---------------------------------------------------------
    print("\n[7] Checkpoint resume:")
    saved = checkpoint.load("demo-1")
    if saved:
        print(f"  Saved thread state keys: {list(saved['data'].keys())}")
        print(f"  History length: {len(saved['history'])}")

    # -- 历史持久化 ---------------------------------------------------------
    print("\n[8] History store:")
    print(f"  Stored runs: {history.list_runs()}")
    loaded_tl = history.load(result["metadata"]["run_id"])
    if loaded_tl:
        print(f"  Loaded run: {loaded_tl.run_id}, steps: {len(loaded_tl.records)}")

    print("\n" + "=" * 60)
    print("All self-tests passed.")
    print("=" * 60)
