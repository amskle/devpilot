### Phase 0：执行契约与 ADR

- LangGraph 唯一编排、自研 Agent Runtime 的架构决策记录。
- Node 输入输出、状态、错误和预算语义。
- Workspace 隔离、审批边界和恢复规范。
- MVP/生产存储映射及模型、Checkpoint 适配方案。
- 完成并验收以下 ADR：
  - [ADR-0001：AgentRunner 工具循环](docs/adr/0001-agent-runner-tool-loop.md)
  - [ADR-0002：GraphState 初始化与 Reducer](docs/adr/0002-graph-state-initialization-and-reducers.md)
  - [ADR-0003：ChangeRequest 与 ReplanRequest](docs/adr/0003-change-request-and-replan-request.md)
  - [ADR-0004：ModelGateway 结构化输出](docs/adr/0004-model-gateway-structured-output.md)
  - [ADR-0005：工具错误与重试边界（已被 ADR-0007 替代）](docs/adr/0005-tool-error-and-retry-boundaries.md)
  - [ADR-0006：Chat 与控制操作边界](docs/adr/0006-chat-and-control-boundaries.md)
  - [ADR-0007：工具级重试统一由 ToolExecutor 执行](docs/adr/0007-tool-executor-retry-ownership.md)

### Phase 1：最小可靠运行时

- LangGraph 主流程和轻量 Agent Runtime。
- 原有 8 个 Skill 适配。
- SQLite Checkpoint、本地 Artifact Store。
- 每任务独立 Git worktree。
- Patch 生成、风险判断、CLI 审批、应用和验证闭环。
- 最大迭代、失败路由、回滚补偿和恢复测试。

### Phase 2：Plan 与 Replanning

- 结构化、版本化 Plan。
- ReplanRequest 和 Planning Agent 复用。
- Plan 原子切换和审计。

### Phase 3：可靠事件

- Event Store / Outbox 先写、Redis Streams 后发。
- 游标补拉、脱敏、WebSocket 基础和完整 Trace。

### Phase 4：FastAPI 与 Human-in-the-loop

- 任务、审批、取消、回滚、恢复和事件 API。
- 认证、授权、幂等和并发控制。

### Phase 5：Vue3 前端

- Dashboard、Task Detail、Timeline、Diff、测试报告。
- 风险审批、人工介入、恢复操作和 ChangeRequest。

### Phase 6：分层记忆

- PostgreSQL 结构化记忆。
- 开发环境必须使用 Milvus Lite，生产可替换为 Milvus。
- 将历史 Bug Pattern 向量化写入 Semantic Memory，并保留可追溯的来源引用。
- Diagnosis 开始前检索相似历史问题；前端展示命中的历史经验、相似度和来源。
- 按策略检索、Token 限制、来源标注和 Prompt Injection 防护。

### Phase 7：Replay 与评测

- Event Replay、State Replay。
- RecoveryPoint Fork / Re-run。
- 评测集、指标和模型/Prompt 对比。

---