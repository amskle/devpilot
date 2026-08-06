# Patch Generate Skill

根据结构化替换规则对目标文件生成 unified diff。默认只产出 diff，不修改磁盘；显式 `apply: true` 时才写盘。用于 Modification Worker 的受控改码流程。
