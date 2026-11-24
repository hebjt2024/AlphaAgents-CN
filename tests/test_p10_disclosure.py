"""P10：公告 / 研报一致预期 / 股东变化。"""

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _announcements() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "公告标题": "关于回购公司股份的公告",
                "公告类型": "回购",
                "公告日期": pd.Timestamp("2026-11-10").date(),
                "网址": "https://example.com/1",
            },
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "公告标题": "关于股东减持计划的公告",
                "公告类型": "持股变动",
                "公告日期": pd.Timestamp("2026-11-12").date(),
                "网址": "https://example.com/2",
            },
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "公告标题": "第三季度报告",
                "公告类型": "财务报告",
                "公告日期": pd.Timestamp("2026-11-14").date(),
                "网址": "https://example.com/3",
            },
        ]
    )


def _research() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "股票简称": "贵州茅台",
                "报告名称": "三季报点评",
                "东财评级": "买入",
                "机构": "中信证券",
                "日期": "2026-11-10",
                "行业": "白酒",
                "2026-盈利预测-收益": 70.0,
                "2026-盈利预测-市盈率": 20.0,
                "报告PDF链接": "https://example.com/a.pdf",
            },
            {
                "股票代码": "600519",
                "股票简称": "贵州茅台",
                "报告名称": "渠道改革",
                "东财评级": "增持",
                "机构": "华泰证券",
                "日期": "2026-11-12",
                "行业": "白酒",
                "2026-盈利预测-收益": 72.0,
                "2026-盈利预测-市盈率": 21.0,
                "报告PDF链接": "https://example.com/b.pdf",
            },
        ]
    )


def _holders() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "股东户数统计截止日": "2026-09-30",
                "股东户数-本次": 100000,
                "股东户数-上次": 120000,
                "股东户数-增减": -20000,
                "股东户数-增减比例": -16.67,
                "户均持股市值": 1.0e6,
                "总市值": 1.0e12,
                "股东户数公告日期": "2026-10-30",
                "代码": "600519",
                "名称": "贵州茅台",
            },
            {
                "股东户数统计截止日": "2026-06-30",
                "股东户数-本次": 120000,
                "股东户数-上次": 110000,
                "股东户数-增减": 10000,
                "股东户数-增减比例": 9.09,
                "户均持股市值": 9.0e5,
                "总市值": 1.0e12,
                "股东户数公告日期": "2026-08-30",
                "代码": "600519",
                "名称": "贵州茅台",
            },
        ]
    )


def _top10() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "名次": 1,
                "股东名称": "茅台集团",
                "股份类型": "流通A股",
                "持股数": 681282935,
                "占总股本持股比例": 54.5,
                "增减": "不变",
            },
            {
                "名次": 2,
                "股东名称": "香港中央结算",
                "股份类型": "流通A股",
                "持股数": 55263682,
                "占总股本持股比例": 4.42,
                "增减": "1552026",
            },
        ]
    )


