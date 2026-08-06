# Code Analysis Skill

对仓库源码做静态结构分析。Python 使用 `ast`，Java 使用轻量启发式解析；返回文件、类、函数和调用线索。

## 失败处理

- 文件无法解析：跳过并记录 warning。
- 无匹配源码：返回空结构，不报错。
