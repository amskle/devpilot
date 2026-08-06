# 安全边界与风险控制

## 修改审批

- Modification Worker 只生成 diff，不直接修改代码。
- RiskAssessment 输出 Low / Medium / High：
  - Low：自动执行
  - Medium：人工确认
  - High：强制审批
- 本地模式 `--approval confirm` 启用人工确认；`--approval auto` 仅用于开发/自动化测试。

## 回滚与审计

- 应用修改前保存原文件快照，验证失败自动恢复。
- AgentTeams 模式使用 Git 快照分支，`rollback` 必须显式确认。
- 审计链记录用户、Agent、Skill、修改内容、测试结果与审批状态。

## 凭证与执行隔离

- Worker 不持有真实 API Key / Git PAT，统一由 Higress AI Gateway 管理。
- 构建与测试在 Docker 沙箱执行，限制网络与文件系统权限。
- 硬编码密钥由 SecurityScan 检测并仅报告，不自动修复。
