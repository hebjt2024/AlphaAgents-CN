"""HTTP 中间件：请求 ID、限流、耗时统计，以及进程内指标注册表。

本模块不引入任何第三方依赖（不依赖 prometheus_client），指标以
Prometheus 文本格式导出。

三个中间件都通过 :class:`~webui.backend.api_config.ApiConfig` **在请求期**
读取配置（而不是导入期），因此修改环境变量后立即生效，便于测试与运维调参。
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from webui.backend.api_config import ApiConfig, load_config

logger = logging.getLogger(__name__)

__all__ = [
    "MetricsRegistry",
    "RateLimitMiddleware",
    "RequestIdMiddleware",
    "TimingMiddleware",
    "current_request_id",
    "metrics",
]

#: 限流滑动窗口长度（秒）。
RATE_LIMIT_WINDOW_SECONDS = 60.0

#: 免鉴权 / 免限流的路径（探针与静态前端不能因为限流而失效）。
EXEMPT_PATHS: frozenset[str] = frozenset({"/", "/healthz", "/readyz", "/metrics"})

#: 以这些前缀开头的路径同样豁免（静态前端资源）。
EXEMPT_PREFIXES: tuple[str, ...] = ("/static",)

_REQUEST_ID_HEADER = "X-Request-ID"
_request_id_var: ContextVar[str | None] = ContextVar("webui_request_id", default=None)


def current_request_id() -> str | None:
    """返回当前请求的 request id（不在请求上下文中时返回 ``None``）。"""
    return _request_id_var.get()


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def _client_identity(request: Request, config: ApiConfig) -> str:
    """解析限流使用的客户端标识。

    默认使用 TCP 对端地址；只有显式开启 ``WEBUI_TRUST_PROXY_HEADERS`` 时才
    采信 ``X-Forwarded-For`` / ``X-Real-IP``（否则客户端可随意伪造）。
    """
    if config.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
    client = request.client
    if client is not None and client.host:
        return str(client.host)
    return "unknown"


# ============================================================================
# 指标注册表
# ============================================================================

#: 耗时直方图的桶边界（秒）。
DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

#: 计算分位数用的样本保留上限。
_MAX_SAMPLES = 2048

_SEGMENT_RE = re.compile(r"\d+")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_ID_RE = re.compile(r"^[0-9a-zA-Z]{16,}$")


def _normalize_path(path: str) -> str:
    """把路径里的数字 / UUID 段归一化，避免指标标签基数爆炸。"""
    normalized = _UUID_RE.sub("{uuid}", path)
    normalized = _SEGMENT_RE.sub("{id}", normalized)
    parts = []
    for segment in normalized.split("/"):
        if _LONG_ID_RE.match(segment):
            parts.append("{id}")
        else:
            parts.append(segment)
    return "/".join(parts)[:200]


def _route_template(request: Request) -> str:
    """尽量还原出路由模板（如 ``/api/jobs/{job_id}``），用于按路由统计。"""
    scope = request.scope
    route = scope.get("route")
    if route is not None:
        template = getattr(route, "path", None)
        if template:
            return str(template)
    app = scope.get("app")
    if app is not None:
        try:
            for candidate in getattr(app, "routes", []):
                match, _child_scope = candidate.matches(scope)
                if getattr(match, "name", "") == "FULL":
                    template = getattr(candidate, "path", None)
                    if template:
                        return str(template)
        except Exception:  # pragma: no cover - 匹配失败时退回原路径
            pass
    return _normalize_path(scope.get("path", ""))


class MetricsRegistry:
    """极简的进程内指标注册表（计数器 + 耗时直方图 + 按路由拆分）。"""

    def __init__(self, max_routes: int = 200) -> None:
        self._lock = threading.Lock()
        self._max_routes = max_routes
        self._started_at = time.time()
        self.requests_total = 0
        self.errors_total = 0
        self.duration_count = 0
        self.duration_sum = 0.0
        self.duration_buckets: dict[float, int] = {b: 0 for b in DURATION_BUCKETS}
        self._samples: deque[float] = deque(maxlen=_MAX_SAMPLES)
        self._routes: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------
    def record(
        self,
        method: str,
        route: str,
        status_code: int,
        duration: float,
    ) -> None:
        """记录一次请求。"""
        label = f"{method.upper()} {route}"
        with self._lock:
            self.requests_total += 1
            if status_code >= 400:
                self.errors_total += 1
            self.duration_count += 1
            self.duration_sum += duration
            self._samples.append(duration)
            for bucket in DURATION_BUCKETS:
                if duration <= bucket:
                    self.duration_buckets[bucket] += 1

            stat = self._routes.get(label)
            if stat is None:
                if len(self._routes) >= self._max_routes:
                    self._routes.popitem(last=False)
                stat = {
                    "count": 0,
                    "errors": 0,
                    "sum": 0.0,
                    "buckets": {b: 0 for b in DURATION_BUCKETS},
                }
                self._routes[label] = stat
            stat["count"] += 1
            if status_code >= 400:
                stat["errors"] += 1
            stat["sum"] += duration
            for bucket in DURATION_BUCKETS:
                if duration <= bucket:
                    stat["buckets"][bucket] += 1
            self._routes.move_to_end(label)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """返回当前指标的浅拷贝（便于测试与调试）。"""
        with self._lock:
            return {
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "duration_count": self.duration_count,
                "duration_sum": self.duration_sum,
                "uptime_seconds": time.time() - self._started_at,
                "routes": {
                    label: {
                        "count": stat["count"],
                        "errors": stat["errors"],
                        "sum": stat["sum"],
                    }
                    for label, stat in self._routes.items()
                },
            }

    def _quantiles(self) -> dict[str, float]:
        with self._lock:
            samples = sorted(self._samples)
        if not samples:
            return {}
        result: dict[str, float] = {}
        for q in (0.5, 0.9, 0.99):
            index = min(len(samples) - 1, int(round(q * (len(samples) - 1))))
            result[f"{q}"] = samples[index]
        return result

    def render_prometheus(self) -> str:
        """导出 Prometheus 文本格式。"""
        lines: list[str] = []
        lines.append("# HELP requests_total Total number of HTTP requests handled.")
        lines.append("# TYPE requests_total counter")
        lines.append(f"requests_total {self.requests_total}")
        lines.append(
            "# HELP errors_total Total number of HTTP responses with status >= 400."
        )
        lines.append("# TYPE errors_total counter")
        lines.append(f"errors_total {self.errors_total}")
        lines.append(
            "# HELP request_duration_seconds HTTP request duration in seconds."
        )
        lines.append("# TYPE request_duration_seconds histogram")
        with self._lock:
            for bucket in DURATION_BUCKETS:
                lines.append(
                    f'request_duration_seconds_bucket{{le="{bucket}"}} '
                    f"{self.duration_buckets[bucket]}"
                )
            lines.append(
                f'request_duration_seconds_bucket{{le="+Inf"}} {self.duration_count}'
            )
            duration_sum = self.duration_sum
            duration_count = self.duration_count
            routes = [(label, dict(stat)) for label, stat in self._routes.items()]
        lines.append(f"request_duration_seconds_sum {duration_sum:.6f}")
        lines.append(f"request_duration_seconds_count {duration_count}")
        quantiles = self._quantiles()
        if quantiles:
            lines.append(
                "# HELP request_duration_seconds_quantile "
                "Approximate request duration quantiles (recent samples)."
            )
            lines.append("# TYPE request_duration_seconds_quantile summary")
            for quantile, value in quantiles.items():
                lines.append(
                    f'request_duration_seconds_quantile{{quantile="{quantile}"}} '
                    f"{value:.6f}"
                )
            lines.append(
                f"request_duration_seconds_quantile_sum {duration_sum:.6f}"
            )
            lines.append(
                f"request_duration_seconds_quantile_count {duration_count}"
            )
        lines.append("# HELP route_requests_total Requests per route.")
        lines.append("# TYPE route_requests_total counter")
        for label, stat in routes:
            method, _, route = label.partition(" ")
            lines.append(
                f'route_requests_total{{method="{method}",route="{route}"}} '
                f"{stat['count']}"
            )
        lines.append("# HELP route_errors_total Error responses per route.")
        lines.append("# TYPE route_errors_total counter")
        for label, stat in routes:
            method, _, route = label.partition(" ")
            lines.append(
                f'route_errors_total{{method="{method}",route="{route}"}} '
                f"{stat['errors']}"
            )
        lines.append(
            "# HELP route_request_duration_seconds Request duration per route."
        )
        lines.append("# TYPE route_request_duration_seconds histogram")
        for label, stat in routes:
            method, _, route = label.partition(" ")
            for bucket in DURATION_BUCKETS:
                lines.append(
                    f'route_request_duration_seconds_bucket{{method="{method}",'
                    f'route="{route}",le="{bucket}"}} {stat["buckets"][bucket]}'
                )
            lines.append(
                f'route_request_duration_seconds_bucket{{method="{method}",'
                f'route="{route}",le="+Inf"}} {stat["count"]}'
            )
            lines.append(
                f'route_request_duration_seconds_sum{{method="{method}",'
                f'route="{route}"}} {stat["sum"]:.6f}'
            )
            lines.append(
                f'route_request_duration_seconds_count{{method="{method}",'
                f'route="{route}"}} {stat["count"]}'
            )
        lines.append("# HELP process_uptime_seconds Seconds since process start.")
        lines.append("# TYPE process_uptime_seconds gauge")
        lines.append(
            f"process_uptime_seconds {time.time() - self._started_at:.3f}"
        )
        return "\n".join(lines) + "\n"


#: 进程级指标注册表。
metrics = MetricsRegistry()


# ============================================================================
# 中间件
# ============================================================================


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 UUID4 hex 的 request id。

    * 写入 ``request.state.request_id``；
    * 通过 ``X-Request-ID`` 响应头回传；
    * 同时写入上下文变量，便于业务代码构造错误载荷。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 的滑动窗口限流器。

    算法：每个客户端维护一个时间戳队列（``collections.deque``），请求到达时
    先丢弃窗口（60 秒）之外的旧时间戳，再判断队列长度是否已达上限。

    * 线程安全：所有状态变更都在 ``threading.Lock`` 保护下进行（WebUI 的
      处理函数运行在线程池里）。
    * 内存有界：最多跟踪 ``config.max_tracked_clients`` 个客户端；超出时先
      清理过期条目，仍超限则淘汰最久未使用的客户端（LRU），避免伪造 IP 造成
      内存无限增长。
    """

    def __init__(
        self,
        app,
        config_provider: Callable[[], ApiConfig] | None = None,
        exempt_paths: frozenset[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._config_provider = config_provider or load_config
        self._exempt_paths = (
            EXEMPT_PATHS if exempt_paths is None else exempt_paths
        )
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._requests_since_prune = 0

    # ------------------------------------------------------------------
    def _check(self, client: str, limit: int, max_clients: int) -> tuple[bool, int]:
        """记录一次命中，返回 ``(是否放行, 建议重试秒数)``。"""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(client)
            if bucket is None:
                bucket = deque()
                self._buckets[client] = bucket

            while bucket and (now - bucket[0]) > RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= limit:
                self._buckets.move_to_end(client)
                oldest = bucket[0]
                retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1
                return False, max(1, retry_after)
            bucket.append(now)
            self._buckets.move_to_end(client)
            # 容量检查放在写入之后：新桶非空，不会被过期清理误删
            self._enforce_capacity_locked(now, max_clients)
            return True, 0

    def _enforce_capacity_locked(self, now: float, max_clients: int) -> None:
        """保证跟踪的客户端数量有界（调用方必须持有锁）。"""
        if len(self._buckets) <= max_clients:
            return
        # 1) 先清理全部过期条目
        for key in list(self._buckets.keys()):
            bucket = self._buckets[key]
            while bucket and (now - bucket[0]) > RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()
            if not bucket:
                del self._buckets[key]
        # 2) 仍超限则淘汰最久未使用的客户端（LRU）
        while len(self._buckets) > max_clients:
            self._buckets.popitem(last=False)

    def _maybe_prune(self) -> None:
        """定期清理过期条目（避免长期运行的进程积累空桶）。"""
        with self._lock:
            self._requests_since_prune += 1
            if self._requests_since_prune < 1000:
                return
            self._requests_since_prune = 0
            now = time.monotonic()
            for key in list(self._buckets.keys()):
                bucket = self._buckets[key]
                while bucket and (now - bucket[0]) > RATE_LIMIT_WINDOW_SECONDS:
                    bucket.popleft()
                if not bucket:
                    del self._buckets[key]

    # ------------------------------------------------------------------
    async def dispatch(self, request: Request, call_next) -> Response:
        config = self._config_provider()
        path = request.url.path
        if not config.rate_limit_enabled or _is_exempt(path):
            return await call_next(request)

        client = _client_identity(request, config)
        allowed, retry_after = self._check(
            client, config.rate_limit, config.max_tracked_clients
        )
        self._maybe_prune()
        if allowed:
            return await call_next(request)

        from webui.backend.errors import build_error_body

        request_id = getattr(request.state, "request_id", None) or current_request_id()
        body = build_error_body(
            "rate_limited",
            f"请求过于频繁，请 {retry_after} 秒后重试",
            {"client": client, "limit": config.rate_limit},
            request_id,
        )
        return JSONResponse(
            status_code=429,
            content=body,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(config.rate_limit),
                _REQUEST_ID_HEADER: request_id or "",
            },
        )


class TimingMiddleware(BaseHTTPMiddleware):
    """记录每次请求的耗时，并写入指标注册表。"""

    def __init__(
        self,
        app,
        registry: MetricsRegistry | None = None,
        config_provider: Callable[[], ApiConfig] | None = None,
    ) -> None:
        super().__init__(app)
        self._registry = registry or metrics
        self._config_provider = config_provider or load_config

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            try:
                self._registry.record(
                    request.method, _route_template(request), status_code, duration
                )
            except Exception:  # pragma: no cover - 指标不应影响主流程
                logger.exception("记录请求指标失败")
            if response is not None:
                response.headers["X-Response-Time-Ms"] = f"{duration * 1000:.2f}"
