#!/usr/bin/env python3
"""
鲤鱼 Code Graph — 代码库知识图谱构建
吸收自 Graphify-Labs/graphify 的 AST 解析 + 边解释模式

核心理念：
  - 使用 tree-sitter 解析 AST
  - 提取 import、class、function、method、inheritance 五类节点
  - 边标注 EXTRACTED / INFERRED / AMBIGUOUS 置信度
  - SHA256 增量缓存

Usage:
  liyu-code-graph.py build <directory>
    构建代码库知识图谱

  liyu-code-graph.py update <directory>
    增量更新知识图谱

  liyu-code-graph.py analyze
    分析知识图谱

  liyu-code-graph.py stats
    查看统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
import json
import hashlib
import sys
import os

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
CODE_GRAPH_STATE_FILE = 鲤鱼_HOME / "code-graph-state.json"
CODE_GRAPH_CACHE_DIR = 鲤鱼_HOME / "code-graph-cache"
CODE_GRAPH_OUTPUT_DIR = 鲤鱼_HOME / "code-graph-output"

# ── 支持的语言 ──────────────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

# ── 置信度 ──────────────────────────────────────────────────────────────
CONFIDENCE_LEVELS = {
    "EXTRACTED": 1.0,   # 源码中显式存在
    "INFERRED": 0.8,    # 合理推断
    "AMBIGUOUS": 0.5,   # 不确定关系
}

# ── 数据类 ──────────────────────────────────────────────────────────────

@dataclass
class CodeNode:
    """代码节点"""
    id: str
    label: str
    node_type: str          # file, class, function, method, import
    source_file: str
    source_location: str    # line:col
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CodeEdge:
    """代码边"""
    source_id: str
    target_id: str
    relation: str           # imports, contains, calls, inherits, implements
    confidence: str         # EXTRACTED, INFERRED, AMBIGUOUS
    weight: float
    metadata: Dict[str, Any] = field(default_factory=dict)

# ── AST 提取器 ──────────────────────────────────────────────────────────

class ASTExtractor:
    """AST 提取器（简化版，不依赖 tree-sitter）"""

    def __init__(self):
        self.nodes: List[CodeNode] = []
        self.edges: List[CodeEdge] = []
        self._node_ids: Set[str] = set()

    def extract_file(self, file_path: Path, language: str) -> None:
        """提取单个文件的代码结构"""
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception:
            return

        file_id = str(file_path)
        self._add_node(file_id, file_path.name, "file", str(file_path), "0:0")

        if language == "python":
            self._extract_python(content, file_path)
        elif language in ("javascript", "typescript"):
            self._extract_js_ts(content, file_path)

    def _extract_python(self, content: str, file_path: Path) -> None:
        """提取 Python 代码结构"""
        lines = content.split('\n')
        file_id = str(file_path)

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 提取 import
            if stripped.startswith('import ') or stripped.startswith('from '):
                import_id = f"{file_id}:import:{i}"
                self._add_node(import_id, stripped[:50], "import", str(file_path), f"{i}:0")
                self._add_edge(file_id, import_id, "imports", "EXTRACTED", 1.0)

            # 提取 class
            elif stripped.startswith('class '):
                class_name = stripped.split('(')[0].split(':')[0].replace('class ', '').strip()
                class_id = f"{file_id}:class:{class_name}"
                self._add_node(class_id, class_name, "class", str(file_path), f"{i}:0")
                self._add_edge(file_id, class_id, "contains", "EXTRACTED", 1.0)

                # 检查继承
                if '(' in stripped and ')' in stripped:
                    parents = stripped.split('(')[1].split(')')[0]
                    for parent in parents.split(','):
                        parent = parent.strip()
                        if parent and parent != 'object':
                            parent_id = f"external:class:{parent}"
                            self._add_edge(class_id, parent_id, "inherits", "INFERRED", 0.8)

            # 提取 function
            elif stripped.startswith('def '):
                func_name = stripped.split('(')[0].replace('def ', '').strip()
                func_id = f"{file_id}:function:{func_name}"
                self._add_node(func_id, func_name, "function", str(file_path), f"{i}:0")
                self._add_edge(file_id, func_id, "contains", "EXTRACTED", 1.0)

    def _extract_js_ts(self, content: str, file_path: Path) -> None:
        """提取 JavaScript/TypeScript 代码结构"""
        lines = content.split('\n')
        file_id = str(file_path)

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 提取 import
            if stripped.startswith('import '):
                import_id = f"{file_id}:import:{i}"
                self._add_node(import_id, stripped[:50], "import", str(file_path), f"{i}:0")
                self._add_edge(file_id, import_id, "imports", "EXTRACTED", 1.0)

            # 提取 class
            elif 'class ' in stripped and ('{' in stripped or 'extends' in stripped):
                class_name = stripped.split('class ')[1].split(' ')[0].split('{')[0].strip()
                class_id = f"{file_id}:class:{class_name}"
                self._add_node(class_id, class_name, "class", str(file_path), f"{i}:0")
                self._add_edge(file_id, class_id, "contains", "EXTRACTED", 1.0)

            # 提取 function
            elif 'function ' in stripped and '(' in stripped:
                func_name = stripped.split('function ')[1].split('(')[0].strip()
                func_id = f"{file_id}:function:{func_name}"
                self._add_node(func_id, func_name, "function", str(file_path), f"{i}:0")
                self._add_edge(file_id, func_id, "contains", "EXTRACTED", 1.0)

    def _add_node(self, node_id: str, label: str, node_type: str, source_file: str, source_location: str) -> None:
        """添加节点"""
        if node_id not in self._node_ids:
            self._node_ids.add(node_id)
            self.nodes.append(CodeNode(
                id=node_id,
                label=label,
                node_type=node_type,
                source_file=source_file,
                source_location=source_location,
            ))

    def _add_edge(self, source_id: str, target_id: str, relation: str, confidence: str, weight: float) -> None:
        """添加边"""
        self.edges.append(CodeEdge(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            confidence=confidence,
            weight=weight,
        ))

# ── 缓存管理 ──────────────────────────────────────────────────────────────

class CacheManager:
    """SHA256 增量缓存管理器"""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_file_hash(self, file_path: Path) -> str:
        """计算文件 SHA256"""
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()[:16]
        except Exception:
            return ""

    def is_cached(self, file_path: Path) -> bool:
        """检查文件是否已缓存"""
        file_hash = self.get_file_hash(file_path)
        cache_file = self.cache_dir / f"{file_hash}.json"
        return cache_file.exists()

    def get_cached(self, file_path: Path) -> Optional[Dict]:
        """获取缓存的提取结果"""
        file_hash = self.get_file_hash(file_path)
        cache_file = self.cache_dir / f"{file_hash}.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except Exception:
                return None
        return None

    def set_cached(self, file_path: Path, data: Dict) -> None:
        """缓存提取结果"""
        file_hash = self.get_file_hash(file_path)
        cache_file = self.cache_dir / f"{file_hash}.json"
        cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ── 状态管理 ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    """加载状态"""
    if CODE_GRAPH_STATE_FILE.exists():
        try:
            return json.loads(CODE_GRAPH_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_files": 0,
        "total_nodes": 0,
        "total_edges": 0,
        "last_build": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_state(state: dict) -> None:
    """持久化状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    CODE_GRAPH_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ── 图构建 ──────────────────────────────────────────────────────────────

