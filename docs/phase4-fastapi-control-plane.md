# Phase 4：FastAPI 与 Human-in-the-loop 控制面

## 实现范围

Phase 4 在 `devpilot/api/` 提供 REST 与只读 WebSocket API，将现有 `TaskService`、Event Store 与 Artifact Store 暴露给 Phase 5 控制台：

- 任务创建、列表、详情及任务级资源授权。
- Plan、Diff、Trace、消息、恢复点与可靠事件查询。
- 绑定 Revision 和具体对象的批准、拒绝、取消、回滚、恢复与 ChangeRequest。
- Bearer 认证、任务所有权、持久化幂等负载绑定和速率限制。
- 单次、短期、任务绑定的 WebSocket 票据；实时连接只接收事件，不接受控制消息。
- OpenAPI 3 文档、字段说明、示例、错误响应与 Swagger UI 直接试调。

普通消息只追加 `message_created` 非状态事件，不会从自然语言推断批准、取消或回滚。正式需求变化只能进入 `change-requests` API。

## 代码分层

Phase 4 API 按职责组织，入口文件不包含路由或业务规则：

```text
devpilot/api/
├── main.py                         # 应用工厂、生命周期、中间件与路由装配
├── core/
│   ├── config.py                   # Token、CORS、票据有效期配置
│   ├── dependencies.py             # FastAPI 鉴权与依赖注入声明
│   ├── security.py                 # 票据与速率限制安全原语
│   ├── errors.py                   # 统一 ProblemDetails 异常映射
│   └── middleware.py               # Request ID 中间件
├── schemas/
│   ├── common.py                   # 通用响应与错误契约
│   ├── tasks.py                    # Task 请求/响应 DTO
│   ├── controls.py                 # 审批、恢复、消息与变更请求 DTO
│   └── evidence.py                 # Plan、Diff、Event Ticket DTO
├── services/
│   └── control_plane.py            # 授权、分页、幂等和 TaskService 适配
└── v1/
    ├── router.py                   # 当前版本路由注册
    └── endpoints/
        ├── system.py
        ├── tasks.py
        ├── evidence.py
        ├── conversation.py
        ├── events.py
        └── controls.py
```

`v1/` 是代码级版本边界；对外路径继续保持前端已经冻结的 `/api/...`，避免无意义地破坏 Phase 5。项目当前没有 SQLAlchemy ORM，因此没有创建空的 `models/` 或 `crud/` 层；持久化仍由既有 `SQLiteControlStore` 和 `ArtifactStore` 负责。领域流程继续集中在根级 `TaskService`，API Service 只处理传输层适配。

## 启动与直接测试

安装锁定依赖后启动本地服务：

```powershell
python -m pip install -r requirements.lock
python -m devpilot api --host 127.0.0.1 --port 8000
```

打开：

- Swagger UI：`http://127.0.0.1:8000/docs`
- ReDoc：`http://127.0.0.1:8000/redoc`
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`
- 存活检查：`http://127.0.0.1:8000/api/health`
- 依赖就绪检查：`http://127.0.0.1:8000/api/ready`

未设置认证配置时，仅为本机开发提供默认管理员 Token `devpilot-local`。在 Swagger 右上角选择 **Authorize**，输入该 Token 即可直接试调。CLI 会拒绝在未配置自定义 Token 时监听非回环地址；任何共享或生产环境必须设置自己的 Token：

```powershell
$env:DEVPILOT_API_TOKENS = '{"replace-with-long-random-token":{"subject":"operator-1","admin":true}}'
python -m devpilot api
```

普通用户只能访问自己通过 API 创建的任务；管理员可以访问历史 CLI 任务和所有 API 任务。为避免泄露任务是否存在，越权访问与不存在任务都返回 HTTP 404。

## 接口契约

所有路径以 `/api` 为前缀。

