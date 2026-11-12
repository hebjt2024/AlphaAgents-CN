"""P0/P1 新增 API 回归测试。

覆盖：
- 估值历史分位
- 业绩预告 / 快报
- 财务排雷
- 事件日历
- 持仓建议 / 止损止盈提醒
- 市场情绪历史与快照

全部通过 patch 服务层实现来测试 API 契约，不触网、不调用 LLM。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from webui.backend import server

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="alphaagents-p01-"), "api.db")
os.environ.setdefault("TA_DB_PATH", _TMP_DB)


VALUATION = {
    "success": True,
    "ticker": "600519",
    "as_of": "2026-11-12",
    "window_years": 5,
    "samples": 1200,
    "pe_ttm": {
        "value": 21.34,
        "percentile": 12.5,
        "min": 15.0,
        "max": 60.0,
        "median": 35.0,
        "p20": 22.0,
        "p80": 48.0,
    },
    "pb": {
        "value": 6.5,
        "percentile": 9.3,
        "min": 4.0,
        "max": 15.0,
        "median": 9.0,
        "p20": 6.0,
        "p80": 12.0,
    },
    "level": "低估",
    "summary": "PE-TTM 21.34，处于近 5 年 12.5% 分位（低估）。",
    "warnings": [],
}

RED_FLAGS = {
    "success": True,
    "ticker": "600519",
    "as_of": "2026-11-12",
    "checks": [
        {
            "code": "goodwill",
            "name": "商誉",
            "level": "ok",
            "value": 0.0,
            "message": "商誉占净资产 0.0%",
            "detail": {},
        },
        {
            "code": "pledge",
            "name": "股权质押",
            "level": "ok",
            "value": 0.0,
            "message": "无质押",
            "detail": {},
        },
    ],
    "risk_score": 0,
    "level": "低风险",
    "summary": "四项检查均正常。",
    "warnings": [],
}

CALENDAR = {
    "success": True,
    "ticker": "600519",
    "as_of": "2026-11-12",
    "horizon_days": 30,
    "events": [
        {
            "date": "2026-11-25",
            "type": "财报披露",
            "title": "三季报披露",
            "detail": "预约披露日",
            "level": "info",
        }
    ],
    "counts": {"财报披露": 1},
    "summary": "未来 30 天有 1 个事件。",
    "warnings": [],
}

ADVICE = {
    "success": True,
    "ticker": "600519",
    "action": "减仓",
    "current_qty": 300.0,
    "current_weight": 0.186,
    "target_weight": 0.12,
    "target_qty": 200.0,
    "delta_qty": -100.0,
    "delta_lots": -1,
    "latest_price": 1402.3,
    "avg_cost": 1300.0,
    "unrealised_pnl": 30690.0,
    "unrealised_pnl_pct": 0.0787,
    "stop_loss": 1280.0,
    "take_profit": 1550.0,
    "risk_reward": 2.1,
    "decision": "SELL",
    "confidence": 62,
    "alerts": [],
    "reasons": ["当前持仓 300 股"],
    "disclaimer": "仅供参考",
}

ALERTS = [
    {
        "ticker": "600519",
        "name": "贵州茅台",
        "code": "stop_loss_hit",
        "level": "danger",
        "message": "跌破止损位 1280",
        "detail": {},
    }
]


class ValuationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_valuation_percentile(self):
        with patch(
            "tradingagents.dataflows.ashare_events.get_valuation_percentile",
            return_value=VALUATION,
        ):
            resp = self.client.get(
                "/api/valuation/percentile",
                params={"ticker": "600519", "years": 5},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["valuation"]["level"], "低估")
        self.assertEqual(data["valuation"]["pe_ttm"]["percentile"], 12.5)

    def test_valuation_failure_is_reported_not_raised(self):
        with patch(
            "tradingagents.dataflows.ashare_events.get_valuation_percentile",
            side_effect=RuntimeError("akshare down"),
        ):
            resp = self.client.get(
                "/api/valuation/percentile", params={"ticker": "600519"}
            )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("akshare down", data["message"])

    def test_earnings_forecast(self):
        forecast = {
            "success": True,
            "ticker": "600519",
            "items": [{"report_period": "2025-12-31", "kind": "预告"}],
            "latest": {"report_period": "2025-12-31"},
            "summary": "净利润预增 15%",
            "warnings": [],
        }
        with patch(
            "tradingagents.dataflows.ashare_events.get_earnings_forecast",
            return_value=forecast,
        ):
            resp = self.client.get(
                "/api/earnings/forecast", params={"ticker": "600519"}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["forecast"]["items"]), 1)

    def test_red_flags(self):
        with patch(
            "tradingagents.dataflows.ashare_events.get_financial_red_flags",
            return_value=RED_FLAGS,
        ):
            resp = self.client.get(
                "/api/fundamentals/red-flags", params={"ticker": "600519"}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["red_flags"]["risk_score"], 0)

    def test_event_calendar(self):
        with patch(
            "tradingagents.dataflows.ashare_events.build_event_calendar",
            return_value=CALENDAR,
        ):
            resp = self.client.get(
                "/api/events/calendar", params={"ticker": "600519", "days": 30}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["calendar"]["events"]), 1)


class PositionAdviceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_position_advice_uses_rule_preview_by_default(self):
        scan = {
            "summary": {
                "net_score": 30,
                "strength": 60,
                "triggered": 2,
                "family_count": 2,
                "consensus": "偏多",
            },
            "price": {
                "close": 100.0,
                "atr": 4.0,
                "recent_high20": 110.0,
                "recent_low20": 96.0,
            },
            "signals": [],
        }
        with (
            patch(
                "tradingagents.strategies.scanner.scan_all_strategies",
                return_value=scan,
            ),
            patch(
                "tradingagents.services.position_advisor.advise_position",
                return_value=ADVICE,
            ),
        ):
            resp = self.client.get(
                "/api/position/advice", params={"ticker": "600519"}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["advice"]["action"], "减仓")
        self.assertEqual(data["decision_source"], "rule_preview")

    def test_position_advice_honours_explicit_decision(self):
        scan = {"summary": {}, "price": {}, "signals": []}
        with (
            patch(
                "tradingagents.strategies.scanner.scan_all_strategies",
                return_value=scan,
            ),
            patch(
                "tradingagents.services.position_advisor.advise_position",
                return_value=ADVICE,
            ) as advisor,
        ):
            self.client.get(
                "/api/position/advice",
                params={"ticker": "600519", "decision": "sell"},
            )
        plan = advisor.call_args.args[1]
        self.assertEqual(plan["decision"], "SELL")

    def test_portfolio_alerts(self):
        with (
            patch(
                "tradingagents.services.portfolio.load_positions",
                return_value=[],
            ),
            patch(
                "tradingagents.services.position_advisor.check_position_alerts",
                return_value=ALERTS,
            ),
        ):
            resp = self.client.get("/api/portfolio/alerts")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["alerts"]), 1)
        self.assertEqual(data["summary"]["danger"], 1)


class SentimentApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_sentiment_history(self):
        payload = {
            "success": True,
            "series": [{"date": "2026-11-11", "up_ratio": 0.4, "limit_up": 30}],
            "stats": {"samples": 1, "percentile": 50.0, "latest": {"up_ratio": 0.4}},
            "trend": "持平",
            "level": "中性",
            "summary": "样本不足",
            "warnings": ["情绪历史样本不足（1 天）"],
        }
        with patch(
            "tradingagents.services.market_sentiment.get_sentiment_history",
            return_value=payload,
        ):
            resp = self.client.get(
                "/api/market/sentiment/history", params={"days": 60}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["level"], "中性")
        self.assertTrue(data["warnings"])

    def test_sentiment_snapshot(self):
        with patch(
            "tradingagents.services.market_sentiment.snapshot_market_sentiment",
            return_value={"success": True, "saved": True, "trade_date": "2026-11-12"},
        ):
            resp = self.client.post("/api/market/sentiment/snapshot")
        data = resp.json()
        self.assertTrue(data["saved"])
        self.assertEqual(data["trade_date"], "2026-11-12")


class OpenApiTagTests(unittest.TestCase):
    def test_new_routes_have_tags_and_summary(self):
        client = TestClient(server.app)
        spec = client.get("/openapi.json").json()
        prefixes = (
            "/api/valuation",
            "/api/earnings",
            "/api/fundamentals/red-flags",
            "/api/events",
            "/api/position",
            "/api/portfolio/alerts",
            "/api/market/sentiment",
        )
        checked = 0
        for path, operations in spec["paths"].items():
            if not path.startswith(prefixes):
                continue
            for method, operation in operations.items():
                checked += 1
                self.assertTrue(
                    operation.get("tags"), f"{method} {path} 缺少 tags"
                )
                self.assertTrue(
                    operation.get("summary"), f"{method} {path} 缺少 summary"
                )
        self.assertGreaterEqual(checked, 8)


if __name__ == "__main__":
    unittest.main()
