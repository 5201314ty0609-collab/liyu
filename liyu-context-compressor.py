#!/usr/bin/env python3
"""
鲤鱼 Context Compressor — 渐进式披露 + 熵感知压缩
吸收自 claude-mem 渐进式披露 + headroom 熵感知压缩

核心理念：
  - 让 Agent 自己决定需要什么，而不是系统预判
  - 索引表显示每条记录的 token 成本，Agent 可以做 ROI 决策
  - 高熵内容（UUID、hash、API key）自动保留

Usage:
  liyu-context-compressor.py prime-index
    生成渐进式披露索引

  liyu-context-compressor.py get <memory_id>
    获取单条记忆详情

  liyu-context-compressor.py compress <text>
    压缩文本

  liyu-context-compressor.py stats
    查看压缩统计
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import re
import sys
import math
from collections import Counter

# ── Paths ──────────────────────────────────────────────────────────────────
鲤鱼_HOME = Path.home() / ".claude" / "liyu"
COMPRESSOR_STATE_FILE = 鲤鱼_HOME / "context-compressor-state.json"

# ── 记忆类型图标 ──────────────────────────────────────────────────────────
MEMORY_TYPE_ICONS = {
    "session-request": "🎯",
    "gotcha": "🔴",
    "problem-solution": "🟡",
    "how-it-works": "🔵",
    "what-changed": "🟢",
    "discovery": "🟣",
    "why-it-exists": "🟠",
    "decision": "🟤",
    "trade-off": "⚖️",
    "episodic": "📅",
    "semantic": "📚",
    "procedural": "🔧",
    "relational": "🔗",
}

# ── Token 估算 ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """估算 token 数（中文约 1.5 字/token，英文约 4 字符/token）"""
    cn_chars = len(re.findall(r'[一-鿿]', text))
    en_chars = len(text) - cn_chars
    return int(cn_chars / 1.5 + en_chars / 4)

# ── 熵计算 ──────────────────────────────────────────────────────────────

def compute_entropy(value: str) -> float:
    """计算归一化 Shannon 熵"""
    if len(value) < 8:
        return 0.0

    freq = Counter(value)
    total = len(value)

    entropy = -sum(
        (count / total) * math.log2(count / total)
        for count in freq.values()
    )

    max_entropy = math.log2(len(freq)) if len(freq) > 1 else 1
    normalized = entropy / max_entropy if max_entropy > 0 else 0

    return normalized

def is_high_entropy(value: str, threshold: float = 0.85) -> bool:
    """判断是否为高熵内容（UUID、hash、API key）"""
    return compute_entropy(value) >= threshold

# ── 渐进式披露索引 ──────────────────────────────────────────────────────

@dataclass
class MemoryIndex:
    """记忆索引条目"""
    id: str
    timestamp: str
    memory_type: str
    title: str
    token_cost: int
    importance: float

def generate_progressive_index(memories: List[Dict], max_tokens: int = 800) -> str:
    """生成渐进式披露索引

    Args:
        memories: 记忆列表
        max_tokens: 索引最大 token 数

    Returns:
        Markdown 格式的索引表
    """
    # 按时间排序（最新在前）
    sorted_memories = sorted(
        memories,
        key=lambda m: m.get("timestamp", m.get("created_at", "")),
        reverse=True
    )

    # 生成索引条目
    index_entries = []
    tokens_used = 0

    for memory in sorted_memories:
        memory_id = memory.get("id", "unknown")
        timestamp = memory.get("timestamp", memory.get("created_at", ""))
        memory_type = memory.get("type", "semantic")
        content = memory.get("content", memory.get("summary", ""))
        importance = memory.get("importance", 0.5)

        # 生成标题（前 20 字符）
        title = content[:20].replace("\n", " ")
        if len(content) > 20:
            title += "..."

        # 估算 token 成本
        token_cost = estimate_tokens(content)

        # 获取图标
        icon = MEMORY_TYPE_ICONS.get(memory_type, "📝")

        # 格式化时间
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            time_str = dt.strftime("%m-%d %H:%M")
        except Exception:
            time_str = "unknown"

        # 生成索引行
        index_line = f"| {memory_id[:8]} | {time_str} | {icon} | {title} | ~{token_cost} |"

        # 检查 token 预算
        line_tokens = estimate_tokens(index_line)
        if tokens_used + line_tokens > max_tokens:
            break

        index_entries.append(index_line)
        tokens_used += line_tokens

    # 生成 Markdown 表格
    header = "| ID | Time | Type | Title | Tokens |"
    separator = "|---|------|------|-------|--------|"

    table = "\n".join([header, separator] + index_entries)

    return f"""## 鲤鱼记忆索引 ({len(index_entries)} 条)

