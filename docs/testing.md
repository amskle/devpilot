# 测试手册

## 1. 单元测试

```powershell
python -m pytest skills tests -q
```

当前测试覆盖：8 个 Skill、plain GraphState、Pydantic 边界、脚本化 Fake Gateway、Tool 权限和唯一重试、SQLite 对账、Git 隔离、诊断前基线测试、Java 字段类型与源码证据、审批中断/过期、Checkpoint/Restore、Progress 双指纹、费用与活跃时间预算、控制命令幂等、版本化 Plan、自动/人工 Replanning、Plan 修订预算、可靠事件、Transactional Outbox、Redis Streams/WebSocket 基础、异常规范化和旧入口兼容。测试数量以 CI 实际结果为准。

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

## 5. Legacy 本地 Demo

```powershell
python runtime\pipeline.py --repo demo\sample_python --approval confirm --output-dir out\demo
```

预期结果：
- 诊断出 `mutable-default-argument`、`bare-except`、`hardcoded-secret`
- 前两项经审批后自动修复，`hardcoded-secret` 仅报告
- 测试通过，报告写入 `out\demo\report.md`，任务状态 `completed`

`--approval auto` 用于自动化测试；真人演示使用 `confirm` 走审批分支。

## 6. 失败与回滚路径

自动化用例：`tests/test_pipeline.py::test_pipeline_rolls_back_when_verification_fails`

手工复现：把 `demo/sample_python/tests/test_app.py` 改成必失败用例，再运行 Demo，观察：
- Verification 判定失败
- 源文件恢复为修改前内容
- 任务状态 `failed`，报告中保留失败证据

## 7. Legacy MCP Server 冒烟

```powershell
python -m mcp run mcp\git_server.py
python -m mcp run mcp\testing_server.py
```

生产环境通过 Higress 注册后，在 Element 中让 Worker 调用 Git/Testing 工具验证。

## 8. Legacy AgentTeams 集成冒烟

1. 打开 Element：`http://127.0.0.1:18088`，登录管理员账号。
2. 给 Manager 发任务，例如：“对 A:\agent\devpilot-infra\demo\sample_python 做一次缺陷诊断”。
3. 观察 Manager -> Team Leader -> Worker 的消息流转。
4. 验证资源状态：

```powershell
docker exec hiclaw-controller hiclaw get workers
docker exec hiclaw-controller hiclaw get teams
```

5. 查看 Worker 日志确认无鉴权或工具调用错误。

## 9. Java 示例（兼容链路）

```powershell
python runtime\pipeline.py --repo demo\sample_spring --approval auto --output-dir out\spring
```

预期：检测到 `n-plus-one-candidate` 与 `sql-injection-candidate`；因无自动修复模板，仅输出报告，不做代码改动。
