# ADR-0005：工具错误与重试边界

- 状态：Superseded by [ADR-0007](0007-tool-executor-retry-ownership.md)
- 日期：2026-08-26

## 背景

工具错误可能发生在 Diagnosis、Patch Generation、Verification 或任意节点。若只有 Verification 后存在 Failure Router，前置异常会绕过统一分类；若 AgentRunner 与 LangGraph 各自维护预算，又会造成重复重试和预算失真。

## 决策

所有工具调用——包括 Agent 发起的工具和确定性节点使用的测试、Git、Docker 工具——必须统一经过 `ToolExecutor`。ToolExecutor 是唯一允许执行工具级重试的组件，并负责把异常转换为 `ToolExecutionError`：

```text
首次调用失败
→ retry_policy == BACKOFF
→ 工具声明 idempotent 或提供 idempotency_key
→ 原子预留全局 retry/tool-call 预算
→ 指数退避 + jitter
→ 重试
```

默认最多一次工具级重试，实际上限取 ToolSpec 与 ExecutionBudget 的较小值。每次实际尝试都增加 `tool_calls_used`；首次之后的尝试增加 `tool_retries_used`。

AgentRunner 和确定性节点只调用 ToolExecutor，不实现自己的 Tool retry loop。每个逻辑工具调用携带稳定的 `operation_id`；同一个 `operation_id` 不得被 AgentRunner、Node 或 Failure Router 再次重试。

工具重试耗尽后，ToolExecutor 返回 `TOOL_RETRY_EXHAUSTED`。调用方将其规范化为 FailureRecord，进入可从任意节点到达的通用 Node Failure Router。Failure Router 不包含 `Retry Tool` 分支，只能根据节点语义选择 REDIAGNOSE、REGENERATE_PATCH、REPLAN、HUMAN 或 FAIL。因工具瞬时错误耗尽产生的 Failure 不得通过重试整个节点来绕过同一工具调用的重试上限。

Verification 正常执行但测试断言失败属于业务结果，不是工具瞬时异常，不在 AgentRunner 内重试。

ToolExecutor、AgentRunner 和 Failure Router 读取同一个 ExecutionBudget，由 Budget Service 在调用前原子预留、完成后核算；不建立第二套内部预算。无论调用来自哪一层，任务级 `tool_retries_used` 都不得超过 `max_tool_retries`。超时或取消后必须阻止迟到结果写入 State。

副作用工具未声明幂等时不得自动重试，转入补偿、人工介入或失败路由。

## 后果

- 瞬时错误在离故障最近的位置低成本恢复。
- 节点级策略仍由 LangGraph 统一决定。
- ToolSpec 需要声明幂等性、错误类别和超时。
- 同一工具调用只有一个重试所有者，避免跨层重复执行。

## 验收条件

- 各节点工具异常都能进入统一错误模型。
- 非幂等工具不会自动重试。
- 每次尝试只消耗一次正确的预算计数。
- 重试耗尽后不会再次进入隐藏工具循环。
- Agent、确定性节点和 Failure Router 不会二次重试同一 `operation_id`。
- 取消或超时后的迟到结果不能更新 GraphState。
