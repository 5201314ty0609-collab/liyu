# 鲤鱼 AIOS Agent 架构

## 核心概念

### 1. Agent 定义

Agent 是一个自主运行的智能体，具有：
- **目标**：要完成的任务
- **能力**：可以使用的工具
- **记忆**：历史信息和知识
- **推理**：决策和规划能力

### 2. Agent 类型

| 类型 | 描述 | 用途 |
|------|------|------|
| **Core Agent** | 核心智能体 | 主要任务执行 |
| **Tool Agent** | 工具智能体 | 特定工具调用 |
| **Planning Agent** | 规划智能体 | 任务分解和规划 |
| **Reflection Agent** | 反思智能体 | 结果评估和改进 |

---

## ReAct 模式

### 核心循环

```
Reasoning → Action → Observation → Reasoning → ...
```

### 实现

```python
class ReActAgent:
    def __init__(self, llm, tools):
        self.llm = llm
        self.tools = tools
    
    def run(self, query: str, max_steps: int = 10) -> str:
        """运行 ReAct 循环"""
        context = []
        
        for step in range(max_steps):
            # 1. Reasoning - 思考下一步
            thought = self.think(query, context)
            context.append({"type": "thought", "content": thought})
            
            # 2. Action - 执行动作
            action = self.decide_action(thought)
            result = self.execute_action(action)
            context.append({"type": "action", "content": action, "result": result})
            
            # 3. Observation - 观察结果
            observation = self.observe(result)
            context.append({"type": "observation", "content": observation})
            
            # 4. 检查是否完成
            if self.is_complete(observation):
                return self.generate_response(context)
        
        return self.generate_response(context)
    
    def think(self, query: str, context: list) -> str:
        """思考下一步"""
        prompt = f"""基于以下上下文，思考下一步应该做什么：

查询: {query}
历史: {context}

思考："""
        return self.llm.generate(prompt)
    
    def decide_action(self, thought: str) -> dict:
        """决定执行什么动作"""
        prompt = f"""基于以下思考，决定执行什么动作：

思考: {thought}

可用工具: {list(self.tools.keys())}

动作："""
        action_str = self.llm.generate(prompt)
        return self.parse_action(action_str)
    
    def execute_action(self, action: dict) -> str:
        """执行动作"""
        tool_name = action["tool"]
        tool_input = action["input"]
        
        if tool_name in self.tools:
            return self.tools[tool_name].execute(tool_input)
        else:
            return f"错误：工具 {tool_name} 不存在"
    
    def observe(self, result: str) -> str:
        """观察结果"""
        return f"执行结果: {result}"
    
    def is_complete(self, observation: str) -> bool:
        """检查是否完成"""
        return "完成" in observation or "结束" in observation
    
    def generate_response(self, context: list) -> str:
        """生成最终响应"""
        prompt = f"""基于以下上下文，生成最终响应：

上下文: {context}

响应："""
        return self.llm.generate(prompt)
```

---

## Plan-and-Execute 模式

### 核心流程

```
Plan → Execute Step 1 → Execute Step 2 → ... → Final Result
```

### 实现

```python
class PlanAndExecuteAgent:
    def __init__(self, llm, tools):
        self.llm = llm
        self.tools = tools
    
    def run(self, query: str) -> str:
        """运行 Plan-and-Execute"""
        # 1. 规划阶段
        plan = self.create_plan(query)
        
        # 2. 执行阶段
        results = []
        for step in plan["steps"]:
            result = self.execute_step(step)
            results.append(result)
            
            # 3. 检查是否需要重新规划
            if self.should_replan(step, result):
                plan = self.replan(query, results)
        
        # 4. 生成最终响应
        return self.generate_response(query, results)
    
    def create_plan(self, query: str) -> dict:
        """创建执行计划"""
        prompt = f"""为以下查询创建执行计划：

查询: {query}

可用工具: {list(self.tools.keys())}

计划（JSON格式）："""
        plan_str = self.llm.generate(prompt)
        return json.loads(plan_str)
    
    def execute_step(self, step: dict) -> str:
        """执行单个步骤"""
        tool_name = step["tool"]
        tool_input = step["input"]
        
        if tool_name in self.tools:
            return self.tools[tool_name].execute(tool_input)
        else:
            return f"错误：工具 {tool_name} 不存在"
    
    def should_replan(self, step: dict, result: str) -> bool:
        """检查是否需要重新规划"""
        return "错误" in result or "失败" in result
    
    def replan(self, query: str, results: list) -> dict:
        """重新规划"""
        prompt = f"""基于以下结果，重新规划：

查询: {query}
结果: {results}

新计划（JSON格式）："""
        plan_str = self.llm.generate(prompt)
        return json.loads(plan_str)
    
    def generate_response(self, query: str, results: list) -> str:
        """生成最终响应"""
        prompt = f"""基于以下结果，生成最终响应：

查询: {query}
结果: {results}

响应："""
        return self.llm.generate(prompt)
```

