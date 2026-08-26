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
- Phase 1 通过 workspace 路径边界和 Tool 白名单隔离；容器沙箱是后续增强项。
- 硬编码密钥由 SecurityScan 检测并仅报告，不自动修复。
