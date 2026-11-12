"""回测 A 股交易成本与数据清洗回归测试。"""

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
        self.assertAlmostEqual(
            costly["gross_return"], clean["total_return"], places=4
        )
        self.assertGreater(costly["cost_rate"], 0)


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


if __name__ == "__main__":
    unittest.main()
