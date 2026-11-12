"""自选股策略预警测试。"""

import unittest
from unittest.mock import patch

from tradingagents.services.strategy_alerts import (
    build_strategy_alerts,
    format_strategy_alerts_markdown,
    send_strategy_alerts,
)


def fake_rows():
    return [
        {
            "ticker": "000001",
            "as_of": "2026-11-10",
            "close": 10.0,
            "change_pct": 1.2,
            "consensus": "中性",
            "strength": 10,
            "triggered": 0,
            "bullish": 0,
            "bearish": 0,
            "top": [],
            "top_signals": [],
        },
        {
            "ticker": "600519",
            "as_of": "2026-11-10",
            "close": 1500.0,
            "change_pct": -0.5,
            "consensus": "偏空",
            "strength": 40,
            "triggered": 2,
            "bullish": 0,
            "bearish": 2,
            "top": ["跌破均线", "放量下跌"],
            "top_signals": [
                {
                    "key": "ma_dead_cross",
                    "name": "均线死叉",
                    "direction": "bearish",
                    "score": 60,
                    "summary": "短期均线下穿长期均线",
                    "reasons": ["MA5 下穿 MA20"],
                }
            ],
        },
        {
            "ticker": "300750",
            "as_of": "2026-11-10",
            "close": 200.0,
            "change_pct": 2.0,
            "consensus": "偏多",
            "strength": 60,
            "triggered": 3,
            "bullish": 3,
            "bearish": 0,
            "top": ["放量突破", "均线金叉"],
            "top_signals": [
                {
                    "key": "volume_breakout",
                    "name": "放量突破",
                    "direction": "bullish",
                    "score": 80,
                    "summary": "放量突破 20 日高点",
                    "reasons": ["量比 2.1"],
                }
            ],
        },
        {"ticker": "000002", "error": "boom"},
    ]


class StrategyAlertTests(unittest.TestCase):
    def test_only_triggered_and_name_mapping(self):
        with (
            patch(
                "tradingagents.storage.watchlist_store.list_watchlist",
                return_value=[
                    {"ticker": "600519", "name": "贵州茅台"},
                    {"ticker": "300750", "name": "宁德时代"},
                    {"ticker": "000001", "name": "平安银行"},
                    {"ticker": "000002", "name": "万科A"},
                ],
            ),
            patch(
                "tradingagents.strategies.scanner.scan_watchlist",
                return_value=fake_rows(),
            ),
        ):
            alerts = build_strategy_alerts(only_triggered=True)

        self.assertTrue(alerts["success"])
        self.assertEqual([item["ticker"] for item in alerts["items"]], ["300750", "600519"])
        self.assertEqual(alerts["items"][1]["name"], "贵州茅台")
        self.assertEqual(alerts["summary"]["total"], 4)
        self.assertEqual(alerts["summary"]["triggered"], 2)
        self.assertEqual(alerts["summary"]["bullish"], 1)
        self.assertEqual(alerts["summary"]["bearish"], 1)
        self.assertEqual(alerts["summary"]["signals"], 5)
        self.assertEqual(alerts["errors"][0]["ticker"], "000002")

    def test_include_non_triggered(self):
        with patch(
            "tradingagents.strategies.scanner.scan_watchlist",
            return_value=fake_rows(),
        ):
            alerts = build_strategy_alerts(
                tickers=["300750", "600519", "000001"],
                only_triggered=False,
            )

        self.assertEqual(len(alerts["items"]), 3)
        self.assertEqual(alerts["items"][-1]["ticker"], "000001")
        self.assertEqual(alerts["summary"]["triggered"], 2)
        self.assertEqual(alerts["summary"]["signals"], 5)

    def test_empty_watchlist(self):
        with patch(
            "tradingagents.storage.watchlist_store.list_watchlist",
            return_value=[],
        ):
            alerts = build_strategy_alerts()

        self.assertTrue(alerts["success"])
        self.assertEqual(alerts["items"], [])
        self.assertIn("自选股为空", alerts["message"])

    def test_format_markdown(self):
        with patch(
            "tradingagents.strategies.scanner.scan_watchlist",
            return_value=fake_rows(),
        ):
            alerts = build_strategy_alerts(
                tickers=["600519", "300750"], only_triggered=True
            )
        text = format_strategy_alerts_markdown(alerts)
        self.assertIn("自选股策略预警", text)
        self.assertIn("放量突破", text)
        self.assertIn("不构成投资建议", text)

    def test_send_calls_notification(self):
        fake_alerts = {
            "success": True,
            "items": [{"ticker": "600519", "top_signals": []}],
            "errors": [],
            "summary": {"total": 1, "triggered": 1},
            "message": "发现 1 只自选股触发策略信号。",
            "disclaimer": "仅供参考。",
        }
        with (
            patch(
                "tradingagents.services.strategy_alerts.build_strategy_alerts",
                return_value=fake_alerts,
            ),
            patch(
                "tradingagents.notify.send_notification",
                return_value={"wecom": {"ok": True}},
            ) as mocked,
        ):
            result = send_strategy_alerts(tickers=["600519"])

        mocked.assert_called_once()
        self.assertEqual(result["notify"], {"wecom": {"ok": True}})

    def test_send_skips_empty_by_default(self):
        fake_alerts = {
            "success": True,
            "items": [],
            "errors": [],
            "summary": {"total": 1, "triggered": 0},
            "message": "本次扫描未发现触发策略信号的自选股。",
            "disclaimer": "仅供参考。",
        }
        with (
            patch(
                "tradingagents.services.strategy_alerts.build_strategy_alerts",
                return_value=fake_alerts,
            ),
            patch(
                "tradingagents.notify.send_notification",
            ) as mocked,
        ):
            result = send_strategy_alerts(tickers=["600519"])

        mocked.assert_not_called()
        self.assertEqual(
            result["notify"]["skipped"],
            "本次扫描未发现触发策略信号的自选股。",
        )

    def test_send_can_notify_empty(self):
        fake_alerts = {
            "success": True,
            "items": [],
            "errors": [],
            "summary": {"total": 1, "triggered": 0},
            "message": "本次扫描未发现触发策略信号的自选股。",
            "disclaimer": "仅供参考。",
        }
        with (
            patch(
                "tradingagents.services.strategy_alerts.build_strategy_alerts",
                return_value=fake_alerts,
            ),
            patch(
                "tradingagents.notify.send_notification",
                return_value={"feishu": {"ok": True}},
            ) as mocked,
        ):
            result = send_strategy_alerts(tickers=["600519"], notify_empty=True)

        mocked.assert_called_once()
        self.assertEqual(result["notify"], {"feishu": {"ok": True}})


if __name__ == "__main__":
    unittest.main()
