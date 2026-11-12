"""回测 A 股交易成本与数据清洗回归测试。"""

import inspect
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from tradingagents.services import backtest as backtest_module


def _csv_prices(rows: int = 120) -> str:
    lines = ["Date,Open,High,Low,Close,Volume"]
    price = 10.0
    current = date(2025, 1, 1)
    for i in range(rows):
        # 制造一段先涨后跌的行情，确保 MA 交叉产生交易
        price += 0.1 if i < rows // 2 else -0.08
        lines.append(
            f"{current.isoformat()},{price:.2f},{price + 0.2:.2f},"
            f"{price - 0.2:.2f},{price:.2f},1000"
        )
        current += timedelta(days=1)
    return "\n".join(lines)


class BacktestCostTests(unittest.TestCase):
    def test_costs_reduce_net_return(self):
        csv_text = _csv_prices()
        with patch.object(
            backtest_module, "route_to_vendor", return_value=csv_text
        ):
            clean = backtest_module.run_backtest(
                "600519",
                "2025-01-01",
                "2025-04-30",
                commission_rate=0.0,
                stamp_duty_rate=0.0,
                slippage_rate=0.0,
            )
            costly = backtest_module.run_backtest(
                "600519", "2025-01-01", "2025-04-30"
            )

        self.assertLess(costly["total_return"], clean["total_return"])
        # 变更说明：旧断言是 ``gross_return == clean.total_return``，隐含
        # "费率置 0 == 零成本"。但 A 股单笔最低佣金 5 元与滑点都不是费率，
        # 无法通过费率参数关闭；现在 ``gross_return`` 是**零成本重算**的
        # 毛收益，必然严格高于净收益。
        self.assertGreater(costly["gross_return"], costly["total_return"])
        self.assertGreater(costly["cost_rate"], 0)
        self.assertGreater(costly["total_cost"], 0)

    def test_default_slippage_is_not_zero(self):
        """滑点默认必须非零：此前默认 0，等于假设委托都能按理想价成交。"""
        signature = inspect.signature(backtest_module.run_backtest)
        self.assertGreater(signature.parameters["slippage_rate"].default, 0.0)
        self.assertEqual(
            signature.parameters["min_commission"].default, 5.0
        )


class BacktestDataCleaningTests(unittest.TestCase):
    def test_duplicate_dates_are_deduplicated(self):
        csv_text = _csv_prices(30)
        first_row = csv_text.splitlines()[1]
        duplicated = csv_text + "\n" + first_row
        with patch.object(
            backtest_module, "route_to_vendor", return_value=duplicated
        ):
            result = backtest_module.run_backtest(
                "600519", "2025-01-01", "2025-04-30"
            )
        self.assertEqual(result["bars"], 30)


class BacktestValidationTests(unittest.TestCase):
    def test_unknown_strategy_raises_value_error(self):
        """未知策略必须显式报错，不能静默回退成 ma_cross。"""
        csv_text = _csv_prices(30)
        with patch.object(
            backtest_module, "route_to_vendor", return_value=csv_text
        ):
            with self.assertRaises(ValueError) as ctx:
                backtest_module.run_backtest(
                    "600519", "2025-01-01", "2025-04-30", strategy="unknown_strategy"
                )
        self.assertIn("unknown_strategy", str(ctx.exception))

    def test_trade_count_is_much_smaller_than_bars(self):
        """事件驱动撮合：交易笔数应当远少于 bar 数（旧实现是仓位翻转计数）。"""
        csv_text = _csv_prices()
        with patch.object(
            backtest_module, "route_to_vendor", return_value=csv_text
        ):
            result = backtest_module.run_backtest("600519", "2025-01-01", "2025-04-30")
        self.assertLess(result["trade_count"], result["bars"])
        self.assertGreater(result["trade_count"], 0)
        self.assertEqual(len(result["equity_curve"]), result["bars"])

    def test_benchmark_is_reported(self):
        csv_text = _csv_prices()
        with patch.object(
            backtest_module, "route_to_vendor", return_value=csv_text
        ):
            result = backtest_module.run_backtest("600519", "2025-01-01", "2025-04-30")
        self.assertIn("benchmark", result)
        self.assertIn("total_return", result["benchmark"])
        self.assertIn("benchmark_return", result)


if __name__ == "__main__":
    unittest.main()
