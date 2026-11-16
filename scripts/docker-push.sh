#!/usr/bin/env bash
# =============================================================================
# 把本地已构建的 AlphaAgents-CN 镜像打标签并推送到镜像仓库。
#
# 用法：
#   docker login ghcr.io                      # 先登录（只需一次）
#   scripts/docker-push.sh -r ghcr.io/your-name
#   scripts/docker-push.sh -r registry.cn-hangzhou.aliyuncs.com/your-ns -t 0.3.0 -l
#
# 参数：
#   -r  镜像仓库前缀（必填）
#   -t  版本标签（默认读取 pyproject.toml）
#   -n  镜像名（默认 alphaagents-cn）
#   -l  同时更新 latest
#   -s  跳过登录提示
#   -h  查看帮助
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REGISTRY=""
TAG=""
IMAGE_NAME="alphaagents-cn"
ALSO_LATEST=0
SKIP_LOGIN=0

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

while getopts "r:t:n:lsh" opt; do
  case "$opt" in
    r) REGISTRY="$OPTARG" ;;
    t) TAG="$OPTARG" ;;
    n) IMAGE_NAME="$OPTARG" ;;
    l) ALSO_LATEST=1 ;;
    s) SKIP_LOGIN=1 ;;
    h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

if [ -z "$REGISTRY" ]; then
  echo "错误：必须用 -r 指定镜像仓库前缀，例如 ghcr.io/your-name" >&2
  exit 1
fi

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

if [ -z "$TAG" ]; then
  TAG="$(grep -m1 -E '^version[[:space:]]*=' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/' || true)"
  [ -n "$TAG" ] || TAG="latest"
fi

LOCAL_IMAGE="${IMAGE_NAME}:${TAG}"
REMOTE_IMAGE="${REGISTRY%/}/${IMAGE_NAME}"

if ! docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
  echo "错误：本地不存在镜像 $LOCAL_IMAGE。请先执行：scripts/docker-build.sh -t $TAG" >&2
  exit 1
fi

if [ "$SKIP_LOGIN" -eq 0 ]; then
  echo "提示：若尚未登录，请先执行  docker login ${REGISTRY%%/*}"
fi

step "打标签 ${LOCAL_IMAGE} -> ${REMOTE_IMAGE}:${TAG}"
docker tag "$LOCAL_IMAGE" "${REMOTE_IMAGE}:${TAG}"

step "推送 ${REMOTE_IMAGE}:${TAG}"
docker push "${REMOTE_IMAGE}:${TAG}"

if [ "$ALSO_LATEST" -eq 1 ]; then
  step "更新 latest"
  docker tag "$LOCAL_IMAGE" "${REMOTE_IMAGE}:latest"
  docker push "${REMOTE_IMAGE}:latest"
fi

echo
echo "推送完成：${REMOTE_IMAGE}:${TAG}"
[ "$ALSO_LATEST" -eq 1 ] && echo "            ${REMOTE_IMAGE}:latest"
