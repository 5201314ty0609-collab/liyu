---
tags: [resource, claude-code]
source: https://docs.anthropic.com/claude-code
last_updated: 2026-07-10
---

# 📖 Claude Code 指南

## 基本命令

```bash
# 启动 Claude Code
claude

# 查看版本
claude --version

# 更新
claude update
```

## CLAUDE.md 配置

在项目根目录创建 `CLAUDE.md` 文件，定义项目特定的指令：

```markdown
# 项目名称

## 项目描述
...

## 编码规范
...

## 测试要求
...
```

## Hooks 系统

| Hook 类型 | 触发时机 | 用途 |
|-----------|---------|------|
| PreToolUse | 工具执行前 | 验证、权限检查 |
| PostToolUse | 工具执行后 | 格式化、检查 |
| Stop | 会话结束 | 最终验证 |

## MCP 集成

Model Context Protocol (MCP) 允许 Claude Code 连接外部工具：

```json
{
  "mcpServers": {
    "server-name": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```

## 常用技巧

1. **Plan Mode**: 复杂任务前先规划
2. **TDD**: 先写测试，再写代码
3. **Code Review**: 写完代码立即审查
4. **Git Workflow**: Conventional commits

## 相关链接

- [[MCP 协议规范]]
- [[ECC 最佳实践]]
- [[鲤鱼系统架构]]

---

*Claude Code 指南 — 鲤鱼的运行时*
