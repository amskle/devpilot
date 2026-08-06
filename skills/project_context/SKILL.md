# Project Context Skill

读取仓库根目录，识别项目类型、技术栈、构建工具、关键配置和顶层结构。用于 Diagnosis 与 Planning 的上下文初始化。

## 调用条件

- 任务开始或 Agent 需要理解仓库时调用。

## 失败处理

- 目录不存在：返回错误，不猜测。
- 无法识别构建工具：返回 `build_tool: unknown`。
