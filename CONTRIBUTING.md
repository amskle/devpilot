# 贡献指南

感谢关注 DevPilot Infra！欢迎通过 Issue 和 Pull Request 参与贡献。

## 开发环境

```powershell
git clone https://github.com/amskle/devpilot-infra.git
cd devpilot-infra
pip install -e ".[dev]"
python -m pytest skills tests -q
```

## 代码规范

- Python 代码遵循 PEP 8，类型标注覆盖所有公开函数。
- 新增 Skill 必须包含 `metadata.yaml`、`executor.py`、`SKILL.md` 和 `tests/test_executor.py`，且测试通过。
- Commit message 使用 Conventional Commits（`feat:`、`fix:`、`docs:`、`test:`、`refactor:`）。

## 新增 Skill 流程

1. 在 `skills/` 下创建目录，命名使用 `kebab-case`。
2. 编写 `metadata.yaml`（name、version、description、inputs、outputs）。
3. 实现 `executor.py`，暴露 `run(context: dict) -> dict` 入口。
4. 编写 `SKILL.md` 描述能力、输入输出与使用示例。
5. 编写 `tests/test_executor.py`，覆盖正常与边界场景。
6. 运行 `python -m pytest skills/<name> -q` 确认通过。
7. 在 `docs/skill-list.md` 登记新 Skill。

## 新增 MCP Server

1. 在 `mcp/` 下创建 `*_server.py`。
2. 更新 `mcp/README.md` 文档。
3. 在对应 Worker YAML 的 `mcpServers` 注释中登记消费关系。

## Pull Request 流程

1. Fork 仓库并创建功能分支（`feat/xxx`、`fix/xxx`）。
2. 确保测试全部通过：`python -m pytest skills tests -q`。
3. 提交 PR 并描述变更内容、动机与测试方式。
4. 等待 CI 通过和 Review。

## 行为准则

保持友善与专业，尊重不同观点。技术讨论聚焦于代码与设计本身。
