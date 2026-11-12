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
from webui.backend.errors import AuthError

__all__ = [
    "API_KEY_HEADER",
    "AUTH_HEADER",
    "EXEMPT_PATHS",
    "api_key_scheme",
    "bearer_scheme",
    "extract_api_key",
    "require_api_key",
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


def verify_api_key(request: Request) -> None:
    """校验 API 密钥。

    未配置 ``WEBUI_API_KEY`` 时直接放行（鉴权关闭）；密钥缺失或不匹配时抛出
    :class:`~webui.backend.errors.AuthError`（401 + 统一错误信封）。
    """
    config = load_config()
    expected = config.api_key
    if not expected:
        return

    provided = extract_api_key(request)
    if not provided:
        raise AuthError(
            "缺少 API 密钥，请通过 X-API-Key 头或 Authorization: Bearer 头提供",
            details={"header": API_KEY_HEADER},
        )
    if not secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise AuthError(
            "API 密钥无效",
            details={"header": API_KEY_HEADER},
        )


async def require_api_key(request: Request) -> None:
    """FastAPI 依赖：``dependencies=[Depends(require_api_key)]``。

    挂到 APIRouter 上即可保护该路由器下的全部路由。
    """
    verify_api_key(request)
