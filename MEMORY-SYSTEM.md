# 鲤鱼 AIOS 记忆系统

## 核心概念

### 记忆类型

| 类型 | 描述 | 生命周期 | 用途 |
|------|------|----------|------|
| **短期记忆** | 当前对话历史 | 会话期间 | 上下文保持 |
| **长期记忆** | 持久化知识 | 永久 | 知识积累 |
| **工作记忆** | 当前任务状态 | 任务期间 | 任务执行 |

---

## 短期记忆

### 实现

```python
class ShortTermMemory:
    """短期记忆 - 对话历史"""
    
    def __init__(self, max_tokens: int = 4000):
        self.messages = []
        self.max_tokens = max_tokens
    
    def add(self, role: str, content: str):
        """添加消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        
        # 超出限制时压缩
        if self.total_tokens > self.max_tokens:
            self.compress()
    
    def get_context(self) -> list:
        """获取上下文"""
        return self.messages
    
    def compress(self):
        """压缩记忆"""
        # 保留最近的消息
        self.messages = self.messages[-10:]
    
    @property
    def total_tokens(self) -> int:
        """计算总 token 数"""
        return sum(len(m["content"]) // 4 for m in self.messages)
```

---

## 长期记忆

### 实现

```python
class LongTermMemory:
    """长期记忆 - 知识库"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                type TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                last_recalled TEXT,
                recall_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts 
            USING fts5(content, type)
        """)
        conn.commit()
        conn.close()
    
    def store(self, content: str, memory_type: str, importance: float = 0.5):
        """存储记忆"""
        memory_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO memories (id, content, type, importance, created_at) VALUES (?, ?, ?, ?, ?)",
            (memory_id, content, memory_type, importance, now)
        )
        conn.execute(
            "INSERT INTO memories_fts (rowid, content, type) VALUES (?, ?, ?)",
            (memory_id, content, memory_type)
        )
        conn.commit()
        conn.close()
        
        return memory_id
    
    def recall(self, query: str, limit: int = 5) -> list:
        """回忆记忆"""
        conn = sqlite3.connect(self.db_path)
        
        # FTS5 搜索
        cursor = conn.execute("""
            SELECT m.id, m.content, m.type, m.importance
            FROM memories_fts fts
            JOIN memories m ON fts.rowid = m.id
            WHERE memories_fts MATCH ?
            ORDER BY m.importance DESC, m.last_recalled DESC
            LIMIT ?
        """, (query, limit))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "content": row[1],
                "type": row[2],
                "importance": row[3]
            })
            
            # 更新回忆时间
            conn.execute(
                "UPDATE memories SET last_recalled = ?, recall_count = recall_count + 1 WHERE id = ?",
                (datetime.now().isoformat(), row[0])
            )
        
        conn.commit()
        conn.close()
        
        return results
    
    def forget(self, memory_id: str):
        """忘记记忆"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (memory_id,))
        conn.commit()
        conn.close()
```

---

## 工作记忆

### 实现

```python
class WorkingMemory:
    """工作记忆 - 当前任务状态"""
    
    def __init__(self):
        self.state = {}
        self.history = []
    
    def set(self, key: str, value: Any):
        """设置状态"""
        self.state[key] = value
        self.history.append({
            "action": "set",
            "key": key,
            "value": value,
            "timestamp": datetime.now().isoformat()
        })
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取状态"""
        return self.state.get(key, default)
    
    def delete(self, key: str):
        """删除状态"""
        if key in self.state:
            del self.state[key]
            self.history.append({
                "action": "delete",
                "key": key,
                "timestamp": datetime.now().isoformat()
            })
    
    def clear(self):
        """清空状态"""
        self.state.clear()
        self.history.append({
            "action": "clear",
            "timestamp": datetime.now().isoformat()
        })
    
    def get_state(self) -> dict:
        """获取完整状态"""
        return self.state.copy()
    
    def get_history(self) -> list:
        """获取历史"""
        return self.history
```

---

## 记忆管理器

### 实现

```python
class MemoryManager:
    """记忆管理器"""
    
    def __init__(self, db_path: str):
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory(db_path)
        self.working = WorkingMemory()
    
    def add_conversation(self, role: str, content: str):
        """添加对话到短期记忆"""
        self.short_term.add(role, content)
    
    def store_knowledge(self, content: str, knowledge_type: str, importance: float = 0.5):
        """存储知识到长期记忆"""
        return self.long_term.store(content, knowledge_type, importance)
    
    def recall_knowledge(self, query: str, limit: int = 5) -> list:
        """从长期记忆回忆知识"""
        return self.long_term.recall(query, limit)
    
    def set_task_state(self, key: str, value: Any):
        """设置任务状态"""
        self.working.set(key, value)
    
    def get_task_state(self, key: str, default: Any = None) -> Any:
        """获取任务状态"""
        return self.working.get(key, default)
    
    def get_context(self) -> dict:
        """获取完整上下文"""
        return {
            "conversation": self.short_term.get_context(),
            "task_state": self.working.get_state()
        }
    
    def compress(self):
        """压缩记忆"""
        self.short_term.compress()
```

