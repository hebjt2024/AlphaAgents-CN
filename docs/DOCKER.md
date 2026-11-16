# Docker 打包、推送与部署

本文覆盖 AlphaAgents-CN 的**镜像构建 → 仓库推送 → 服务器部署 → 运维排障**全流程。
所有命令都已在 Docker 29 / Compose v2 上实测通过（镜像约 740MB，非 root 运行）。

## 交付物一览

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 多阶段构建：builder 装依赖，runtime 仅保留运行所需；非 root（uid=1000） |
| `.dockerignore` | 排除 `.env`、数据目录、测试等，避免把密钥打进镜像 |
| `docker-compose.yml` | 本地构建 / 单机部署（端口、卷、健康检查、资源限制） |
| `docker-compose.prod.yml` | 服务器部署覆盖文件：只拉取已推送镜像，禁止本地构建 |
| `.env.docker.example` | 部署配置模板（含构建镜像源 / 代理说明） |
| `scripts/docker-build.ps1` / `.sh` | 构建（支持多架构、镜像源、代理、推送） |
| `scripts/docker-push.ps1` / `.sh` | 给已有本地镜像打标签并推送 |
| `scripts/docker-deploy.ps1` / `.sh` | 一键部署（本地构建或拉取镜像） |
| `.github/workflows/docker.yml` | GitHub Actions：多架构构建 + 推送 GHCR + 冒烟测试 |

---

## 1. 前置要求

- Docker Engine 24+ / Docker Desktop 4.30+（需要 BuildKit，多架构需要 buildx）
- Docker Compose v2.24+（`docker-compose.prod.yml` 使用了 `!reset`；更早版本见 §6.3）
- 磁盘 ≥ 5GB（镜像 ~740MB，构建缓存约 1.5GB）
- 能访问镜像源（国内网络见 §3.2）

```bash
docker --version
docker compose version
docker buildx version
```

---

## 2. 快速开始（3 分钟）

```bash
# 1) 准备配置（必须填写 LLM 密钥）
cp .env.docker.example .env
#    编辑 .env：ANTHROPIC_API_KEY（或 MINIMAX_API_KEY）

# 2) 构建并启动
docker compose up -d --build

# 3) 验证
curl http://127.0.0.1:8000/healthz   # {"status":"ok"}
curl http://127.0.0.1:8000/readyz    # {"status":"ready", ...}
```

浏览器打开 `http://127.0.0.1:8000`。

> Windows 用户也可以直接用脚本：`pwsh scripts/docker-deploy.ps1 -Build`

---

## 3. 镜像打包

### 3.1 本地单架构

```bash
docker build -t alphaagents-cn:0.3.0 -t alphaagents-cn:latest .
```

用脚本（自动从 `pyproject.toml` 读版本、注入 Git 提交与构建时间）：

```bash
bash scripts/docker-build.sh
pwsh scripts/docker-build.ps1
```

### 3.2 国内网络：镜像源或代理（二选一）

**推荐：使用国内镜像源**（同时写进 `.env` 后 compose 构建也会生效）：

```bash
docker build \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  -t alphaagents-cn:latest .
```

**备选：使用本机代理**。⚠️ 容器内的 `127.0.0.1` 是容器自己，**不是宿主机**：

| 环境 | 代理地址 |
|---|---|
| Docker Desktop（Windows/macOS） | `http://host.docker.internal:7890` |
| Linux（默认 bridge） | `http://172.17.0.1:7890`（宿主机网关） |
| Linux（`--network=host` 构建） | `http://127.0.0.1:7890` |

```bash
# Docker Desktop
docker build \
  --build-arg HTTP_PROXY=http://host.docker.internal:7890 \
  --build-arg HTTPS_PROXY=http://host.docker.internal:7890 \
  -t alphaagents-cn:latest .

# Linux
docker build --network=host \
  --build-arg HTTP_PROXY=http://127.0.0.1:7890 \
  --build-arg HTTPS_PROXY=http://127.0.0.1:7890 \
  -t alphaagents-cn:latest .
```

脚本会自动把 `127.0.0.1/localhost` 转成 `host.docker.internal`：

```bash
bash scripts/docker-build.sh -x http://127.0.0.1:7890
pwsh scripts/docker-build.ps1 -Proxy http://127.0.0.1:7890
```