| 能力 | 方法与路径 | 关键约束 |
|---|---|---|
| 健康检查 | `GET /health` | 无需认证 |
| 创建任务 | `POST /tasks` | 仓库必须是允许访问的干净 Git 仓库；按主体限流 |
| 任务列表/详情 | `GET /tasks`、`GET /tasks/{task_id}` | 任务级授权；列表使用游标分页 |
| Plan/Diff/Trace | `GET /tasks/{task_id}/plan|diff|trace` | 只读执行证据 |
| 消息 | `GET/POST /tasks/{task_id}/messages` | POST 必须带 `Idempotency-Key`；不修改 GraphState |
| 事件补拉 | `GET /tasks/{task_id}/events` | 必须指定 `run_id` 与 `after_sequence` |
| 实时票据 | `POST /tasks/{task_id}/event-ticket` | 30 秒有效、单次使用、绑定主体与 Task |
| 实时事件 | `WS /tasks/{task_id}/events` | `run_id`、`ticket`、`after_sequence`；客户端发消息会以 1008 关闭 |
| 审批 | `POST /tasks/{task_id}/approve|reject` | 绑定 approval、Patch hash、base revision、state revision 与操作者 |
| 取消 | `POST /tasks/{task_id}/cancel` | 绑定 state revision |
| 恢复点 | `GET /tasks/{task_id}/recovery-points` | 当前可用 RecoveryPoint |
| 回滚/恢复 | `POST /tasks/{task_id}/rollback|restore` | 绑定 RecoveryPoint 与 state revision；恢复创建新 Run |
| 正式变更 | `POST /tasks/{task_id}/change-requests` | 绑定 state revision；等待审批时必须确认废弃 Patch |

控制请求与消息请求的 `Idempotency-Key` 长度为 8～255。后端持久化绑定 Key 与规范化请求负载的 SHA-256；相同 Key 和相同负载返回原结果，相同 Key 配合不同负载返回 HTTP 409。

## ChangeRequest 原子语义

ChangeRequest 使用独立不可变记录，并与内部 ReplanRequest 建立来源链接：

```text
认证与任务授权
→ 校验 expected_state_revision
→ 校验任务非终态和 Plan 修订预算
→ 保存 ChangeRequest
→ 保存 ReplanRequest(source_change_request_id)
→ 若有待审批对象，写 approval_invalidated / patch_invalidated
→ 写 change_request_accepted
→ 更新 Task projection
→ 确认 Checkpoint
→ Planning(mode=replan)
```

记录、失效事件和 Task projection 在同一个 SQLite 事务中提交。审批命令与 ChangeRequest 并发时，只有先通过 Revision 乐观锁的操作能够提交。

## 错误格式

业务错误使用统一响应：

```json
{
  "code": "STATE_CONFLICT",
  "detail": "expected state_revision 8, actual 9",
  "request_id": "89a99f90-4199-4640-ac4c-071a92fd5198"
}
```

| HTTP | 含义 |
|---|---|
| 401 | Bearer Token 缺失或无效 |
| 403 | 安全策略拒绝 |
| 404 | 资源不存在或当前主体无权访问 |
| 409 | Revision、生命周期、预算或幂等负载冲突 |
| 422 | 请求 Schema 或领域参数不合法 |
| 429 | 创建、消息或控制操作超过速率限制 |

每个 HTTP 响应都返回 `X-Request-ID`，调用方也可以主动传入同名请求头用于日志关联。

## 配置

| 环境变量 | 说明 |
|---|---|
| `DEVPILOT_API_TOKENS` | Token 到主体信息的 JSON 映射；生产必填 |
| `DEVPILOT_API_CORS_ORIGINS` | 逗号分隔的允许 Origin；同源部署无需设置 |
| `DEVPILOT_ENV` | 环境名称；非 `development` 时禁用默认 Token 并要求 Redis |
| `DEVPILOT_REDIS_URL` | Phase 6 共享票据、限流和实时事件 Redis URL |
| `DEVPILOT_DATA_DIR` | Control DB、Checkpoint、Artifact 与 worktree 根目录 |
| `DEVPILOT_MODEL_API_KEY` | OpenAI-compatible 模型凭据 |
| `DEVPILOT_MODEL_BASE_URL` | 模型服务地址 |
| `DEVPILOT_MODEL` | 默认模型；任务实际模型仍从价格快照读取并返回 |

Phase 4 的进程内实现只保留给单 worker 开发模式。Phase 6 配置 Redis 后会自动切换到跨 worker 的票据、限流、Outbox Relay 和实时 Stream；Event Store 仍是补拉和审计的唯一可靠来源，详见 [Phase 6 控制面](phase6-distributed-control-plane.md)。

## 验证

```powershell
python -m pytest tests/test_phase4_api.py -q
python -m pytest skills tests -q
```

专项测试覆盖 OpenAPI 注解、认证、任务级授权、真实模型投影、消息/控制边界、幂等负载冲突、Revision 409、事件游标、单次票据、只读 WebSocket，以及 ChangeRequest 的确认与审计链。