---

## 记忆检索

### 语义检索

```python
class SemanticRetriever:
    """语义检索器"""
    
    def __init__(self, embedding_model):
        self.embedding_model = embedding_model
        self.vectors = {}
    
    def index(self, memory_id: str, content: str):
        """索引记忆"""
        vector = self.embedding_model.encode(content)
        self.vectors[memory_id] = vector
    
    def search(self, query: str, limit: int = 5) -> list:
        """语义搜索"""
        query_vector = self.embedding_model.encode(query)
        
        # 计算相似度
        similarities = []
        for memory_id, vector in self.vectors.items():
            similarity = cosine_similarity(query_vector, vector)
            similarities.append((memory_id, similarity))
        
        # 排序并返回
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:limit]
```

### 混合检索

```python
class HybridRetriever:
    """混合检索器"""
    
    def __init__(self, fts_retriever, semantic_retriever):
        self.fts_retriever = fts_retriever
        self.semantic_retriever = semantic_retriever
    
    def search(self, query: str, limit: int = 5) -> list:
        """混合搜索"""
        # FTS5 搜索
        fts_results = self.fts_retriever.search(query, limit * 2)
        
        # 语义搜索
        semantic_results = self.semantic_retriever.search(query, limit * 2)
        
        # RRF 融合
        fused_results = self.rrf_fusion(fts_results, semantic_results)
        
        return fused_results[:limit]
    
    def rrf_fusion(self, *result_lists) -> list:
        """Reciprocal Rank Fusion"""
        scores = {}
        
        for results in result_lists:
            for rank, (doc_id, _) in enumerate(results):
                if doc_id not in scores:
                    scores[doc_id] = 0
                scores[doc_id] += 1 / (60 + rank)
        
        # 排序
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results
```

---

## 记忆压缩

### 实现

```python
class MemoryCompressor:
    """记忆压缩器"""
    
    def __init__(self, llm):
        self.llm = llm
    
    def compress(self, memories: list) -> str:
        """压缩记忆"""
        # 合并相似记忆
        merged = self.merge_similar(memories)
        
        # 生成摘要
        summary = self.generate_summary(merged)
        
        return summary
    
    def merge_similar(self, memories: list) -> list:
        """合并相似记忆"""
        # 计算相似度矩阵
        # 合并高相似度的记忆
        pass
    
    def generate_summary(self, memories: list) -> str:
        """生成摘要"""
        prompt = f"""请为以下记忆生成简洁摘要：

记忆: {memories}

摘要："""
        return self.llm.generate(prompt)
```

---

## 鲤鱼 记忆集成

### 集成方案

```python
class liyuMemorySystem:
    """鲤鱼 记忆系统"""
    
    def __init__(self, config: dict):
        self.config = config
        self.memory_manager = MemoryManager(config["db_path"])
        self.retriever = HybridRetriever(
            fts_retriever=FTSRetriever(config["db_path"]),
            semantic_retriever=SemanticRetriever(load_embedding_model())
        )
        self.compressor = MemoryCompressor(load_llm())
    
    def on_conversation(self, role: str, content: str):
        """对话事件处理"""
        # 添加到短期记忆
        self.memory_manager.add_conversation(role, content)
        
        # 提取知识存储到长期记忆
        knowledge = self.extract_knowledge(content)
        if knowledge:
            self.memory_manager.store_knowledge(
                knowledge["content"],
                knowledge["type"],
                knowledge["importance"]
            )
    
    def on_task_start(self, task_id: str):
        """任务开始事件"""
        self.memory_manager.set_task_state("task_id", task_id)
        self.memory_manager.set_task_state("start_time", datetime.now().isoformat())
    
    def on_task_complete(self, task_id: str, result: str):
        """任务完成事件"""
        # 存储任务结果
        self.memory_manager.store_knowledge(
            f"任务 {task_id} 完成: {result}",
            "task_result",
            importance=0.8
        )
        
        # 清空工作记忆
        self.memory_manager.working.clear()
    
    def query(self, query: str, limit: int = 5) -> list:
        """查询记忆"""
        return self.memory_manager.recall_knowledge(query, limit)
    
    def extract_knowledge(self, content: str) -> dict:
        """从内容中提取知识"""
        # 使用 LLM 提取知识
        pass
```

---

## 学习资源

- [MemGPT 论文](https://arxiv.org/abs/2310.08560)
- [LangChain Memory](https://python.langchain.com/docs/modules/memory/)
- [Mem0 文档](https://docs.mem0.ai/)