> 构建期代理只用于 apt/pip，**不会写入最终镜像**；运行期代理请配置在 `.env` 的
> `HTTP_PROXY/HTTPS_PROXY`（compose 会自动传入容器，并已声明 `host.docker.internal`）。

### 3.3 多架构（amd64 + arm64）

多架构镜像无法 `--load` 到本地，必须配合推送：

```bash
bash scripts/docker-build.sh \
  -r ghcr.io/your-name -a linux/amd64,linux/arm64 -P
```

或直接用 buildx：

```bash
docker buildx create --name alphaagents-builder --driver docker-container --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/your-name/alphaagents-cn:0.3.0 \
  --push .
```

### 3.4 构建参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `PYTHON_VERSION` | `3.11` | 基础镜像 Python 版本（3.10–3.12 均可） |
| `APP_VERSION` | `0.0.0` | 写入 OCI 标签 `image.version` |
| `VCS_REF` | `unknown` | Git 提交号，写入 `image.revision` |
| `BUILD_DATE` | `unknown` | 构建时间，写入 `image.created` |
| `APP_UID` / `APP_GID` | `1000` | 容器内运行用户，Linux 上与宿主机目录属主保持一致 |
| `APT_MIRROR` | 空 | apt 镜像（如 `mirrors.aliyun.com`） |
| `PIP_INDEX_URL` | 空 | pip 镜像（如 `https://mirrors.aliyun.com/pypi/simple/`） |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 空 | 构建期代理（仅 builder 阶段） |

### 3.5 标签规范

- 发布：`<version>`（如 `0.3.0`）、`<major>.<minor>`（`0.3`）、`latest`
- CI：`sha-<短哈希>`、分支名、PR 号
- `latest` 只跟随默认分支，避免生产误拉预发布镜像

---

## 4. 推送镜像

### 4.1 GitHub Container Registry（默认，自动）

仓库已内置 `.github/workflows/docker.yml`：

| 触发 | 行为 |
|---|---|
| push `main` / `master` | 构建并推送 `latest`、`sha-xxxx`、分支标签 |
| push tag `v*` | 按语义化版本推送 `1.2.3` / `1.2` / `latest` |
| PR | 只构建不推送（验证 Dockerfile） |
| 手动触发 | 可选择是否推送、目标平台 |

```bash
git tag v0.3.0
git push origin v0.3.0
```

镜像地址：`ghcr.io/<owner>/<repo>:0.3.0`（`owner/repo` 会自动转小写）。

拉取前如需鉴权：`echo $GITHUB_TOKEN | docker login ghcr.io -u <user> --password-stdin`
（公开仓库的 public 包可直接匿名拉取）。

### 4.2 手动推送

```bash
docker login <registry>
bash scripts/docker-push.sh -r ghcr.io/your-name -t 0.3.0 -l
pwsh scripts/docker-push.ps1 -Registry ghcr.io/your-name -Tag 0.3.0 -AlsoLatest
```

### 4.3 其它仓库

| 仓库 | 登录 | 镜像名前缀示例 |
|---|---|---|
| Docker Hub | `docker login` | `docker.io/yourname/alphaagents-cn` |
| 阿里云 ACR | `docker login registry.cn-hangzhou.aliyuncs.com` | `registry.cn-hangzhou.aliyuncs.com/your-ns/alphaagents-cn` |
| 腾讯云 TCR | `docker login ccr.ccs.tencentyun.com` | `ccr.ccs.tencentyun.com/your-ns/alphaagents-cn` |
| 私有 Harbor | `docker login harbor.example.com` | `harbor.example.com/library/alphaagents-cn` |

构建时直接指定仓库前缀即可（`-Registry/-r` 或 `IMAGE_REPO`）：

```bash
bash scripts/docker-build.sh -r registry.cn-hangzhou.aliyuncs.com/your-ns -P
```

**同时推多个仓库**（在 `docker.yml` 的 build 步骤里加登录 + 在 metadata 里加 images）：

```yaml
      - name: 登录 Docker Hub
        if: steps.gate.outputs.push == 'true' && secrets.DOCKERHUB_USERNAME != ''
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}
      # 然后把 metadata-action 的 images 改成：
      #   ghcr.io/${{ github.repository }}
      #   docker.io/${{ secrets.DOCKERHUB_USERNAME }}/alphaagents-cn
```

