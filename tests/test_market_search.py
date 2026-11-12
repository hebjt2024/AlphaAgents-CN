"""股票搜索与热门股测试。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.services import market_search as ms


def hot_frame():
    return pd.DataFrame(
        [
            {
                "代码": "002160",
                "名称": "常铝股份",
                "涨跌幅": 10.0,
                "最新价": 4.4,
                "成交额": 5.4e8,
                "封板资金": 5.4e7,
                "连板数": 2,
                "炸板次数": 5,
                "所属行业": "工业金属",
                "首次封板时间": "092500",
                "最后封板时间": "094533",
            },
            {
                "代码": "002076",
                "名称": "星光股份",
                "涨跌幅": 9.9,
                "最新价": 2.22,
                "成交额": 2.5e8,
                "封板资金": 5.4e7,
                "连板数": 1,
                "炸板次数": 1,
                "所属行业": "照明设备",
                "首次封板时间": "092500",
                "最后封板时间": "093257",
            },
        ]
    )


class MarketSearchTests(unittest.TestCase):
    def test_search_by_name(self):
        with patch.object(
            ms,
            "_names",
            return_value={"贵州茅台": "600519", "宁德时代": "300750"},
        ):
            result = ms.search_stocks("茅台")

        self.assertTrue(result["success"])
        self.assertEqual(result["items"][0]["ticker"], "600519")
        self.assertEqual(result["items"][0]["market"], "沪市")

    def test_search_by_code(self):
        with patch.object(
            ms,
            "_names",
            return_value={"贵州茅台": "600519", "宁德时代": "300750"},
        ):
            result = ms.search_stocks("3007")

        self.assertEqual(result["items"][0]["ticker"], "300750")

    def test_search_empty(self):
        result = ms.search_stocks("")
        self.assertEqual(result["items"], [])

    def test_hot_stocks_sorted_by_streak(self):
        with (
            patch.object(ms, "_trade_date", return_value="2026-11-11"),
            patch.object(
                ms,
                "_call",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch(
                "akshare.stock_zt_pool_em",
                return_value=hot_frame(),
            ),
        ):
            result = ms.get_hot_stocks(limit=10)

        self.assertTrue(result["success"])
        self.assertEqual(result["date"], "2026-11-11")
        self.assertEqual(result["items"][0]["ticker"], "002160")
        self.assertEqual(result["items"][0]["streak"], 2)
        self.assertEqual(result["items"][0]["industry"], "工业金属")
        self.assertEqual(result["count"], 2)


if __name__ == "__main__":
    unittest.main()
