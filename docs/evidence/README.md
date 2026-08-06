# 运行证据与度量

本目录提供 DevPilot Infra 闭环运行的度量指标与 Trace 样本结构，用于复赛评估对齐。

## 文件说明

- `metrics-sample.json`：一次完整闭环的度量快照，包含 Pipeline 阶段耗时、缺陷检测与修复结果、验证结果、知识沉淀、OTel Trace Span 结构与核心指标。

## 度量指标定义

| 指标 | 含义 | 采集方式 |
|------|------|---------|
| `issues_detected` | 检测到的缺陷总数 | bug-detection + security-scan 汇总 |
| `issues_fixed` | 自动修复的缺陷数 | patch-generate 成功应用数 |
| `fix_rate` | 修复率 = fixed / detected | 计算值 |
| `false_positive_rate` | 误报率 | LLM-as-Judge 或人工标注 |
| `verification_pass_rate` | 验证通过率 = passed / total | test-execution 结果 |
| `auto_approval_rate` | 自动审批率 | risk-assessment Low 占比 |
| `rollback_triggered` | 是否触发回滚 | pipeline 状态机 |

## Trace 结构

每个 Pipeline 阶段对应一个 OTel Span，父 Span 为 `pipeline.run`，子 Span 为各 Skill 调用。在 AgentTeams 模式下，Span 由 AgentLoop 自动生成并上报至可观测平台。
