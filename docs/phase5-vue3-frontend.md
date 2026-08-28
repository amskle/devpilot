# Phase 5：Vue3 前端控制台

## 实现范围

Phase 5 在 `frontend/vue3/` 提供 Vue 3 + TypeScript 控制台：

- Conversation Dashboard：底部任务输入、仓库与运行选项，以及从左侧历史快速恢复任务。
- Task Detail：以用户/Agent 对话为主线，在同一工作区查看当前节点、Run/Revision、实际任务模型、Plan、预算、Timeline、Diff、验证报告和消息记录。
- Workspace Shell：左侧任务历史与可展开运行概览；移动端切换为可关闭侧栏；右上角提供持久化的白天/夜间模式。
- Human-in-the-loop：绑定审批对象的批准/拒绝、取消、回滚、完整恢复和正式 ChangeRequest。
- 可靠事件：先按 `(run_id, after_sequence)` 从 Event Store 补拉，再用短期票据建立 WebSocket；按 `event_id` 去重，序号缺口触发重新补拉。
- 重连边界：网络错误和服务端暂时失败继续退避重连；认证失败或任务不可访问（HTTP 401/403/404）直接关闭事件流，等待用户重新认证或切换任务。
- 响应式与无障碍基础：键盘可操作控件、语义化状态、移动端侧栏，以及内置字体资源的静态构建。

当前代码库已包含 Phase 4 FastAPI 实现，前端按同一冻结契约完成网络适配；启动 `python -m devpilot api` 后即可端到端联调。前端不使用模拟数据作为生产回退。

## 安全与一致性

控制操作不从消息文本推断。普通消息通过独立的 `POST /messages` 保存为非状态事件；每个控制请求仍使用专用 API，并携带：

- `expected_state_revision`，HTTP 409 后刷新最新任务，不自动重放到新 Revision。
- `Idempotency-Key`，网络结果不确定时复用原 Key；收到明确 HTTP 响应后释放。
- 审批的 `approval_id`、`patch_hash` 和 `base_revision`。
- 恢复操作的 `recovery_point_id`。
- ChangeRequest 的 `confirm_patch_invalidation`；等待审批时必须显式二次确认。

访问 Token 只保存在浏览器 `sessionStorage`。Diff、消息和 Event Payload 只以文本方式渲染，不注入 HTML。WebSocket 只接收事件，不发送控制消息。

Phase 2/3 修正也进入前端契约：

- Task Detail 展示 Phase 1 价格快照固化的实际 task model，避免模型选择与 UI 认知不一致。
- Timeline 区分 `state_revision` 事件、非状态独立事件和 Checkpoint 确认状态。
- 恢复产生新 `run_id` 时清空旧游标，从新 Run 的序号 0 重新补拉。

## API 契约

前端使用 `VITE_API_BASE_URL`，默认 `/api`。除 `change-requests` 采用复数资源名外，控制操作均为 Task 子路径。

| 能力 | 请求 |
|---|---|
| 任务 | `GET/POST /tasks`、`GET /tasks/{task_id}` |
| 详情 | `GET /tasks/{task_id}/plan|diff|trace|messages` |
| 普通消息 | `POST /tasks/{task_id}/messages`，只记录上下文，不修改 Plan |
| 持久事件 | `GET /tasks/{task_id}/events?run_id=&after_sequence=` |
| 实时票据 | `POST /tasks/{task_id}/event-ticket` |
| WebSocket | `WS /tasks/{task_id}/events?run_id=&ticket=&after_sequence=` |
| 审批/控制 | `POST /tasks/{task_id}/approve|reject|cancel|rollback|restore` |
| 恢复点 | `GET /tasks/{task_id}/recovery-points` |
| 需求变更 | `POST /tasks/{task_id}/change-requests` |

Phase 4 返回当前 `TaskState`，并在详情响应中提供由价格快照解析出的只读 `model_profile`，不会仅回显服务默认模型。

## 本地运行

```powershell
cd frontend\vue3
npm ci
npm run dev
```

开发服务器把 `/api` 转发到 `http://127.0.0.1:8000`。生产环境可在构建时设置：

```powershell
$env:VITE_API_BASE_URL = "https://devpilot.example.com/api"
npm run build
```

## 验证

```powershell
cd frontend\vue3
npm run typecheck
npm test
npm run build
```

测试覆盖明暗主题、普通消息与 ChangeRequest 分离、控制命令绑定、Bearer 认证、幂等重试、Revision 冲突、事件游标/去重、非状态事件语义，以及审批和 ChangeRequest 二次确认。CI 使用 Node.js 22 执行上述三项检查。
