"""板块热度历史测试。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.services import sector_history as sh


def fake_names():
    return pd.DataFrame(
        [{"name": "半导体", "code": "881121"}, {"name": "白酒", "code": "881273"}]
    )


def fake_history():
    dates = pd.date_range("2026-10-01", periods=25, freq="D")
    closes = [100 + i for i in range(25)]
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘价": closes,
            "最高价": [c + 1 for c in closes],
            "最低价": [c - 1 for c in closes],
            "收盘价": closes,
            "成交量": [1_000_000] * 25,
            "成交额": [1.0e10] * 25,
        }
    )


class SectorHistoryTests(unittest.TestCase):
    def setUp(self):
        sh._name_cache = None

    def test_list_names_and_cache(self):
        with (
            patch.object(
                sh,
                "_call",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch(
                "akshare.stock_board_industry_name_ths",
                return_value=fake_names(),
            ) as mocked,
        ):
            first = sh.list_sector_names()
            second = sh.list_sector_names()

        self.assertEqual(first[0]["name"], "半导体")
        self.assertEqual(second[1]["code"], "881273")
        mocked.assert_called_once()

    def test_history_metrics(self):
        with (
            patch.object(
                sh,
                "_call",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch(
                "akshare.stock_board_industry_index_ths",
                return_value=fake_history(),
            ),
        ):
            result = sh.get_sector_history("半导体", days=20)

        self.assertTrue(result["success"])
        self.assertEqual(result["name"], "半导体")
        self.assertEqual(result["days"], 20)
        self.assertEqual(result["close"], 124.0)
        self.assertAlmostEqual(result["change_1d"], 0.81, places=2)
        self.assertAlmostEqual(result["change_5d"], 4.2, places=2)
        self.assertAlmostEqual(result["change_20d"], 19.23, places=2)
        self.assertEqual(result["turnover_yi"], 100.0)
        self.assertEqual(len(result["series"]), 20)

    def test_history_empty(self):
        with (
            patch.object(
                sh,
                "_call",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch(
                "akshare.stock_board_industry_index_ths",
                return_value=pd.DataFrame(),
            ),
        ):
            result = sh.get_sector_history("不存在")

        self.assertFalse(result["success"])
        self.assertIn("未获取到", result["message"])

    def test_history_requires_name(self):
        result = sh.get_sector_history("")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
