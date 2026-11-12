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

    def test_statistical_keys_are_exposed(self):
        """样本内/样本外、有效样本量、显著性与排名依据必须同时给出。"""
        frame = make_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = run_strategy_backtest(
                "600519", "2025-01-01", ["bull_trend"], days=120
            )
        item = result["strategies"][0]
        for key in (
            "forward_in_sample",
            "forward_out_of_sample",
            "effective_sample_size",
            "sample_insufficient",
            "p_value",
            "p_value_adjusted",
            "ranking_basis",
            "rank",
        ):
            self.assertIn(key, item)
        for key in ("ranking_basis", "sample_insufficient", "benchmark"):
            self.assertIn(key, result)
        # 有效样本量必须小于原始（重叠）观测数
        horizon = item["forward"]["10"]
        if horizon["count"] > 0:
            self.assertLess(horizon["effective_count"], horizon["count"])

    def test_insufficient_sample_is_flagged(self):
        """样本量不足时必须显式标记，不能把 count=1 当性能指标渲染。"""
        frame = make_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = run_strategy_backtest(
                "600519", "2025-01-01", ["rsi_reversal"], days=120
            )
        item = result["strategies"][0]
        self.assertTrue(item["sample_insufficient"])
        self.assertEqual(item["ranking_basis"], "in_sample_only")
        self.assertIn("样本外有效样本量", item["note"])

    def test_equity_uses_shared_cost_model(self):
        """净值模拟必须走共享引擎：交易成本必须体现在净值里。"""
        frame = make_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = run_strategy_backtest(
                "600519", "2025-01-01", ["box_oscillation"], days=120
            )
        equity = result["strategies"][0]["equity"]
        self.assertIsNotNone(equity)
        for key in (
            "total_return",
            "max_drawdown",
            "sharpe",
            "win_rate",
            "trade_count",
            "equity_curve",
        ):
            self.assertIn(key, equity)
        self.assertGreater(equity["total_cost"], 0)


if __name__ == "__main__":
    unittest.main()
