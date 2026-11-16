#Requires -Version 5.1
<#
.SYNOPSIS
    把本地已构建的 AlphaAgents-CN 镜像打标签并推送到镜像仓库。

.DESCRIPTION
    适合"已经 build 过、只差推送"的场景。如果还没构建，建议直接用
    scripts/docker-build.ps1 -Push（构建与推送一次完成，且支持多架构）。

.EXAMPLE
    # 先登录（只需一次）
    docker login ghcr.io
    pwsh scripts/docker-push.ps1 -Registry ghcr.io/your-name

.EXAMPLE
    # 推送到阿里云 ACR，并同时更新 latest
    pwsh scripts/docker-push.ps1 -Registry registry.cn-hangzhou.aliyuncs.com/your-ns -Tag 0.3.0 -AlsoLatest
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Registry,
    [string]$Tag = "",
    [string]$ImageName = "alphaagents-cn",
    [switch]$AlsoLatest,
    [switch]$SkipLoginCheck
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    if (-not $Tag) {
        $pyproject = Get-Content -Raw (Join-Path $Root "pyproject.toml")
        $match = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
        $Tag = if ($match.Success) { $match.Groups[1].Value } else { "latest" }
    }

    $LocalImage = "${ImageName}:$Tag"
    $RemoteImage = "$($Registry.TrimEnd('/'))/$ImageName"

    # 本地镜像不存在时给出可执行的下一步，而不是让 docker tag 报晦涩错误
    & docker image inspect $LocalImage *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "本地不存在镜像 $LocalImage。请先执行：pwsh scripts/docker-build.ps1 -Tag $Tag"
    }

    if (-not $SkipLoginCheck) {
        Write-Host "提示：若尚未登录，请先执行  docker login $($Registry.Split('/')[0])" -ForegroundColor Yellow
    }

    Write-Step "打标签 ${LocalImage} -> ${RemoteImage}:${Tag}"
    & docker tag $LocalImage "${RemoteImage}:${Tag}"
    if ($LASTEXITCODE -ne 0) { throw "docker tag 失败" }

    Write-Step "推送 ${RemoteImage}:${Tag}"
    & docker push "${RemoteImage}:${Tag}"
    if ($LASTEXITCODE -ne 0) { throw "docker push 失败（检查登录状态与仓库权限）" }

    if ($AlsoLatest) {
        Write-Step "更新 latest"
        & docker tag $LocalImage "${RemoteImage}:latest"
        & docker push "${RemoteImage}:latest"
        if ($LASTEXITCODE -ne 0) { throw "推送 latest 失败" }
    }

    Write-Host ""
    Write-Host "推送完成：${RemoteImage}:${Tag}$(if ($AlsoLatest) { ' 和 :latest' })" -ForegroundColor Green
}
finally {
    Pop-Location
}
