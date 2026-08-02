---
name: subagent-dispatching
description: Use when facing 3+ independent problems or complex tasks that can be parallelized across multiple agents.
---

# 鲤鱼子 Agent 调度框架

## 核心原则

每个独立问题域派发一个 Agent，并行执行。

## 适用条件

- 3+ 个不相关失败
- Agent 不会修改相同文件
- 任务可以独立完成

## 四步流程

### 1. 识别独立域

检查清单：
- [ ] 问题是否可以独立解决？
- [ ] 解决方案是否会修改相同文件？
- [ ] 是否有 3+ 个不相关问题？

### 2. 构造聚焦任务

每个子任务必须：
- 有明确的输入和输出
- 可以独立验证
- 不依赖其他子任务的结果

### 3. 并行派发

**关键技巧：** 同一响应中发出多个 subagent 调用触发并发，逐个发出则顺序执行。

```python
# 并行派发
agent("任务1", isolation="worktree")
agent("任务2", isolation="worktree")
agent("任务3", isolation="worktree")
```

### 4. 审查集成

- 收集所有子任务结果
- 验证每个结果
- 集成到主流程

## 子 Agent 驱动开发

### 协调器模式

- 协调器保持上下文用于编排
- 具体实现委托给隔离的子 Agent
- 用文件传递而非粘贴大段文本

### 质量门

每个子任务必须经过：
1. **实现者** — 完成任务
2. **审查者** — 验证质量
3. **修复者**（如需）— 修复问题

### 模型选择

- 每个角色用最便宜的够用模型
- "轮次计数比 token 价格重要"
- 简单任务用轻量模型，复杂任务用重量模型

## 自适应时间策略

- 并行启动 stagger：成功 → 减半间隔，失败 → 加倍间隔 (上限 60s)
- 空闲超时：3× 中位周期时间 (下限 30s, 上限 600s)

## 上下文预清理

- ≤30%: 警告 ⚡
- ≤20%: 主动 compact，不让 Agent 撞上下文墙
- 长对话中的子 Agent 在启动前检查上下文压力

## 与鲤鱼的集成

1. **liyu-iteration-budget.py** — 子 Agent 迭代预算控制
2. **liyu-circuit-breaker.py** — 子 Agent 熔断器
3. **liyu-security-layer.py** — 子 Agent 安全检查
4. **liyu-memory-v2.py** — 子 Agent 记忆隔离

## Domains

subagent, parallel, dispatching, orchestration

## Evolution History

- Created: 2026-07-10 from obra/superpowers
- Source: Superpowers 子 Agent 调度模式
