# ADR-0012：Replay 真相源与评测可比性

- 状态：Accepted
- 日期：2026-08-29

## 背景

Execution Event 适合审计和实时传输，但 Payload 只包含阶段证据与状态转换摘要，不包含每个 GraphState 字段。若从事件推测完整状态，Replay 会静默制造数据。模型/Prompt 评测还容易因数据集变化、非隔离重跑或只记录标签而失去可比性。

## 决策

Event Replay 只验证 Event Store 自身的顺序、因果、Revision 和确认约束。State Replay 以 LangGraph Checkpoint 为完整状态真相，并使用 Event Store 对账。两者均为只读执行，结果写入独立审计表，不追加源任务事件。

RecoveryPoint Fork 从恢复点 commit 创建独立 Task/Run/worktree，并记录源与目标引用。它属于真实重新执行，而不是只读 Replay。

评测集采用严格版本化 Schema 和内容摘要。每个 Case 独立运行并保留 Task ID。Prompt Override 必须真实进入 Agent instructions，报告同时记录可读版本和实际 Prompt digest。只允许比较相同 Dataset digest 的报告。

## 后果

- Replay 不会调用模型或工具，也不会改变源任务状态。
- 无法从 Event Store 单独恢复完整状态；这项限制是显式契约。
- Fork 和评测会产生新 workspace、模型调用和费用，需要调用者主动执行。
- Prompt/模型结果仍可能受外部模型非确定性影响，但输入数据集、Prompt 内容和运行证据可追溯。

## 验收条件

- 对同一未变化 Run 重复 Event Replay 得到相同 source digest，且源 Task 投影与事件保持不变。
- State Replay 能读取指定历史 Revision，并对 Checkpoint 身份和已确认事件 Revision 做一致性检查。
- RecoveryPoint Fork 的目标 Task、Run 和 worktree 与源任务隔离，且记录父 Run 与恢复点引用。
- 评测报告包含完整 Dataset 快照、Dataset digest、Prompt digest、Case 任务引用、质量指标和资源消耗。
- 不同 Dataset digest 的报告被拒绝比较；Prompt Override 确实进入 Agent system message，但不改变运行时策略边界。
