# Langfuse 追踪与评估准备

项目使用 Langfuse Python SDK 4.15.2，依赖已纳入 `pyproject.toml` 和 `requirements.lock`。采用官方 OpenAI 集成记录真实模型请求，DevPilot 在外层补充任务、图节点、Agent 和工具层级。没有接入 LangChain 回调，避免与原生 OpenAI 请求重复记录。

## 本地运行

在项目根目录 `.env` 中配置以下变量，真实密钥不要提交到 Git：

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://localhost:3000
LANGFUSE_TRACING_ENVIRONMENT=development
LANGFUSE_TRACING_ENABLED=true
```

CLI 的环境加载器同时读取 `DEVPILOT_*` 和 `LANGFUSE_*`，且保留进程中已有的环境变量。SDK 在环境加载完成后延迟初始化。直接调用 Python API 时，应先调用 `load_devpilot_env()` 或设置进程环境。

```powershell
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m devpilot api --host 127.0.0.1 --port 8000
```

如果服务已经启动，需要重启后端，单独刷新前端不会加载新代码或环境变量。前端无需接触 Langfuse 密钥。

## DevPilot 也运行在 Docker 中

`compose.yaml` 已将 Langfuse 配置传给 API 容器。容器中的 `localhost` 指向容器自身，因此默认通过已配置的 host gateway 访问宿主机发布的 3000 端口：

```dotenv
DEVPILOT_LANGFUSE_DOCKER_BASE_URL=http://host.docker.internal:3000
```

如果 Langfuse 使用其他端口或网络地址，修改这个变量。然后重建 API 服务：

```powershell
docker compose up -d --build api
```

## 实际记录的数据

- 每次图调用是一个 `devpilot-task-run` trace，输入为任务需求，输出为状态、验证结果、修改文件和总结。
- 同一任务的调用使用 `task_id` 作为 session；`run_id`、父运行 ID、是否恢复、实际任务模型和基线 revision 放在 metadata 中。
- 节点下的 `planning`、`diagnosis`、`patch_generation`、`review` 为 Agent 观测。每次物理模型请求由 OpenAI 集成产生一条 generation，包括提示词、输出、模型、Token 明细、耗时和错误；结构化输出降级中的请求也分别记录。
- 工具观测与请求它的 generation 同属 Agent。直接由运行时调用的工具位于对应图节点下。
- SDK 在发送前复用项目审计脱敏，移除敏感字段、Bearer 凭证及配置中的密钥文本；超长字符串按审计规则截断到 16,000 字符，完整证据仍保留在本地 artifact store。
- 未配置密钥或 `LANGFUSE_TRACING_ENABLED=false` 时不启用追踪；SDK 初始化、写入或导出失败不会改变任务结果或吞掉业务异常。CLI / 服务关闭时会 flush 队列。

## 自检

```powershell
.\.venv\Scripts\python -m scripts.check_langfuse
```

该命令检查认证，在 `out/langfuse-smoke/` 中建立独立示例 Git 仓库，运行测试和真实 Agent 流程，并 flush 数据；会使用配置的模型并产生调用费用。输出的 `langfuse_session_id` 可用于查找本次追踪。已有项目的源代码和任务数据库不会被自检修改。

## 开始评估

本次接入完成的是可观测性和评估所需的数据采集，不会自动创建 LLM-as-a-judge、数据集或质量分数。可以围绕以下内容配置后续评估：

| 对象 | 观测名称 | 可用证据 |
|---|---|---|
| 整个任务 | `devpilot-task-run` | 需求、最终状态、验证结果、总结 |
| 计划 | `planning` / `generate-planning` | 实际模型输入、结构化计划 |
| 诊断 | `diagnosis` / `generate-diagnosis` | 诊断结果、上下文、工具证据 |
| 修改 | `patch_generation` / `generate-patch_generation` | 修改建议与模型输出 |
| 审查 | `review` / `generate-review` | 审查结论与验证上下文 |

优先明确验收标准，再建立代表性数据集并配置评估器。若 Langfuse 不认识所用的自定义模型名称，Token 和耗时仍可记录，但费用为空；需要为该模型设置价格，不能把空费用当成零费用。

官方参考：[接入流程](https://langfuse.com/docs/observability/get-started)、[OpenAI Python 集成](https://langfuse.com/integrations/model-providers/openai-py)、[追踪最佳实践](https://langfuse.com/docs/observability/best-practices)。
