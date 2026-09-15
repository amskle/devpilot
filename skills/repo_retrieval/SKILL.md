# Repo Retrieval Skill

在隔离工作区内执行离线确定性检索：将文本文件切成固定行窗口（Chunk），用轻量 TF-IDF 对查询词打分，返回按分数排序的片段及 `path:start-end` 引用。用于 Planning 与 Diagnosis 在制定计划或定位问题前获取可溯源的仓库证据。

## 调用条件

- Agent 需要从一个具体问题（如符号名、报错关键字、中文需求描述）定位相关代码位置时调用。
- 查询为空或没有可检索词时返回错误，不做无意义扫描。

## 参数

- `query`：检索词，支持英文/数字标识符、camelCase/snake_case 拆分以及中文（按双字切分）。
- `max_chunks`：返回片段数上限（1–50，默认 5）。
- `max_chunks_per_file`：单个文件最多返回的片段数（1–5，默认 1），避免同一大文件反复扩张模型上下文。
- `chunk_lines` / `overlap_lines`：切分窗口与重叠行数，默认 40/10，作为调参入口。

## 失败处理

- 仓库不存在：返回错误。
- 二进制文件、隐藏路径、忽略目录（node_modules 等）与超限文件被跳过。
- 无匹配片段：返回空 `matches`，不猜测。
- 扫描触及文件、字节或 Chunk 上限时，`coverage.truncated` 为真，并在 `limit_reasons` 中说明原因；调用方不得把此时的空结果当作全仓库结论。

## 安全边界

- 只读；结果中的 `citation`、行号、`content_sha256` 与 `repository_revision` 可共同回查证据版本。
- `excerpt` 围绕实际命中行生成并带行号；`excerpt_truncated` 明确表示是否省略了 Chunk 内容。