---

## 5. 运行镜像（不使用 Compose）

```bash
docker run -d --name alphaagents-cn \
  --restart unless-stopped \
  --init \
  -p 8000:8000 \
  --env-file .env \
  -e TA_DB_PATH=/app/data/tradingagents.db \
  -v "$PWD/data:/app/data" \
  -v "$PWD/eval_results:/app/eval_results" \
  -v "$PWD/logs:/app/logs" \
  alphaagents-cn:latest
```

健康检查内置于镜像（`HEALTHCHECK` 调 `/readyz`），`docker ps` 的 STATUS 会显示 `healthy`。

---

## 6. 服务器部署

### 6.1 用已推送的镜像（推荐）

服务器上只需要三个文件 + `.env`：

```bash
# 目录准备
mkdir -p /opt/alphaagents && cd /opt/alphaagents
# 复制 docker-compose.yml、docker-compose.prod.yml、.env.docker.example 到本目录
cp .env.docker.example .env && vi .env      # 填 LLM 密钥，建议设 WEBUI_API_KEY

export IMAGE_REPO=ghcr.io/your-name/alphaagents-cn
export IMAGE_TAG=0.3.0

docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

或用脚本：

```bash
bash scripts/docker-deploy.sh -r ghcr.io/your-name/alphaagents-cn -t 0.3.0
pwsh scripts/docker-deploy.ps1 -ImageRepo ghcr.io/your-name/alphaagents-cn -ImageTag 0.3.0
```

### 6.2 Linux 目录权限（最常见坑）

容器以 uid=1000 运行，宿主机挂载目录必须对它可写：

```bash
mkdir -p data eval_results logs
sudo chown -R 1000:1000 data eval_results logs
```

若宿主机用户不是 1000，可在 compose 里覆盖运行用户：

```yaml
    user: "${APP_UID:-1000}:${APP_GID:-1000}"
```

或在构建时指定：`--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`。

### 6.3 旧版 Compose（无 `!reset` 支持）

`docker-compose.prod.yml` 用了 `build: !reset null`（Compose ≥2.24）。
旧版本改用命令行参数，同样不会本地构建：

```bash
docker compose --pull always --no-build up -d
```

### 6.4 Nginx 反向代理 + HTTPS

```nginx
server {
    listen 443 ssl http2;
    server_name alpha.example.com;

    ssl_certificate     /etc/letsencrypt/live/alpha.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/alpha.example.com/privkey.pem;

    # WebSocket（分析过程实时推送）
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;   # 深度分析可能持续数分钟
    }
}
```

> 反向代理后记得在 `.env` 设置 `WEBUI_API_KEY`，否则任何能访问域名的人都能消耗 LLM 额度。

### 6.5 开机自启

`restart: unless-stopped` 已保证容器随 Docker 启动；再确认 Docker 服务本身开机启动：

```bash
sudo systemctl enable --now docker
```

---

## 7. 配置项（容器相关）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8000` | 宿主机端口（compose 插值） |
| `IMAGE_REPO` / `IMAGE_TAG` | 空 / `latest` | 生产部署使用的镜像 |
| `TA_DB_PATH` | `/app/data/tradingagents.db` | 容器内 SQLite 路径 |
| `WEBUI_HOST` / `WEBUI_PORT` | `0.0.0.0` / `8000` | 容器内监听地址（compose 已固定） |
| `WEBUI_API_KEY` | 空 | 开启 API 鉴权（生产强烈建议） |
| `TZ` | `Asia/Shanghai` | 影响交易时段判断 |
| `SCHEDULE_ENABLED` | `0` | 定时分析；开启后 `/readyz` 会检查调度线程 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | **运行期**代理 |
| `APT_MIRROR` / `PIP_INDEX_URL` | 空 | **构建期**镜像源 |
| `BUILD_HTTP_PROXY` / `BUILD_HTTPS_PROXY` | 空 | **构建期**代理（compose 专用） |

完整业务配置见 `.env.docker.example` 与 `docs/FEATURES_AND_API.md`。

---

## 8. 数据持久化与备份

