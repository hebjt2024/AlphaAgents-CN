"""策略回测服务测试（不访问网络）。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.services.strategy_backtest import run_strategy_backtest


def make_frame(rows: int = 300) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    closes = []
    price = 10.0
    for i in range(rows):
        price += 0.05 if i < rows // 2 else -0.02
        closes.append(price)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [c * 0.998 for c in closes],
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1000] * rows,
        }
    )


class StrategyBacktestTests(unittest.TestCase):
    def test_single_strategy_backtest(self):
        frame = make_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = run_strategy_backtest(
                "600519",
                "2025-01-01",
                ["bull_trend"],
                days=120,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["ticker"], "600519")
        self.assertEqual(len(result["strategies"]), 1)
        item = result["strategies"][0]
        self.assertEqual(item["key"], "bull_trend")
        self.assertIn("forward", item)
        self.assertIn("5", item["forward"])
        self.assertIn("equity", item)
        self.assertIn("benchmark_return", result)

    def test_invalid_key_falls_back_to_selection(self):
        frame = make_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = run_strategy_backtest(
                "600519", "2025-01-01", ["not_a_strategy"], days=90
            )
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
