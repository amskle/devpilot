# ADR-0013：告警规则引擎与状态派生告警

- 状态：Accepted
- 日期：2026-09-15

## 背景

任务在预算耗尽、节点失败、审批挂起或等待人工介入后停下，但只有主动查询任务状态才会发现，缺少面向操作者的统一告警面。已有的 Execution Event 面向审计回放，语义过细；直接对每个事件生成通知又会产生大量噪声。需要在事件之上增加一层确定性、可去重的告警投影。

## 决策

告警是已结算 GraphState 的纯函数投影：`evaluate_alerts(state, created_at)` 读取状态后返回告警列表，不修改状态、不追加事件、不调用模型或工具。

当前规则集固定为四条：`BUDGET_EXHAUSTED`（任务明确因预算进入人工介入且对应维度 used >= limit，CRITICAL）、`TASK_FAILED`（终态失败，CRITICAL）、`APPROVAL_PENDING`（等待风险审批，WARNING）、`HUMAN_INTERVENTION`（等待人工处理，WARNING）。预算上限与运行时语义一致：`0` 表示零额度；仅仅在成功结束时刚好用完额度不构成预算事故。

持久化以 `(task_id, run_id, fingerprint)` 唯一约束做降噪：同一 run 的重复评估通过 `INSERT OR IGNORE` 幂等落库，新 run 中再次发生的事故仍会产生新告警。`fingerprint` 编码触发原因（如 `BUDGET_EXHAUSTED:llm_calls`）；`alert_id` 是 task、run 与 fingerprint 的确定性摘要，不依赖时间。评估在检查点确认后的运行入口执行；告警投影失败只记录日志，不阻断任务结果。

## 后果

- 告警不参与状态机，不改变 LangGraph 检查点契约与事件序列。
- 同一状态的重复评估不会产生重复告警；历史告警不会因状态好转而删除。
- 无 Webhook 或前端看板；现有实时链路为 Outbox、Redis Streams 与 WebSocket，告警查询经由服务层 `alert_history` 与 CLI `alerts list`。
- 删除终态任务时，告警与其他控制面记录一并清理。

## 验收条件

- 对同一 run 的同一状态重复评估不新增告警记录，`alert_id` 稳定；新 run 可记录同类事故。
- 预算耗尽任务产生 `BUDGET_EXHAUSTED`（CRITICAL）告警，证据包含维度、used 与 limit。
- `FAILED`、`WAITING_RISK_APPROVAL`、`WAITING_HUMAN_INTERVENTION` 状态分别产生对应规则的告警。
- 告警评估不改变 GraphState、state_revision 或 Execution Event 序列。
- 删除终态任务后其告警记录为空。
