# 测试手册

## 1. 单元测试

```powershell
python -m pytest skills tests -q
```

当前测试覆盖：8 个 Skill、plain GraphState、Pydantic 边界、脚本化 Fake Gateway、Tool 权限和唯一重试、SQLite 对账、Git 隔离、诊断前基线测试、Java 字段类型与源码证据、审批中断/过期、Checkpoint/Restore、Progress 双指纹、费用与活跃时间预算、控制命令幂等、版本化 Plan、自动/人工 Replanning、Plan 修订预算、可靠事件、Transactional Outbox、Redis Streams/WebSocket 基础、异常规范化和旧入口兼容。测试数量以 CI 实际结果为准。

Phase 6 另覆盖跨 worker 单次票据、共享限流、Redis 故障 readiness、生产配置校验和前端终止重连状态。

Phase 7 覆盖 Event digest 稳定性、序号缺口检测、历史 Checkpoint State Replay、RecoveryPoint 隔离 Fork、评测错误隔离、指标聚合、真实 Prompt Override 和同数据集报告比较。

## 2. Phase 1 CLI

```powershell
python -m devpilot --help
python -m devpilot task create --repo C:\path\to\clean-repo --request "修复失败测试"
python -m devpilot task status --task-id TASK_ID
python -m devpilot admin reconcile --task-id TASK_ID
```

真实模型 smoke 需要显式设置 `DEVPILOT_MODEL_API_KEY`、`DEVPILOT_MODEL_BASE_URL` 和 `DEVPILOT_MODEL`，CI 只使用 ScriptedFakeModelGateway。

## 3. Phase 2 重规划

Diagnosis 返回 `PLAN_INVALID` 时，确定性路由会在预算允许时自动创建 ReplanRequest 并复用 Planning Agent。任务已停在人工介入时，也可显式重规划：

```powershell
python -m devpilot task replan --task-id TASK_ID --expected-revision REVISION --reason "原计划假设不成立"
python -m devpilot task plan --task-id TASK_ID
```

专项测试：

```powershell
python -m pytest tests/test_phase2_planning.py -q
```

## 4. Phase 3 可靠事件

事件、Outbox、Redis Streams 适配和 WebSocket 订阅基础的专项测试：

```powershell
python -m pytest tests/test_phase3_events.py -q
```

分布式控制面专项测试：

```powershell
python -m pytest tests/test_phase6_distributed_api.py -q
```

Replay 与评测专项测试：

```powershell
python -m pytest tests/test_phase7_replay_evaluation.py -q
```

## 5. Phase 5 Vue3 前端

```powershell
cd frontend\vue3
npm ci
npm run typecheck
npm test
npm run build
```

专项测试覆盖审批对象与 Revision 绑定、网络失败后的幂等 Key 复用、HTTP 409 冲突、Event Store 游标补拉、`event_id` 去重、独立事件/Checkpoint 事件语义，以及 ChangeRequest 废弃待审批 Patch 的二次确认。完整浏览器联调可通过 `python -m devpilot api` 启动 Phase 4 API。

Phase 4 的路由按业务模块拆分，但测试始终通过应用工厂和公开 HTTP/WebSocket 契约执行，防止内部重构改变 OpenAPI、鉴权、状态码或前端路径。

## 6. Legacy 本地 Demo

```powershell
python runtime\pipeline.py --repo demo\sample_python --approval confirm --output-dir out\demo
```

预期结果：
- 诊断出 `mutable-default-argument`、`bare-except`、`hardcoded-secret`
- 前两项经审批后自动修复，`hardcoded-secret` 仅报告
- 测试通过，报告写入 `out\demo\report.md`，任务状态 `completed`

`--approval auto` 用于自动化测试；真人演示使用 `confirm` 走审批分支。

## 7. 失败与回滚路径

自动化用例：`tests/test_pipeline.py::test_pipeline_rolls_back_when_verification_fails`

手工复现：把 `demo/sample_python/tests/test_app.py` 改成必失败用例，再运行 Demo，观察：
- Verification 判定失败
- 源文件恢复为修改前内容
- 任务状态 `failed`，报告中保留失败证据

## 8. Legacy MCP Server 冒烟

```powershell
python -m mcp run mcp\git_server.py
python -m mcp run mcp\testing_server.py
```

生产环境通过 Higress 注册后，在 Element 中让 Worker 调用 Git/Testing 工具验证。

## 9. Legacy AgentTeams 集成冒烟

1. 打开 Element：`http://127.0.0.1:18088`，登录管理员账号。
2. 给 Manager 发任务，例如：“对 A:\agent\devpilot-infra\demo\sample_python 做一次缺陷诊断”。
3. 观察 Manager -> Team Leader -> Worker 的消息流转。
4. 验证资源状态：

```powershell
docker exec hiclaw-controller hiclaw get workers
docker exec hiclaw-controller hiclaw get teams
```

5. 查看 Worker 日志确认无鉴权或工具调用错误。

## 10. Java 示例（兼容链路）

```powershell
python runtime\pipeline.py --repo demo\sample_spring --approval auto --output-dir out\spring
```

预期：检测到 `n-plus-one-candidate` 与 `sql-injection-candidate`；因无自动修复模板，仅输出报告，不做代码改动。
