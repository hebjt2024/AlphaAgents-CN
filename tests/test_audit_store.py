"""SQLite 审计事件存储（tradingagents/storage/audit_store.py）单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tradingagents.storage.audit_store import (
    append_audit_event,
    init_audit_db,
    list_audit_events,
)


class AppendAuditEventTests(unittest.TestCase):
    def test_append_returns_id_and_list_roundtrip(self):
        event_id = append_audit_event(
            username="alice",
            role="admin",
            action="分析：TESTAUD",
            status=200,
            detail={"ticker": "TESTAUD"},
        )
        self.assertIsNotNone(event_id)
        events = list_audit_events(limit=50, username="alice")
        matches = [e for e in events if e.get("action") == "分析：TESTAUD"]
        self.assertTrue(matches, "写入的审计事件应可读回")
        item = matches[0]
        self.assertEqual(item["username"], "alice")
        self.assertEqual(item["role"], "admin")
        self.assertEqual(item["status"], 200)
        self.assertEqual(item["detail"], {"ticker": "TESTAUD"})

    def test_append_with_defaults(self):
        event_id = append_audit_event(action="写操作A")
        self.assertIsNotNone(event_id)
        events = list_audit_events(limit=10)
        self.assertTrue(any(e["action"] == "写操作A" for e in events))

    def test_append_swallows_errors_returns_none(self):
        with patch(
            "tradingagents.storage.audit_store.init_audit_db",
            side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(append_audit_event(action="x"))

    def test_list_filter_by_username_and_order(self):
        append_audit_event(username="bob", action="写操作B1")
        append_audit_event(username="bob", action="写操作B2")
        append_audit_event(username="carol", action="写操作C1")
        bob = list_audit_events(limit=10, username="bob")
        self.assertEqual({e["action"] for e in bob}, {"写操作B1", "写操作B2"})
        # 新写入在前（ORDER BY created_at DESC, id DESC）
        self.assertEqual(bob[0]["action"], "写操作B2")

    def test_list_unknown_username_empty(self):
        self.assertEqual(list_audit_events(limit=10, username="nobody-xyz"), [])

    def test_init_audit_db_idempotent(self):
        init_audit_db()
        init_audit_db()


class UsersDelegationTests(unittest.TestCase):
    def test_users_audit_log_and_list_audit_logs_delegate(self):
        from webui.backend.users import audit_log, list_audit_logs

        audit_log(
            username="delegate",
            role=None,
            action="delegated",
            status=200,
            detail={"k": 1},
        )
        items = list_audit_logs(limit=10, username="delegate")
        self.assertTrue(any(i.get("action") == "delegated" for i in items))


if __name__ == "__main__":
    unittest.main()
