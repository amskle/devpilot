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

## Phase 4 API

本机开发可使用默认 Token `devpilot-local` 启动，并在 `/docs` 中授权试调。启动日志会打印安全警告；该 Token 只允许用于本机开发：

```powershell
python -m devpilot api --host 127.0.0.1 --port 8000
```

共享环境必须覆盖默认 Token：

```powershell
$env:DEVPILOT_ENV = "production"
$env:DEVPILOT_API_TOKENS = '{"replace-with-long-random-token":{"subject":"operator-1","admin":true}}'
$env:DEVPILOT_API_CORS_ORIGINS = "https://devpilot.example.com"
python -m devpilot api --host 0.0.0.0 --port 8000
```

当前 API 必须以单进程、单 worker 运行。`EventTicketStore` 和 `RateLimiter` 是进程内状态；使用 `uvicorn --workers 2`（或更大值）会导致票据随机校验失败，并使限流按进程分裂。若绕过 DevPilot CLI 直接启动 Uvicorn，必须显式使用 `--workers 1`：

```powershell
uvicorn devpilot.api.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1
```

完整接口、安全和试调说明见 [Phase 4 控制面](phase4-fastapi-control-plane.md)。

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
