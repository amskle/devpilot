# ADR-0009：CLI-only 审批惰性过期

- 状态：Accepted
- 日期：2026-08-26

## 背景

Phase 1 没有后台任务调度器，不能依赖定时任务在 `expires_at` 到达时主动唤醒暂停的 Graph。

## 决策

所有读取或操作待审批任务的 CLI Service 入口先使用可注入 `Clock` 检查审批期限。若已过期，立即执行 `approval_expired` 状态迁移，清除审批对象并取消任务，然后拒绝原审批/恢复操作。

## 后果

任务在无人查询时可物理保持旧 Checkpoint，但第一次观察即得到逻辑正确的过期状态。未来后台调度器只能提前触发同一幂等迁移，不改变语义。

## 验收条件

- `status`、`approve`、`reject`、`resume` 均不能使用过期审批。
- 测试通过 FrozenClock 推进时间，不进行真实等待。
