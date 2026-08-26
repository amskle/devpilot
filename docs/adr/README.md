# DevPilot Architecture Decision Records

本目录保存 DevPilot 已确认的架构决策。ADR 一经接受不直接改写历史结论；决策变化时应新建 ADR，并标记替代关系。

| ADR | 状态 | 决策 |
|---|---|---|
| [ADR-0001](0001-agent-runner-tool-loop.md) | Accepted | AgentRunner 使用 SDK 原生 Tool Calling，由 DevPilot 控制循环边界 |
| [ADR-0002](0002-graph-state-initialization-and-reducers.md) | Accepted | GraphState 完整初始化，Phase 1 默认使用字段替换语义 |
| [ADR-0003](0003-change-request-and-replan-request.md) | Accepted | 用户 ChangeRequest 与内部 ReplanRequest 分离并显式转换 |
| [ADR-0004](0004-model-gateway-structured-output.md) | Accepted | ModelGateway 采用能力协商和分级结构化输出策略 |
| [ADR-0005](0005-tool-error-and-retry-boundaries.md) | Superseded by ADR-0007 | 工具级重试与节点级路由分层，共享统一预算 |
| [ADR-0006](0006-chat-and-control-boundaries.md) | Accepted | Chat 不作为审批、回滚、恢复或取消的控制通道 |
| [ADR-0007](0007-tool-executor-retry-ownership.md) | Accepted | ToolExecutor 是唯一工具执行和工具级重试入口 |
| [ADR-0008](0008-checkpoint-projection-consistency.md) | Accepted | Checkpoint 为恢复真相，Task/Event 投影采用事件优先与显式对账 |
| [ADR-0009](0009-cli-approval-expiration.md) | Accepted | CLI-only 阶段在控制入口惰性判定审批过期 |

每份 ADR 至少包含背景、决策、后果和验收条件。
