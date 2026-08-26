# ADR-0007：工具级重试统一由 ToolExecutor 执行

- 状态：Accepted
- 日期：2026-08-26
- 修订并替代：[ADR-0005](0005-tool-error-and-retry-boundaries.md)

## 背景

工具调用既可能来自 AgentRunner，也可能来自 Verification 等确定性节点。若 AgentRunner、确定性节点或 Failure Router 各自实现重试，同一个逻辑工具调用可能被跨层重复执行，导致全局预算失真，并给有副作用的工具带来重复执行风险。

ADR-0005 对工具级重试所有者的约束不再作为现行决策。本 ADR 将工具执行和工具级重试统一收敛到 ToolExecutor。

## 决策

ToolExecutor 是唯一的工具执行和工具级重试入口。AgentRunner、确定性节点和 Failure Router 都必须通过 ToolExecutor 调用工具，不得实现自己的工具重试循环，也不得重新执行同一个逻辑工具调用。

每个逻辑工具调用必须携带稳定的 `operation_id`。ToolExecutor 负责白名单与输入 Schema 校验、超时控制、错误分类、幂等性检查、预算预留和工具级重试：

```text
首次调用失败
→ 错误被声明为瞬时且 retry_policy == BACKOFF
→ 工具声明 idempotent 或提供 idempotency_key
→ 原子预留统一 ExecutionBudget
→ 指数退避 + jitter
→ 重试
```

默认最多重试一次，实际上限取 ToolSpec 与 ExecutionBudget 的较小值。每次实际尝试增加 `tool_calls_used`，首次之后的尝试增加 `tool_retries_used`；预算的预留与核算由 Budget Service 原子执行。

重试耗尽后，ToolExecutor 返回 `TOOL_RETRY_EXHAUSTED`。调用方将其规范化为 `FailureRecord` 并交给通用 Node Failure Router，由确定性路由决定进入 `WAITING_HUMAN_INTERVENTION` 或 `FAILED`。Failure Router 不包含 `Retry Tool` 分支，也不得通过重试整个节点绕过同一 `operation_id` 的工具重试上限。

非幂等且没有幂等键的副作用工具不得自动重试。取消或超时后，迟到的工具结果不得写入 GraphState。

## 后果

- Agent 工具与确定性节点工具共享同一执行、错误和预算语义。
- 同一个逻辑工具调用只有一个重试所有者。
- ToolSpec 必须声明重试策略、幂等性、错误类别和超时。
- Node Failure Router 只决定节点级处置，不执行工具级重试。
- Phase 1 的已完成 `operation_id` 仅保存在 ToolExecutor 进程内；进程重启后的工具结果去重不作保证。具有外部副作用的控制命令使用 SQLite 持久幂等键，持久化工具幂等将在后续可靠执行存储中补齐。

## 验收条件

- AgentRunner、确定性节点和 Failure Router 均不包含工具重试循环。
- 所有工具调用均通过 ToolExecutor，并携带稳定的 `operation_id`。
- 每次尝试准确消耗 `tool_calls_used`，每次重试准确消耗 `tool_retries_used`。
- 非幂等且无幂等键的工具不会自动重试。
- `TOOL_RETRY_EXHAUSTED` 只进入 HUMAN 或 FAIL 路由，不会触发隐藏重试。
- 取消或超时后的迟到结果不能更新 GraphState。