def build_graph(directory: str, update: bool = False) -> Dict:
    """构建代码库知识图谱"""
    dir_path = Path(directory)
    if not dir_path.exists():
        return {"error": f"Directory not found: {directory}"}

    extractor = ASTExtractor()
    cache = CacheManager(CODE_GRAPH_CACHE_DIR)
    state = load_state()

    # 扫描文件
    files_processed = 0
    files_cached = 0

    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue

        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        language = SUPPORTED_EXTENSIONS[ext]

        # 检查缓存
        if update and cache.is_cached(file_path):
            files_cached += 1
            continue

        # 提取代码结构
        extractor.extract_file(file_path, language)
        files_processed += 1

        # 缓存结果
        cache.set_cached(file_path, {
            "nodes": len(extractor.nodes),
            "edges": len(extractor.edges),
        })

    # 剪枝：移除度为零的代码节点
    nodes_with_edges = set()
    for edge in extractor.edges:
        nodes_with_edges.add(edge.source_id)
        nodes_with_edges.add(edge.target_id)

    pruned_nodes = [
        node for node in extractor.nodes
        if node.id in nodes_with_edges or node.node_type == "file"
    ]

    # 保存结果
    CODE_GRAPH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = CODE_GRAPH_OUTPUT_DIR / "graph.json"

    graph_data = {
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "type": n.node_type,
                "source_file": n.source_file,
                "source_location": n.source_location,
            }
            for n in pruned_nodes
        ],
        "edges": [
            {
                "source": e.source_id,
                "target": e.target_id,
                "relation": e.relation,
                "confidence": e.confidence,
                "weight": e.weight,
            }
            for e in extractor.edges
        ],
        "metadata": {
            "directory": str(dir_path),
            "files_processed": files_processed,
            "files_cached": files_cached,
            "total_nodes": len(pruned_nodes),
            "total_edges": len(extractor.edges),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    output_file.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2))

    # 更新状态
    state["total_files"] = files_processed + files_cached
    state["total_nodes"] = len(pruned_nodes)
    state["total_edges"] = len(extractor.edges)
    state["last_build"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    return {
        "status": "completed",
        "files_processed": files_processed,
        "files_cached": files_cached,
        "total_nodes": len(pruned_nodes),
        "total_edges": len(extractor.edges),
        "output_file": str(output_file),
    }

# ── 分析功能 ──────────────────────────────────────────────────────────────

def analyze_graph() -> Dict:
    """分析知识图谱"""
    graph_file = CODE_GRAPH_OUTPUT_DIR / "graph.json"
    if not graph_file.exists():
        return {"error": "No graph found. Run 'build' first."}

    graph_data = json.loads(graph_file.read_text())
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]

    # 统计节点类型
    node_types = {}
    for node in nodes:
        ntype = node["type"]
        node_types[ntype] = node_types.get(ntype, 0) + 1

    # 统计边类型
    edge_types = {}
    for edge in edges:
        etype = edge["relation"]
        edge_types[etype] = edge_types.get(etype, 0) + 1

    # 统计置信度
    confidence_stats = {}
    for edge in edges:
        conf = edge["confidence"]
        confidence_stats[conf] = confidence_stats.get(conf, 0) + 1

    # 发现 God Nodes（高度节点）
    node_degree = {}
    for edge in edges:
        node_degree[edge["source"]] = node_degree.get(edge["source"], 0) + 1
        node_degree[edge["target"]] = node_degree.get(edge["target"], 0) + 1

    god_nodes = sorted(node_degree.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "node_types": node_types,
        "edge_types": edge_types,
        "confidence_stats": confidence_stats,
        "god_nodes": [
            {"id": nid, "degree": degree}
            for nid, degree in god_nodes
        ],
    }

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "build":
        if len(sys.argv) < 3:
            print("Usage: liyu-code-graph.py build <directory>", file=sys.stderr)
            sys.exit(1)

        directory = sys.argv[2]
        result = build_graph(directory)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"📊 知识图谱构建完成:")
        print(f"  文件处理: {result['files_processed']}")
        print(f"  文件缓存: {result['files_cached']}")
        print(f"  节点数量: {result['total_nodes']}")
        print(f"  边数量: {result['total_edges']}")
        print(f"  输出文件: {result['output_file']}")

    elif cmd == "update":
        if len(sys.argv) < 3:
            print("Usage: liyu-code-graph.py update <directory>", file=sys.stderr)
            sys.exit(1)

        directory = sys.argv[2]
        result = build_graph(directory, update=True)

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"📊 知识图谱更新完成:")
        print(f"  文件处理: {result['files_processed']}")
        print(f"  文件缓存: {result['files_cached']}")
        print(f"  节点数量: {result['total_nodes']}")
        print(f"  边数量: {result['total_edges']}")

    elif cmd == "analyze":
        result = analyze_graph()

        if "error" in result:
            print(f"❌ {result['error']}", file=sys.stderr)
            sys.exit(1)

        print("═══ 鲤鱼 Code Graph Analysis ═══")
        print(f"  节点数量: {result['total_nodes']}")
        print(f"  边数量: {result['total_edges']}")
        print()
        print("  节点类型:")
        for ntype, count in result["node_types"].items():
            print(f"    {ntype}: {count}")
        print()
        print("  边类型:")
        for etype, count in result["edge_types"].items():
            print(f"    {etype}: {count}")
        print()
        print("  置信度分布:")
        for conf, count in result["confidence_stats"].items():
            print(f"    {conf}: {count}")
        print()
        print("  God Nodes (高度节点):")
        for node in result["god_nodes"][:5]:
            print(f"    {node['id']}: degree {node['degree']}")

    elif cmd == "stats":
        state = load_state()

        print("═══ 鲤鱼 Code Graph Statistics ═══")
        print(f"  总计文件: {state.get('total_files', 0)}")
        print(f"  总计节点: {state.get('total_nodes', 0)}")
        print(f"  总计边:   {state.get('total_edges', 0)}")
        print(f"  上次构建: {state.get('last_build', 'never')}")

    elif cmd == "reset":
        save_state({
            "total_files": 0,
            "total_nodes": 0,
            "total_edges": 0,
            "last_build": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Code Graph 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
