# ADR-0001：AgentRunner 工具循环

- 状态：Accepted
- 日期：2026-08-26

## 背景

LangGraph 负责唯一工作流编排，自研 Agent Runtime 只负责一次受控 Agent 执行。需要明确 `AgentRunner.invoke()` 如何完成模型与工具之间的有限循环，同时避免重新实现第二套 Agent 编排框架。

## 决策

AgentRunner 使用模型提供商 SDK 或兼容层提供的原生 Tool Calling。ModelGateway 将不同响应统一成 DevPilot 内部 `ToolCall` DTO；不手工解析自然语言中的函数调用，也不使用会创建第二套 Graph、Checkpoint 或路由语义的高层 Agent 封装。

AgentRunner 自己控制：

- 根据 `AgentSpec.allowed_tools` 校验工具白名单。
- 使用工具 Schema 校验参数。
- 强制 `max_tool_rounds`、超时和任务全局预算。
- 记录模型、Token、工具调用和 Artifact 引用。
- 对最终输出执行结构化 Schema 校验。

一个 tool round 定义为：模型返回一组 tool calls，Runtime 执行后将结果交回模型。只读且声明可并行的工具可以并行；有副作用的工具必须串行并携带幂等键。

`max_tool_rounds` 耗尽后返回 `TOOL_ROUND_BUDGET_EXHAUSTED`，不得在 AgentRunner 内重新规划或切换 Agent。

## 后果

- 保留 SDK 对 Tool Calling 协议和提供商差异的处理能力。
- DevPilot 仍能精确控制权限、预算、事件和输出。
- ModelGateway 需要维护稳定的内部 ToolCall/ToolResult DTO。

## 验收条件

- 未授权工具在执行前被拒绝。
- 工具参数无效时不调用真实工具。
- 超过 `max_tool_rounds` 后没有额外调用。
- 副作用工具不会被并行或隐式重复执行。
- 每轮调用均产生结构化事件和用量记录。
