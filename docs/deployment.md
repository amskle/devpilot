# 部署说明

## 本地开发模式

```powershell
cd A:\agent\devpilot-infra
python runtime\pipeline.py --repo demo\sample_python --approval confirm --output-dir out\demo
```

## AgentTeams 模式

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
