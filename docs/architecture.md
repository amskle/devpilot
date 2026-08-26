# 系统架构

> 当前架构自 v0.2 起以 LangGraph 为唯一编排器。下方旧 AgentTeams 图仅保留为迁移背景，不再描述默认执行链。

```text
CLI / Python API
      ↓
TaskService（控制命令、惰性超时、乐观锁）
      ↓
LangGraph（GraphState、Checkpoint、Interrupt、条件路由）
      ↓
AgentRunner（一次节点授权的有限模型/工具循环）
      ↓
ToolExecutor（白名单、Schema、路径、重试、预算）
      ↓
Skill / Git Worktree / Test Execution
      ↓
Artifact Store + Event/Task Projection + SQLite Checkpoint
```

Phase 1 的执行与一致性语义见 [phase1-execution-contract.md](phase1-execution-contract.md)。

## Legacy 架构图

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

## Legacy AgentTeams 映射

- Manager 资源：`agentteams/manager.yaml`
- Worker 资源：`agentteams/workers/*.yaml`
- Team 资源：`agentteams/team.yaml`
- Human 资源：`agentteams/human.yaml`

这些声明文件仅作为旧比赛资产和迁移来源保留。`runtime/pipeline.py` 是新 TaskService 的兼容投影，不再模拟或选择旧后端。
