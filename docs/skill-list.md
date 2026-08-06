# 核心 Skill 清单

每个 Skill 均包含 `metadata.yaml`、`SKILL.md`、`executor.py` 与 `tests/`，通过 Git Tag 管理版本。

| Skill | 用途 | 输入 | 输出 | 调用条件 | 依赖工具 | 失败处理 | 安全边界 | 复用价值 | 与协同流程的关系 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| project-context | 项目结构与技术栈识别 | repo_path | project_type/tech_stack/build_tool | 任务开始 | 文件系统 | 返回 error | 只读 | 多 Agent 复用 | Diagnosis/Planning 初始化上下文 |
| code-analysis | 语法与调用关系分析 | repo_path/language | files/classes/functions/calls | 诊断阶段 | 文件系统 | 跳过坏文件 | 只读 | 多语言复用 | Diagnosis |
| bug-detection | 常见缺陷检测 | repo_path/language | issues | 诊断阶段 | 文件系统 | 返回空结果 | 只读 | 多项目复用 | Diagnosis |
| security-scan | 安全风险检测 | repo_path | issues | 诊断阶段 | 文件系统 | 返回空结果 | 只读 | 多项目复用 | Diagnosis |
| patch-generate | 生成 Git Diff | target_file/replacements/apply | diff/new_content | 修改阶段 | 文件系统 | 目标不存在报错 | 默认不写盘 | 多 Agent 复用 | Modification |
| risk-assessment | 风险分级评估 | diff | level/score/reasons | 修改前 | 无 | 空 diff 返回 Low | 只读 | 审批流程复用 | Modification/Manager |
| test-execution | 构建与测试执行 | command/cwd/timeout | passed/exit_code/output | 验证阶段 | Docker 沙箱 | 超时判失败 | 沙箱隔离 | 多项目复用 | Verification |
| knowledge-extract | 经验提取与规则生成 | notes | patterns/rule/confidence | 复盘阶段 | Engineering Memory | 返回空规则 | 人工审核后写入 | 跨任务复用 | Review |
