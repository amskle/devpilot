# 开源与合规说明

## 开源范围

- 计划开源：核心 Skill、MCP 契约、评测数据集、示例仓库。
- 协议：Apache-2.0（与 AgentTeams 一致）。

## 第三方依赖

- AgentTeams（Apache-2.0）
- Python MCP SDK、python-docx / python-pptx / reportlab（文档工具）
- Redis / PostgreSQL / Qdrant（状态、记忆与检索）
- LLM 服务：通过 LLM 网关统一接入，具体服务商与模型在部署配置中披露

## 模型与 API

- 允许使用商业 API 与闭源模型，但必须披露调用环节、费用假设、权限范围、可替代性与锁定风险。
- 默认使用 OpenAI 兼容网关（如 DeepSeek / 百炼 / Qwen），不将模型密钥写入 Worker。

当前运行时通过可配置 OpenAI-compatible Chat Completions 端点接入模型，可使用 DashScope/Higress 等兼容网关；API Key 仅从本机环境变量读取，不进入代码仓库、GraphState、事件或 Artifact。AgentTeams 配置仅为 legacy 迁移资产。

## 数据与授权

- 用户代码库数据仅用于本次任务分析；复赛/决赛材料需明确数据来源、授权状态与脱敏方式。
- 评测数据、示例代码与复盘规则可开放，但不得包含未授权企业数据与个人隐私。

## 可复现性

- 提供 README、部署说明、环境要求、样例输入输出、测试命令与评测结果。
- 决赛封版后按 README 应可复现核心 Demo。