| 容器路径 | 宿主机 | 内容 |
|---|---|---|
| `/app/data` | `./data` | `tradingagents.db`（报告/自选/组合/任务/用户）、`market.db`（行情缓存） |
| `/app/eval_results` | `./eval_results` | 每次分析的完整报告 |
| `/app/logs` | `./logs` | 运行日志 |

**备份**（推荐先停写，避免 SQLite 半写状态）：

```bash
docker compose stop
tar czf backup-$(date +%F).tar.gz data eval_results
docker compose start
```

如需不停机备份，用 SQLite 自带的热备（容器内）：

```bash
docker compose exec alphaagents python -c "
import sqlite3
src = sqlite3.connect('/app/data/tradingagents.db')
dst = sqlite3.connect('/app/data/backup.db')
src.backup(dst); dst.close(); src.close()
"
```

> ⚠️ **不要**在容器运行时用宿主机上的工具（SQLite 客户端、本地 CLI 等）打开同一个
> `data/tradingagents.db`。WAL 模式依赖 `-shm/-wal` 文件，跨系统并发访问会让容器侧的
> 句柄失效，表现为 `unable to open database file`、`/readyz` 返回 503。
> 需要查看数据时请用 `docker compose exec alphaagents python ...` 在容器内操作，
> 或者先 `docker compose stop`。

---

## 9. 运维

```bash
docker compose ps                      # 状态（含 healthy）
docker compose logs -f --tail=200      # 日志
docker compose restart                 # 重启
docker compose stop / start            # 停止/启动（保留容器）
docker compose down                    # 删除容器（数据卷/绑定目录保留）
docker compose down -v                 # 连具名卷一起删（慎用）
```

**升级**：

```bash
export IMAGE_TAG=0.4.0
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**回滚**：把 `IMAGE_TAG` 改回旧版本再 `pull && up -d` 即可（数据库结构向后兼容，迁移由应用自动完成）。

**健康接口**：

- `/healthz`：存活探针（进程活着即 200），适合 liveness
- `/readyz`：就绪探针（检查数据库可写 + 表结构 + 调度线程），并额外返回
  `vendor_cooldowns`（当前处于限流冷却的数据源），适合 readiness 与排障
- `/metrics`：Prometheus 文本指标

**清理**：

```bash
docker image prune -f                  # 清理悬空镜像
docker builder prune -f                # 清理构建缓存（可回收数 GB）
```

---

## 10. 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 构建卡在 `apt-get update` / 404 | 无法访问 `deb.debian.org` | 加 `--build-arg APT_MIRROR=mirrors.aliyun.com` |
| `pip` 下载超时 | 无法访问 `pypi.org` | 加 `--build-arg PIP_INDEX_URL=...` 或用 `-Proxy` |
| 代理不起作用 | 用了 `127.0.0.1` | 改为 `host.docker.internal`（Desktop）或网关 IP（Linux） |
| 容器 `unhealthy` | `/readyz` 失败 | `docker logs`；多为数据目录不可写或数据库被占用 |
| `unable to open database file` | 目录权限 / 宿主机并发打开 DB | `chown -R 1000:1000 data`；停止宿主机侧访问后 `docker compose restart` |
| 端口被占用 | 8000 已被本地服务使用 | 改 `PORT=18000` 或 `-p 18000:8000` |
| 时间不对 | 容器时区 | 确认 `TZ=Asia/Shanghai`（镜像已装 tzdata） |
| 镜像太大 | pandas/akshare 依赖 | 属正常（~740MB）；可定期 `docker builder prune` |
| 分析中途很慢 / 报限流 | 东方财富 IP 限流 | 见 `docs/FEATURES_AND_API.md` 数据源韧性一节；冷却期会自动跳过 |

---

## 11. 安全清单

- [ ] `.env` 不提交仓库（已在 `.gitignore`），镜像内不含任何密钥
- [ ] 生产设置 `WEBUI_API_KEY`（未设置时仅建议监听 127.0.0.1）
- [ ] 容器以非 root（uid=1000）运行，`no-new-privileges:true`
- [ ] 只暴露必要端口；前面套 HTTPS 反向代理
- [ ] 定期备份 `data/`（含报告、自选、组合、用户）
- [ ] 及时更新基础镜像：`docker compose build --pull`
- [ ] 不要把 Docker socket 挂进容器
