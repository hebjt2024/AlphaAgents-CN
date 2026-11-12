"""策略信号与扫描服务测试（不访问网络）。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.strategies import (
    STRATEGY_META,
    format_strategy_report,
    scan_all_strategies,
)
from tradingagents.strategies.classic import (
    box_oscillation,
    bull_trend,
    one_yang_three_yin,
    shrink_pullback,
    volume_breakout,
)


def make_df(closes, volumes=None, opens=None, highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    volumes = volumes or [1000] * n
    opens = opens or [c * 0.995 for c in closes]
    highs = highs or [max(o, c) * 1.01 for o, c in zip(opens, closes, strict=False)]
    lows = lows or [min(o, c) * 0.99 for o, c in zip(opens, closes, strict=False)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        }
    )


class StrategySignalTests(unittest.TestCase):
    def test_bull_trend_triggered(self):
        closes = [10 + i * 0.05 for i in range(80)] + [
            14 + i * 0.01 for i in range(20)
        ]
        signal = bull_trend(make_df(closes))
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "bullish")

    def test_volume_breakout_triggered(self):
        closes = [10.0] * 40 + [11.5]
        volumes = [1000] * 40 + [3000]
        opens = [10.0] * 40 + [10.6]
        highs = [10.1] * 40 + [11.6]
        lows = [9.9] * 40 + [10.5]
        signal = volume_breakout(
            make_df(closes, volumes=volumes, opens=opens, highs=highs, lows=lows)
        )
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "bullish")

    def test_shrink_pullback_triggered(self):
        closes = [10 + i * 0.08 for i in range(60)]
        closes += [14.7, 14.5, 14.3, 14.2, 14.3]
        volumes = [1000] * 60 + [400] * 5
        signal = shrink_pullback(make_df(closes, volumes=volumes))
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "bullish")

    def test_one_yang_three_yin_triggered(self):
        closes = [10.2] * 10 + [10.3, 10.0, 9.8, 10.6]
        opens = [10.2] * 10 + [10.5, 10.3, 10.0, 9.9]
        volumes = [1000] * 10 + [1000, 1000, 1000, 2200]
        signal = one_yang_three_yin(
            make_df(closes, volumes=volumes, opens=opens)
        )
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "bullish")

    def test_box_oscillation_low_buy(self):
        closes = [10.0, 10.8] * 33 + [10.1]
        opens = [9.95, 10.85] * 33 + [9.98]
        highs = [10.9] * 67
        lows = [9.9] * 67
        signal = box_oscillation(
            make_df(closes, opens=opens, highs=highs, lows=lows)
        )
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.direction, "bullish")


class ScannerTests(unittest.TestCase):
    def test_scan_all_and_report(self):
        closes = [10 + i * 0.04 for i in range(120)]
        frame = make_df(closes)
        all_keys = [item["key"] for item in STRATEGY_META]
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = scan_all_strategies("600519", "2025-04-30", all_keys)
            report = format_strategy_report(result)

        self.assertTrue(result["success"])
        self.assertEqual(result["bars"], 120)
        self.assertGreaterEqual(len(result["signals"]), 15)
        self.assertIn("consensus", result["summary"])
        self.assertIn("策略扫描", report)
        self.assertIn("600519", report)

    def test_scan_defaults_to_core_subset(self):
        closes = [10 + i * 0.04 for i in range(120)]
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=make_df(closes),
        ):
            result = scan_all_strategies("600519", "2025-04-30")
        self.assertEqual(len(result["signals"]), 6)
        self.assertEqual(len(result["selected_strategies"]), 6)


if __name__ == "__main__":
    unittest.main()
