param(
    [string]$EnvFile = "C:\Users\deng\hiclaw-manager.env"
)

$ErrorActionPreference = "Stop"

function Get-EnvMapFromFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "env file not found: $Path"
    }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $name = $line.Substring(0, $idx).Trim()
            $value = $line.Substring($idx + 1).Trim()
            if ($name) { $map[$name] = $value }
        }
    }
    return $map
}

function Get-ContainerEnv {
    param([string]$Name)
    $json = docker inspect $Name --format "{{json .Config.Env}}"
    $items = $json | ConvertFrom-Json
    $map = @{}
    foreach ($item in $items) {
        $idx = $item.IndexOf("=")
        if ($idx -gt 0) {
            $map[$item.Substring(0, $idx)] = $item.Substring($idx + 1)
        }
    }
    return $map
}

function New-EnvArgs {
    param([System.Collections.IDictionary]$EnvMap)
    $args = @()
    foreach ($key in $EnvMap.Keys) {
        $args += "-e"
        $args += "$key=$($EnvMap[$key])"
    }
    return $args
}

$fileEnv = Get-EnvMapFromFile -Path $EnvFile

$manager = Get-ContainerEnv -Name "hiclaw-manager"
$controller = Get-ContainerEnv -Name "hiclaw-controller"

foreach ($key in @("HICLAW_LLM_API_KEY", "HICLAW_DEFAULT_MODEL", "HICLAW_OPENAI_BASE_URL", "HICLAW_LLM_PROVIDER")) {
    if ($fileEnv.ContainsKey($key)) {
        $manager[$key] = $fileEnv[$key]
        $controller[$key] = $fileEnv[$key]
    }
}

Write-Host "Applying env overrides:"
foreach ($key in @("HICLAW_LLM_API_KEY", "HICLAW_DEFAULT_MODEL", "HICLAW_OPENAI_BASE_URL")) {
    $val = $manager[$key]
    $masked = if ($val -and $val.Length -gt 10) { $val.Substring(0, 6) + "****" + $val.Substring($val.Length - 4) } else { $val }
    Write-Host ("  {0}={1}" -f $key, $masked)
}

Write-Host "Removing old containers (data volume is preserved)..."
docker rm -f hiclaw-manager hiclaw-controller

$controllerArgs = @(
    "run", "-d", "--name", "hiclaw-controller",
    "--network", "hiclaw-net",
    "--network-alias", "aigw-local.hiclaw.io",
    "--network-alias", "matrix-local.hiclaw.io",
    "--network-alias", "matrix-client-local.hiclaw.io",
    "--network-alias", "fs-local.hiclaw.io",
    "--network-alias", "console-local.hiclaw.io",
    "--restart", "unless-stopped",
    "-p", "127.0.0.1:18001:8001",
    "-p", "127.0.0.1:8090:8080",
    "-p", "127.0.0.1:18088:8088",
    "-v", "//var/run/docker.sock:/var/run/docker.sock",
    "-v", "hiclaw-data:/data",
    "-v", "/run/desktop/mnt/host/c/Users/deng/hiclaw-manager:/root/hiclaw-fs/agents/manager"
)
$controllerArgs += New-EnvArgs -EnvMap $controller
$controllerArgs += "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-embedded:latest"

Write-Host "Creating hiclaw-controller..."
docker @controllerArgs

Start-Sleep -Seconds 20

$managerArgs = @(
    "run", "-d", "--name", "hiclaw-manager",
    "--network", "hiclaw-net",
    "--restart", "unless-stopped",
    "-p", "127.0.0.1:18888:18799",
    "-v", "A:\main:/host-share",
    "-v", "C:\Users\deng\hiclaw-manager:/root/manager-workspace"
)
$managerArgs += New-EnvArgs -EnvMap $manager
$managerArgs += "higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/hiclaw-manager-copaw:latest"

if (docker ps -a --filter "name=^/hiclaw-manager$" --format "{{.Names}}") {
    Write-Host "hiclaw-manager is managed by the controller; skipping manual creation."
} else {
    Write-Host "Creating hiclaw-manager..."
    docker @managerArgs
}

Write-Host "Done. Containers recreated with updated env."
