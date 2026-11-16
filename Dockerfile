# syntax=docker/dockerfile:1.7
# =============================================================================
# AlphaAgents-CN 生产镜像（多阶段构建）
#
# 构建：
#   docker build -t alphaagents-cn:latest .
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
#   docker buildx build --platform linux/amd64,linux/arm64 -t <registry>/alphaagents-cn:1.0.0 --push .
#
# WHY 多阶段：pip 安装 pandas / lxml / akshare 等包需要编译器（build-essential，
# 约 300MB）。这些工具在运行期完全用不到，留在最终镜像里既臃肿又扩大攻击面。
# builder 阶段装依赖 → runtime 阶段只拷贝 /opt/venv。
#
# WHY 非 root：应用只需要写 /app/data、/app/eval_results、/app/logs。
# 容器内以 uid=1000 的 app 用户运行，避免应用被攻破后拿到容器 root。
# =============================================================================
ARG PYTHON_VERSION=3.11

# -----------------------------------------------------------------------------
# 第一阶段：builder —— 安装 Python 依赖（含编译工具）
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# 可选：国内网络加速 pip（只在本阶段生效，不进入最终镜像）
ARG PIP_INDEX_URL=

# 可选：apt 源镜像（国内网络访问 deb.debian.org 经常超时/404）。
# 例：--build-arg APT_MIRROR=mirrors.aliyun.com
ARG APT_MIRROR=

# 可选：构建期代理（仅用于本阶段的 apt / pip，不会写入最终镜像）。
# 注意：容器内的 127.0.0.1 指的是容器自己，不是宿主机！
#   Docker Desktop：--build-arg HTTPS_PROXY=http://host.docker.internal:7890
#   Linux        ：--network=host 构建，或使用宿主机网关 IP
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=
ENV HTTP_PROXY="${HTTP_PROXY}" \
    HTTPS_PROXY="${HTTPS_PROXY}" \
    NO_PROXY="${NO_PROXY}"

RUN if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$f" ]; then \
          sed -i "s|deb.debian.org|${APT_MIRROR}|g" "$f"; \
        fi; \
      done; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 先只拷依赖描述与服务端源码，利用 Docker 层缓存：
# 只要 pyproject.toml 没变，源码改动不会触发重新安装依赖。
COPY pyproject.toml requirements.txt README.md NOTICE LICENSE ./
COPY tradingagents ./tradingagents
COPY cli ./cli
COPY webui ./webui

# 用 BuildKit 缓存挂载：pip 下载缓存不写入镜像层，重复构建更快。
# PIP_INDEX_URL 直接用 --index-url 传入，不落任何 pip 配置文件。
RUN --mount=type=cache,target=/root/.cache/pip \
    set -eu; \
    PIP_ARGS=""; \
    if [ -n "${PIP_INDEX_URL}" ]; then PIP_ARGS="--index-url ${PIP_INDEX_URL}"; fi; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install ${PIP_ARGS} --upgrade pip; \
    /opt/venv/bin/pip install ${PIP_ARGS} .

# -----------------------------------------------------------------------------
# 第二阶段：runtime —— 只保留运行所需
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# 构建元信息（由 CI / 脚本传入，写入 OCI 标准标签，便于溯源）
ARG APP_VERSION=0.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG APP_UID=1000
ARG APP_GID=1000
ARG APT_MIRROR=
# 构建期代理（只用于下面这一次 apt-get，不会留在最终镜像的环境变量里）
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=

LABEL org.opencontainers.image.title="AlphaAgents-CN" \
      org.opencontainers.image.description="A 股多智能体投研系统（WebUI + CLI）" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/hebjt2024/AlphaAgents-CN" \
      org.opencontainers.image.documentation="https://github.com/hebjt2024/AlphaAgents-CN/blob/main/docs/DOCKER.md"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    PATH="/opt/venv/bin:${PATH}" \
    TA_DB_PATH=/app/data/tradingagents.db \
    WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8000

# tzdata：让 TZ=Asia/Shanghai 真正生效（交易时段判断依赖本地时区）
# libgomp1：numpy / pandas 的 OpenMP 运行时（部分 wheel 依赖它）
#
# 代理只在本次 RUN 内 export（apt 读的是小写 http_proxy），
# 不写入 ENV —— 运行期代理应通过 .env / compose 配置，不要把构建期代理固化进镜像。
RUN set -eu; \
    if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$f" ]; then \
          sed -i "s|deb.debian.org|${APT_MIRROR}|g" "$f"; \
        fi; \
      done; \
    fi; \
    if [ -n "${HTTP_PROXY}" ]; then \
      http_proxy="${HTTP_PROXY}"; \
      https_proxy="${HTTPS_PROXY}"; \
      no_proxy="${NO_PROXY}"; \
      export http_proxy https_proxy no_proxy; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata libgomp1; \
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime; \
    echo "${TZ}" > /etc/timezone; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid "${APP_GID}" app; \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" \
         --create-home --shell /bin/bash app

WORKDIR /app

# Python 依赖（含已安装的 alphaagents-cn 包）
COPY --from=builder /opt/venv /opt/venv

# 运行所需源码：入口脚本 webui/run_webui.py 依赖 webui/ 与 cli/ 目录
COPY --chown=app:app pyproject.toml README.md NOTICE LICENSE ./
COPY --chown=app:app tradingagents ./tradingagents
COPY --chown=app:app cli ./cli
COPY --chown=app:app webui ./webui

# 运行期需要可写的目录（SQLite / 报告 / 日志）。
# 使用 bind mount 时宿主机目录需对 uid=${APP_UID} 可写，详见 docs/DOCKER.md。
RUN mkdir -p /app/data /app/eval_results /app/logs \
    && chown -R app:app /app/data /app/eval_results /app/logs

USER app

EXPOSE 8000
STOPSIGNAL SIGTERM

# 就绪探针：/readyz 会真实检查数据库可写与表结构（首次访问会自动建表）。
# 比 /healthz 更严格——能提前发现"数据卷没权限"这类最常见的部署错误。
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import sys, urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).status == 200 else 1)"

CMD ["python", "webui/run_webui.py", "--host", "0.0.0.0", "--port", "8000"]
