#!/usr/bin/env bash
# =============================================================================
# 构建 AlphaAgents-CN Docker 镜像（可选多架构 / 推送）
#
# 用法：
#   scripts/docker-build.sh                          # 本地单架构
#   scripts/docker-build.sh -t 0.3.1                 # 指定标签
#   scripts/docker-build.sh -p https://pypi.tuna.tsinghua.edu.cn/simple
#   scripts/docker-build.sh -r ghcr.io/your-name -P \
#       -a linux/amd64,linux/arm64                   # 多架构并推送
#
# 参数：
#   -t  版本标签（默认读取 pyproject.toml）
#   -r  镜像仓库前缀（例如 ghcr.io/your-name），留空则只打本地镜像名
#   -n  镜像名（默认 alphaagents-cn）
#   -a  目标平台（默认 linux/amd64；多平台必须配合 -P）
#   -p  pip 镜像源（国内加速）
#   -m  apt 镜像源（例如 mirrors.aliyun.com）
#   -x  构建期代理（例如 http://127.0.0.1:7890）
#   -P  构建后推送
#   -N  不使用构建缓存
#   -h  查看帮助
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG=""
REGISTRY=""
IMAGE_NAME="alphaagents-cn"
PLATFORMS=""
PIP_INDEX_URL=""
APT_MIRROR=""
PROXY=""
PUSH=0
NO_CACHE=0
BUILDER="alphaagents-builder"

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
}

while getopts "t:r:n:a:p:m:x:PNh" opt; do
  case "$opt" in
    t) TAG="$OPTARG" ;;
    r) REGISTRY="$OPTARG" ;;
    n) IMAGE_NAME="$OPTARG" ;;
    a) PLATFORMS="$OPTARG" ;;
    p) PIP_INDEX_URL="$OPTARG" ;;
    m) APT_MIRROR="$OPTARG" ;;
    x) PROXY="$OPTARG" ;;
    P) PUSH=1 ;;
    N) NO_CACHE=1 ;;
    h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# ---- 版本 / 提交 / 时间 ------------------------------------------------------
if [ -z "$TAG" ]; then
  TAG="$(grep -m1 -E '^version[[:space:]]*=' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/' || true)"
  [ -n "$TAG" ] || TAG="dev"
fi
VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- 代理（可选）------------------------------------------------------------
# 容器内的 127.0.0.1 不是宿主机：Docker Desktop 需要 host.docker.internal。
# Linux 上如果代理监听在宿主机回环地址，请显式传宿主机网关 IP。
PROXY_URL=""
if [ -n "$PROXY" ]; then
  PROXY_URL="$PROXY"
  case "$PROXY_URL" in
    *://*) ;;
    *) PROXY_URL="http://$PROXY_URL" ;;
  esac
  if printf '%s' "$PROXY_URL" | grep -Eq '^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/?$'; then
    PORT_PART="$(printf '%s' "$PROXY_URL" | sed -E 's#^https?://[^:]+:?([0-9]*)/?$#\1#')"
    [ -n "$PORT_PART" ] || PORT_PART="7890"
    PROXY_URL="http://host.docker.internal:${PORT_PART}"
    echo "提示：已把本机代理转换为容器可访问地址 $PROXY_URL"
  fi
fi

FULL_IMAGE="$IMAGE_NAME"
[ -n "$REGISTRY" ] && FULL_IMAGE="${REGISTRY%/}/$IMAGE_NAME"

if [ -z "$PLATFORMS" ]; then
  PLATFORMS="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
fi
case "$PLATFORMS" in
  *,*) IS_MULTI=1 ;;
  *)   IS_MULTI=0 ;;
esac
if [ "$IS_MULTI" -eq 1 ] && [ "$PUSH" -eq 0 ]; then
  echo "错误：多架构构建（$PLATFORMS）必须配合 -P：Docker 无法把多架构镜像加载到本地。" >&2
  exit 1
fi

cat <<EOF
镜像:      $FULL_IMAGE
标签:      $TAG, latest
平台:      $PLATFORMS
版本号:    $TAG (rev=$VCS_REF, built=$BUILD_DATE)
EOF
[ -n "$APT_MIRROR" ] && echo "apt 镜像:  $APT_MIRROR"
[ -n "$PIP_INDEX_URL" ] && echo "pip 镜像:  $PIP_INDEX_URL"
[ -n "$PROXY_URL" ] && echo "构建代理:  $PROXY_URL"
echo

# ---- 准备 buildx builder（多架构与推送需要 docker-container 驱动）----------
if ! docker buildx ls --format '{{.Name}}' 2>/dev/null | grep -qx "$BUILDER"; then
  step "创建 buildx builder: $BUILDER"
  docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi
docker buildx inspect --builder "$BUILDER" --bootstrap >/dev/null

# ---- 组装构建参数 -----------------------------------------------------------
BUILD_ARGS=(
  buildx build
  --builder "$BUILDER"
  --platform "$PLATFORMS"
  --tag "${FULL_IMAGE}:${TAG}"
  --tag "${FULL_IMAGE}:latest"
  --build-arg "APP_VERSION=$TAG"
  --build-arg "VCS_REF=$VCS_REF"
  --build-arg "BUILD_DATE=$BUILD_DATE"
)
[ -n "$PIP_INDEX_URL" ] && BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
[ -n "$APT_MIRROR" ] && BUILD_ARGS+=(--build-arg "APT_MIRROR=$APT_MIRROR")
if [ -n "$PROXY_URL" ]; then
  BUILD_ARGS+=(--build-arg "HTTP_PROXY=$PROXY_URL" --build-arg "HTTPS_PROXY=$PROXY_URL")
  BUILD_ARGS+=(--build-arg "NO_PROXY=localhost,127.0.0.1,::1")
fi
[ "$NO_CACHE" -eq 1 ] && BUILD_ARGS+=(--no-cache)
if [ "$PUSH" -eq 1 ]; then
  BUILD_ARGS+=(--push)
else
  BUILD_ARGS+=(--load)
fi
BUILD_ARGS+=(.)

step "开始构建"
docker "${BUILD_ARGS[@]}"

echo
if [ "$PUSH" -eq 0 ]; then
  echo "构建完成: $FULL_IMAGE"
  echo "本地验证：docker run --rm -p 8000:8000 --env-file .env ${FULL_IMAGE}:${TAG}"
else
  echo "已推送：${FULL_IMAGE}:${TAG} 和 ${FULL_IMAGE}:latest"
fi