---

## Multi-Agent 协作

### 协作模式

| 模式 | 描述 | 适用场景 |
|------|------|----------|
| **主从模式** | 一个主 Agent 协调多个从 Agent | 复杂任务分解 |
| **对等模式** | Agent 之间平等协作 | 多视角分析 |
| **竞争模式** | 多个 Agent 竞争最佳方案 | 优化问题 |
| **流水线模式** | Agent 按顺序处理 | 数据处理 |

### 实现

```python
class MultiAgentSystem:
    def __init__(self, agents: dict):
        self.agents = agents
    
    def run(self, query: str, mode: str = "master-slave") -> str:
        """运行多 Agent 协作"""
        if mode == "master-slave":
            return self.master_slave_mode(query)
        elif mode == "peer":
            return self.peer_mode(query)
        elif mode == "competitive":
            return self.competitive_mode(query)
        elif mode == "pipeline":
            return self.pipeline_mode(query)
        else:
            raise ValueError(f"未知模式: {mode}")
    
    def master_slave_mode(self, query: str) -> str:
        """主从模式"""
        # 主 Agent 分解任务
        master = self.agents["master"]
        tasks = master.decompose(query)
        
        # 从 Agent 执行任务
        results = []
        for task in tasks:
            slave = self.agents[task["agent"]]
            result = slave.execute(task["input"])
            results.append(result)
        
        # 主 Agent 汇总结果
        return master.summarize(results)
    
    def peer_mode(self, query: str) -> str:
        """对等模式"""
        # 所有 Agent 并行分析
        results = []
        for name, agent in self.agents.items():
            result = agent.analyze(query)
            results.append({"agent": name, "result": result})
        
        # 汇总所有分析
        return self.aggregate_results(results)
    
    def competitive_mode(self, query: str) -> str:
        """竞争模式"""
        # 所有 Agent 竞争最佳方案
        solutions = []
        for name, agent in self.agents.items():
            solution = agent.solve(query)
            solutions.append({"agent": name, "solution": solution})
        
        # 选择最佳方案
        return self.select_best_solution(solutions)
    
    def pipeline_mode(self, query: str) -> str:
        """流水线模式"""
        # Agent 按顺序处理
        result = query
        for name, agent in self.agents.items():
            result = agent.process(result)
        
        return result
```

---

## 工具调用机制

### 工具定义

```python
class Tool:
    """工具基类"""
    
    def __init__(self, name: str, description: str, parameters: dict):
        self.name = name
        self.description = description
        self.parameters = parameters
    
    def execute(self, **kwargs) -> str:
        """执行工具"""
        raise NotImplementedError

class SearchTool(Tool):
    """搜索工具"""
    
    def __init__(self):
        super().__init__(
            name="search",
            description="搜索网络信息",
            parameters={
                "query": {
                    "type": "string",
                    "description": "搜索查询"
                }
            }
        )
    
    def execute(self, query: str) -> str:
        """执行搜索"""
        # 实现搜索逻辑
        return f"搜索结果: {query}"
```

### 工具注册

```python
class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self.tools = {}
    
    def register(self, tool: Tool):
        """注册工具"""
        self.tools[tool.name] = tool
    
    def get(self, name: str) -> Tool:
        """获取工具"""
        if name not in self.tools:
            raise ValueError(f"工具 {name} 不存在")
        return self.tools[name]
    
    def list_tools(self) -> list:
        """列出所有工具"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
            for tool in self.tools.values()
        ]
```

---

## 鲤鱼 Agent 集成

### Agent 核心

```python
class liyuAgent:
    """鲤鱼 Agent 核心"""
    
    def __init__(self, config: dict):
        self.config = config
        self.llm = self._init_llm()
        self.tools = self._init_tools()
        self.memory = self._init_memory()
        self.react_agent = ReActAgent(self.llm, self.tools)
        self.plan_agent = PlanAndExecuteAgent(self.llm, self.tools)
    
    def run(self, query: str, mode: str = "react") -> str:
        """运行 Agent"""
        if mode == "react":
            return self.react_agent.run(query)
        elif mode == "plan":
            return self.plan_agent.run(query)
        else:
            raise ValueError(f"未知模式: {mode}")
    
    def _init_llm(self):
        """初始化 LLM"""
        # 实现 LLM 初始化
        pass
    
    def _init_tools(self) -> dict:
        """初始化工具"""
        # 实现工具初始化
        pass
    
    def _init_memory(self):
        """初始化记忆"""
        # 实现记忆初始化
        pass
```

---

## 学习资源

- [ReAct 论文](https://arxiv.org/abs/2210.03629)
- [Plan-and-Solve 论文](https://arxiv.org/abs/2305.04091)
- [AutoGen 文档](https://microsoft.github.io/autogen/)
- [CrewAI 文档](https://docs.crewai.com/)
