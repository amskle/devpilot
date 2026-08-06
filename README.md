# DevPilot Infra

面向软件研发全生命周期的多 Agent 协同优化基础设施，以 AgentTeams 为协同设计基点，覆盖项目理解、缺陷发现、根因分析、优化规划、安全修改、自动验证、经验沉淀与能力进化。

## 项目结构

```text
agentteams/       AgentTeams 声明式资源（Manager/Worker/Team/Human）
skills/           8 个核心 Skill（metadata + executor + tests）
mcp/              MCP Server（Git / Testing）
shared_state/     共享状态与事件模型
runtime/          本地编排管线（状态机、审批、报告）
demo/             Spring Boot 示例场景
docs/             设计、安全、部署、合规文档
tests/            冒烟测试
```

## 快速开始（本地模式）

```powershell
python runtime/pipeline.py --repo demo/sample_python --approval confirm --output-dir out/demo
```

本地模式不依赖 Docker，直接调用 Skill 完成“诊断 -> 规划 -> 修改 -> 验证 -> 复盘”的演示闭环。
示例运行证据见 `docs/examples/`。

## AgentTeams 模式

1. 启动 Docker Desktop 并安装 AgentTeams（官方安装脚本）。
2. 确认平台状态后，通过控制器容器内的 `hiclaw` CLI 应用资源：

```powershell
docker exec hiclaw-controller hiclaw status
docker cp agentteams/workers hiclaw-controller:/tmp/workers
docker exec hiclaw-controller sh -c "for f in /tmp/workers/*.yaml; do hiclaw apply -f \"\$f\"; done"
docker exec hiclaw-controller hiclaw apply -f agentteams/team.yaml
docker exec hiclaw-controller hiclaw apply -f agentteams/human.yaml
```

3. 将 `skills/` 打包为 `devpilot-skills.zip` 并上传为 Worker package。
```powershell
cd skills
Compress-Archive -Path * -DestinationPath ..\devpilot-skills.zip -Force
cd ..
```
4. 将 `mcp/` 部署为 MCP Server，并在 Higress 网关注册。

当前已验证环境：模型 `qwen3.7-flash`（DashScope 兼容模式）经 Higress AI 网关调用返回 200；Manager `default`、Worker `devpilot-*`、Team `devpilot-team`、Human `code-reviewer` 已上线。Element Web 访问 `http://127.0.0.1:18088`。

## 测试

```powershell
python -m pytest skills tests -q
```

测试覆盖 8 个 Skill、本地端到端闭环、失败自动回滚。详细测试说明见 `docs/testing.md`。

## 合规与披露

本项目参赛方案以 AgentTeams 为多 Agent 协同设计基点；Skill 为必选项，MCP 为工具连接层；高风险代码修改采用分级审批、自动回滚与全链路审计。第三方依赖、商业 API、闭源模型、数据来源与授权边界见 `docs/compliance.md`。
