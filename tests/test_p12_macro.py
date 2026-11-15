"""P12：宏观 / 两融 / 行业轮动 / ETF 资金流。"""

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _yearly(name: str, value: float, previous: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"商品": name, "日期": "2026-09-01", "今值": previous, "预测值": None, "前值": previous - 0.5},
            {"商品": name, "日期": "2026-10-01", "今值": value, "预测值": value, "前值": previous},
        ]
    )


def _lpr() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"TRADE_DATE": "2026-09-20", "LPR1Y": 3.0, "LPR5Y": 3.5, "RATE_1": None, "RATE_2": None},
            {"TRADE_DATE": "2026-10-20", "LPR1Y": 3.0, "LPR5Y": 3.5, "RATE_1": None, "RATE_2": None},
        ]
    )


def _shibor() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"日期": "2026-11-12", "O/N-定价": 1.3, "1W-定价": 1.4},
            {"日期": "2026-11-13", "O/N-定价": 1.25, "1W-定价": 1.38},
        ]
    )


def _bonds() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": "2026-11-13",
                "中国国债收益率10年": 1.61,
                "美国国债收益率10年": 5.15,
            }
        ]
    )


def _margin(offset: float = 0.0) -> pd.DataFrame:
    rows = []
    for index in range(30):
        rows.append(
            {
                "日期": f"2026-10-{index + 1:02d}",
                "融资买入额": 1e9,
                "融资余额": 1.0e12 + offset + index * 1e9,
                "融券余额": 1e10,
                "融资融券余额": 1.01e12 + offset + index * 1e9,
            }
        )
    return pd.DataFrame(rows)


def _rotation(indicator: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "序号": 1,
                "名称": "半导体",
                f"{indicator}涨跌幅": 3.0 if indicator == "今日" else 8.0,
                f"{indicator}主力净流入-净额": 2.0e9,
                f"{indicator}主力净流入-净占比": 8.0,
                f"{indicator}主力净流入最大股": "中芯国际",
            },
            {
                "序号": 2,
                "名称": "银行",
                f"{indicator}涨跌幅": 1.0 if indicator == "今日" else 3.0,
                f"{indicator}主力净流入-净额": 1.0e9,
                f"{indicator}主力净流入-净占比": 4.0,
                f"{indicator}主力净流入最大股": "招商银行",
            },
            {
                "序号": 3,
                "名称": "煤炭",
                f"{indicator}涨跌幅": -2.0 if indicator == "今日" else -5.0,
                f"{indicator}主力净流入-净额": -1.5e9,
                f"{indicator}主力净流入-净占比": -6.0,
                f"{indicator}主力净流入最大股": "中国神华",
            },
        ]
    )


def _etf() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "510300",
                "名称": "沪深300ETF",
                "最新价": 4.0,
                "涨跌幅": 1.2,
                "成交额": 5.0e9,
                "换手率": 2.0,
                "主力净流入-净额": 1.2e9,
                "主力净流入-净占比": 10.0,
                "最新份额": 1.0e10,
                "总市值": 4.0e10,
            },
            {
                "代码": "588000",
                "名称": "科创50ETF",
                "最新价": 1.0,
                "涨跌幅": -0.5,
                "成交额": 2.0e9,
                "换手率": 3.0,
                "主力净流入-净额": -8.0e8,
                "主力净流入-净占比": -7.0,
                "最新份额": 5.0e9,
                "总市值": 5.0e9,
            },
        ]
    )


