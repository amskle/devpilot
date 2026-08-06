# 系统架构

```text
用户 / CI 系统
      ↓
Manager（Supervisor：任务规划、调度、审批策略）
      ↓
Shared State（Redis + Event Bus + Trace）
      ↓
Planning Worker（Team Leader）
      ├── Diagnosis Worker
      ├── Modification Worker
      ├── Verification Worker
      └── Review Worker
      ↓
Skill 能力层（8 个核心 Skill）
      ↓
MCP 工具层（Git / Testing）
      ↓
Execution Runtime（Docker 沙箱）
      ↓
Engineering Memory（向量库 + 规则库）
      ↓
OpenTelemetry Trace / Log / Metrics + AgentLoop
```

## 八环节闭环

1. 任务输入：用户提交仓库与任务描述
2. 任务拆解：Manager 拆解并委派 Team Leader
3. 上下文传递：Shared State + Matrix Room + 结构化消息
4. 工具调用：Skill → MCP → 外部工具
5. 结果验证：Verification Worker 在 Docker 沙箱执行测试
6. 执行证据沉淀：日志、Trace、Metrics、报告
7. 审批与回滚：风险分级审批、失败自动回滚、审计链
8. 经验沉淀：Review Worker 生成规则并写入 Engineering Memory

## AgentTeams 映射

- Manager 资源：`agentteams/manager.yaml`
- Worker 资源：`agentteams/workers/*.yaml`
- Team 资源：`agentteams/team.yaml`
- Human 资源：`agentteams/human.yaml`

本地模式由 `runtime/pipeline.py` 模拟相同状态机，便于无 Docker 环境开发；接入 AgentTeams 后由 Manager 与 Matrix Room 承担消息协作。
