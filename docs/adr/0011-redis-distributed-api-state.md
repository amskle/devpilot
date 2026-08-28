# ADR-0011：Redis 承载分布式 API 短期状态

- 状态：Accepted
- 日期：2026-08-28

## 背景

Phase 4 的 WebSocket 票据和限流器保存在 Python 进程内。多 worker 部署时，票据可能由一个 worker 签发、由另一个 worker 校验；每个 worker 的独立限流窗口也会放大实际配额。

## 决策

配置 Redis 后，票据、限流与实时事件 Stream 使用同一 Redis 服务和隔离命名空间。票据原子消费，限流原子计数。API worker 同时运行基于 SQLite 租约的 Outbox Relay。生产环境和多 worker 模式必须配置 Redis，依赖故障时安全能力不降级到本地状态。

SQLite Event Store 继续作为事件真相，Redis Stream 仍为至少一次的实时副本。开发环境单 worker 可不配置 Redis，并继续使用进程内实现。

## 后果

- 任意 worker 都能消费另一个 worker 签发的票据；全局限流不再随 worker 数量放大。
- Redis 成为生产控制面的强依赖，需要 readiness、监控和持久化/高可用策略。
- 事件不会因 Redis 中断而丢失，但实时推送会延迟到 Outbox 重试成功。
- Phase 6 不解决共享文件系统和 SQLite 的吞吐上限；更大规模部署需后续迁移持久存储。
