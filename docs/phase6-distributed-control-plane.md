# Phase 6：Redis 分布式控制面

## 范围

Phase 6 将 Phase 4/5 的单进程控制面推进到可安全运行多个 Uvicorn worker：

- WebSocket 票据存入 Redis，票据只保存哈希键，消费通过 Lua 脚本原子完成；
- API 限流计数存入 Redis，同一主体在所有 worker 之间共享配额；
- 每个 worker 都可运行 Transactional Outbox Relay，SQLite 租约保证同一事件只被一个 Relay 领取；
- Relay 将已确认事件发布到 Redis Streams，WebSocket 先从 SQLite Event Store 补拉，再消费 Redis 实时副本；
- `/api/health` 仅表示进程存活，`/api/ready` 检查 Redis，依赖不可用时返回 HTTP 503；
- Redis 故障时安全状态采取 fail-closed：不签发/消费票据，也不绕过限流。

SQLite Event Store、任务投影和 LangGraph Checkpoint 仍是持久真相。Redis 只承担短期安全状态与实时传输，不替代审计数据。数据库迁移到 PostgreSQL 不在本阶段范围内，因此多 worker 必须共享同一 `DEVPILOT_DATA_DIR`。

## 配置

本机 Redis 位于 `127.0.0.1:6379` 时：

```powershell
$env:DEVPILOT_REDIS_URL = "redis://127.0.0.1:6379/0"
$env:DEVPILOT_REDIS_KEY_PREFIX = "devpilot"
$env:DEVPILOT_API_WORKERS = "4"
python -m devpilot api --host 0.0.0.0 --port 8000 --workers 4
```

生产环境还必须设置 `DEVPILOT_ENV=production` 和 `DEVPILOT_API_TOKENS`。非开发环境缺少 Redis 或显式 Token 时启动失败。`--reload` 与多 worker 互斥。

可调参数：

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `DEVPILOT_REDIS_URL` | 未设置 | Redis/Redis TLS URL；多 worker 和非开发环境必填 |
| `DEVPILOT_REDIS_KEY_PREFIX` | `devpilot` | 票据、限流、Stream 的命名空间 |
| `DEVPILOT_EVENT_TICKET_TTL_SECONDS` | `30` | 单次 WebSocket 票据有效期 |
| `DEVPILOT_RELAY_POLL_SECONDS` | `0.25` | Outbox 空闲轮询间隔 |
| `DEVPILOT_API_WORKERS` | `1` | 工厂直接启动时的部署校验值；CLI 自动设置 |

## 一致性与故障语义

票据的 Redis Key 使用 SHA-256，不直接暴露 bearer 值。消费脚本在一次 Redis 原子操作内读取并删除票据，因此 worker A 签发后可由 worker B 消费，但不能二次使用。

限流使用 Redis 固定时间窗和原子 `INCR + EXPIRE`。Redis 不可用时返回 503，而不是回退到进程内限流。Outbox 发布保持至少一次语义；客户端继续按不可变 `event_id` 去重，并在 Stream 截断、断线或序号缺口时通过 REST 游标补拉。

## 运维检查

负载均衡器使用：

- liveness：`GET /api/health`
- readiness：`GET /api/ready`

启动会先执行 Redis `PING`。运行中 Redis 断开后 readiness 返回 503；已持久化任务和事件不会丢失，Outbox 在 Redis 恢复后继续发布。
