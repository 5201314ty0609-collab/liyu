---
tags: [area, ai, agent]
last_updated: 2026-07-10
---

# 🧠 AI Agent 知识

## 核心概念

### ReAct 推理引擎

```
THINK → ACT → OBSERVE → REPEAT
```

- **THINK**: 分析问题，制定计划
- **ACT**: 执行动作
- **OBSERVE**: 观察结果
- **REPEAT**: 循环直到完成

### 记忆系统

| 层级 | 类型 | 保留时间 | 用途 |
|------|------|---------|------|
| STM | Short-Term Memory | 会话级 | 当前对话 |
| WM | Working Memory | 任务级 | 当前任务 |
| LTM | Long-Term Memory | 永久 | 长期知识 |

### 多 Agent 协作

| 模式 | 描述 | 适用场景 |
|------|------|---------|
| Delegation | 主 Agent 委派子任务 | 简单任务分解 |
| Collaboration | 多 Agent 平等协作 | 需要多视角 |
| Debate | Agent 互相辩论 | 需要验证决策 |
| Pipeline | 链式处理 | 流水线任务 |

## 关键项目

### ECC (Everything Claude Code)

- 228k stars
- Orchestrator patterns
- Hook reliability
- AgentShield 安全

### MUNDO Agent

- 5层安全防御
- 熔断器模式
- 反思循环引擎

### Claude Soul

- Identity drift detection
- Correction lifecycle
- 3-tier reflection

### Metacog

- 7 senses 系统
- Nociception 检测
- 反向强化学习

### Superpowers

- 技能框架标准
- TDD 强制执行
- 子 Agent 调度

### DeerFlow

- Session Goals
- 子 Agent 隔离
- 中间件链

### TradingAgents

- 辩论决策机制
- 多 Agent 专业化分工
- 检查点恢复

### CowAgent

- 三层记忆蒸馏
- Deep Dream 夜间处理
- 知识库 Wiki 化

### FastMCP

- 装饰器注册模式
- Schema 自动生成
- 中间件管道

## 学习资源

- [[Claude Code 官方文档]]
- [[MCP 协议规范]]
- [[RAG 最佳实践]]

---

*AI Agent 知识领域 — 鲤鱼的核心能力*
