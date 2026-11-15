"""P8：盘中实时行情与告警中心。

覆盖：快照归一化、交易时段判断、规则 CRUD、冷却去重、持仓止损止盈、
API 契约与定时器入口。
"""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


class TempDbMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.storage import init_db

        init_db()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old
        super().tearDown()


class RealtimeTests(unittest.TestCase):
    def test_quote_normalization(self):
        from tradingagents.dataflows.realtime import get_realtime_quotes

        snapshot = pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "最新价": 1400.0,
                    "涨跌幅": 1.25,
                    "涨跌额": 17.0,
                    "今开": 1385.0,
                    "昨收": 1383.0,
                    "最高": 1410.0,
                    "最低": 1380.0,
                    "成交量": 30000,
                    "成交额": 4.2e9,
                    "量比": 1.5,
                    "换手率": 0.3,
                }
            ]
        )
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_spot_snapshot",
            return_value=snapshot,
        ):
            quotes = get_realtime_quotes(["600519", "000001"])
        self.assertIn("600519", quotes)
        quote = quotes["600519"]
        self.assertEqual(quote["name"], "贵州茅台")
        self.assertAlmostEqual(quote["price"], 1400.0)
        self.assertAlmostEqual(quote["change_pct"], 1.25)
        self.assertAlmostEqual(quote["volume_ratio"], 1.5)

    def test_market_session(self):
        from tradingagents.dataflows.realtime import market_session

        with patch(
            "tradingagents.dataflows.realtime.is_cn_trading_day",
            return_value=True,
        ):
            morning = market_session(datetime(2026, 11, 13, 10, 0))
            lunch = market_session(datetime(2026, 11, 13, 12, 0))
            closed = market_session(datetime(2026, 11, 13, 16, 0))
        self.assertTrue(morning["is_open"])
        self.assertEqual(morning["status"], "open")
        self.assertEqual(lunch["status"], "lunch_break")
        self.assertEqual(closed["status"], "closed")

        with patch(
            "tradingagents.dataflows.realtime.is_cn_trading_day",
            return_value=False,
        ):
            holiday = market_session(datetime(2026, 11, 14, 10, 0))
        self.assertEqual(holiday["status"], "holiday")
        self.assertFalse(holiday["is_open"])


class AlertStoreTests(TempDbMixin):
    def test_rule_crud_and_events(self):
        from tradingagents.storage import (
            ack_alert_event,
            add_alert_rule,
            delete_alert_rule,
            list_alert_events,
            list_alert_rules,
            record_alert_event,
            update_alert_rule,
        )

        rule = add_alert_rule("600519", "price_above", 1500.0, cooldown_minutes=10)
        self.assertTrue(rule["enabled"])
        self.assertEqual(len(list_alert_rules()), 1)

        updated = update_alert_rule(rule["id"], enabled=False, threshold=1600.0)
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["threshold"], 1600.0)

        event = record_alert_event(
            rule_id=rule["id"],
            ticker="600519",
            rule_type="price_above",
            message="价格上穿",
            price=1601.0,
        )
        self.assertFalse(event["acknowledged"])
        self.assertEqual(len(list_alert_events(unacked_only=True)), 1)
        self.assertTrue(ack_alert_event(event["id"]))
        self.assertEqual(len(list_alert_events(unacked_only=True)), 0)

        self.assertTrue(delete_alert_rule(rule["id"]))
        self.assertEqual(list_alert_rules(), [])


