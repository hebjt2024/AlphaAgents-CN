"""A 股资金流向数据与工具注册测试（不访问网络）。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import akshare_adapter, interface
from tradingagents.dataflows.akshare_adapter import get_akshare_capital_flow


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": "2024-01-05",
                "收盘价": 1700.0,
                "涨跌幅": 1.2,
                "主力净流入-净额": 5.0e8,
                "主力净流入-净占比": 3.5,
                "超大单净流入-净额": 3.0e8,
                "大单净流入-净额": 2.0e8,
                "中单净流入-净额": -1.0e8,
                "小单净流入-净额": -4.0e8,
            },
            {
                "日期": "2024-01-04",
                "收盘价": 1680.0,
                "涨跌幅": -0.5,
                "主力净流入-净额": -2.0e8,
                "主力净流入-净占比": -1.2,
                "超大单净流入-净额": -1.0e8,
                "大单净流入-净额": -1.0e8,
                "中单净流入-净额": 5.0e7,
                "小单净流入-净额": 1.5e8,
            },
        ]
    )


class CapitalFlowDataTests(unittest.TestCase):
    def test_capital_flow_output(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_individual_fund_flow",
            return_value=_sample_frame(),
        ):
            output = get_akshare_capital_flow("600519", "2024-01-05")

        self.assertIn("600519", output)
        self.assertIn("主力净流入", output)
        self.assertIn("合计", output)

    def test_non_a_share_returns_message(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_individual_fund_flow",
            side_effect=AssertionError("should not be called"),
        ):
            output = get_akshare_capital_flow("NVDA")
        self.assertIn("不是 A 股标的", output)

    def test_snapshot_fallback_not_used_for_historical_date(self):
        with (
            patch.object(
                akshare_adapter.ak,
                "stock_individual_fund_flow",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_individual_fund_flow_rank",
                return_value=pd.DataFrame(
                    [{"代码": "600519", "今日主力净流入-净额": 1.0e8}]
                ),
            ),
        ):
            output = get_akshare_capital_flow("600519", "2024-01-05")
        self.assertIn("No capital flow data", output)

    def test_future_data_is_not_leaked(self):
        """请求历史日期时，不能返回分析日之后的资金流数据。"""
        future = _sample_frame().copy()
        future["日期"] = ["2026-11-10", "2026-11-09"]
        with patch.object(
            akshare_adapter.ak,
            "stock_individual_fund_flow",
            return_value=future,
        ):
            output = get_akshare_capital_flow("600519", "2024-01-05")
        self.assertIn("No capital flow data", output)
        self.assertNotIn("2026-11-10", output)


class CapitalFlowRegistrationTests(unittest.TestCase):
    def test_category_registered(self):
        self.assertEqual(
            interface.get_category_for_method("get_capital_flow"),
            "capital_flow",
        )

    def test_vendor_registered(self):
        self.assertIn(
            "akshare", interface.VENDOR_METHODS["get_capital_flow"]
        )


if __name__ == "__main__":
    unittest.main()
