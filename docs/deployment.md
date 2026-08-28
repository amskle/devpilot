# 部署说明

> Phase 1 默认部署方式是本地 CLI + SQLite + 独立 Git worktree。AgentTeams 部分仅为 legacy 资产说明。

## 本地开发模式

```powershell
cd A:\agent\devpilot-infra
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
$env:DEVPILOT_MODEL_API_KEY = "..."
$env:DEVPILOT_MODEL_BASE_URL = "https://compatible-endpoint/v1"
$env:DEVPILOT_MODEL = "model-name"
python -m devpilot task create --repo C:\path\to\clean-repo --request "修复失败测试"
```

## Phase 6 API

本机开发可使用默认 Token `devpilot-local` 启动，并在 `/docs` 中授权试调：

```powershell
python -m devpilot api --host 127.0.0.1 --port 8000
```

开发模式使用默认管理员 Token 时会打印安全警告。该默认值不得用于共享环境。

共享环境必须覆盖默认 Token：

```powershell
$env:DEVPILOT_API_TOKENS = '{"replace-with-long-random-token":{"subject":"operator-1","admin":true}}'
$env:DEVPILOT_API_CORS_ORIGINS = "https://devpilot.example.com"
$env:DEVPILOT_ENV = "production"
$env:DEVPILOT_REDIS_URL = "redis://127.0.0.1:6379/0"
python -m devpilot api --host 0.0.0.0 --port 8000 --workers 4
```

生产和多 worker 模式必须使用 Redis。当前本机安装位于 `A:\Redis` 时，可先确认 Windows 服务 `Redis` 已启动，再检查 `GET /api/ready`。所有 worker 还必须共享同一个 `DEVPILOT_DATA_DIR`。完整接口说明见 [Phase 4 控制面](phase4-fastapi-control-plane.md)，分布式部署语义见 [Phase 6 控制面](phase6-distributed-control-plane.md)。

## Legacy AgentTeams 模式

1. 启动 Docker Desktop，并安装 AgentTeams：

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; $wc=New-Object Net.WebClient; $wc.Encoding=[Text.Encoding]::UTF8; iex $wc.DownloadString('https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.ps1')
```

2. 应用资源（本机控制器容器内已内置 `hiclaw` CLI，等价于文档中的 `agt`）：

```bash
docker exec hiclaw-controller hiclaw status
docker cp agentteams/workers hiclaw-controller:/tmp/workers
docker exec hiclaw-controller hiclaw apply -f /tmp/workers
docker exec hiclaw-controller hiclaw apply -f agentteams/team.yaml
docker exec hiclaw-controller hiclaw apply -f agentteams/human.yaml
```

说明：当前安装镜像的 Team 契约使用 `spec.leader.name + spec.workers`，与仓库内 `agentteams/team.yaml` 一致。Manager 资源继续使用平台自带 `default`，`manager.yaml` 作为扩展配置保留。

3. 上传 Worker 包（包含 `skills/` 与运行配置）：

```bash
cd skills && zip -r ../devpilot-skills.zip . && cd ..
agt apply -f - <<EOF
apiVersion: agentteams.io/v1beta1
kind: Worker
metadata:
  name: devpilot-diagnosis
spec:
  package: file://./devpilot-skills.zip
EOF
```

4. 部署 MCP Server 并注册到 Higress 网关。

## 环境要求

- Python 3.10+
- Docker Desktop 4.x（AgentTeams 模式）
- 最低 2C4GB，建议 4C8GB