{table}

> 💡 使用 `python3 liyu-context-compressor.py get <id>` 获取详情
> 📊 总计约 {tokens_used} tokens
"""

# ── SmartCrusher JSON 压缩（吸收自 Headroom） ──────────────────────────────

@dataclass
class JSONCompressionConfig:
    """JSON 压缩配置"""
    short_value_threshold: int = 20      # 短字符串阈值
    entropy_threshold: float = 0.85      # 熵阈值（UUID、hash 自动保留）
    max_array_items_full: int = 3        # 数组保留项数
    max_number_digits: int = 10          # 数字保留位数

def compress_json_smart(data: Any, config: JSONCompressionConfig = None) -> Any:
    """SmartCrusher 风格的 JSON 压缩

    保留结构，压缩值。高熵内容（UUID、hash）自动保留。
    """
    if config is None:
        config = JSONCompressionConfig()

    if isinstance(data, dict):
        compressed = {}
        for key, value in data.items():
            # 键名始终保留
            compressed[key] = compress_json_smart(value, config)
        return compressed

    elif isinstance(data, list):
        if len(data) <= config.max_array_items_full:
            return [compress_json_smart(item, config) for item in data]
        else:
            # 只保留前 N 个元素
            compressed = [compress_json_smart(item, config) for item in data[:config.max_array_items_full]]
            compressed.append(f"... ({len(data) - config.max_array_items_full} more items)")
            return compressed

    elif isinstance(data, str):
        # 短字符串保留
        if len(data) <= config.short_value_threshold:
            return data
        # 高熵内容保留（UUID、hash、API key）
        if is_high_entropy(data, config.entropy_threshold):
            return data
        # 长字符串压缩
        return data[:config.short_value_threshold] + "..."

    elif isinstance(data, (int, float)):
        # 数字：短数字保留，长数字压缩
        str_num = str(data)
        if len(str_num) <= config.max_number_digits:
            return data
        return str_num[:config.max_number_digits] + "..."

    elif isinstance(data, bool):
        return data

    elif data is None:
        return data

    else:
        return str(data)[:config.short_value_threshold]


def compress_json_with_stats(data: Any, config: JSONCompressionConfig = None) -> tuple:
    """压缩 JSON 并返回统计信息

    Returns:
        (compressed_data, original_tokens, compressed_tokens)
    """
    original_json = json.dumps(data, ensure_ascii=False)
    original_tokens = estimate_tokens(original_json)

    compressed = compress_json_smart(data, config)
    compressed_json = json.dumps(compressed, ensure_ascii=False, indent=2)
    compressed_tokens = estimate_tokens(compressed_json)

    return compressed, original_tokens, compressed_tokens


# ── 结构化压缩 ──────────────────────────────────────────────────────────

STRUCTURED_COMPRESSION_PROMPT = """分析以下会话观察，生成结构化摘要：

{observations}

