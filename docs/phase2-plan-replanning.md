# Phase 2：Plan 与 Replanning

## 实现范围

Phase 2 在 Phase 1 的同步 CLI/SQLite 运行时上增加：

- 不可变 `PlanDocument`：同一 `plan_id` 下按 `version` 递增，记录父版本、仓库快照、变更原因、验收条件和内容哈希。
- 可变 `PlanLifecycle`：一个 Task 同时只能有一个 `ACTIVE` 版本，旧版本切换为 `SUPERSEDED`。
- 不可变 `ReplanRequest`：记录原因、证据、来源 Plan 和创建时间；消费状态单独保存在控制库中。
- Planning Agent 复用：初始规划和重规划使用同一个 AgentSpec，通过 `mode`、当前 Plan 和 ReplanRequest 区分上下文。
- 有限重规划：每次 Prepare Replan 消耗一次 `max_plan_revisions`，预算耗尽后进入人工介入，且不会再次调用 Planning Agent。

Phase 2 不实现 ChangeRequest HTTP API、认证授权、WebSocket 或前端；这些属于 Phase 4/5。

## 自动重规划流程

```text
Diagnosis(outcome=PLAN_INVALID)
→ FailureRecord(recovery_action=REPLAN)
→ Failure Router（检查 plan revision budget）
→ Prepare Replan（持久化 ReplanRequest、消耗预算）
→ Planning(mode=replan)
→ 原子激活 Plan version N+1
→ Diagnosis
```

Diagnosis 只能报告 Plan 假设失效，不能直接修改 Plan。创建请求、预算检查和版本切换均由确定性代码执行。

## 原子切换边界

新 Plan 的内容先写入内容寻址 Artifact Store。随后一个 SQLite 事务同时完成：

1. 保存新的 PlanDocument 元数据和 Artifact 引用；
2. 将旧 ACTIVE 生命周期标记为 SUPERSEDED；
3. 将新版本标记为唯一 ACTIVE；
4. 将 ReplanRequest 标记为 CONSUMED；
5. 更新 GraphState 控制投影；
6. 写入 `plan_activated` 审计事件。

事务失败时控制状态、生命周期和审计事件一起回滚；事务开始前产生但未引用的内容寻址制品是安全的孤立制品，可由后续垃圾回收清理。

## 人工介入后的显式重规划

```powershell
python -m devpilot task replan `
  --task-id TASK_ID `
  --expected-revision REVISION `
  --reason "原计划假设不成立，需要按新证据调整" `
  --idempotency-key OPTIONAL_STABLE_KEY
```

该命令只接受处于 `WAITING_HUMAN_INTERVENTION` 的任务，并执行乐观并发检查。相同幂等键不会创建第二个 ReplanRequest 或 Plan 版本。

Python 调用方可通过 `TaskService.plan_history(task_id)` 和 `TaskService.replan_history(task_id)` 查询版本与请求历史。

CLI 可查看完整 Plan 版本和生命周期：

```powershell
python -m devpilot task plan --task-id TASK_ID
```
