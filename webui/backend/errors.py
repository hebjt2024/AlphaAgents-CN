"""统一错误信封与异常处理。

设计目标：把 WebUI 里两套互不相容的错误约定收敛成**一种**：

* 框架级错误（404 / 405 / 422 校验失败 / 未捕获异常）→ HTTP 4xx/5xx +
  统一信封；
* 业务级失败（"任务不存在"、"已有分析任务正在运行"）→ 保持 HTTP 200 +
  ``{"success": false, ...}``，以免破坏前端与既有测试，但**载荷结构**同样
  使用统一信封（补齐 ``code`` / ``request_id``）。

统一信封（所有错误载荷字段固定，便于客户端稳定解析）::

    {
        "success": false,
        "code": "not_found",        # 稳定的机器可读错误码
        "message": "资源不存在",      # 人类可读描述
        "details": null,            # 可选的结构化补充信息
        "request_id": "3f2a..."      # 与响应头 X-Request-ID 一致
    }

错误码一览
----------
``bad_request``            400 请求参数不合法
``unauthorized``           401 缺少或错误的 API 密钥
``not_found``              404 资源不存在
``conflict``               409 状态冲突
``validation_error``       422 请求体/查询参数校验失败
``rate_limited``           429 触发限流
``internal_error``         500 未捕获异常（绝不回传堆栈）
``service_unavailable``    503 依赖不可用
``http_error``             其它 HTTPException 的兜底码
``business_error``         200 业务失败（success=false）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

__all__ = [
    "ApiError",
    "AuthError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "RateLimitError",
    "ServiceUnavailableError",
    "build_error_body",
    "error_payload",
    "error_response",
    "safe_exception_message",
    "register_exception_handlers",
]

#: 稳定的错误码常量表（便于客户端引用）。
ERROR_CODES: tuple[str, ...] = (
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "validation_error",
    "rate_limited",
    "internal_error",
    "service_unavailable",
    "http_error",
    "business_error",
)


class ApiError(Exception):
    """所有 API 异常的基类。

    Attributes:
        code: 稳定的机器可读错误码（子类默认给出）。
        message: 人类可读描述。
        status_code: 对应的 HTTP 状态码。
        details: 可选的结构化补充信息。
    """

    code: str = "api_error"
    status_code: int = 500

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        self.headers = headers or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class BadRequestError(ApiError):
    """400 请求参数不合法。"""

    code = "bad_request"
    status_code = 400


class NotFoundError(ApiError):
    """404 资源不存在。"""

    code = "not_found"
    status_code = 404


class ConflictError(ApiError):
    """409 与当前资源状态冲突。"""

    code = "conflict"
    status_code = 409


class RateLimitError(ApiError):
    """429 触发限流。

    Attributes:
        retry_after: 建议客户端等待的秒数。
    """

    code = "rate_limited"
    status_code = 429

    def __init__(
        self,
        message: str = "请求过于频繁，请稍后再试",
        *,
        retry_after: int = 60,
        details: Any = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after = max(1, int(retry_after))
        self.headers = {"Retry-After": str(self.retry_after)}


class AuthError(ApiError):
    """401 缺少或错误的 API 密钥。"""

    code = "unauthorized"
    status_code = 401


class ForbiddenError(ApiError):
    """403 已认证但权限不足。"""

    code = "forbidden"
    status_code = 403


class ServiceUnavailableError(ApiError):
    """503 依赖（数据库 / 调度器 / 上游服务）不可用。"""

    code = "service_unavailable"
    status_code = 503


def build_error_body(
    code: str,
    message: str,
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """构造统一错误信封（字段顺序与集合固定）。"""
    return {
        "success": False,
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }


def error_payload(
    message: str,
    code: str = "business_error",
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """业务级失败载荷（HTTP 200 + ``success=false``），结构与错误信封一致。

    未显式给出 ``request_id`` 时自动取当前请求的 request id。
    """
    if request_id is None:
        from webui.backend.middleware import current_request_id

        request_id = current_request_id()
    return build_error_body(code, message, details, request_id)


def safe_exception_message(exc: Exception, default: str = "服务暂时不可用，请稍后重试") -> str:
    """对外错误信息：只透传可控的业务异常，内部异常写日志后返回稳定文案。

    RuntimeError / ValueError 通常来自业务校验或数据源，可保留原文；
    sqlite3 / OSError / AttributeError / KeyError 等可能包含路径、表名、
    内部 URL，统一隐藏，避免信息泄露。
    """
    if isinstance(exc, (ValueError, RuntimeError, TimeoutError, ConnectionError)):
        text = str(exc).strip()
        if text:
            return text[:300]
    logger.warning("API 内部异常（已脱敏返回）：%s", type(exc).__name__, exc_info=exc)
    return default


def _request_id(request: Request | None) -> str | None:
    """从请求状态里取回 request id。"""
    if request is None:
        return None
    state = getattr(request, "state", None)
    value = getattr(state, "request_id", None)
    if value:
        return str(value)
    from webui.backend.middleware import current_request_id

    return current_request_id()


def error_response(
    exc: Exception,
    request_id: str | None = None,
    *,
    status_code: int | None = None,
    code: str | None = None,
    message: str | None = None,
    details: Any = None,
) -> JSONResponse:
    """把异常转换成统一信封的 JSON 响应。"""
    if isinstance(exc, ApiError):
        body = build_error_body(
            code or exc.code,
            message or exc.message,
            details if details is not None else exc.details,
            request_id,
        )
        final_status = status_code or exc.status_code
        headers = dict(getattr(exc, "headers", {}) or {})
    else:
        body = build_error_body(
            code or "internal_error",
            message or "服务内部错误",
            details,
            request_id,
        )
        final_status = status_code or 500
        headers = {}
    headers.setdefault("X-Request-ID", request_id or "")
    if not headers["X-Request-ID"]:
        headers.pop("X-Request-ID")
    return JSONResponse(status_code=final_status, content=body, headers=headers)


async def _api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc, _request_id(request))


async def _validation_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = exc.errors()
    safe_details = []
    for item in details:
        entry = dict(item)
        # ctx 里可能带有不可 JSON 序列化的对象（如 ValueError 实例）
        ctx = entry.get("ctx")
        if isinstance(ctx, dict):
            entry["ctx"] = {k: str(v) for k, v in ctx.items()}
        safe_details.append(entry)
    return error_response(
        exc,
        _request_id(request),
        status_code=422,
        code="validation_error",
        message="请求参数校验失败",
        details=safe_details,
    )


async def _http_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail
    code = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(exc.status_code, "http_error")
    message = detail if isinstance(detail, str) else str(detail)
    response = error_response(
        exc,
        _request_id(request),
        status_code=exc.status_code,
        code=code,
        message=message,
        details=None,
    )
    for key, value in (getattr(exc, "headers", None) or {}).items():
        response.headers[key] = value
    return response


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """兜底处理器：记录完整堆栈，但不把堆栈泄漏给客户端。"""
    logger.exception(
        "未捕获异常 request_id=%s path=%s", _request_id(request), request.url.path
    )
    return error_response(
        exc,
        _request_id(request),
        status_code=500,
        code="internal_error",
        message="服务内部错误",
        details=None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """安装统一异常处理器（须在应用开始接收请求之前调用）。"""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


class NotFoundResponder:
    """未匹配路由的 ASGI 兜底：返回统一错误信封而不是纯文本 404。

    Starlette 的 Router 对未匹配路径默认返回 ``PlainTextResponse("Not Found")``
    （并不会抛 HTTPException），因此必须替换 ``router.default`` 才能让 404
    也走统一信封。
    """

    def __init__(self, message: str = "请求的资源不存在") -> None:
        self.message = message

    async def __call__(self, scope, receive, send) -> None:
        request = Request(scope)
        response = error_response(
            NotFoundError(self.message),
            _request_id(request),
        )
        await response(scope, receive, send)
