"""市场复盘服务测试。"""

import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.services import market_review as mr


def fake_frames():
    index_df = pd.DataFrame(
        [
            {
                "代码": "sh000001",
                "名称": "上证指数",
                "最新价": 3200.0,
                "涨跌幅": 1.0,
                "成交额": 1.0e11,
            },
            {
                "代码": "sz399001",
                "名称": "深证成指",
                "最新价": 10000.0,
                "涨跌幅": -0.5,
                "成交额": 6.0e10,
            },
        ]
    )
    sector_df = pd.DataFrame(
        [
            {
                "板块": "半导体",
                "涨跌幅": 3.2,
                "总成交额": 5.0e10,
                "公司家数": 100,
                "股票名称": "中芯国际",
                "个股-涨跌幅": 8.0,
            },
            {
                "板块": "银行",
                "涨跌幅": -1.2,
                "总成交额": 3.0e10,
                "公司家数": 40,
                "股票名称": "招商银行",
                "个股-涨跌幅": -2.0,
            },
        ]
    )
    breadth_df = pd.DataFrame(
        {
            "item": [
                "上涨",
                "下跌",
                "平盘",
                "涨停",
                "真实涨停",
                "跌停",
                "真实跌停",
                "停牌",
                "活跃度",
                "统计日期",
            ],
            "value": [
                3000,
                1500,
                100,
                80,
                70,
                5,
                4,
                10,
                "65.0%",
                "2026-11-11 15:00:00",
            ],
        }
    )
    zt_df = pd.DataFrame({"所属行业": ["半导体", "半导体", "银行"]})
    fund_df = pd.DataFrame(
        [
            {
                "行业": "半导体",
                "行业-涨跌幅": 3.2,
                "流入资金": 100.0,
                "流出资金": 80.0,
                "净额": 20.0,
                "公司家数": 100,
                "领涨股": "中芯国际",
                "领涨股-涨跌幅": 8.0,
            },
            {
                "行业": "银行",
                "行业-涨跌幅": -1.2,
                "流入资金": 50.0,
                "流出资金": 70.0,
                "净额": -20.0,
                "公司家数": 40,
                "领涨股": "招商银行",
                "领涨股-涨跌幅": -2.0,
            },
        ]
    )
    return index_df, sector_df, breadth_df, zt_df, fund_df


class MarketReviewTests(unittest.TestCase):
    def setUp(self):
        mr._cache.clear()
        self.frames = fake_frames()

    def _patches(self):
        index_df, sector_df, breadth_df, zt_df, fund_df = self.frames
        return (
            patch.object(
                mr,
                "_call",
                side_effect=lambda func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch("akshare.stock_zh_index_spot_sina", return_value=index_df),
            patch("akshare.stock_sector_spot", return_value=sector_df),
            patch("akshare.stock_market_activity_legu", return_value=breadth_df),
            patch("akshare.stock_zt_pool_em", return_value=zt_df),
            patch("akshare.stock_fund_flow_industry", return_value=fund_df),
        )

    def test_build_review(self):
        patches = self._patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
        ):
            review = mr.build_market_review()

        self.assertTrue(review["success"])
        self.assertEqual(review["as_of"], "2026-11-11")
        self.assertEqual(review["indices"][0]["name"], "上证指数")
        self.assertEqual(review["indices"][0]["close"], 3200.0)
        self.assertEqual(review["breadth"]["up"], 3000)
        self.assertEqual(review["sectors"]["top"][0]["name"], "半导体")
        self.assertEqual(review["sectors"]["bottom"][0]["name"], "银行")
        self.assertEqual(review["fund_flow"]["top"][0]["name"], "半导体")
        self.assertIn("半导体", review["summary"]["top_inflow"])
        self.assertEqual(review["limit_up_industries"][0], {"name": "半导体", "count": 2})
        self.assertEqual(review["summary"]["tone"], "偏强")
        self.assertIn("市场复盘", review["markdown"])
        self.assertIn("资金净流入居前", review["markdown"])
        self.assertIn("半导体", review["markdown"])
        self.assertIn("涨停行业分布", review["markdown"])

    def test_partial_failure_degrades(self):
        patches = self._patches()
        with (
            patches[0],
            patch(
                "akshare.stock_zh_index_spot_sina",
                side_effect=RuntimeError("index down"),
            ),
            patches[2],
            patches[3],
            patches[4],
            patches[5],
        ):
            review = mr.build_market_review()

        self.assertTrue(review["success"])
        self.assertTrue(
            any("指数数据获取失败" in item for item in review["warnings"])
        )
        self.assertEqual(review["indices"], [])
        self.assertEqual(review["sectors"]["top"][0]["name"], "半导体")

    def test_cache(self):
        patches = self._patches()
        with (
            patches[0],
            patches[1] as index_mock,
            patches[2],
            patches[3],
            patches[4],
            patches[5],
        ):
            mr.build_market_review()
            mr.build_market_review()

        index_mock.assert_called_once()

    def test_send_review(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "markdown": "## 市场复盘 2026-11-11",
        }
        with (
            patch.object(mr, "build_market_review", return_value=fake),
            patch(
                "tradingagents.notify.send_notification",
                return_value={"wecom": {"ok": True}},
            ) as mocked,
        ):
            result = mr.send_market_review()

        mocked.assert_called_once()
        self.assertEqual(result["notify"], {"wecom": {"ok": True}})

    def test_send_review_skips_when_unavailable(self):
        fake = {"success": False, "message": "数据不可用"}
        with (
            patch.object(mr, "build_market_review", return_value=fake),
            patch("tradingagents.notify.send_notification") as mocked,
        ):
            result = mr.send_market_review()

        mocked.assert_not_called()
        self.assertEqual(result["notify"]["skipped"], "数据不可用")

    def test_requested_date_mismatch(self):
        patches = self._patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
        ):
            review = mr.build_market_review("2026-11-10")

        self.assertTrue(
            any("无法回溯" in item for item in review["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
