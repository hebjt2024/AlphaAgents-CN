"""北向资金与龙虎榜数据测试（不访问网络）。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import akshare_adapter, interface
from tradingagents.dataflows.akshare_adapter import (
    get_akshare_dragon_tiger,
    get_akshare_northbound_flow,
)


def _northbound_frame(latest: str = "2026-11-10") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": latest,
                "当日成交净买额": 5.0,
                "当日资金流入": 6.0,
                "历史累计净买额": 100.0,
                "沪深300": 4000.0,
                "沪深300-涨跌幅": 0.5,
            },
            {
                "日期": "2026-11-07",
                "当日成交净买额": -2.0,
                "当日资金流入": -1.0,
                "历史累计净买额": 95.0,
                "沪深300": 3980.0,
                "沪深300-涨跌幅": -0.2,
            },
            {
                "日期": "2026-12-01",
                "当日成交净买额": 99.0,
                "当日资金流入": 99.0,
                "历史累计净买额": 199.0,
                "沪深300": 4100.0,
                "沪深300-涨跌幅": 1.0,
            },
        ]
    )


def _lhb_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "上榜日": "2026-11-05",
                "收盘价": 1700.0,
                "涨跌幅": 5.0,
                "龙虎榜净买额": 1.0e8,
                "龙虎榜买入额": 2.0e8,
                "龙虎榜卖出额": 1.0e8,
                "换手率": 1.0,
                "上榜原因": "测试原因",
                "上榜后1日": 1.0,
            },
            {
                "代码": "000001",
                "名称": "平安银行",
                "上榜日": "2026-11-05",
                "收盘价": 10.0,
                "涨跌幅": 3.0,
                "龙虎榜净买额": 5.0e7,
                "龙虎榜买入额": 1.0e8,
                "龙虎榜卖出额": 5.0e7,
                "换手率": 0.5,
                "上榜原因": "测试原因B",
                "上榜后1日": -1.0,
            },
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "上榜日": "2026-12-01",
                "收盘价": 1800.0,
                "涨跌幅": 6.0,
                "龙虎榜净买额": 9.0e8,
                "龙虎榜买入额": 9.0e8,
                "龙虎榜卖出额": 0.0,
                "换手率": 2.0,
                "上榜原因": "未来数据",
                "上榜后1日": 2.0,
            },
        ]
    )


class NorthboundTests(unittest.TestCase):
    def setUp(self):
        akshare_adapter._NORTHBOUND_CACHE["df"] = None
        akshare_adapter._NORTHBOUND_CACHE["ts"] = 0.0

    def test_summary_and_no_future_leak(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_hsgt_hist_em",
            return_value=_northbound_frame(),
        ):
            output = get_akshare_northbound_flow("2026-11-10", 5)

        self.assertIn("北向净流入合计: 3.00 亿元", output)
        self.assertIn("最新交易日北向净流入: 5.00 亿元", output)
        self.assertNotIn("2026-12-01", output)
        self.assertNotIn("99.0", output)

    def test_stale_data_warning(self):
        stale = pd.DataFrame(
            [
                {
                    "日期": "2024-08-16",
                    "当日成交净买额": 1.0,
                    "当日资金流入": 1.0,
                    "历史累计净买额": 100.0,
                    "沪深300": 3300.0,
                    "沪深300-涨跌幅": 0.1,
                },
                {
                    "日期": "2024-08-15",
                    "当日成交净买额": 2.0,
                    "当日资金流入": 2.0,
                    "历史累计净买额": 99.0,
                    "沪深300": 3290.0,
                    "沪深300-涨跌幅": 0.2,
                },
            ]
        )
        with patch.object(
            akshare_adapter.ak,
            "stock_hsgt_hist_em",
            return_value=stale,
        ):
            output = get_akshare_northbound_flow("2026-11-10", 5)
        self.assertIn("注意", output)
        self.assertIn("2024-08-16", output)

    def test_no_data_before_curr_date(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_hsgt_hist_em",
            return_value=_northbound_frame(latest="2025-01-02"),
        ):
            output = get_akshare_northbound_flow("2024-01-01", 5)
        self.assertIn("No northbound capital flow data", output)

    def test_missing_net_values_are_warned(self):
        frame = pd.DataFrame(
            [
                {
                    "日期": "2026-11-10",
                    "当日成交净买额": None,
                    "当日资金流入": None,
                    "历史累计净买额": None,
                    "沪深300": 4000.0,
                    "沪深300-涨跌幅": 0.1,
                }
            ]
        )
        with patch.object(
            akshare_adapter.ak,
            "stock_hsgt_hist_em",
            return_value=frame,
        ):
            output = get_akshare_northbound_flow(None, 5)
        self.assertIn("无有效数据", output)


class DragonTigerTests(unittest.TestCase):
    def setUp(self):
        akshare_adapter._LHB_CACHE.clear()

    def test_filter_by_code_and_no_future_leak(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_lhb_detail_em",
            return_value=_lhb_frame(),
        ):
            output = get_akshare_dragon_tiger("600519", "2026-11-10", 60)

        self.assertIn("600519", output)
        self.assertIn("最近上榜次数: 1 次", output)
        self.assertIn("龙虎榜净买额", output)
        self.assertNotIn("000001", output)
        self.assertNotIn("2026-12-01", output)

    def test_non_a_share(self):
        with patch.object(
            akshare_adapter.ak,
            "stock_lhb_detail_em",
            side_effect=AssertionError("should not be called"),
        ):
            output = get_akshare_dragon_tiger("NVDA")
        self.assertIn("不是 A 股标的", output)


class RegistrationTests(unittest.TestCase):
    def test_categories(self):
        for method in ("get_northbound_flow", "get_dragon_tiger"):
            self.assertEqual(
                interface.get_category_for_method(method), "capital_flow"
            )
            self.assertIn("akshare", interface.VENDOR_METHODS[method])


if __name__ == "__main__":
    unittest.main()
