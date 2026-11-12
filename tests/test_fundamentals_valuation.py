"""A 股基本面历史估值与快照提示测试。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import akshare_adapter


class HistoricalValuationTests(unittest.TestCase):
    def test_picks_latest_row_not_after_curr_date(self):
        def _fake(symbol, indicator, period):
            series = {
                "市盈率(TTM)": [18.0, 20.0, 99.0],
                "市净率": [4.0, 5.0, 99.0],
            }[indicator]
            dates = ["2024-01-02", "2024-01-09", "2024-02-01"]
            return pd.DataFrame({"date": dates, "value": series})

        akshare_adapter._VALUATION_CACHE.clear()
        with patch.object(
            akshare_adapter.ak,
            "stock_zh_valuation_baidu",
            side_effect=_fake,
        ):
            result = akshare_adapter._fetch_historical_valuation(
                "600519", "2024-01-10"
            )

        self.assertEqual(result["市盈率-TTM"], 20.0)
        self.assertEqual(result["市净率"], 5.0)
        self.assertEqual(result["_valuation_date"], "2024-01-09")


class FundamentalsSnapshotTests(unittest.TestCase):
    def test_historical_analysis_marks_snapshot_and_uses_valuation(self):
        info = pd.DataFrame(
            [{"item": "股票简称", "value": "贵州茅台"}, {"item": "行业", "value": "白酒"}]
        )
        spot = pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "股票简称": "贵州茅台",
                    "总市值": 2.0e12,
                    "市盈率-动态": 45.0,
                    "市净率": 12.0,
                    "换手率": 0.5,
                }
            ]
        )
        valuation = {
            "市盈率-TTM": 30.0,
            "市净率": 8.0,
            "_valuation_date": "2024-01-09",
        }

        with (
            patch.object(
                akshare_adapter.ak,
                "stock_individual_info_em",
                return_value=info,
            ),
            patch.object(
                akshare_adapter,
                "_get_spot_snapshot",
                return_value=spot,
            ),
            patch.object(
                akshare_adapter,
                "_fetch_abstract_metrics",
                return_value={},
            ),
            patch.object(
                akshare_adapter,
                "_fetch_historical_valuation",
                return_value=valuation,
            ),
        ):
            output = akshare_adapter.get_akshare_fundamentals(
                "600519", "2024-01-10"
            )

        self.assertIn("市盈率(TTM): 30.0", output)
        self.assertIn("估值日期: 2024-01-09", output)
        self.assertIn("数据说明", output)
        self.assertIn("当前实时快照", output)


if __name__ == "__main__":
    unittest.main()
