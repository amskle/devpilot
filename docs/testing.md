# 测试手册

## 1. 单元测试

```powershell
python -m pytest skills tests -q
```

覆盖内容：
- 8 个核心 Skill 的输入输出与关键规则
- 本地管线端到端闭环（诊断 -> 修复 -> 验证 -> 复盘）
- 验证失败时自动回滚，源文件恢复原状

## 2. 本地端到端 Demo

```powershell
python runtime\pipeline.py --repo demo\sample_python --approval confirm --output-dir out\demo
```

预期结果：
- 诊断出 `mutable-default-argument`、`bare-except`、`hardcoded-secret`
- 前两项经审批后自动修复，`hardcoded-secret` 仅报告
- 测试通过，报告写入 `out\demo\report.md`，任务状态 `completed`

`--approval auto` 用于自动化测试；真人演示使用 `confirm` 走审批分支。

## 3. 失败与回滚路径

自动化用例：`tests/test_pipeline.py::test_pipeline_rolls_back_when_verification_fails`

手工复现：把 `demo/sample_python/tests/test_app.py` 改成必失败用例，再运行 Demo，观察：
- Verification 判定失败
- 源文件恢复为修改前内容
- 任务状态 `failed`，报告中保留失败证据

## 4. MCP Server 冒烟

```powershell
python -m mcp run mcp\git_server.py
python -m mcp run mcp\testing_server.py
```

生产环境通过 Higress 注册后，在 Element 中让 Worker 调用 Git/Testing 工具验证。

## 5. AgentTeams 集成冒烟

1. 打开 Element：`http://127.0.0.1:18088`，登录管理员账号。
2. 给 Manager 发任务，例如：“对 A:\agent\devpilot-infra\demo\sample_python 做一次缺陷诊断”。
3. 观察 Manager -> Team Leader -> Worker 的消息流转。
4. 验证资源状态：

```powershell
docker exec hiclaw-controller hiclaw get workers
docker exec hiclaw-controller hiclaw get teams
```

5. 查看 Worker 日志确认无鉴权或工具调用错误。

## 6. Java 示例（诊断链路）

```powershell
python runtime\pipeline.py --repo demo\sample_spring --approval auto --output-dir out\spring
```

预期：检测到 `n-plus-one-candidate` 与 `sql-injection-candidate`；因无自动修复模板，仅输出报告，不做代码改动。
