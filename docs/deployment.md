# 部署说明

> 当前推荐的图形化部署方式是 Docker Compose + Nginx；本地 CLI 继续适用于开发和离线管理。

## Docker Compose 快速部署

Compose 启动四个服务：一次性数据卷初始化、Redis、FastAPI 和承载 Vue 3 静态文件的
Nginx。只有 Nginx 对宿主发布端口，浏览器通过同一 Origin 访问 REST 与 WebSocket，
因此不需要额外配置 CORS。

### 1. 准备配置

Windows Docker Desktop：

```powershell
Copy-Item .env.docker.example .env
notepad .env
```

Linux：

```bash
cp .env.docker.example .env
${EDITOR:-vi} .env
```

至少修改以下配置：

- `DEVPILOT_REPOSITORY_ROOT_HOST`：宿主机上存放目标 Git 仓库的共同根目录。Windows
  使用 `C:/repos` 形式，Linux 可使用 `/srv/repos`。
- `DEVPILOT_MODEL_API_KEY`、`DEVPILOT_MODEL_BASE_URL` 和 `DEVPILOT_MODEL`：模型连接信息。
- `DEVPILOT_API_TOKENS`：完整 JSON 对象；生产模式下 Token 至少 32 字符。

`.env` 已被 Git 忽略，但运行时环境变量仍可被拥有 Docker 管理权限的用户读取。因此
该方式只适用于本地、可信内网和受控宿主机，不应把 Docker 管理权限授予非可信用户。

可以在 PowerShell 中生成 32 字节随机 Token，再将输出填入 JSON 对象的键：

```powershell
$bytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
[Convert]::ToHexString($bytes).ToLowerInvariant()
```

例如（仅展示结构，不要使用示例值）：

```dotenv
DEVPILOT_API_TOKENS={"将随机Token填在这里":{"subject":"local-operator","admin":true}}
```

### 2. 选择工具链

默认轻量镜像：

```dotenv
DEVPILOT_TOOLCHAIN_PROFILE=python
```

该镜像包含 Python 3.13、pytest 和 Git。需要验证 JavaScript、Java、Go 或 Rust 仓库时改为：

```dotenv
DEVPILOT_TOOLCHAIN_PROFILE=full
```

完整镜像额外包含 Node.js 22、JDK 17、Maven、Gradle 8、Go 1.24 和 Rust/Cargo。
镜像只提供工具，不会自动为目标仓库执行 `npm ci`、`pip install` 等依赖安装；Maven、
Gradle、Go 和 Cargo 可按项目自身配置解析依赖，其他项目应预先提供可运行环境。

### 3. 启动与访问

```bash
docker compose config
docker compose up --build --wait -d
docker compose ps
```

默认入口：

- 控制台：<http://127.0.0.1:8080>
- 健康检查：<http://127.0.0.1:8080/api/health>
- Readiness：<http://127.0.0.1:8080/api/ready>
- OpenAPI：<http://127.0.0.1:8080/docs>

首次打开控制台时，页面会自动展开“访问凭证”。复制
`DEVPILOT_API_TOKENS` JSON 对象最外层的 Token 键并保存；不要粘贴整段 JSON，也不要粘贴
`DEVPILOT_MODEL_API_KEY`。凭证只保存在当前浏览器会话中，关闭标签页后需要重新输入。
出现 `Bearer token required` 表示尚未保存凭证，出现 `Invalid bearer token` 表示输入值与
API 容器中的 Token 不一致。修改 `.env` 后需要执行以下命令重建 API 容器：

```bash
docker compose up --build --force-recreate --wait -d api nginx
```

前端创建任务时使用容器路径 `/repos/<仓库目录>`。例如宿主仓库为
`C:/repos/sample`，API 请求路径应为 `/repos/sample`。`/repos` 在 API 容器中是只读的；
DevPilot 会将仓库 clone 到 `/data` 持久卷中的隔离 worktree。

允许同一内网中的其他机器访问时，显式修改：

```dotenv
DEVPILOT_BIND_ADDRESS=0.0.0.0
```

