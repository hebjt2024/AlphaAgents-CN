#Requires -Version 5.1
<#
.SYNOPSIS
    构建 AlphaAgents-CN Docker 镜像（可选多架构 / 推送）。

.DESCRIPTION
    - 版本号自动取自 pyproject.toml，同时打 <version> 与 latest 两个标签。
    - 自动注入 OCI 标签（版本 / Git 提交 / 构建时间），便于溯源。
    - 多架构构建必须配合 -Push（Docker 无法把多架构镜像 load 到本地）。

.EXAMPLE
    # 本地单架构构建，产出 alphaagents-cn:0.3.0 与 :latest
    pwsh scripts/docker-build.ps1

.EXAMPLE
    # 国内网络加速 + 指定版本
    pwsh scripts/docker-build.ps1 -Tag 0.3.1 -PipIndexUrl https://pypi.tuna.tsinghua.edu.cn/simple

.EXAMPLE
    # 多架构构建并推送到 GHCR
    pwsh scripts/docker-build.ps1 -Registry ghcr.io/your-name -Platforms linux/amd64,linux/arm64 -Push

.EXAMPLE
    # 推到阿里云 ACR（需先 docker login）
    pwsh scripts/docker-build.ps1 -Registry registry.cn-hangzhou.aliyuncs.com/your-ns -Push

.EXAMPLE
    # 网络受限：换国内 apt/pip 源
    pwsh scripts/docker-build.ps1 -AptMirror mirrors.aliyun.com `
        -PipIndexUrl https://mirrors.aliyun.com/pypi/simple/

.EXAMPLE
    # 网络受限：走本机代理（会自动转换为 host.docker.internal）
    pwsh scripts/docker-build.ps1 -Proxy http://127.0.0.1:7890
#>
[CmdletBinding()]
param(
    [string]$Tag = "",
    [string]$Registry = "",
    [string]$ImageName = "alphaagents-cn",
    [string]$Platforms = "",
    [string]$PipIndexUrl = "",
    [string]$AptMirror = "",
    [string]$Proxy = "",
    [string]$Builder = "alphaagents-builder",
    [switch]$Push,
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') 执行失败（exit=$LASTEXITCODE）"
    }
}

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    # ---- 版本 / 提交 / 时间 -------------------------------------------------
    if (-not $Tag) {
        $pyproject = Get-Content -Raw (Join-Path $Root "pyproject.toml")
        $match = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
        if ($match.Success) { $Tag = $match.Groups[1].Value } else { $Tag = "dev" }
    }
    $VcsRef = "unknown"
    try {
        $ref = git rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $ref) { $VcsRef = $ref.Trim() }
    } catch { }
    $BuildDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

    $FullImage = if ($Registry) { "$($Registry.TrimEnd('/'))/$ImageName" } else { $ImageName }
    if (-not $Platforms) {
        $Platforms = if ($env:DOCKER_DEFAULT_PLATFORM) { $env:DOCKER_DEFAULT_PLATFORM } else { "linux/amd64" }
    }
    $IsMultiArch = $Platforms.Contains(",")
    if ($IsMultiArch -and -not $Push) {
        throw "多架构构建（$Platforms）必须配合 -Push：Docker 无法把多架构镜像加载到本地。"
    }

    Write-Host "镜像:      $FullImage"
    Write-Host "标签:      $Tag, latest"
    Write-Host "平台:      $Platforms"
    if ($AptMirror) { Write-Host "apt 镜像:  $AptMirror" }
    if ($PipIndexUrl) { Write-Host "pip 镜像:  $PipIndexUrl" }
    if ($proxyUrl) { Write-Host "构建代理:  $proxyUrl" }
    Write-Host "版本号:    $Tag (rev=$VcsRef, built=$BuildDate)"
    Write-Host ""

    # ---- 准备 buildx builder（多架构与 --push 需要 docker-container 驱动）--
    $builders = (& docker buildx ls --format "{{.Name}}" 2>$null) -split "`r?`n"
    if ($builders -notcontains $Builder) {
        Write-Step "创建 buildx builder: $Builder"
        Invoke-Native @("buildx", "create", "--name", $Builder, "--driver", "docker-container") | Out-Null
    }
    Invoke-Native @("buildx", "inspect", "--builder", $Builder, "--bootstrap") | Out-Null

    # ---- 代理（可选）-------------------------------------------------------
    # 容器内的 127.0.0.1 不是宿主机。Docker Desktop 需把本机代理地址
    # 换成 host.docker.internal；Linux 上请直接用宿主机网关 IP。
    $proxyUrl = ""
    if ($Proxy) {
        $proxyUrl = $Proxy.Trim()
        if ($proxyUrl -match "^(https?://)?(127\.0\.0\.1|localhost)(:.*)?$") {
            $port = if ($proxyUrl -match ":(\d+)/?$") { $Matches[1] } else { "7890" }
            $proxyUrl = "http://host.docker.internal:$port"
            Write-Host "提示：已把本机代理转换为容器可访问地址 $proxyUrl" -ForegroundColor Yellow
        }
        if ($proxyUrl -notmatch "^https?://") { $proxyUrl = "http://$proxyUrl" }
    }

    # ---- 组装构建参数 -------------------------------------------------------
    $buildArgs = @(
        "buildx", "build",
        "--builder", $Builder,
        "--platform", $Platforms,
        "--tag", "${FullImage}:$Tag",
        "--tag", "${FullImage}:latest",
        "--build-arg", "APP_VERSION=$Tag",
        "--build-arg", "VCS_REF=$VcsRef",
        "--build-arg", "BUILD_DATE=$BuildDate"
    )
    if ($PipIndexUrl) {
        $buildArgs += @("--build-arg", "PIP_INDEX_URL=$PipIndexUrl")
    }
    if ($AptMirror) {
        $buildArgs += @("--build-arg", "APT_MIRROR=$AptMirror")
    }
    if ($proxyUrl) {
        $buildArgs += @("--build-arg", "HTTP_PROXY=$proxyUrl")
        $buildArgs += @("--build-arg", "HTTPS_PROXY=$proxyUrl")
        $buildArgs += @("--build-arg", "NO_PROXY=localhost,127.0.0.1,::1")
    }
    if ($NoCache) { $buildArgs += "--no-cache" }
    if ($Push) {
        $buildArgs += "--push"
    } else {
        $buildArgs += "--load"
    }
    $buildArgs += "."

    # ---- 执行 ---------------------------------------------------------------
    Write-Step "开始构建"
    Invoke-Native $buildArgs

    Write-Host ""
    Write-Host "构建完成: $FullImage" -ForegroundColor Green
    if (-not $Push) {
        Write-Host "本地验证：docker run --rm -p 8000:8000 --env-file .env $FullImage`:$Tag"
    } else {
        Write-Host "已推送：$FullImage`:$Tag 和 $FullImage`:latest"
    }
}
finally {
    Pop-Location
}
