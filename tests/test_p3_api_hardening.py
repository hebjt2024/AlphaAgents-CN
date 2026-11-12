"""P3：WebUI HTTP API 硬化测试（鉴权 / 错误信封 / 限流 / 探针 / 指标 / OpenAPI）。

约定：
* 每个用例都在 ``setUp`` 里快照 ``os.environ``、在 ``tearDown`` 里完整还原；
* 需要不同配置的用例一律 **新建应用实例**（``server.create_app()``），
  模块级的 ``server.app`` 不会被污染；
* 鉴权依赖与限流中间件都是 **请求期** 读配置，因此改完环境变量立即生效。
"""

import os
import unittest

from fastapi.testclient import TestClient

from webui.backend import server
from webui.backend.api_config import load_config
from webui.backend.errors import (
    ApiError,
    AuthError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from webui.backend.middleware import RateLimitMiddleware

API_KEY = "p3-test-key"

ENVELOPE_KEYS = {"success", "code", "message", "details", "request_id"}


class EnvMixin(unittest.TestCase):
    """统一的环境变量快照 / 还原。"""

    def setUp(self) -> None:
        super().setUp()
        self._env_backup = dict(os.environ)
        for name in (
            "WEBUI_API_KEY",
            "WEBUI_ALLOWED_ORIGINS",
            "WEBUI_RATE_LIMIT",
            "WEBUI_RATE_LIMIT_ENABLED",
            "WEBUI_API_PREFIX",
            "WEBUI_HOST",
            "WEBUI_PORT",
            "WEBUI_RATE_LIMIT_MAX_CLIENTS",
            "WEBUI_TRUST_PROXY_HEADERS",
        ):
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)
        super().tearDown()


class ApiKeyAuthTests(EnvMixin):
    """``WEBUI_API_KEY`` 开启后的鉴权行为。"""

    def setUp(self) -> None:
        super().setUp()
        os.environ["WEBUI_API_KEY"] = API_KEY
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_protected_route_requires_api_key(self):
        resp = self.client.get("/api/jobs")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "unauthorized")
        self.assertIn("message", body)

    def test_protected_route_accepts_x_api_key_header(self):
        resp = self.client.get("/api/jobs", headers={"X-API-Key": API_KEY})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_protected_route_accepts_bearer_token(self):
        resp = self.client.get(
            "/api/jobs", headers={"Authorization": f"Bearer {API_KEY}"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_wrong_key_returns_stable_error_envelope(self):
        resp = self.client.get("/api/jobs", headers={"X-API-Key": "wrong-key"})
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["success"], False)
        self.assertEqual(body["code"], "unauthorized")
        self.assertTrue(body["message"])
        self.assertTrue(body["request_id"])
        # 不应出现 FastAPI 默认的 {"detail": ...}
        self.assertNotIn("detail", body)

    def test_probe_routes_are_exempt_from_auth(self):
        for path in ("/healthz", "/readyz", "/metrics"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)

    def test_static_frontend_is_exempt_from_auth(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_validation_error_uses_same_envelope(self):
        resp = self.client.get(
            "/api/jobs",
            params={"limit": "not-an-int"},
            headers={"X-API-Key": API_KEY},
        )
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "validation_error")
        self.assertTrue(body["request_id"])
        self.assertNotIn("detail", body)

    def test_unknown_route_uses_same_envelope(self):
        resp = self.client.get("/api/definitely-not-a-route")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "not_found")
        self.assertTrue(body["request_id"])
        self.assertNotIn("detail", body)


class NoApiKeyBackwardsCompatTests(EnvMixin):
    """未配置 API 密钥时保持完全向后兼容。"""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("WEBUI_API_KEY", None)
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_protected_routes_still_work_without_key(self):
        for path in ("/api/status", "/api/jobs", "/api/watchlist"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)

    def test_missing_key_header_is_not_required(self):
        resp = self.client.get("/api/config/defaults")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_existing_success_payload_shape_is_preserved(self):
        resp = self.client.get("/api/status")
        body = resp.json()
        self.assertEqual(
            set(body),
            {"running", "company", "date", "start_time", "job_id"},
        )


