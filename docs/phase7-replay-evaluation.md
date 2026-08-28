# Phase 7：Replay 与评测

## 目标与边界

Phase 7 提供三类能力：

- Event Replay：离线验证一个 Run 的事件顺序、因果引用、State Revision 单调性和 Checkpoint 确认状态，并生成稳定摘要哈希。
- State Replay：直接读取 LangGraph SQLite Checkpoint 中的真实 GraphState，在指定 Revision 恢复只读快照，并与对应 Event 区间对账。
- RecoveryPoint Fork / Re-run：从恢复点的 Git commit 创建新 Task、新 Run 和新 worktree，保留父 Run 引用，不覆盖或回滚源 Task。

Event Replay 和 State Replay 不调用模型、不执行工具、不修改任务投影。Replay 结果写入独立的 `replay_records` 审计表，因此重复 Replay 不会改变源事件摘要。

现有 Event Envelope 记录审计事实和状态转换元数据，但不是完整 GraphState 的逐事件快照。Phase 7 不伪造 Event Sourcing：完整状态恢复以 Checkpoint 为准，Event Store 用于一致性证明。

## Event Replay

```powershell
python -m devpilot replay events --task-id task_x --run-id run_x
python -m devpilot replay events --task-id task_x --through-sequence 20
```

结果包含：

- 事件数量、首尾序号和事件类型计数；
- 最后一个 State Revision；
- 对完整事件信封计算的 SHA-256 `source_digest`；
- `SEQUENCE_GAP`、`DANGLING_CAUSATION`、`STATE_REVISION_REGRESSION` 等问题；
- 未确认状态事件警告。

## State Replay

```powershell
python -m devpilot replay state --task-id task_x
python -m devpilot replay state --task-id task_x --run-id run_x --state-revision 4
python -m devpilot replay history --task-id task_x
```

State Replay 返回 Checkpoint ID、父 Checkpoint、真实 GraphState、状态摘要和对应事件摘要。只有 Checkpoint 身份、Revision 和 Event Store 对账均通过时，`consistent` 才为 `true`。

## RecoveryPoint Fork

```powershell
python -m devpilot replay fork `
  --task-id task_x `
  --recovery-point-id recovery_x `
  --model candidate-model
```

Fork 从原工作区的 bare Git repository 克隆恢复点 commit。目标 Task 拥有独立 workspace lease 和生命周期；源 Task 保持不变。Fork 是真实重新运行，会调用模型和工具并消耗预算，与只读 Replay 的安全语义不同。

## 评测数据集

评测文件支持 YAML 或 JSON：

```yaml
name: python-no-change
version: "1"
cases:
  - case_id: clean-python
    repo: C:\repos\sample
    request: inspect repository
    revision: HEAD
    expectation:
      statuses: [COMPLETED_NO_CHANGES]
      changed_files: []
      requires_approval: false
```

执行与查询：

```powershell
python -m devpilot eval run --dataset dataset.yaml --model model-a
python -m devpilot eval list
python -m devpilot eval show --evaluation-id eval_x
python -m devpilot eval compare --baseline eval_a --candidate eval_b
```

每个 Case 独立创建 Task。单个 Case 的仓库、模型或运行错误只记为 Case error，不中止整个数据集。报告保存完整 Dataset 快照及其 digest，并记录任务引用、状态准确率、验证准确率、审批准确率、Changed Files precision/recall/F1、Token 与费用。

## Prompt 对比

Prompt Override 文件是 Agent ID 到完整 instructions 的映射：

```yaml
planning: Create a minimal plan with explicit rollback criteria.
diagnosis: Diagnose only from deterministic repository evidence.
```

```powershell
python -m devpilot eval run `
  --dataset dataset.yaml `
  --prompt-version candidate-v2 `
  --prompt-overrides prompts-v2.yaml
```

Override 会真实进入对应 Agent 的 system message，而不只是报告标签。报告保存全部生效 Prompt 的 `prompt_digest`。Tool 白名单、路径边界、预算和审批由运行时强制，Prompt Override 不能绕过这些控制。

只允许比较 `dataset_digest` 相同的报告，避免把数据集变化误判成模型或 Prompt 改进。`prompt_version` 是人类可读标签，`prompt_digest` 是实际内容证据。

## 初始接口范围

Phase 7 首版通过 `TaskService` Python API 和本地 CLI 暴露。批量评测接受本机仓库路径且可能产生模型费用，因此未开放为普通远程 HTTP 接口；未来若进入控制面，应增加管理员权限、数据集注册、费用上限和作业队列。