class AlertEngineTests(TempDbMixin):
    def test_rule_triggers_once_then_cools_down(self):
        from tradingagents.services.alert_engine import check_alerts
        from tradingagents.storage import add_alert_rule

        add_alert_rule("600519", "price_above", 1400.0, cooldown_minutes=30)
        quote = {
            "ticker": "600519",
            "name": "贵州茅台",
            "price": 1450.0,
            "change_pct": 3.0,
        }
        with patch(
            "tradingagents.services.alert_engine.get_realtime_quotes",
            return_value={"600519": quote},
        ), patch(
            "tradingagents.services.alert_engine.list_positions",
            return_value=[],
        ):
            first = check_alerts(include_portfolio=False)
            second = check_alerts(include_portfolio=False)

        self.assertEqual(first["triggered"], 1)
        self.assertEqual(first["events"][0]["rule_type"], "price_above")
        self.assertEqual(second["triggered"], 0)
        self.assertIn("冷却中", [item["reason"] for item in second["skipped"]])

    def test_portfolio_stop_loss_and_take_profit(self):
        from tradingagents.services.alert_engine import check_alerts

        positions = [
            {
                "ticker": "600519",
                "name": "贵州茅台",
                "stop_loss": 100.0,
                "take_profit": 120.0,
                "last_price": 110.0,
            }
        ]
        quote = {"ticker": "600519", "price": 95.0, "change_pct": -4.0}
        with patch(
            "tradingagents.services.alert_engine.get_realtime_quotes",
            return_value={"600519": quote},
        ), patch(
            "tradingagents.services.alert_engine.list_positions",
            return_value=positions,
        ):
            result = check_alerts()
        types = {item["rule_type"] for item in result["events"]}
        self.assertIn("stop_loss", types)
        self.assertNotIn("take_profit", types)


class AlertApiTests(TempDbMixin):
    def setUp(self):
        super().setUp()
        self.client = TestClient(server.app)

    def test_rule_and_event_api(self):
        created = self.client.post(
            "/api/alerts/rules",
            json={
                "ticker": "600519",
                "rule_type": "price_above",
                "threshold": 1500,
                "cooldown_minutes": 15,
            },
        ).json()
        self.assertTrue(created["success"])
        rule_id = created["item"]["id"]

        rules = self.client.get("/api/alerts/rules").json()
        self.assertEqual(len(rules["items"]), 1)

        updated = self.client.patch(
            f"/api/alerts/rules/{rule_id}", json={"enabled": False}
        ).json()
        self.assertFalse(updated["item"]["enabled"])

        with patch(
            "tradingagents.services.alert_engine.check_alerts",
            return_value={"triggered": 0, "events": [], "quotes": {}},
        ):
            checked = self.client.post(
                "/api/alerts/check", json={"include_portfolio": False}
            ).json()
        self.assertTrue(checked["success"])
        self.assertEqual(checked["triggered"], 0)

    def test_quote_and_session_api(self):
        with patch(
            "tradingagents.dataflows.realtime.get_realtime_quotes",
            return_value={"600519": {"ticker": "600519", "price": 1400.0}},
        ):
            data = self.client.get(
                "/api/realtime/quote", params={"ticker": "600519"}
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"]["600519"]["price"], 1400.0)

        with patch(
            "tradingagents.dataflows.realtime.market_session",
            return_value={"is_open": True, "label": "上午交易", "status": "open"},
        ):
            session = self.client.get("/api/realtime/session").json()
        self.assertTrue(session["is_open"])

    def test_invalid_rule_type_is_rejected(self):
        data = self.client.post(
            "/api/alerts/rules",
            json={"ticker": "600519", "rule_type": "unknown", "threshold": 1},
        ).json()
        self.assertFalse(data["success"])
        self.assertEqual(data["code"], "bad_request")


class SchedulerIntradayTests(unittest.TestCase):
    def test_intraday_disabled_by_default(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.services.alert_engine.check_alerts"
            ) as mocked,
        ):
            scheduler._run_intraday_alerts_once()
        mocked.assert_not_called()

    def test_intraday_runs_in_session(self):
        from tradingagents import scheduler

        with (
            patch.dict(
                os.environ, {"SCHEDULE_INTRADAY_ALERTS": "1"}, clear=True
            ),
            patch(
                "tradingagents.dataflows.realtime.market_session",
                return_value={"is_open": True},
            ),
            patch(
                "tradingagents.services.alert_engine.check_alerts",
                return_value={"triggered": 1, "quotes": {"600519": {}}},
            ) as mocked,
        ):
            scheduler._run_intraday_alerts_once()
        mocked.assert_called_once_with(notify=True)


if __name__ == "__main__":
    unittest.main()
