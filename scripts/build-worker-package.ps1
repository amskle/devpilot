<#
.SYNOPSIS
  Build the DevPilot skill package for Worker injection.

.DESCRIPTION
  Compresses the skills/ directory into devpilot-skills.zip, ready to upload
  as a Worker package via `hiclaw apply worker --zip` or the Element Web UI.

.PARAMETER OutputPath
  Destination path for the zip. Defaults to ./devpilot-skills.zip.

.EXAMPLE
  .\scripts\build-worker-package.ps1
  .\scripts\build-worker-package.ps1 -OutputPath C:\releases\v1.zip
#>
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "..\devpilot-skills.zip")
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$skillsDir = Join-Path $repoRoot "skills"

if (-not (Test-Path $skillsDir)) {
    Write-Error "Skills directory not found: $skillsDir"
    exit 1
}

$OutputPath = (Resolve-Path -Path (Split-Path $OutputPath) -Relative).Path
if (-not $OutputPath.EndsWith(".zip")) { $OutputPath = Join-Path $OutputPath "devpilot-skills.zip" }

Write-Host "Building skill package from: $skillsDir"
$skillCount = (Get-ChildItem $skillsDir -Directory).Count
Write-Host "Found $skillCount skills"

Compress-Archive -Path (Join-Path $skillsDir "*") -DestinationPath $OutputPath -Force

$size = [math]::Round((Get-Item $OutputPath).Length / 1KB, 1)
Write-Host "Package created: $OutputPath ($size KB)"
Write-Host ""
Write-Host "Upload via Element Web or CLI:"
Write-Host "  docker cp $OutputPath hiclaw-controller:/tmp/devpilot-skills.zip"
Write-Host "  docker exec hiclaw-controller hiclaw apply worker --zip /tmp/devpilot-skills.zip"
