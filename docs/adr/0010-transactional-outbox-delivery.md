# ADR-0010：可靠事件采用 Transactional Outbox 与至少一次投递

- 状态：Accepted
- 日期：2026-08-27

## 背景

LangGraph 节点、控制投影和审计事件必须保持原子关系，但 SQLite 业务事务无法与 Redis Streams 建立分布式事务。若业务提交后直接发布 Redis，进程崩溃会丢失实时事件；若先发布 Redis，业务回滚会产生不存在的状态事件。

## 决策

SQLite MVP 在追加 `execution_events` 的同一事务中写入一条 `event_outbox`。状态事件同时记录产生它的 `state_revision`，Checkpoint 只确认同一 Run 中 revision 不高于当前 Checkpoint 的事件。独立 `OutboxRelay` 只领取已确认事件，使用有限租约领取待发布记录，提交到 Redis Streams 后再将 Outbox 标记为 `PUBLISHED`。发布失败按有界指数退避重新开放记录；Relay 只在上一条事件成功后领取下一条，保持数据库顺序。Checkpoint 对账会把失效分支的未确认 Outbox 标记为 `DISCARDED`，避免后续 Checkpoint 误发布旧分支事件。

传输语义是至少一次。Relay 在 Redis 接收消息后、确认 Outbox 前崩溃时允许重复投递，所有下游消费者必须使用不可变 `event_id` 去重。Redis Streams 是实时传输层，不是审计真相；断线客户端始终使用 Event Store 的 `(run_id, sequence_number)` 游标补拉。

事件写入前执行递归脱敏和长度限制。事件只保存阶段摘要和 Artifact 引用，不保存模型隐藏推理、完整环境变量或大段源码。

## 后果

- 业务事务提交后即使 Redis 暂时不可用，事件仍可恢复发布。
- Event Store、Outbox 和控制投影共享 SQLite 事务，Plan/Replan 的原子切换不变。
- 未确认 Checkpoint 的状态事件不会进入实时流；非状态消息在持久化事务提交后即可发布。
- WebSocket 队列可以丢弃过载的实时副本，因为持久化游标可以补齐；不得把内存队列当作历史来源。
- Phase 4 只需在认证授权边界之上封装游标查询和 WebSocket handler，无需改变投递语义。
- ADR-0008 的 Checkpoint/投影对账继续负责跨组件一致性；Outbox 只替代“直接实时发布”，不声称提供 Checkpoint 与控制库之间的跨库 ACID。

## 验收条件

- 事件、Outbox 和控制投影任一事务失败时一起回滚。
- Relay 失败不丢事件，并在退避到期后重试。
- Relay 租约过期后其他实例可以重新领取。
- Redis 与 WebSocket 消费者可以恢复游标并按 `event_id` 去重。
- 旧 Phase 1/2 SQLite 事件表可原地迁移。
- 事件载荷中的凭据和环境变量在持久化前被脱敏。
