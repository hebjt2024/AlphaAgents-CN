"""WebUI HTTP API 配置（全部来自环境变量，带安全默认值）。

配置读取函数 :func:`load_config` **每次调用都会重新读取环境变量**，不做任何缓存。
这样中间件与鉴权依赖都能在**请求期**拿到最新配置：
既方便测试（改完 ``os.environ`` 立即生效），也支持运行时调整限流阈值等参数。

环境变量说明
------------
``WEBUI_API_KEY``
    访问 API 所需的密钥。
    **未设置时鉴权完全关闭** —— 本项目默认在本机运行，强制鉴权会破坏所有
    现有用户的启动流程。设置后，除豁免路由外的所有 API 请求都必须携带该
    密钥（``X-API-Key`` 头或 ``Authorization: Bearer <key>`` 头）。
``WEBUI_ALLOWED_ORIGINS``
    逗号分隔的 CORS 白名单，例如 ``http://localhost:5173,http://127.0.0.1:5173``。
    为空表示不安装 CORS 中间件（只允许同源访问）。
``WEBUI_RATE_LIMIT``
    单个客户端每分钟允许的请求数，默认 ``120``。
``WEBUI_RATE_LIMIT_ENABLED``
    是否启用限流，默认开启（``1``）。设为 ``0`` 可完全关闭限流。
``WEBUI_API_PREFIX``
    API 路由前缀，默认 ``/api``（保持既有路径不变）。改成 ``/api/v1`` 会让
    旧路径失效，仅在你愿意破坏兼容时使用。
``WEBUI_HOST`` / ``WEBUI_PORT``
    仅用于**启动期安全告警**判断服务是否监听在非回环地址。
    注意：真正的绑定地址由 uvicorn 启动参数决定（``webui/run_webui.py`` 默认
    ``0.0.0.0``），这里只是给运维一个显式声明的入口。
``WEBUI_RATE_LIMIT_MAX_CLIENTS``
    限流器最多跟踪的客户端数量，默认 ``4096``，用于防止伪造 IP 造成内存无限增长。
``WEBUI_TRUST_PROXY_HEADERS``
    是否信任 ``X-Forwarded-For`` / ``X-Real-IP`` 作为客户端标识，默认关闭。
    只有在服务确实位于可信反向代理之后时才应开启，否则客户端可随意伪造 IP
    绕过限流。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "ApiConfig",
    "DEFAULT_API_PREFIX",
    "DEFAULT_MAX_TRACKED_CLIENTS",
    "DEFAULT_RATE_LIMIT",
    "get_config",
    "load_config",
]

DEFAULT_API_PREFIX = "/api"
DEFAULT_RATE_LIMIT = 120
DEFAULT_MAX_TRACKED_CLIENTS = 4096
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("0", "false", "no", "off")

_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost", "localhost.localdomain")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ApiConfig:
    """一份不可变的 API 配置快照。"""

    api_key: str | None = None
    allowed_origins: tuple[str, ...] = ()
    rate_limit: int = DEFAULT_RATE_LIMIT
    rate_limit_enabled: bool = True
    api_prefix: str = DEFAULT_API_PREFIX
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_tracked_clients: int = DEFAULT_MAX_TRACKED_CLIENTS
    trust_proxy_headers: bool = False
    multi_user: bool = False
    session_days: int = 7

    @property
    def auth_enabled(self) -> bool:
        """是否启用了鉴权（API Key 或多用户会话）。"""
        return bool(self.api_key) or bool(self.multi_user)

    @property
    def cors_enabled(self) -> bool:
        """是否需要安装 CORS 中间件。"""
        return bool(self.allowed_origins)

    @property
    def is_loopback_host(self) -> bool:
        """绑定地址是否只监听回环。"""
        return (self.host or "").strip().lower() in _LOOPBACK_HOSTS


def _normalize_prefix(raw: str | None) -> str:
    prefix = (raw or "").strip() or DEFAULT_API_PREFIX
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    prefix = prefix.rstrip("/")
    return prefix or DEFAULT_API_PREFIX


def load_config() -> ApiConfig:
    """从环境变量读取当前配置（每次调用都重新读取，不缓存）。"""
    api_key = (os.getenv("WEBUI_API_KEY") or "").strip() or None
    origins = tuple(
        item.strip()
        for item in (os.getenv("WEBUI_ALLOWED_ORIGINS") or "").split(",")
        if item.strip()
    )
    rate_limit = max(1, _env_int("WEBUI_RATE_LIMIT", DEFAULT_RATE_LIMIT))
    max_clients = max(
        16, _env_int("WEBUI_RATE_LIMIT_MAX_CLIENTS", DEFAULT_MAX_TRACKED_CLIENTS)
    )
    return ApiConfig(
        api_key=api_key,
        allowed_origins=origins,
        rate_limit=rate_limit,
        rate_limit_enabled=_env_bool("WEBUI_RATE_LIMIT_ENABLED", True),
        api_prefix=_normalize_prefix(os.getenv("WEBUI_API_PREFIX")),
        host=(os.getenv("WEBUI_HOST") or "").strip() or DEFAULT_HOST,
        port=_env_int("WEBUI_PORT", DEFAULT_PORT),
        max_tracked_clients=max_clients,
        trust_proxy_headers=_env_bool("WEBUI_TRUST_PROXY_HEADERS", False),
        multi_user=_env_bool("WEBUI_MULTI_USER", False),
        session_days=max(1, _env_int("WEBUI_SESSION_DAYS", 7)),
    )


#: 别名，便于在其他模块里按习惯引用。
get_config = load_config
