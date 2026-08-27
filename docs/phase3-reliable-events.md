# Phase 3：可靠事件、实时传输与 Trace

## 实现范围

Phase 3 在现有 SQLite 控制事务上增加：

- 版本化 `ExecutionEvent` 信封：包含 Task/Run、节点、尝试次数、序号、关联与因果 ID、Artifact 引用和 Checkpoint 确认状态。
- Transactional Outbox：控制状态、Plan 生命周期、审计事件和 Outbox 在同一个 SQLite 事务提交。
- `OutboxRelay`：有限租约、顺序领取、有界指数退避和至少一次发布。
- `RedisStreamTransport` / `RedisStreamConsumer`：每个 Task Run 使用独立 Stream，并传输完整事件信封。
- `EventSubscriptionHub` / `RedisWebSocketBridge`：为 Phase 4 的 WebSocket API 提供与 Web 框架无关的订阅和转发基础。
- 持久化游标补拉、递归脱敏、旧库迁移与完整事件 Trace。

Phase 3 不实现 FastAPI 路由、用户认证、Task 资源授权或 WebSocket 票据；这些属于 Phase 4。

## 可靠写入链路

```text
LangGraph Node / Control Command / Message
→ SQLite Transaction
   ├── execution_events
   ├── event_outbox
   └── task_projection / Plan / Replan records
→ Commit
→ OutboxRelay
→ Redis Stream
→ RedisWebSocketBridge
→ EventSubscriptionHub
→ Phase 4 WebSocket handler
```

任何业务事务回滚都会同时移除对应 Event 和 Outbox。Redis 不可用只影响实时性，不影响审计记录和任务状态。

## 游标与顺序

`sequence_number` 在 `(task_id, run_id)` 范围从 1 严格递增。补拉调用应同时携带 `run_id` 与 `after_sequence`：

```python
events = service.event_history(
    task_id,
    run_id,
    after_sequence=last_sequence,
    limit=100,
)
```

Fork/Restore 创建新 Run 后从新 Run 的序号 1 开始。跨 Run Trace 可以整体查询，但实时恢复游标不能只保存序号而丢失 Run ID。

## 投递语义

Redis 发布采用至少一次语义。下游根据 `event_id` 去重，不能根据 Event 类型或 Payload 猜测重复。状态转换事件只有在相应 LangGraph Checkpoint 已确认后才能被 Relay 领取；对账判定失效的旧分支记录进入 `DISCARDED`。非状态事件不依赖 Checkpoint，在 Event/Outbox 事务提交后即可领取。Outbox 状态：

```text
PENDING → PROCESSING → PUBLISHED
             │
             └── publish failure / expired lease → PENDING or reclaimed

PENDING → DISCARDED（Checkpoint 对账判定为失效分支）
```

Relay 每次只领取数据库顺序中的下一条记录；失败时停止本轮，防止同一链路后续事件越过失败事件。

## 脱敏边界

Event 写入前递归处理：

- 密钥、Token、Authorization、密码、私钥、完整环境变量统一替换为 `[REDACTED]`。
- 文本中的 Bearer Credential 被替换。
- 超长字符串截断；大日志、报告、Diff 和源码写入 Artifact Store，Event 只保留引用。
- 允许 `agent_summary`，禁止模型隐藏思维链。

## 运行方式

生产进程创建 Redis 客户端并注入传输层：

```python
from devpilot.events import OutboxRelay, RedisStreamTransport

transport = RedisStreamTransport.from_url("redis://127.0.0.1:6379/0")
relay = OutboxRelay(service.control, transport, relay_id="relay-1")
result = relay.run_once()
```

部署调度器应持续调用 `run_once()`；没有可发布记录时自行退避。Phase 3 核心类不启动隐藏后台线程，进程生命周期由部署层管理。

## 验证

专项测试：

```powershell
python -m pytest tests/test_phase3_events.py -q
```

测试覆盖事件契约、游标、Trace、脱敏、事务回滚、Relay 顺序发布、失败退避、Redis 编解码、WebSocket 桥接和旧 SQLite 迁移。