class MacroServiceTests(unittest.TestCase):
    def setUp(self):
        from tradingagents.dataflows.macro_data import clear_macro_cache

        clear_macro_cache()
        from tradingagents.dataflows import akshare_adapter

        self.ak = akshare_adapter.ak

    def _patches(self):
        return [
            patch.object(self.ak, "macro_china_pmi_yearly", return_value=_yearly("PMI", 50.5, 49.8)),
            patch.object(self.ak, "macro_china_cpi_yearly", return_value=_yearly("CPI", 0.5, 0.3)),
            patch.object(self.ak, "macro_china_ppi_yearly", return_value=_yearly("PPI", -1.2, -1.8)),
            patch.object(self.ak, "macro_china_m2_yearly", return_value=_yearly("M2", 8.2, 7.9)),
            patch.object(self.ak, "macro_china_lpr", return_value=_lpr()),
            patch.object(self.ak, "macro_china_shibor_all", return_value=_shibor()),
            patch.object(self.ak, "bond_zh_us_rate", return_value=_bonds()),
        ]

    def test_macro_indicators(self):
        from tradingagents.dataflows.macro_data import get_macro_indicators

        for item in self._patches():
            item.start()
            self.addCleanup(item.stop)
        result = get_macro_indicators()
        self.assertTrue(result["success"])
        keys = {item["key"] for item in result["items"]}
        self.assertIn("制造业 PMI", keys)
        self.assertIn("LPR 1 年", keys)
        self.assertIn("中美 10Y 利差", keys)
        self.assertIn("PMI 扩张", result["summary"])

    def test_margin_snapshot(self):
        from tradingagents.dataflows.macro_data import get_margin_snapshot

        with patch.object(self.ak, "macro_china_market_margin_sh", return_value=_margin()), patch.object(
            self.ak, "macro_china_market_margin_sz", return_value=_margin(1e11)
        ):
            result = get_margin_snapshot()
        self.assertTrue(result["success"])
        self.assertGreater(result["balance_wanyi"], 0)
        self.assertTrue(result["series"])
        self.assertIn("两融余额", result["summary"])

    def test_industry_rotation(self):
        from tradingagents.dataflows.macro_data import get_industry_rotation

        with patch.object(
            self.ak, "stock_sector_fund_flow_rank", side_effect=lambda indicator, sector_type: _rotation(indicator)
        ):
            result = get_industry_rotation(10)
        self.assertTrue(result["success"])
        self.assertEqual(result["items"][0]["name"], "半导体")
        self.assertGreater(result["items"][0]["rotation_score"], 0)
        self.assertIn("领涨", result["summary"])

    def test_etf_flows(self):
        from tradingagents.dataflows.macro_data import get_etf_flows

        with patch.object(self.ak, "fund_etf_spot_em", return_value=_etf()):
            result = get_etf_flows(10)
        self.assertTrue(result["success"])
        self.assertEqual(result["items"][0]["code"], "510300")
        self.assertGreater(result["items"][0]["main_net_yi"], 0)
        self.assertLess(result["outflow"][0]["main_net_yi"], 0)

    def test_dashboard_combines_sections(self):
        from tradingagents.dataflows.macro_data import get_macro_dashboard

        with patch(
            "tradingagents.dataflows.macro_data.get_macro_indicators",
            return_value={"success": True, "summary": "宏观", "items": [], "warnings": []},
        ), patch(
            "tradingagents.dataflows.macro_data.get_margin_snapshot",
            return_value={"success": True, "summary": "两融", "warnings": []},
        ), patch(
            "tradingagents.dataflows.macro_data.get_industry_rotation",
            return_value={"success": True, "summary": "轮动", "items": [], "warnings": []},
        ), patch(
            "tradingagents.dataflows.macro_data.get_etf_flows",
            return_value={"success": True, "summary": "ETF", "items": [], "warnings": []},
        ):
            result = get_macro_dashboard()
        self.assertTrue(result["success"])
        self.assertIn("宏观", result["summary"])
        self.assertIn("ETF", result["summary"])


class MacroApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_dashboard_api(self):
        canned = {
            "success": True,
            "summary": "测试面板",
            "macro": {"success": True, "items": [], "warnings": []},
            "margin": {"success": True, "series": [], "warnings": []},
            "rotation": {"success": True, "items": [], "warnings": []},
            "etf": {"success": True, "items": [], "warnings": []},
            "warnings": [],
        }
        with patch(
            "tradingagents.dataflows.macro_data.get_macro_dashboard",
            return_value=canned,
        ):
            data = self.client.get("/api/macro/dashboard").json()
        self.assertTrue(data["success"])
        self.assertEqual(data["summary"], "测试面板")

    def test_rotation_api(self):
        with patch(
            "tradingagents.dataflows.macro_data.get_industry_rotation",
            return_value={"success": True, "items": [{"name": "半导体"}]},
        ):
            data = self.client.get("/api/market/rotation").json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["name"], "半导体")


class MacroToolTests(unittest.TestCase):
    def test_macro_tool_renders(self):
        from tradingagents.agents.utils.macro_tools import get_macro_overview

        canned = {
            "success": True,
            "summary": "PMI 扩张",
            "items": [
                {
                    "label": "制造业 PMI",
                    "value": 50.5,
                    "unit": "点",
                    "date": "2026-10-01",
                    "status": "上升",
                }
            ],
            "warnings": [],
        }
        with patch(
            "tradingagents.agents.utils.macro_tools._get_macro_indicators",
            return_value=canned,
        ):
            text = get_macro_overview.invoke({})
        self.assertIn("宏观指标", text)
        self.assertIn("制造业 PMI", text)


class MacroFrontendTests(unittest.TestCase):
    def test_macro_card_is_wired(self):
        from pathlib import Path

        html = (
            Path(__file__).resolve().parents[1]
            / "webui"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="macroCard"', html)
        self.assertIn("async function loadMacro()", html)


if __name__ == "__main__":
    unittest.main()
