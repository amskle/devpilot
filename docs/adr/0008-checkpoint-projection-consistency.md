# ADR-0008：Checkpoint 与控制投影一致性

- 状态：Accepted
- 日期：2026-08-26

## 背景

LangGraph SQLite Checkpoint 与 DevPilot Task/Event 表由不同组件写入，Phase 1 无法为二者提供一个跨组件 ACID 事务。

## 决策

Checkpoint 是 Graph 恢复真相，Task 表是支持 CAS 和列表查询的投影。节点先在一个 SQLite 事务内追加事件并以 `state_revision` 更新投影，LangGraph 再写 Checkpoint；运行返回后将 `checkpoint_revision` 标记为已确认。

若两者 revision 不一致，读取任务会先为刚提交、尚待 LangGraph 写入 Checkpoint 的投影保留 5 秒确认窗口，窗口内返回可重试的状态冲突而不回退；超过窗口后，以最新持久 Checkpoint 重建 Task 投影，并写不可删除的 `projection_reconciled` 事件。`devpilot admin reconcile` 是显式强制对账，不等待该窗口。控制命令必须先在投影执行 expected revision 校验，随后才能调用 `graph.update_state` 或恢复 Graph。所有 revision 的读校验和写入使用 SQLite `BEGIN IMMEDIATE` 串行化，避免多 worker 同时通过同一旧 revision。

## 后果

- 崩溃可能短暂产生未确认事件，但不会静默覆盖 Graph Checkpoint。
- Phase 3 引入 Transactional Outbox 后可以替换该补偿协议。

## 验收条件

- Checkpoint/投影任一侧领先时均能检测。
- 旧 revision 的审批或取消不能提交。
- 对账操作有结构化审计事件并可重复执行。
