# ADR-0004：ModelGateway 结构化输出

- 状态：Accepted
- 日期：2026-08-26

## 背景

Planning、Diagnosis、Patch Generation 和 Review 必须返回结构化结果，但模型提供商对 JSON Schema、JSON Mode 和 Tool Calling 的支持不同，需要统一能力协商、校验和降级语义。

## 决策

Pydantic 模型作为 DevPilot 内部输出契约和唯一校验入口。ModelGateway 根据 Provider Capability 按以下顺序选择：

1. 提供商原生严格 JSON Schema/Structured Output。
2. Function Calling，使用合成的 `submit_result` 工具提交结果。
3. JSON Mode，并使用同一 Pydantic Schema 本地校验。

不把自由文本正则提取作为正式降级方案。需要使用工具时，先完成有限 Tool Calling 循环，再发起最终结构化输出请求，避免在同一响应中混合副作用工具和结果提交。

输出校验先执行安全的确定性修复：移除完整 JSON Markdown 围栏，并把模型引用的检索证据替换成 Tool 返回的规范字段、删除虚构引用。其余校验失败最多执行一次无工具 repair 调用。repair 计入 LLM、Token、费用和 active-time 预算；再次失败或达到节点 Generation 上限时返回 `MODEL_OUTPUT_INVALID`，交给 Node Failure Router。

Provider fallback 仅用于明确的不可用、限流或能力不支持。语义失败、Schema 校验失败和安全策略拒绝不能通过切换模型掩盖。发生副作用工具调用后，除非具备相同幂等键和可验证恢复点，否则不自动切换 Provider 重放整个节点。

Fallback 顺序由任务的 `ModelProfile` 列表配置；候选 Provider 必须满足相同的数据处理和安全策略。每次选择和切换都写审计事件。

ModelGateway 将实际 Prompt/Completion/Total Token 返回给 Budget Service，并必须把 `ModelProfile.max_completion_tokens` 作为 Provider 的单次生成硬限制。费用按任务启动时固化的版本化 Pricing Catalog 快照核算：调用前根据当前 Messages、Tool Schema、输出 Schema 与最大 Completion 余量做动态预留，调用后按 Provider 总用量结算。不得按 ModelProfile 的最大 Prompt 容量逐次预留，否则会造成名义预算不可使用；实际使用超过 Token 或费用上限时仍由结算阶段执行硬限制。预算策略 v2 还为最后一次无工具结果生成保留容量，并限制 Planning 的节点 Token 与 Generation 数。预留失败必须携带此前已结算的 ExecutionBudget。价格更新只作用于新任务；启用 `max_cost` 但模型缺少单价时，任务启动必须安全失败。金额持久化使用定点小数或最小货币单位。

## 后果

- Agent Node 获得统一结构化结果，不依赖提供商专有对象。
- 每个 Provider Adapter 必须声明能力矩阵。
- repair 和 fallback 都受统一预算约束。
- 同一任务的费用核算口径不会因运行中价格更新而变化。

## 验收条件

- 三种结构化输出路径通过模拟适配测试。
- 非法字段、额外字段和错误类型被统一拒绝。
- repair 最多一次且不能调用工具。
- 不支持能力时按配置降级并产生事件。
- 副作用完成后不会无幂等保护地自动重放节点。
- `max_cost`、`cost_used` 和 Pricing Catalog 快照可以完成预留与核算测试。