class RateLimitTests(EnvMixin):
    """限流器行为。"""

    def setUp(self) -> None:
        super().setUp()
        os.environ["WEBUI_RATE_LIMIT"] = "3"
        os.environ["WEBUI_RATE_LIMIT_ENABLED"] = "1"
        # 模拟反向代理场景：用 X-Forwarded-For 区分客户端
        os.environ["WEBUI_TRUST_PROXY_HEADERS"] = "1"
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_limit_exceeded_returns_429_with_retry_after(self):
        headers = {"X-Forwarded-For": "10.0.0.1"}
        for _ in range(3):
            resp = self.client.get("/api/jobs", headers=headers)
            self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/jobs", headers=headers)
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(int(resp.headers["Retry-After"]) >= 1)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "rate_limited")
        self.assertTrue(body["request_id"])

    def test_limit_is_per_client(self):
        first = {"X-Forwarded-For": "10.0.0.1"}
        second = {"X-Forwarded-For": "10.0.0.2"}
        for _ in range(3):
            self.client.get("/api/jobs", headers=first)
        self.assertEqual(
            self.client.get("/api/jobs", headers=first).status_code, 429
        )
        # 另一个客户端不受影响
        self.assertEqual(
            self.client.get("/api/jobs", headers=second).status_code, 200
        )

    def test_probe_routes_are_not_rate_limited(self):
        headers = {"X-Forwarded-For": "10.0.0.9"}
        for _ in range(10):
            self.assertEqual(
                self.client.get("/healthz", headers=headers).status_code, 200
            )

    def test_rate_limit_can_be_disabled(self):
        os.environ["WEBUI_RATE_LIMIT_ENABLED"] = "0"
        headers = {"X-Forwarded-For": "10.0.0.3"}
        for _ in range(10):
            self.assertEqual(
                self.client.get("/api/jobs", headers=headers).status_code, 200
            )

    def test_tracked_clients_stay_bounded(self):
        """跟踪的客户端数量必须有界（防止伪造 IP 撑爆内存）。"""

        async def _noop_app(scope, receive, send):  # pragma: no cover
            return None

        limiter = RateLimitMiddleware(_noop_app)
        limit = 1000
        max_clients = 16
        for index in range(200):
            allowed, _retry = limiter._check(
                f"10.1.{index // 256}.{index % 256}", limit, max_clients
            )
            self.assertTrue(allowed)
        self.assertLessEqual(len(limiter._buckets), max_clients)


class RequestIdTests(EnvMixin):
    """请求 ID 贯穿响应头与错误信封。"""

    def setUp(self) -> None:
        super().setUp()
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_request_id_header_present_on_success(self):
        resp = self.client.get("/healthz")
        request_id = resp.headers.get("X-Request-ID")
        self.assertTrue(request_id)
        self.assertEqual(len(request_id), 32)

    def test_request_id_matches_error_envelope(self):
        resp = self.client.get("/api/no-such-route")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("X-Request-ID"), resp.json()["request_id"])

    def test_request_id_is_unique_per_request(self):
        first = self.client.get("/healthz").headers.get("X-Request-ID")
        second = self.client.get("/healthz").headers.get("X-Request-ID")
        self.assertNotEqual(first, second)


class ProbeAndMetricsTests(EnvMixin):
    """就绪探针与指标端点。"""

    def setUp(self) -> None:
        super().setUp()
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_healthz_is_liveness_only(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_readyz_reports_database_check(self):
        resp = self.client.get("/readyz")
        self.assertIn(resp.status_code, (200, 503))
        body = resp.json()
        self.assertIn("checks", body)
        names = {check["name"] for check in body["checks"]}
        self.assertIn("database", names)
        database = next(c for c in body["checks"] if c["name"] == "database")
        self.assertIn(database["status"], ("ok", "failed"))
        self.assertTrue(database["detail"])

    def test_readyz_reports_503_when_a_required_check_fails(self):
        original = server._check_database
        try:
            server._check_database = lambda: {
                "name": "database",
                "status": "failed",
                "required": True,
                "detail": "boom",
            }
            resp = self.client.get("/readyz")
        finally:
            server._check_database = original
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertFalse(body["ready"])
        self.assertEqual(body["status"], "not_ready")

    def test_metrics_returns_prometheus_text(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp.headers["content-type"])
        text = resp.text
        self.assertIn("requests_total", text)
        self.assertIn("errors_total", text)
        self.assertIn("request_duration_seconds", text)
        self.assertIn("route_requests_total", text)
        self.assertIn("# TYPE requests_total counter", text)

    def test_metrics_records_requests(self):
        before = server.metrics.snapshot()["requests_total"]
        self.client.get("/healthz")
        after = server.metrics.snapshot()["requests_total"]
        self.assertGreaterEqual(after, before + 1)

    def test_scheduler_check_is_skipped_when_disabled(self):
        check = server._check_scheduler()
        self.assertEqual(check["name"], "scheduler")
        self.assertEqual(check["status"], "skipped")
        self.assertFalse(check["required"])

    def test_scheduler_check_reports_live_thread(self):
        os.environ["SCHEDULE_ENABLED"] = "1"

        class _FakeThread:
            @staticmethod
            def is_alive() -> bool:
                return True

        from tradingagents import scheduler as scheduler_module

        original = getattr(scheduler_module, "_thread", None)
        try:
            scheduler_module._thread = _FakeThread()
            check = server._check_scheduler()
        finally:
            scheduler_module._thread = original
        self.assertEqual(check["status"], "ok")
        self.assertTrue(check["required"])

    def test_startup_warning_when_auth_disabled_on_public_host(self):
        os.environ["WEBUI_HOST"] = "0.0.0.0"
        with self.assertLogs("webui.backend.server", level="WARNING") as logs:
            server._warn_if_unauthenticated(load_config())
        self.assertTrue(any("安全警告" in line for line in logs.output))

    def test_no_startup_warning_on_loopback(self):
        os.environ["WEBUI_HOST"] = "127.0.0.1"
        with self.assertLogs("webui.backend.server", level="INFO") as logs:
            server._warn_if_unauthenticated(load_config())
        self.assertFalse(any("安全警告" in line for line in logs.output))


class ErrorEnvelopeTests(EnvMixin):
    """错误信封与异常类型。"""

    def test_error_types_have_stable_codes_and_status(self):
        cases = [
            (BadRequestError("x"), "bad_request", 400),
            (NotFoundError("x"), "not_found", 404),
            (ConflictError("x"), "conflict", 409),
            (RateLimitError("x"), "rate_limited", 429),
            (AuthError("x"), "unauthorized", 401),
            (ServiceUnavailableError("x"), "service_unavailable", 503),
        ]
        for exc, code, status in cases:
            self.assertIsInstance(exc, ApiError)
            self.assertEqual(exc.code, code)
            self.assertEqual(exc.status_code, status)

    def test_unhandled_exception_returns_500_without_stack_trace(self):
        app = server.create_app()

        @app.get("/api/p3-boom")
        async def _boom():
            raise RuntimeError("p3-secret-internal-detail")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/p3-boom")
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "internal_error")
        self.assertTrue(body["request_id"])
        self.assertEqual(resp.headers.get("X-Request-ID"), body["request_id"])
        self.assertNotIn("p3-secret-internal-detail", resp.text)
        self.assertNotIn("Traceback", resp.text)


