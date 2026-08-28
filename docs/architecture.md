# 系统架构

> 当前架构自 v0.2 起以 LangGraph 为唯一编排器。下方旧 AgentTeams 图仅保留为迁移背景，不再描述默认执行链。

```text
CLI / Python API
      ↓
TaskService（稳定门面）
      ├── TaskRuntimeCore（依赖装配、模型选择、Checkpoint）
      ├── TaskCommands（审批、取消、重规划、变更、恢复执行）
      ├── TaskRecoveryCommands（回滚、恢复、对账）
      └── TaskQueries（任务视图、消息、历史与 Trace）
      ↓
LangGraph Nodes + Topology（节点实现、条件路由分离）
      ↓
AgentRunner（一次节点授权的有限模型/工具循环）
      ↓
ToolExecutor（白名单、Schema、路径、重试、预算）
      ↓
Skill / Git Worktree / Test Execution
      ↓
Artifact Store + Control Projection + Plan Store + Outbox + SQLite Checkpoint
```

Phase 6 API 在该持久层之外增加 Redis 分布式短期状态：WebSocket 单次票据、跨 worker 限流以及 Outbox 的实时 Stream。SQLite Event Store 仍是审计真相，客户端始终可以通过持久游标恢复。

Phase 1 的执行与一致性语义见 [phase1-execution-contract.md](phase1-execution-contract.md)，Plan 版本与重规划语义见 [phase2-plan-replanning.md](phase2-plan-replanning.md)。
多 worker 控制面见 [phase6-distributed-control-plane.md](phase6-distributed-control-plane.md)。

## 活跃运行时模块边界

- `devpilot/service.py` 只保留公共 `TaskService` 门面；CLI、API 和兼容层无需感知内部拆分。
- `devpilot/services/task_runtime.py` 负责生命周期和 LangGraph 调用，用户控制命令、恢复命令、查询投影分别位于对应的 `task_*` 模块。
- `devpilot/services/storage.py` 负责任务投影、事件日志与 checkpoint 一致性；制品、Plan 生命周期、Outbox 和幂等记录由独立存储能力模块提供，并组合进 `SQLiteControlStore`。
- `devpilot/orchestration/graph.py` 实现节点行为，`devpilot/orchestration/topology.py` 集中维护节点连接与条件路由。

这些边界是内部结构约束，不改变 `devpilot.service.TaskService` 和 `devpilot.services.storage` 的兼容导入路径。

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
