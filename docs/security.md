# 安全边界与风险控制

## 修改审批

- Patch Generation Agent 只生成不可变 Patch Artifact，不直接修改代码。
- RiskAssessment 输出 `AUTO_ALLOWED / APPROVAL_REQUIRED / DENY`；敏感路径、路径逃逸和禁止区域直接 `DENY`，不可人工绕过。
- 审批绑定 `approval_id`、`patch_hash`、`base_revision` 和 `expected_state_revision`；CLI-only 阶段在所有控制入口惰性检查过期时间。

## 回滚与审计

- 每任务使用独立 clone/worktree，Apply 前创建 RecoveryPoint，源仓库工作树保持只读。
- 验证失败或无进展执行 Git 补偿回滚，完整 Restore 创建新 run。
- 审计链记录阶段摘要、Agent、Tool、Artifact 引用、测试结果、审批和预算，不记录隐藏推理。

## 凭证与执行隔离

- API Key 只从 `DEVPILOT_MODEL_API_KEY` 读取，不写入 State、Event 或 Artifact。
- 默认管理员 Token `devpilot-local` 仅在 development 可用；非开发环境缺少显式 API Token 时启动失败。
- 生产 WebSocket 票据和限流状态存入 Redis。票据仅以哈希 Key 保存并原子消费；Redis 不可用时 fail-closed。
- Phase 1 通过 workspace 路径边界和 Tool 白名单隔离；容器沙箱是后续增强项。
- 硬编码密钥由 SecurityScan 检测并仅报告，不自动修复。

## Replay 与评测

- Event/State Replay 只读取 Event Store 和 Checkpoint，不调用模型、工具或控制命令。
- RecoveryPoint Fork 和评测是显式执行操作，只在隔离 worktree 中运行，不修改源 Task 或源代码仓库。
- Prompt Override 只能改变 Agent instructions，不能改变 Tool 白名单、路径、预算、审批或恢复策略。
- 评测数据集中的仓库路径属于本机受信任运维输入；Phase 7 首版不向普通远程 API 暴露批量评测。
