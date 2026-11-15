"""API 密钥鉴权。

鉴权模型
--------
* **默认关闭**：``WEBUI_API_KEY`` 未配置时，所有接口保持匿名可访问 —— 本项目
  默认在本机运行，强制鉴权会破坏所有现有用户的启动流程。
* **开启后**：除豁免路由外的所有 API 请求都必须携带密钥，密钥通过
  ``X-API-Key`` 头或 ``Authorization: Bearer <key>`` 头传递，比较使用
  ``secrets.compare_digest``（常量时间，避免时序侧信道）。

豁免路由（不受 API 密钥保护）
------------------------------
===========================  ==========================================
路径                         原因
===========================  ==========================================
``/healthz``                 存活探针（Docker HEALTHCHECK 直接命中）
``/readyz``                  就绪探针
``/metrics``                 指标端点（Prometheus 抓取）
``/`` 与 ``/static/*``       静态前端页面与资源
``/ws``                      WebSocket：浏览器无法自定义 WS 头，改为在
                             连接时用查询参数 ``?api_key=<key>`` 校验
===========================  ==========================================

其余所有 ``/api`` 前缀的路由都受保护。鉴权依赖挂在 **APIRouter 级别**
（见 ``webui/backend/server.py`` 的 ``api_router``），而不是逐个路由声明，
这样新增路由默认就是受保护的，不会漏配。
"""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.security import APIKeyHeader, HTTPBearer

from webui.backend.api_config import load_config
from webui.backend.errors import AuthError, ForbiddenError
from webui.backend.users import READ_METHODS, get_session_user

__all__ = [
    "API_KEY_HEADER",
    "AUTH_HEADER",
    "EXEMPT_PATHS",
    "api_key_scheme",
    "bearer_scheme",
    "current_user",
    "extract_api_key",
    "require_api_key",
    "require_role",
    "require_risk_ack",
    "verify_api_key",
]

#: 携带密钥的自定义头。
API_KEY_HEADER = "X-API-Key"

#: 标准鉴权头（``Bearer <key>``）。
AUTH_HEADER = "Authorization"

#: 免鉴权的路径前缀（与静态前端、探针保持一致）。
EXEMPT_PATHS: tuple[str, ...] = ("/", "/healthz", "/readyz", "/metrics", "/static")

#: 仅用于 OpenAPI 文档的安全方案声明（``auto_error=False``，真正的校验在
#: :func:`verify_api_key` 里完成，这样错误也能走统一错误信封）。
api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER, scheme_name="ApiKeyAuth", auto_error=False
)
bearer_scheme = HTTPBearer(scheme_name="BearerAuth", auto_error=False)


def extract_api_key(request: Request) -> str | None:
    """从请求头里提取 API 密钥（``X-API-Key`` 优先，其次 Bearer 头）。"""
    header_value = request.headers.get(API_KEY_HEADER)
    if header_value and header_value.strip():
        return header_value.strip()

    auth_value = request.headers.get(AUTH_HEADER)
    if auth_value:
        parts = auth_value.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token:
                return token
    return None


def _local_principal() -> dict[str, str]:
    return {"username": "local", "role": "admin", "display_name": "本机用户"}


def verify_api_key(request: Request) -> None:
    """校验 API 密钥 / 多用户会话，并把用户写入 ``request.state.user``。

    - 单用户模式（未配置 API Key 且未开启多用户）：匿名放行，角色视为 admin；
    - 配置 ``WEBUI_API_KEY``：密钥匹配视为 admin（供自动化/脚本使用）；
    - 开启 ``WEBUI_MULTI_USER``：``X-API-Key`` / Bearer 里可以放会话 token；
      会话用户按角色执行 RBAC，viewer 禁止写操作。
    """
    path = request.url.path
    # 登录与登录页配置必须在未认证时访问。
    if path.endswith("/auth/login") or path.endswith("/auth/config"):
        return

    config = load_config()
    provided = extract_api_key(request)

    if config.api_key and provided and secrets.compare_digest(
        provided.encode("utf-8"), config.api_key.encode("utf-8")
    ):
        request.state.user = {
            "username": "api-key",
            "role": "admin",
            "display_name": "API Key",
        }
        return

    if config.multi_user:
        if not provided:
            raise AuthError(
                "请先登录，或通过 X-API-Key / Authorization: Bearer 提供会话 token",
                details={"header": API_KEY_HEADER},
            )
        user = get_session_user(provided)
        if not user:
            raise AuthError("会话已失效，请重新登录")
        viewer_self_service = path.endswith(
            ("/auth/logout", "/auth/risk-ack", "/auth/change-password")
        )
        if (
            user.get("role") == "viewer"
            and request.method.upper() not in READ_METHODS
            and not viewer_self_service
        ):
            raise ForbiddenError("viewer 角色为只读，不能执行写操作")
        request.state.user = user
        return

    if config.api_key:
        if not provided:
            raise AuthError(
                "缺少 API 密钥，请通过 X-API-Key 头或 Authorization: Bearer 头提供",
                details={"header": API_KEY_HEADER},
            )
        raise AuthError("API 密钥无效", details={"header": API_KEY_HEADER})

    # 单用户模式：不需要登录。
    request.state.user = _local_principal()


async def require_api_key(request: Request) -> None:
    """FastAPI 依赖：``dependencies=[Depends(require_api_key)]``。"""
    verify_api_key(request)


async def current_user(request: Request) -> dict[str, str]:
    """返回当前请求的用户信息（单用户模式返回本机 admin）。"""
    user = getattr(request.state, "user", None)
    if user:
        return user
    if load_config().auth_enabled:
        raise AuthError("未认证")
    return _local_principal()


async def require_risk_ack(request: Request) -> None:
    """多用户模式下，分析/交易类写接口必须先确认风险揭示。"""
    config = load_config()
    if not config.multi_user:
        return
    user = getattr(request.state, "user", None) or {}
    if not user.get("risk_ack_at"):
        raise ForbiddenError("请先阅读并确认风险揭示后再执行该操作")


def require_role(*roles: str):
    """生成一个角色校验依赖：``Depends(require_role("admin"))``。"""

    allowed = set(roles)

    async def _dependency(request: Request) -> dict[str, str]:
        user = getattr(request.state, "user", None)
        if not user:
            if load_config().auth_enabled:
                raise AuthError("未认证")
            user = _local_principal()
        if user.get("role") not in allowed:
            raise ForbiddenError(
                f"需要角色：{', '.join(sorted(allowed))}；当前：{user.get('role')}"
            )
        return user

    return _dependency
