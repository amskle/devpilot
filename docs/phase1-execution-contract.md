# Phase 1 执行契约

本文是 `change2.md`、ADR-0001～0009 在 Phase 1 代码中的约束摘要。

## State 与 DTO

- LangGraph 中的 `GraphState` 始终为 `TypedDict`/plain dict，只含 JSON 可序列化的基本类型、list 和 dict。
- Pydantic 是 DTO 与边界校验层；`to_state_dict()` 使用 JSON mode，任何 Pydantic 实例进入 State 前都必须转换。
- `create_initial_state()` 是唯一初始工厂，`progress_window` 整体替换且最多保留 6 轮。

## Checkpoint、Event 与投影

Phase 1 采用事件优先协议：追加脱敏事件并通过 `state_revision` CAS 更新 Task 投影，LangGraph 随后写 Checkpoint，调用返回后确认 `checkpoint_revision`。Task 表是可修复投影，不是第二份状态真相。

若进程在双写之间退出，启动/读取任务时比较投影与 Checkpoint。`devpilot admin reconcile --task-id ...` 会以持久 Checkpoint 修复投影并写 `projection_reconciled` 审计事件。

## CLI-only 审批

Phase 1 没有后台调度器。`task status`、`task approve`、`task reject` 和 `task resume` 在读取状态时惰性检查 `expires_at`；过期后写 `approval_expired`，清除审批对象并转为 `CANCELLED`。时钟通过 `Clock` 注入，测试不等待真实 24 小时。

## Fake Model

`ScriptedFakeModelGateway` 不模拟推理。测试按 Agent ID 配置一组 `ModelResponse` 或异常，按调用顺序消费；响应可以是最终 JSON、tool calls、非法 Schema、超时或预算耗尽场景。测试可断言调用次数并调用 `assert_consumed()` 检查脚本是否完全消费。

## 失败进展窗口

首次验证失败建立进展基线并允许在迭代预算内自动重诊断。连续两轮无进展、重复修改、A-B-A 震荡或全局预算耗尽才停止自动执行并进入人工介入。验证通过时必须清空上一轮 `latest_failure`，避免成功结果被错误路由回失败分支。Restore 创建新 run 时清空诊断、复盘和进展窗口，避免继承旧 run 的停滞信号。

## Legacy 状态投影

| 新状态 | 旧 `TaskStatus` |
|---|---|
| `CREATED` | `ANALYZING` |
| `RUNNING`（workspace/context） | `ANALYZING` |
| `RUNNING`（planning/diagnosis） | `PLANNING` |
| `RUNNING`（patch/risk/apply） | `MODIFYING` |
| `RUNNING`（verification） | `VERIFYING` |
| `WAITING_RISK_APPROVAL` | `AWAITING_APPROVAL` |
| `COMPLETED` / `COMPLETED_NO_CHANGES` | `COMPLETED` |
| `WAITING_HUMAN_INTERVENTION` / `CANCELLING` / `CANCELLED` / `FAILED` / `POLICY_REJECTED` | `FAILED` |

`runtime.run_pipeline()` 只调用新的 `TaskService`，`approval=auto` 不能绕过 Risk Policy。`runtime/pipeline.py` 仅保留兼容转发，原比赛实现已移至 `runtime/legacy_pipeline.py` 作为迁移参考，不能进入默认执行链。

## 依赖、Skill 与费用

- `requirements.lock` 由 `pip-tools` 生成；CI 先从锁文件安装，再以 `--no-deps` 安装项目，并执行 LangGraph/SQLite import smoke。
- `metadata.yaml` 与 `SKILL.md` 继续作为可读 Skill 描述；代码中的 Pydantic `ToolSpec` 是可执行 Schema、权限、幂等和重试语义的来源。`validate_skill_metadata()` 检查名称一致性。
- `workspace_id` 是 AgentRunner 绑定的可信运行时字段，不暴露给模型选择。Agent 工具 Schema 隐藏该字段，执行前由 Runner 注入当前 Workspace ID；ToolExecutor 仍执行严格一致性校验，确定性节点继续显式携带 Workspace ID。
- Pricing Catalog 默认读取 `DEVPILOT_DATA_DIR/pricing/catalog.json`，仓库不内置真实价格。`max_cost=None` 时不需要价格；配置费用上限但模型无价格时在创建工作区前失败。
- 启用 `max_cost` 时，任务将所选模型和价格表固化为内容寻址快照。每次模型调用前按 `ModelProfile` 最大 Prompt/Completion 用量预留费用，响应后按实际 Token 核算并回补；失败调用保留已预留预算。模型和工具的实际执行时间按整秒向上累计，达到 `max_active_seconds` 后不再发起下一次调用。
- Git worktree 固定使用 `DevPilot <devpilot@local>` 身份提交，不依赖机器全局 Git 配置。
- Patch 状态按 `PROPOSED → WAITING_RISK_APPROVAL/APPROVED → APPLIED → VERIFIED/INVALIDATED` 推进。每次应用新 Patch 前拒绝意外的 tracked 改动，并清理上一轮验证留下的未跟踪构建产物，防止缓存进入 Patch 提交或遮蔽新源码。
- Phase 1 的 `ToolExecutor` 仅在当前进程内按 `operation_id` 去重；进程重启后的持久工具幂等留待可靠任务执行存储补齐。控制命令幂等键已持久化到 SQLite，不受该限制。
