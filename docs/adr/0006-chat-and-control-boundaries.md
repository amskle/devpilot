# ADR-0006：Chat 与控制操作边界

- 状态：Accepted
- 日期：2026-08-26

## 背景

自然语言消息无法可靠绑定 `approval_id`、Patch hash、RecoveryPoint 和预期 State revision。若把“我批准了”“回滚吧”等消息解释为控制命令，可能批准错误 Patch、重复回滚或被 Prompt Injection 触发高危操作。

## 决策

Chat 是消息和解释通道，不是控制平面。以下操作只能通过经过认证、授权和幂等保护的专用 API/UI 控件执行：

- 批准或拒绝 Patch。
- 取消任务。
- 回滚代码。
- 恢复 RecoveryPoint。
- 提交正式 ChangeRequest。
- 扩大执行预算。

Chat 不解析上述自然语言为命令。用户输入“我批准了”时，系统可以提示使用审批按钮，但不能改变任务状态。

审批必须绑定 `approval_id`、`patch_hash`、`base_revision` 和 `expected_state_revision`；回滚/恢复必须绑定 `recovery_point_id` 并二次确认。WebSocket 只推送事件，不接受控制消息。

普通消息写入 Event Store，可在允许节点作为上下文检索，但不能直接写 GraphState。需求变更必须通过 ChangeRequest API，并遵循 ADR-0003。

## 后果

- 自然语言交互更安全，但高风险动作必须使用明确控件。
- 前端需要区分 Chat、ChangeRequest、Approval 和 Recovery 区域。
- 后端不维护自然语言控制命令解析器。

## 验收条件

- Chat 中的批准、拒绝、回滚和取消文字不会改变状态。
- 审批对象不匹配或过期时专用 API 拒绝操作。
- WebSocket 控制消息被拒绝。
- ChangeRequest 只能通过专用 API 创建。
- 控制操作均有身份、目标对象、幂等键和审计事件。
