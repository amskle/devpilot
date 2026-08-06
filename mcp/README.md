# DevPilot MCP Servers

实现两个 MCP Server：`git_server.py` 负责仓库读取、分支快照、补丁应用与回滚；`testing_server.py` 负责测试发现与执行。

## 运行

```bash
python -m mcp run git_server.py
python -m mcp run testing_server.py
```

在生产环境通过 Higress AI Gateway 注册，Worker 只持有消费者令牌，真实 Git/Shell 凭证由平台侧管理。

## 安全边界

- `apply_patch` 默认只做 `git apply --check`，不写盘。
- `rollback` 要求显式 `confirm: "yes"`，且基于快照分支执行。
- 所有工具调用在服务端记录审计日志。