输出 JSON：
{{
  "requests": ["用户请求1", ...],
  "findings": ["发现1", ...],
  "completions": ["完成项1", ...],
  "next_steps": ["下一步1", ...],
  "files_modified": ["file1.py", ...],
  "type": "bugfix|feature|refactor|investigation",
  "importance": 0.0-1.0,
  "title": "10字语义标题"
}}
"""

def compress_structured(observations: str) -> Dict[str, Any]:
    """结构化压缩（模拟 AI 压缩）"""
    # 简化版：基于规则提取
    requests = []
    findings = []
    completions = []
    next_steps = []
    files_modified = []

    # 提取请求
    request_patterns = [
        r'(?:用户|user)\s*(?:要求|请求|ask)\s*[：:]\s*(.+)',
        r'(?:请|please)\s*(.+)',
    ]
    for pattern in request_patterns:
        matches = re.findall(pattern, observations, re.IGNORECASE)
        requests.extend(matches[:3])

    # 提取发现
    finding_patterns = [
        r'(?:发现|found|discovered)\s*[：:]\s*(.+)',
        r'(?:问题是|issue is|problem is)\s*[：:]\s*(.+)',
    ]
    for pattern in finding_patterns:
        matches = re.findall(pattern, observations, re.IGNORECASE)
        findings.extend(matches[:3])

    # 提取完成项
    completion_patterns = [
        r'(?:完成|completed|done)\s*[：:]\s*(.+)',
        r'(?:已|have)\s*(.+)',
    ]
    for pattern in completion_patterns:
        matches = re.findall(pattern, observations, re.IGNORECASE)
        completions.extend(matches[:3])

    # 提取文件
    file_pattern = r'(?:文件|file)\s*[：:]\s*(\S+\.\w+)'
    files_modified = re.findall(file_pattern, observations)[:5]

    # 判断类型
    obs_lower = observations.lower()
    if any(word in obs_lower for word in ["bug", "fix", "error", "错误"]):
        task_type = "bugfix"
    elif any(word in obs_lower for word in ["feature", "new", "add", "新增"]):
        task_type = "feature"
    elif any(word in obs_lower for word in ["refactor", "重构", "clean"]):
        task_type = "refactor"
    else:
        task_type = "investigation"

    # 生成标题
    title = requests[0][:10] if requests else "会话摘要"

    return {
        "requests": requests[:3],
        "findings": findings[:3],
        "completions": completions[:3],
        "next_steps": next_steps[:3],
        "files_modified": files_modified,
        "type": task_type,
        "importance": 0.7,
        "title": title,
    }

# ── CCR 可逆压缩存储 ──────────────────────────────────────────────────

import hashlib

class CCRStore:
    """Content-addressable Compression and Retrieval Store"""

    def __init__(self, storage_dir: Path = None):
        self.storage_dir = storage_dir or 鲤鱼_HOME / "ccr"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_dir / "index.json"
        self._load_index()

    def _load_index(self):
        if self.index_file.exists():
            try:
                self.index = json.loads(self.index_file.read_text())
            except (json.JSONDecodeError, OSError):
                self.index = {}
        else:
            self.index = {}

    def _save_index(self):
        self.index_file.write_text(json.dumps(self.index, ensure_ascii=False, indent=2))

    def store(self, content: str, metadata: Dict = None) -> str:
        """存储内容，返回 CCR key"""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        content_file = self.storage_dir / f"{content_hash}.txt"
        content_file.write_text(content, encoding='utf-8')

        self.index[content_hash] = {
            'size': len(content),
            'tokens': estimate_tokens(content),
            'metadata': metadata or {},
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        self._save_index()

        return content_hash

    def retrieve(self, ccr_key: str) -> Optional[str]:
        """根据 CCR key 检索原始内容"""
        content_file = self.storage_dir / f"{ccr_key}.txt"
        if content_file.exists():
            return content_file.read_text(encoding='utf-8')
        return None

    def expand(self, compressed: str) -> str:
        """展开 CCR 压缩的内容"""
        match = re.search(r'\[CCR:([a-f0-9]+)\]', compressed)
        if match:
            ccr_key = match.group(1)
            original = self.retrieve(ccr_key)
            if original:
                return original
        return compressed

# ── State Management ──────────────────────────────────────────────────────

def load_state() -> dict:
    """加载压缩器状态"""
    if COMPRESSOR_STATE_FILE.exists():
        try:
            return json.loads(COMPRESSOR_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "total_compressions": 0,
        "tokens_saved": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def save_state(state: dict) -> None:
    """持久化压缩器状态"""
    鲤鱼_HOME.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    COMPRESSOR_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "prime-index":
        # 从记忆系统加载记忆
        try:
            sys.path.insert(0, str(鲤鱼_HOME))
            from liyu_memory_v2 import UnifiedMemoryManager
            manager = UnifiedMemoryManager()
            memories = manager.search("", limit=50)
        except Exception:
            # 回退：使用示例数据
            memories = [
                {"id": "mem-001", "timestamp": "2026-06-30T12:00:00", "type": "episodic", "content": "完成鲤鱼更名", "importance": 0.9},
                {"id": "mem-002", "timestamp": "2026-06-30T11:00:00", "type": "semantic", "content": "安全层部署完成", "importance": 0.8},
            ]

        index = generate_progressive_index(memories)
        print(index)

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: liyu-context-compressor.py get <memory_id>", file=sys.stderr)
            sys.exit(1)

        memory_id = sys.argv[2]

        # 尝试从 CCR 检索
        ccr = CCRStore()
        content = ccr.retrieve(memory_id)

        if content:
            print(content)
        else:
            # 从记忆系统检索
            try:
                sys.path.insert(0, str(鲤鱼_HOME))
                from liyu_memory_v2 import UnifiedMemoryManager
                manager = UnifiedMemoryManager()
                results = manager.search(memory_id, limit=1)
                if results:
                    print(results[0].get("content", "No content"))
                else:
                    print(f"Memory not found: {memory_id}")
            except Exception:
                print(f"Memory not found: {memory_id}")

    elif cmd == "compress":
        if len(sys.argv) < 3:
            print("Usage: liyu-context-compressor.py compress '<text>'", file=sys.stderr)
            sys.exit(1)

        text = sys.argv[2]
        original_tokens = estimate_tokens(text)

        # 结构化压缩
        compressed = compress_structured(text)
        compressed_json = json.dumps(compressed, ensure_ascii=False, indent=2)
        compressed_tokens = estimate_tokens(compressed_json)

        # CCR 存储
        ccr = CCRStore()
        ccr_key = ccr.store(text, {"type": "compressed"})

        # 更新统计
        state = load_state()
        state["total_compressions"] += 1
        state["tokens_saved"] += original_tokens - compressed_tokens
        save_state(state)

        print(f"📊 压缩结果:")
        print(f"  原始: {original_tokens} tokens")
        print(f"  压缩: {compressed_tokens} tokens")
        print(f"  节省: {original_tokens - compressed_tokens} tokens ({(1 - compressed_tokens/original_tokens)*100:.1f}%)")
        print(f"  CCR Key: {ccr_key}")
        print()
        print(f"压缩内容:")
        print(compressed_json)

    elif cmd == "compress-json":
        if len(sys.argv) < 3:
            print("Usage: liyu-context-compressor.py compress-json '<json>'", file=sys.stderr)
            sys.exit(1)

        try:
            data = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

        compressed, original_tokens, compressed_tokens = compress_json_with_stats(data)

        # CCR 存储
        ccr = CCRStore()
        ccr_key = ccr.store(json.dumps(data, ensure_ascii=False), {"type": "json-compressed"})

        # 更新统计
        state = load_state()
        state["total_compressions"] += 1
        state["tokens_saved"] += original_tokens - compressed_tokens
        save_state(state)

        print(f"📊 SmartCrusher JSON 压缩结果:")
        print(f"  原始: {original_tokens} tokens")
        print(f"  压缩: {compressed_tokens} tokens")
        print(f"  节省: {original_tokens - compressed_tokens} tokens ({(1 - compressed_tokens/original_tokens)*100:.1f}%)")
        print(f"  CCR Key: {ccr_key}")
        print()
        print(f"压缩内容:")
        print(json.dumps(compressed, ensure_ascii=False, indent=2))

    elif cmd == "stats":
        state = load_state()
        print("═══ 鲤鱼 Context Compressor Statistics ═══")
        print(f"  总计压缩:     {state.get('total_compressions', 0)}")
        print(f"  节省 tokens:  {state.get('tokens_saved', 0)}")

    elif cmd == "reset":
        save_state({
            "total_compressions": 0,
            "tokens_saved": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print("✅ Context Compressor 状态已重置")

    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
