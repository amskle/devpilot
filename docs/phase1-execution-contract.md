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

`runtime.run_pipeline()` 只调用新的 `TaskService`，`approval=auto` 不能绕过 Risk Policy。

## 依赖、Skill 与费用

- `requirements.lock` 由 `pip-tools` 生成；CI 先从锁文件安装，再以 `--no-deps` 安装项目，并执行 LangGraph/SQLite import smoke。
- `metadata.yaml` 与 `SKILL.md` 继续作为可读 Skill 描述；代码中的 Pydantic `ToolSpec` 是可执行 Schema、权限、幂等和重试语义的来源。`validate_skill_metadata()` 检查名称一致性。
- Pricing Catalog 默认读取 `DEVPILOT_DATA_DIR/pricing/catalog.json`，仓库不内置真实价格。`max_cost=None` 时不需要价格；配置费用上限但模型无价格时在创建工作区前失败。
- Git worktree 固定使用 `DevPilot <devpilot@local>` 身份提交，不依赖机器全局 Git 配置。
