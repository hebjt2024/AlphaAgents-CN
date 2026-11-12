"""快速预览测试。"""

import unittest
from unittest.mock import patch

from tradingagents.services.fast_preview import build_fast_preview


def fake_scan():
    return {
        "success": True,
        "ticker": "600519",
        "as_of": "2026-11-11",
        "bars": 250,
        "price": {
            "close": 1402.3,
            "change_pct": -2.55,
            "atr": 35.6,
            "recent_high20": 1500.0,
            "recent_low20": 1380.0,
        },
        "summary": {
            "consensus": "偏多",
            "net_score": 30,
            "strength": 60,
            "triggered": 2,
        },
        "signals": [
            {
                "name": "放量突破",
                "direction": "bullish",
                "score": 80,
                "summary": "放量突破 20 日高点",
                "triggered": True,
            }
        ],
    }


class FastPreviewTests(unittest.TestCase):
    def test_build_preview(self):
        with patch(
            "tradingagents.strategies.scanner.scan_all_strategies",
            return_value=fake_scan(),
        ):
            preview = build_fast_preview(
                "600519", "2026-11-11", ["volume_breakout"]
            )

        self.assertTrue(preview["success"])
        self.assertEqual(preview["ticker"], "600519")
        self.assertEqual(preview["preliminary_decision"], "BUY")
        self.assertEqual(preview["summary"]["triggered"], 2)
        self.assertEqual(preview["top_signals"][0]["name"], "放量突破")
        self.assertGreater(preview["decision_plan"]["score"], 0)
        self.assertIn("快速预览", preview["markdown"])
        self.assertIn("不构成投资建议", preview["markdown"])

    def test_hold_when_consensus_neutral(self):
        data = fake_scan()
        data["summary"]["net_score"] = 0
        data["summary"]["strength"] = 0
        with patch(
            "tradingagents.strategies.scanner.scan_all_strategies",
            return_value=data,
        ):
            preview = build_fast_preview("600519")

        self.assertEqual(preview["preliminary_decision"], "HOLD")
        self.assertEqual(preview["decision_plan"]["position_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
