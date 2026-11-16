#!/usr/bin/env bash
# =============================================================================
# 部署 AlphaAgents-CN：本地构建 或 拉取已推送的镜像。
#
# 用法：
#   scripts/docker-deploy.sh -B                                   # 本地构建并启动
#   scripts/docker-deploy.sh -r ghcr.io/your-name/alphaagents-cn -t 1.0.0
#   scripts/docker-deploy.sh -r ghcr.io/your-name/alphaagents-cn -t 1.0.0 -k
#
# 参数：
#   -B  本地构建（docker compose up -d --build）
#   -r  镜像仓库地址（不构建时必填）
#   -t  镜像标签（默认 latest）
#   -p  宿主机端口（默认 8000）
#   -k  跳过拉取（直接用本地已有镜像启动）
#   -m  apt 镜像源（配合 -B，例如 mirrors.aliyun.com）
#   -i  pip 镜像源（配合 -B，例如 https://mirrors.aliyun.com/pypi/simple/）
#   -x  构建期代理（配合 -B，例如 http://127.0.0.1:7890）
#   -h  查看帮助
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BUILD=0
IMAGE_REPO=""
IMAGE_TAG="latest"
PORT="8000"
SKIP_PULL=0
APT_MIRROR=""
PIP_INDEX_URL=""
BUILD_PROXY=""

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

while getopts "Br:t:p:m:i:x:kh" opt; do
  case "$opt" in
    B) BUILD=1 ;;
    r) IMAGE_REPO="$OPTARG" ;;
    t) IMAGE_TAG="$OPTARG" ;;
    p) PORT="$OPTARG" ;;
    m) APT_MIRROR="$OPTARG" ;;
    i) PIP_INDEX_URL="$OPTARG" ;;
    x) BUILD_PROXY="$OPTARG" ;;
    k) SKIP_PULL=1 ;;
    h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# ---- 配置与目录 -------------------------------------------------------------
if [ ! -f .env ]; then
  if [ -f .env.docker.example ]; then
    cp .env.docker.example .env
    echo "已生成 .env（来自 .env.docker.example）。" >&2
  fi
  echo "请先编辑 .env，至少填写 ANTHROPIC_API_KEY 或 MINIMAX_API_KEY，然后重新运行。" >&2
  exit 1
fi

mkdir -p data eval_results logs

export IMAGE_TAG="$IMAGE_TAG"
export PORT

if [ "$BUILD" -eq 1 ]; then
  [ -n "$APT_MIRROR" ] && export APT_MIRROR
  [ -n "$PIP_INDEX_URL" ] && export PIP_INDEX_URL
  if [ -n "$BUILD_PROXY" ]; then
    p="$BUILD_PROXY"
    case "$p" in *://*) ;; *) p="http://$p" ;; esac
    if printf '%s' "$p" | grep -Eq '^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/?$'; then
      port="$(printf '%s' "$p" | sed -E 's#^https?://[^:]+:?([0-9]*)/?$#\1#')"
      [ -n "$port" ] || port="7890"
      p="http://host.docker.internal:${port}"
      echo "提示：已把本机代理转换为容器可访问地址 $p"
    fi
    export BUILD_HTTP_PROXY="$p"
    export BUILD_HTTPS_PROXY="$p"
  fi
  step "本地构建并启动"
  docker compose -f docker-compose.yml up -d --build
else
  if [ -z "$IMAGE_REPO" ]; then
    echo "错误：拉取部署需要 -r 指定镜像仓库（或使用 -B 本地构建）。" >&2
    exit 1
  fi
  export IMAGE_REPO
  if [ "$SKIP_PULL" -eq 0 ]; then
    step "拉取镜像 ${IMAGE_REPO}:${IMAGE_TAG}"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
  fi
  step "启动服务"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
fi

echo
echo "服务已启动：http://127.0.0.1:${PORT}"
echo "健康检查：curl http://127.0.0.1:${PORT}/healthz"
echo "就绪检查：curl http://127.0.0.1:${PORT}/readyz"
echo "查看日志：docker compose logs -f"
