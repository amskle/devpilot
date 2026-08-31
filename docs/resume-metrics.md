# 可复现项目指标与简历取数

本页用于生成可审计的项目量化指标。指标分为三类：版本控制中的静态事实、自动化质量门、带机器上下文的本地微基准。真实模型质量和分布式运行指标需要额外环境，不能由单元测试替代。

## 一键采集

在仓库根目录执行：

```powershell
.\.venv\Scripts\python scripts\collect-project-metrics.py `
  --run-tests `
  --benchmark `
  --transitions 1000 `
  --artifacts 100 `
  --output-dir out\resume-metrics
```

输出包括：

- `project-metrics.json`：机器可读原始结果；
- `project-metrics.md`：简历取数摘要；
- `evidence/backend-junit.xml` 和 `frontend-junit.xml`：测试数量证据；
- `evidence/*.log`：测试及类型检查日志。

采集器返回非零退出码表示质量门失败或数据完整性检查失败。静态统计会读取当前代码，而不是把节点数、接口数等数字硬编码进报告。

## 可以直接使用的指标

以下指标可在注明版本或采集日期后用于简历：

- LangGraph 工作流节点、Agent、Skill、API 和预算边界数量；
- 后端和前端通过、失败、跳过的测试数量；
- 指定次数的 SQLite Event/Projection 连续转换中，事件序列缺口、Revision 不一致和未确认事件数量；
- 指定数量与大小的内容寻址 Artifact 往返校验失败数量。

吞吐和延迟属于本机微基准。引用时必须同时注明操作数量、机器环境和“本地基准”，不能写成生产 QPS、SLA 或并发能力。

## 真实模型功能评测

这部分需要模型凭据和一组干净、已提交的 Git 仓库，无法由离线测试替代。

### 1. 准备数据集

建议准备至少 20 个 Case，并分成以下类型：

| 类型 | 建议数量 | 目标 |
|---|---:|---|
| 无需修改 | 4 | 检验误修改率和 `COMPLETED_NO_CHANGES` |
| Python 缺陷修复 | 6 | 检验状态、文件定位和验证结果 |
| Java/npm 缺陷修复 | 4 | 检验跨技术栈能力 |
| 高风险修改 | 3 | 检验审批召回率 |
| 敏感路径修改 | 3 | 检验策略拒绝率 |

每个仓库必须是干净的 Git 根目录，并固定 `revision`。示例：

```yaml
name: resume-evaluation
version: "1"
cases:
  - case_id: python-fix-001
    repo: C:\eval-repos\python-fix-001
    request: 修复失败测试并保持改动最小
    revision: 0123456789abcdef0123456789abcdef01234567
    expectation:
      statuses: [COMPLETED]
      changed_files: [app.py, tests/test_app.py]
      verification_passed: true
      requires_approval: false
```

### 2. 运行评测

```powershell
Copy-Item .env.example .env
notepad .env
```

CLI 会自动从项目根目录 `.env` 加载模型名称、Base URL 与 API Key，无需在每个 PowerShell 会话中临时设置。

先从实际模型供应商的控制台或账单确认每百万 Token 的输入、输出单价，并写入本机价格目录。模型名必须与 `DEVPILOT_MODEL` 完全一致；不要把未知价格填成 `0`：

```powershell
$modelName = "deepseek-v4-flash-0731"
$promptPrice = Read-Host "普通输入价格（每百万 Token，USD）"
$completionPrice = Read-Host "输出价格（每百万 Token，USD）"
$pricingDir = Join-Path $env:USERPROFILE ".devpilot\pricing"
New-Item $pricingDir -ItemType Directory -Force | Out-Null
$models = @{}
$models[$modelName] = @{
  prompt_per_million = [string]$promptPrice
  completion_per_million = [string]$completionPrice
}
@{ models = $models } |
  ConvertTo-Json -Depth 4 |
  Set-Content (Join-Path $pricingDir "catalog.json") -Encoding utf8
```

先运行覆盖 5 类行为的冒烟集。CLI 会在每个 Case 完成后向控制台输出进度；最终 JSON 仍可单独保存：

```powershell
.\.venv\Scripts\python -m devpilot eval run `
  --dataset .\resume-evaluation-smoke.yaml |
  Tee-Object .\out\evaluation\smoke.json
```

冒烟集全部进入预期状态后，再运行完整数据集：

```powershell
.\.venv\Scripts\python -m devpilot eval run `
  --dataset .\resume-evaluation.yaml
```

至少重复运行 3 次。记录每次报告 ID，然后计算均值与标准差，避免把单次随机结果当作稳定结论。应收集：

- `average_score`、`status_accuracy`；
- `verification_accuracy`、`approval_accuracy`；
- `changed_files_f1`；
- 每 Case 平均 Prompt/Completion Token；
- 每 Case 平均费用和耗时；
- 错误 Case 数与错误原因分布。

只有报告中的 `metrics.cost_available` 为 `true` 时，`total_cost` 才能作为费用指标；为 `false` 表示缺少该模型价格，而不是模型免费。

对比模型或 Prompt 时必须使用相同的 `dataset_digest`：

```powershell
.\.venv\Scripts\python -m devpilot eval compare `
  --baseline BASELINE_EVALUATION_ID `
  --candidate CANDIDATE_EVALUATION_ID
```

只有重复实验仍能保持的差值，才适合写成“准确率提升”或“Token 降低”。

## Redis 多 Worker 与实时事件测试

这部分需要真实 Redis 和多个 API Worker。建议在隔离测试环境操作：

1. 使用部署配置启动 Redis、API 和 Nginx，将 `DEVPILOT_API_WORKERS` 设为 4。
2. 确认 `/api/ready` 返回就绪，并保存容器版本、CPU、内存和 Redis 配置。
3. 创建至少 50 个固定任务，通过持久 Event API 和 WebSocket 同时记录事件。
4. 对每个 Run 检查事件序号连续、无重复，持久补拉结果与实时事件集合一致。
5. 在任务运行中短暂重启一个 API Worker，再确认票据、限流和事件补拉仍符合预期。
6. 统计任务数、事件总数、丢失/重复事件数、端到端事件延迟 P50/P95/P99 和恢复耗时。

未完成以上测试前，只能写“支持 Redis Streams 多 Worker 实时事件同步”，不能写吞吐、可用性或零丢失百分比。

## 人工验收记录模板

每次功能测试至少保存以下字段：

```text
commit:
model / prompt version:
dataset digest:
machine / deployment:
case count:
successful / failed / waiting approval:
status accuracy:
verification accuracy:
approval accuracy:
changed-files F1:
average tokens per case:
average cost per case:
duration P50 / P95:
event loss / duplication:
notes and known limitations:
```

不要根据单次 Demo 推导成功率，也不要把单机 SQLite 微基准描述为分布式生产性能。