class DisclosureServiceTests(unittest.TestCase):
    def setUp(self):
        from tradingagents.dataflows.disclosure import clear_disclosure_cache

        clear_disclosure_cache()

    def test_classification(self):
        from tradingagents.dataflows.disclosure import classify_announcement

        positive = classify_announcement("关于回购公司股份的公告", "回购")
        self.assertEqual(positive["impact"], "positive")
        self.assertEqual(positive["level"], "high")
        negative = classify_announcement("关于股东减持计划的公告", "持股变动")
        self.assertEqual(negative["impact"], "negative")
        neutral = classify_announcement("第三季度报告", "财务报告")
        self.assertEqual(neutral["impact"], "neutral")

    def test_announcements_normalization(self):
        from tradingagents.dataflows.disclosure import get_announcements

        with patch(
            "akshare.stock_fundamental.stock_notice._stock_notice_report",
            return_value=_announcements(),
        ):
            result = get_announcements("600519", curr_date="2026-11-15")
        self.assertTrue(result["success"])
        self.assertEqual(result["counts"]["total"], 3)
        self.assertEqual(result["counts"]["positive"], 1)
        self.assertEqual(result["counts"]["negative"], 1)
        self.assertEqual(result["items"][0]["date"], "2026-11-14")

    def test_research_consensus(self):
        from tradingagents.dataflows import akshare_adapter
        from tradingagents.dataflows.disclosure import get_research_consensus

        with patch.object(
            akshare_adapter.ak,
            "stock_research_report_em",
            return_value=_research(),
        ):
            result = get_research_consensus("600519", curr_date="2026-11-15")
        self.assertTrue(result["success"])
        self.assertEqual(result["consensus"], "偏多")
        self.assertEqual(result["ratings"]["买入"], 1)
        self.assertEqual(result["ratings"]["增持"], 1)
        self.assertAlmostEqual(result["forecasts"]["2026"]["eps"], 71.0)

    def test_research_without_ratings_is_not_bearish(self):
        from tradingagents.dataflows import akshare_adapter
        from tradingagents.dataflows.disclosure import get_research_consensus

        frame = _research().copy()
        frame["东财评级"] = ""
        with patch.object(
            akshare_adapter.ak,
            "stock_research_report_em",
            return_value=frame,
        ):
            result = get_research_consensus("600519", curr_date="2026-11-15")
        self.assertEqual(result["consensus"], "无评级数据")
        self.assertIsNone(result["buy_ratio"])

    def test_shareholder_changes(self):
        from tradingagents.dataflows import akshare_adapter
        from tradingagents.dataflows.disclosure import get_shareholder_changes

        with patch.object(
            akshare_adapter.ak,
            "stock_zh_a_gdhs_detail_em",
            return_value=_holders(),
        ), patch.object(
            akshare_adapter.ak,
            "stock_gdfx_top_10_em",
            return_value=_top10(),
        ):
            result = get_shareholder_changes("600519", curr_date="2026-11-15")
        self.assertTrue(result["success"])
        self.assertEqual(result["trend"], "筹码集中")
        self.assertEqual(len(result["top10"]), 2)
        self.assertIn("58.92", result["summary"])

    def test_summary_combines_sections(self):
        from tradingagents.dataflows import disclosure

        with patch.object(
            disclosure,
            "get_announcements",
            return_value={"success": True, "summary": "公告", "items": [], "warnings": []},
        ), patch.object(
            disclosure,
            "get_research_consensus",
            return_value={"success": True, "summary": "研报", "warnings": []},
        ), patch.object(
            disclosure,
            "get_shareholder_changes",
            return_value={"success": True, "summary": "股东", "warnings": []},
        ):
            result = disclosure.get_disclosure_summary("600519")
        self.assertTrue(result["success"])
        self.assertIn("公告", result["summary"])
        self.assertIn("研报", result["summary"])
        self.assertIn("股东", result["summary"])


class DisclosureApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_summary_endpoint(self):
        canned = {
            "success": True,
            "ticker": "600519",
            "as_of": "2026-11-15",
            "announcements": {"success": True, "items": [], "counts": {"total": 0}},
            "research": {"success": True, "consensus": "中性"},
            "shareholders": {"success": True, "trend": "未知"},
            "summary": "测试摘要",
            "warnings": [],
        }
        with patch(
            "tradingagents.dataflows.disclosure.get_disclosure_summary",
            return_value=canned,
        ):
            data = self.client.get(
                "/api/disclosure/summary", params={"ticker": "600519"}
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["summary"], "测试摘要")

    def test_announcement_endpoint(self):
        canned = {
            "success": True,
            "ticker": "600519",
            "items": [{"title": "回购", "impact": "positive"}],
            "counts": {"total": 1},
        }
        with patch(
            "tradingagents.dataflows.disclosure.get_announcements",
            return_value=canned,
        ):
            data = self.client.get(
                "/api/disclosure/announcements", params={"ticker": "600519"}
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["impact"], "positive")


class DisclosureToolTests(unittest.TestCase):
    def test_tool_renders_text(self):
        from tradingagents.agents.utils.disclosure_tools import get_announcements

        canned = {
            "success": True,
            "ticker": "600519",
            "as_of": "2026-11-15",
            "summary": "近 30 天共 1 条公告",
            "items": [
                {
                    "date": "2026-11-10",
                    "impact_label": "利好",
                    "level": "high",
                    "title": "回购",
                    "type": "回购",
                }
            ],
            "high_impact": [
                {
                    "date": "2026-11-10",
                    "impact_label": "利好",
                    "level": "high",
                    "title": "回购",
                    "type": "回购",
                }
            ],
            "warnings": [],
        }
        with patch(
            "tradingagents.agents.utils.disclosure_tools._get_announcements",
            return_value=canned,
        ):
            text = get_announcements.invoke(
                {"ticker": "600519", "curr_date": "2026-11-15"}
            )
        self.assertIn("高影响公告", text)
        self.assertIn("回购", text)


if __name__ == "__main__":
    unittest.main()
