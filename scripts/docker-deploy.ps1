#Requires -Version 5.1
<#
.SYNOPSIS
    部署 AlphaAgents-CN：本地构建 或 拉取已推送的镜像。

.DESCRIPTION
    默认行为（不传 -Build）：
      1) 拉取 ${IMAGE_REPO}:${IMAGE_TAG}
      2) 用 docker-compose.yml + docker-compose.prod.yml 启动
    传 -Build 时：
      在本机构建镜像并启动（等价于 docker compose up -d --build）。

    首次执行会自动创建 data/、eval_results/、logs/ 目录；
    缺少 .env 时会从 .env.docker.example 复制一份并提示你去填 Key。

.EXAMPLE
    # 本地构建并启动（开发/单机）
    pwsh scripts/docker-deploy.ps1 -Build

.EXAMPLE
    # 服务器：拉取 GHCR 上的 1.0.0 并启动
    pwsh scripts/docker-deploy.ps1 -ImageRepo ghcr.io/your-name/alphaagents-cn -ImageTag 1.0.0
#>
[CmdletBinding()]
param(
    [string]$ImageRepo = "",
    [string]$ImageTag = "latest",
    [int]$Port = 8000,
    [switch]$Build,
    [switch]$Pull,
    # 仅与 -Build 配合：构建期网络配置（国内网络常用）
    [string]$AptMirror = "",
    [string]$PipIndexUrl = "",
    [string]$BuildProxy = ""
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    # ---- 配置与目录 ---------------------------------------------------------
    $EnvPath = Join-Path $Root ".env"
    if (-not (Test-Path $EnvPath)) {
        $example = Join-Path $Root ".env.docker.example"
        if (Test-Path $example) {
            Copy-Item $example $EnvPath
            Write-Host "已生成 .env（来自 .env.docker.example）。" -ForegroundColor Yellow
        }
        Write-Host "请先编辑 .env，至少填写 ANTHROPIC_API_KEY 或 MINIMAX_API_KEY，然后重新运行。" -ForegroundColor Yellow
        exit 1
    }

    foreach ($dir in @("data", "eval_results", "logs")) {
        $path = Join-Path $Root $dir
        if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
    }

    # ---- 环境变量 -----------------------------------------------------------
    if ($ImageRepo) { $env:IMAGE_REPO = $ImageRepo }
    $env:IMAGE_TAG = $ImageTag
    $env:PORT = "$Port"

    if ($Build) {
        # 透传给 compose 的 build.args（见 docker-compose.yml）
        if ($AptMirror) { $env:APT_MIRROR = $AptMirror }
        if ($PipIndexUrl) { $env:PIP_INDEX_URL = $PipIndexUrl }
        if ($BuildProxy) {
            $p = $BuildProxy.Trim()
            if ($p -match "^(https?://)?(127\.0\.0\.1|localhost)(:.*)?$") {
                $port = if ($p -match ":(\d+)/?$") { $Matches[1] } else { "7890" }
                $p = "http://host.docker.internal:$port"
                Write-Host "提示：已把本机代理转换为容器可访问地址 $p" -ForegroundColor Yellow
            }
            if ($p -notmatch "^https?://") { $p = "http://$p" }
            $env:BUILD_HTTP_PROXY = $p
            $env:BUILD_HTTPS_PROXY = $p
        }
        Write-Step "本地构建并启动（docker compose up -d --build）"
        & docker compose -f docker-compose.yml up -d --build
        if ($LASTEXITCODE -ne 0) { throw "启动失败" }
    }
    else {
        if (-not $env:IMAGE_REPO) {
            throw "拉取部署需要指定镜像仓库：-ImageRepo ghcr.io/your-name/alphaagents-cn（或先用 -Build）"
        }
        Write-Step "拉取镜像 $($env:IMAGE_REPO):$ImageTag"
        & docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
        if ($LASTEXITCODE -ne 0) { throw "拉取失败（检查 docker login 与仓库地址）" }
        Write-Step "启动服务"
        & docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
        if ($LASTEXITCODE -ne 0) { throw "启动失败" }
    }

    Write-Host ""
    Write-Host "服务已启动：http://127.0.0.1:$Port" -ForegroundColor Green
    Write-Host "健康检查：curl http://127.0.0.1:$Port/healthz"
    Write-Host "就绪检查：curl http://127.0.0.1:$Port/readyz"
    Write-Host "查看日志：docker compose logs -f"
}
finally {
    Pop-Location
}