class CorsTests(EnvMixin):
    """CORS 中间件。"""

    def test_cors_middleware_installed_when_origins_configured(self):
        os.environ["WEBUI_ALLOWED_ORIGINS"] = "http://example.com"
        app = server.create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/healthz", headers={"Origin": "http://example.com"})
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"), "http://example.com"
        )

    def test_preflight_is_answered(self):
        os.environ["WEBUI_ALLOWED_ORIGINS"] = "http://example.com"
        app = server.create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/jobs",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"), "http://example.com"
        )


class ApiConfigTests(EnvMixin):
    """配置读取。"""

    def test_defaults(self):
        config = load_config()
        self.assertIsNone(config.api_key)
        self.assertFalse(config.auth_enabled)
        self.assertFalse(config.cors_enabled)
        self.assertEqual(config.api_prefix, "/api")
        self.assertEqual(config.rate_limit, 120)
        self.assertTrue(config.rate_limit_enabled)

    def test_environment_overrides(self):
        os.environ["WEBUI_API_KEY"] = "  k  "
        os.environ["WEBUI_ALLOWED_ORIGINS"] = "http://a.com, http://b.com"
        os.environ["WEBUI_RATE_LIMIT"] = "7"
        os.environ["WEBUI_RATE_LIMIT_ENABLED"] = "0"
        os.environ["WEBUI_API_PREFIX"] = "api/v1/"
        config = load_config()
        self.assertEqual(config.api_key, "k")
        self.assertEqual(config.allowed_origins, ("http://a.com", "http://b.com"))
        self.assertEqual(config.rate_limit, 7)
        self.assertFalse(config.rate_limit_enabled)
        self.assertEqual(config.api_prefix, "/api/v1")

    def test_loopback_detection(self):
        os.environ["WEBUI_HOST"] = "127.0.0.1"
        self.assertTrue(load_config().is_loopback_host)
        os.environ["WEBUI_HOST"] = "0.0.0.0"
        self.assertFalse(load_config().is_loopback_host)


class OpenApiTests(EnvMixin):
    """OpenAPI 文档质量。"""

    def setUp(self) -> None:
        super().setUp()
        self.app = server.create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_every_route_has_tags_and_summary(self):
        schema = self.client.get("/openapi.json").json()
        self.assertTrue(schema["paths"])
        missing = []
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                if not operation.get("tags") or not operation.get("summary"):
                    missing.append(f"{method.upper()} {path}")
        self.assertEqual(missing, [])

    def test_security_schemes_are_declared(self):
        schema = self.client.get("/openapi.json").json()
        schemes = schema["components"]["securitySchemes"]
        self.assertIn("ApiKeyAuth", schemes)
        self.assertEqual(schemes["ApiKeyAuth"]["name"], "X-API-Key")

    def test_protected_routes_declare_security_requirement(self):
        schema = self.client.get("/openapi.json").json()
        security = schema["paths"]["/api/jobs"]["get"].get("security")
        self.assertTrue(security)

    def test_probe_routes_are_not_marked_as_secured(self):
        schema = self.client.get("/openapi.json").json()
        self.assertIsNone(schema["paths"]["/healthz"]["get"].get("security"))

    def test_response_models_are_declared(self):
        schema = self.client.get("/openapi.json").json()
        content = schema["paths"]["/api/jobs"]["get"]["responses"]["200"][
            "content"
        ]
        self.assertIn("application/json", content)
        self.assertTrue(content["application/json"]["schema"])


if __name__ == "__main__":
    unittest.main()