第一版不在 Nginx 容器内终止 TLS。公网部署必须在外层负载均衡器或反向代理上配置
HTTPS，并继续只向外暴露 Nginx。

### 4. 宿主模型服务

Docker Desktop 可通过 `host.docker.internal` 访问宿主服务：

```dotenv
DEVPILOT_MODEL_BASE_URL=http://host.docker.internal:9000/v1
```

Compose 已为 Linux 配置 `host-gateway` 映射，因此同一地址也可在现代 Docker Engine 上使用。

### 5. 日志、更新与数据

```bash
docker compose logs -f api nginx redis
docker compose restart api
docker compose down
docker compose pull
docker compose up --build --wait -d
```

`docker compose down` 不删除 `devpilot-data`，API 重建后 SQLite、Artifact 和 worktree 会保留。
升级前可以备份命名卷：

```bash
docker run --rm -v devpilot_devpilot-data:/data -v "$PWD:/backup" \
  busybox:1.37.0 tar czf /backup/devpilot-data.tar.gz -C /data .
```

项目名或 Compose 工作目录改变时，先通过 `docker volume ls` 确认实际卷名。恢复必须在 API
停止后进行。只有明确需要永久清空所有任务、事件、Artifact 和 worktree 时才执行：

```bash
docker compose down -v
```

### 6. 故障检查

```bash
docker compose ps
docker compose logs --tail=200 api nginx redis
docker compose exec api id
docker compose exec api git -C /repos/<仓库> rev-parse --show-toplevel
docker compose exec api python -m devpilot --help
```

Redis 不可用时 `/api/ready` 返回 503，票据和限流 fail closed；SQLite 中已经持久化的任务
不会丢失，Redis 恢复后 outbox 会继续投递。

Docker 容器降低了目标代码直接接触宿主系统的范围，但目标仓库测试仍与 API 进程共享
容器环境、模型密钥和 `/data`。因此 Docker Compose 不是每任务安全沙箱，`admin` 或
`task_creator` 只能授予可信主体。

## 本地开发模式

```powershell
cd A:\agent\devpilot-infra
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
Copy-Item .env.example .env
# 编辑 .env 中的模型 API Key、Base URL 和模型名称
python -m devpilot task create --repo C:\path\to\clean-repo --request "修复失败测试"
```

## Phase 6 API

本机开发可使用默认 Token `devpilot-local` 启动，并在 `/docs` 中授权试调。启动日志会打印安全警告；该 Token 只允许用于本机开发：

```powershell
python -m devpilot api --host 127.0.0.1 --port 8000
```

共享环境必须覆盖默认 Token：

```powershell
$env:DEVPILOT_ENV = "production"
$env:DEVPILOT_API_TOKENS = '{"replace-with-long-random-token":{"subject":"operator-1","admin":true}}'
$env:DEVPILOT_API_REPOSITORY_ROOTS = '["C:\\repos"]'
$env:DEVPILOT_API_CORS_ORIGINS = "https://devpilot.example.com"
$env:DEVPILOT_REDIS_URL = "redis://127.0.0.1:6379/0"
python -m devpilot api --host 0.0.0.0 --port 8000 --workers 4
```

生产和多 worker 模式必须使用 Redis。当前本机安装位于 `A:\Redis` 时，可先确认 Windows 服务 `Redis` 已启动，再检查 `GET /api/ready`。所有 worker 还必须共享同一个 `DEVPILOT_DATA_DIR`。完整接口说明见 [Phase 4 控制面](phase4-fastapi-control-plane.md)，分布式部署语义见 [Phase 6 控制面](phase6-distributed-control-plane.md)。

`DEVPILOT_API_REPOSITORY_ROOTS` 必须是绝对路径组成的 JSON 数组。配置后，管理员和普通用户都只能从这些根目录内创建任务；未配置时，仅管理员可以提交服务器本地仓库路径，避免普通远程用户探测或读取任意服务器仓库。

由于任务验证会执行仓库的测试/构建入口，非管理员 Token 还必须显式包含 `"task_creator": true` 才能创建任务。不要把该权限授予不应拥有服务器代码执行能力的主体；当前版本的路径与 Tool 边界不能替代 OS/容器沙箱。

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
