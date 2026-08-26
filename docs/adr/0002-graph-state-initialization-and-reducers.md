# ADR-0002：GraphState 初始化与 Reducer

- 状态：Accepted
- 日期：2026-08-26

## 背景

若 GraphState 初始值不完整或 Reducer 语义不明确，节点局部更新、Checkpoint 恢复和后续并行执行会出现字段缺失、重复追加或状态膨胀。

## 决策

Graph 只能通过唯一的 `create_initial_state()` 启动。工厂填充全部键，不依赖节点补默认值，也不使用共享可变默认对象。

```text
schema_version       = 当前 Schema 版本
state_revision       = 0
status               = CREATED
pause_reason         = null
current_node         = workspace_setup
workspace_ref        = null
baseline_context_ref = null
其他 Artifact 引用  = null
progress_window      = 空的固定长度窗口
execution_budget     = 完整预算及零值计数器
```

其中 `cost_used=0`，`cost_currency` 和 `pricing_snapshot_ref` 在任务启动时由模型配置与 Pricing Catalog 固化；不能在执行中切换计价口径。

Phase 1 使用字段替换语义。`progress_window` 不使用无限 append Reducer，由 Evaluate Progress 节点完成去重、裁剪后整体替换，最多保留最近 4～6 轮。事件、消息、日志和完整失败历史不进入 Reducer。

Phase 1 主流程保持串行，不定义集合合并 Reducer。未来引入并行 Diagnosis 时，必须通过显式 Merge Node 按稳定 ID 去重；在新 ADR 和并发测试完成前，不允许为同一 State slice 增加多个并行写入者。

`state_revision` 由状态持久化边界以乐观锁方式递增，不使用简单加法 Reducer。

## 后果

- 节点可以安全返回局部更新。
- Checkpoint 大小有明确上限。
- 并行化需要显式设计，不能依赖隐式列表拼接。

## 验收条件

- 初始工厂每次返回独立对象且包含全部键。
- 首节点、空结果和恢复分支不存在 KeyError。
- `progress_window` 永远不超过上限。
- State → Checkpoint → Restore 字段等值。
- 并发写入旧 `state_revision` 时只有一个提交成功。
