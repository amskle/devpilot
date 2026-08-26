# ADR-0003：ChangeRequest 与 ReplanRequest

- 状态：Accepted
- 日期：2026-08-26

## 背景

API 暴露用户发起的 `ChangeRequest`，工作流内部存在 `ReplanRequest`。若二者混为消息或同一对象，会丢失用户意图、系统判断和 Plan 修订之间的审计链。

## 决策

两者是不同的不可变领域对象：

- `ChangeRequest`：用户通过专用 API 明确提交的需求变更，记录原始内容、身份和目标 Task。
- `ReplanRequest`：系统内部要求 Planning 重新生成 Plan 的指令，包含原因、证据和来源引用。

普通 Chat 消息不会自动转换为 ChangeRequest。

```text
POST change-requests
→ 认证、授权、幂等和 state_revision 校验
→ 保存 ChangeRequest
→ ChangeRequest Router 在安全节点边界处理
→ 接受时创建 ReplanRequest(source_change_request_id=...)
→ Planning(mode=replan)
```

若任务正在等待 Patch 审批，前端提交 ChangeRequest 前必须二次确认当前待审批 Patch 将被废弃。确认后执行以下原子状态迁移：

```text
WAITING_RISK_APPROVAL
→ 接受 ChangeRequest
→ 旧 Approval 标记 INVALIDATED
→ 旧 PatchProposal 标记 INVALIDATED
→ 写 change_request_accepted / approval_invalidated / patch_invalidated 事件
→ 创建 ReplanRequest(source_change_request_id=...)
→ status = RUNNING
→ Prepare Replan → Planning(mode=replan)
```

事务必须校验 `expected_state_revision`。审批与 ChangeRequest 并发到达时只允许一个提交，另一个返回状态冲突。若任务已进入终态，不修改原 run，应创建新 Task 或 Fork。

无论重规划由用户还是系统触发，只要生成新 PlanVersion，都消耗统一的 `max_plan_revisions`。预算不足时进入人工介入，由用户选择创建新任务或扩大预算。

## 后果

- 用户原始请求、系统判断和 Plan 修订可以分别审计。
- 运行中变更只在节点安全边界生效。
- API 和前端需要独立的“提交变更需求”动作。

## 验收条件

- 普通 Chat 中的修改要求不会改变 Plan。
- ChangeRequest 可追溯到 ReplanRequest 和新 PlanVersion。
- 接受变更后旧 Patch 审批不能继续使用。
- 等待审批期间未经二次确认不能接受 ChangeRequest。
- ChangeRequest 与审批并发时不会同时生效。
- 终态任务不会被原地重新打开。
- 重复 Idempotency-Key 不会创建多个请求。
