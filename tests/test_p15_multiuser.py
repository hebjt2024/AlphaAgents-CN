"""P15：多用户、角色权限、会话与审计。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from webui.backend import server


class TempUserDbMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = {key: os.environ.get(key) for key in (
            "TA_DB_PATH",
            "WEBUI_MULTI_USER",
            "WEBUI_ADMIN_USER",
            "WEBUI_ADMIN_PASSWORD",
            "WEBUI_API_KEY",
        )}
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        os.environ["WEBUI_MULTI_USER"] = "1"
        os.environ["WEBUI_ADMIN_USER"] = "admin"
        os.environ["WEBUI_ADMIN_PASSWORD"] = "adminpass123"
        os.environ.pop("WEBUI_API_KEY", None)
        from tradingagents.storage import init_db

        init_db()

    def tearDown(self):
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()


class UserStoreTests(TempUserDbMixin):
    def test_password_session_and_roles(self):
        from webui.backend.users import (
            ack_risk,
            authenticate,
            create_session,
            create_user,
            get_session_user,
            get_user,
            revoke_session,
            update_user,
        )

        user = create_user("alice", "password123", role="analyst")
        self.assertEqual(user["role"], "analyst")
        self.assertIsNotNone(authenticate("alice", "password123"))
        self.assertIsNone(authenticate("alice", "wrong"))
        token = create_session("alice")
        session = get_session_user(token)
        self.assertEqual(session["username"], "alice")
        self.assertEqual(session["role"], "analyst")
        self.assertIn("risk_ack_at", session)
        updated = ack_risk("alice")
        self.assertIsNotNone(updated["risk_ack_at"])
        update_user("alice", role="admin")
        self.assertEqual(get_user("alice")["role"], "admin")
        self.assertTrue(revoke_session(token))
        self.assertIsNone(get_session_user(token))

    def test_cannot_remove_last_admin(self):
        from webui.backend.users import create_user, delete_user, update_user

        create_user("admin2", "password123", role="admin")
        with self.assertRaises(ValueError):
            update_user("admin2", role="viewer")
        with self.assertRaises(ValueError):
            delete_user("admin2")

    def test_audit_log(self):
        from webui.backend.users import audit_log, list_audit_logs

        audit_log(
            username="alice",
            role="analyst",
            action="POST /api/analyze",
            method="POST",
            path="/api/analyze",
            status=200,
        )
        rows = list_audit_logs()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["action"], "POST /api/analyze")


class MultiUserApiTests(TempUserDbMixin):
    def setUp(self):
        super().setUp()
        self.app = server.create_app()
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

    def _login(self, username: str, password: str) -> str:
        data = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        ).json()
        self.assertTrue(data["success"], data)
        return data["token"]

    def test_login_me_and_risk_ack(self):
        config = self.client.get("/api/auth/config").json()
        self.assertTrue(config["multi_user"])
        token = self._login("admin", "adminpass123")
        headers = {"X-API-Key": token}

        me = self.client.get("/api/auth/me", headers=headers).json()
        self.assertTrue(me["success"])
        self.assertEqual(me["user"]["username"], "admin")
        self.assertIsNone(me["user"]["risk_ack_at"])

        acked = self.client.post("/api/auth/risk-ack", headers=headers).json()
        self.assertTrue(acked["success"])
        me = self.client.get("/api/auth/me", headers=headers).json()
        self.assertIsNotNone(me["user"]["risk_ack_at"])

    def test_viewer_is_read_only(self):
        token = self._login("admin", "adminpass123")
        created = self.client.post(
            "/api/users",
            headers={"X-API-Key": token},
            json={"username": "viewer1", "password": "password123", "role": "viewer"},
        ).json()
        self.assertTrue(created["success"])
        viewer = self._login("viewer1", "password123")
        headers = {"X-API-Key": viewer}
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 200)
        resp = self.client.post(
            "/api/watchlist", headers=headers, json={"ticker": "600519"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "forbidden")
        # 只读用户仍可确认风险揭示 / 退出登录
        ack = self.client.post("/api/auth/risk-ack", headers=headers)
        self.assertEqual(ack.status_code, 200)
        logout = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 200)

    def test_admin_user_management_and_audit(self):
        token = self._login("admin", "adminpass123")
        headers = {"X-API-Key": token}
        created = self.client.post(
            "/api/users",
            headers=headers,
            json={"username": "bob", "password": "password123", "role": "analyst"},
        ).json()
        self.assertTrue(created["success"])
        users = self.client.get("/api/users", headers=headers).json()
        self.assertEqual({item["username"] for item in users["items"]}, {"admin", "bob"})

        # 分析师可以写业务数据
        bob_token = self._login("bob", "password123")
        watch = self.client.post(
            "/api/watchlist",
            headers={"X-API-Key": bob_token},
            json={"ticker": "600519", "name": "贵州茅台"},
        )
        self.assertTrue(watch.json()["success"])

        audit = self.client.get("/api/audit", headers=headers).json()
        self.assertTrue(audit["success"])
        self.assertTrue(any(item.get("username") == "bob" for item in audit["items"]))
        self.assertTrue(any(item.get("action") == "login" for item in audit["items"]))

    def test_account_watchlist_and_report_ownership(self):
        admin = self._login("admin", "adminpass123")
        admin_headers = {"X-API-Key": admin}
        for username, role in (("bob", "analyst"), ("eve", "viewer")):
            self.client.post(
                "/api/users",
                headers=admin_headers,
                json={"username": username, "password": "password123", "role": role},
            )
        bob = self._login("bob", "password123")
        eve = self._login("eve", "password123")

        # bob 默认账户是隔离的
        portfolio = self.client.get(
            "/api/portfolio", headers={"X-API-Key": bob}
        ).json()["portfolio"]
        self.assertEqual(portfolio["account"], "默认账户@bob")
        created = self.client.post(
            "/api/portfolio/position",
            headers={"X-API-Key": bob},
            json={"ticker": "600519", "quantity": 100, "avg_cost": 1400},
        )
        self.assertTrue(created.json()["success"])

        # eve 不能读 bob 的账户
        resp = self.client.get(
            "/api/portfolio",
            headers={"X-API-Key": eve},
            params={"account": "默认账户@bob"},
        )
        self.assertEqual(resp.status_code, 403)

        # 自选股隔离：bob 添加后，eve 看不到，admin 能看到
        self.client.post(
            "/api/watchlist",
            headers={"X-API-Key": bob},
            json={"ticker": "600519"},
        )
        bob_watch = self.client.get(
            "/api/watchlist", headers={"X-API-Key": bob}
        ).json()["items"]
        eve_watch = self.client.get(
            "/api/watchlist", headers={"X-API-Key": eve}
        ).json()["items"]
        admin_watch = self.client.get(
            "/api/watchlist", headers=admin_headers
        ).json()["items"]
        self.assertEqual({item["ticker"] for item in bob_watch}, {"600519"})
        self.assertEqual(eve_watch, [])
        self.assertIn("600519", {item["ticker"] for item in admin_watch})

        # 报告隔离
        from tradingagents.storage import save_report

        report_id = save_report(
            "600519",
            "2026-11-14",
            "BUY",
            "test",
            {},
            owner="bob",
        )
        bob_reports = self.client.get(
            "/api/reports", headers={"X-API-Key": bob}
        ).json()["reports"]
        eve_reports = self.client.get(
            "/api/reports", headers={"X-API-Key": eve}
        ).json()["reports"]
        self.assertTrue(any(item["id"] == report_id for item in bob_reports))
        self.assertFalse(any(item["id"] == report_id for item in eve_reports))
        self.assertEqual(
            self.client.get(
                f"/api/reports/{report_id}", headers={"X-API-Key": eve}
            ).status_code,
            404,
        )

    def test_internal_exception_is_sanitized_and_status_is_4xx(self):
        admin = self._login("admin", "adminpass123")
        headers = {"X-API-Key": admin}
        first = self.client.post(
            "/api/users",
            headers=headers,
            json={"username": "dup", "password": "password123", "role": "analyst"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/users",
            headers=headers,
            json={"username": "dup", "password": "password123", "role": "analyst"},
        )
        self.assertEqual(second.status_code, 400)
        body = second.json()
        self.assertFalse(body["success"])
        self.assertNotIn("UNIQUE constraint", body["message"])
        self.assertNotIn("users.username", body["message"])

    def test_invalid_session_is_rejected(self):
        resp = self.client.get("/api/auth/me", headers={"X-API-Key": "bad-token"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["code"], "unauthorized")


class SingleUserCompatibilityTests(unittest.TestCase):
    def test_single_user_mode_remains_anonymous(self):
        with patch.dict(
            os.environ,
            {"WEBUI_MULTI_USER": "0", "WEBUI_API_KEY": ""},
            clear=False,
        ):
            app = server.create_app()
            with TestClient(app) as client:
                config = client.get("/api/auth/config").json()
                self.assertFalse(config["multi_user"])
                me = client.get("/api/auth/me").json()
                self.assertTrue(me["success"])
                self.assertEqual(me["user"]["username"], "local")
                self.assertEqual(me["user"]["role"], "admin")


if __name__ == "__main__":
    unittest.main()
