"""数据正确性与工具 Schema 回归测试（不访问网络）。"""

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals
from tradingagents.agents.utils.news_data_tools import get_news
from tradingagents.dataflows import akshare_adapter, baostock_adapter, interface
from tradingagents.dataflows.akshare_adapter import get_akshare_news
from tradingagents.dataflows.config import set_config

REPO_ROOT = Path(__file__).resolve().parents[1]


class ToolSchemaConsistencyTests(unittest.TestCase):
    def test_get_news_uses_ticker_not_query(self):
        self.assertIn("ticker", get_news.args)
        self.assertNotIn("query", get_news.args)

        with self.assertRaises(ValidationError):
            get_news.invoke(
                {
                    "query": "AAPL news",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                }
            )

    def test_get_fundamentals_curr_date_is_optional(self):
        schema = get_fundamentals.args.get("curr_date", {})
        self.assertIsNone(schema.get("default"))

    def test_prompts_reference_ticker(self):
        for rel in (
            "tradingagents/agents/analysts/news_analyst.py",
            "tradingagents/agents/analysts/social_media_analyst.py",
        ):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("get_news(ticker", text, rel)
            self.assertNotIn("get_news(query", text, rel)


class DataMappingTests(unittest.TestCase):
    def test_akshare_news_uses_real_column_names(self):
        frame = pd.DataFrame(
            [
                {
                    "关键词": "600519",
                    "新闻标题": "测试标题",
                    "新闻内容": "测试正文",
                    "发布时间": "2024-01-03 10:00:00",
                    "文章来源": "测试来源",
                    "新闻链接": "https://example.com/news",
                }
            ]
        )
        with patch.object(
            akshare_adapter.ak, "stock_news_em", return_value=frame
        ):
            output = get_akshare_news("600519", "2024-01-01", "2024-01-05")

        self.assertIn("测试标题", output)
        self.assertIn("测试正文", output)
        self.assertIn("测试来源", output)
        self.assertNotIn("No title", output)

    def test_alpha_vantage_indicator_uses_matching_signature(self):
        func = interface.VENDOR_METHODS["get_indicators"]["alpha_vantage"]
        self.assertEqual(func.__name__, "get_stockstats_indicator")

    def test_baostock_symbol_and_report_period(self):
        self.assertEqual(baostock_adapter._normalize("sh.600519"), "600519")
        self.assertEqual(baostock_adapter._normalize("sz.000001"), "000001")
        self.assertEqual(
            baostock_adapter._to_baostock_code("830799"), "bj.830799"
        )
        self.assertEqual(
            baostock_adapter._latest_report_period("2026-07-15"), (2026, 1)
        )
        self.assertEqual(
            baostock_adapter._latest_report_period("2026-02-01"), (2025, 4)
        )


class SuspensionNoteTests(unittest.TestCase):
    def test_missing_trading_days_are_reported(self):
        with patch(
            "tradingagents.dataflows.trade_calendar.is_cn_trading_day",
            return_value=True,
        ):
            note = akshare_adapter._missing_trading_days_note(
                "2024-01-02",
                "2024-01-05",
                ["2024-01-02", "2024-01-03", "2024-01-05"],
            )
        self.assertIn("1/4", note)
        self.assertIn("停牌", note)

    def test_no_note_when_all_days_present(self):
        with patch(
            "tradingagents.dataflows.trade_calendar.is_cn_trading_day",
            return_value=True,
        ):
            note = akshare_adapter._missing_trading_days_note(
                "2024-01-02",
                "2024-01-03",
                ["2024-01-02", "2024-01-03"],
            )
        self.assertEqual(note, "")


class HistoryNormalizationTests(unittest.TestCase):
    def test_finalize_hist_sorts_and_dedupes(self):
        frame = pd.DataFrame(
            {
                "日期": ["2024-01-03", "2024-01-02", "2024-01-03"],
                "开盘": [1, 1, 1],
                "最高": [3, 3, 3],
                "最低": [1, 1, 1],
                "收盘": ["3.0", "2.0", "3.0"],
                "成交量": ["100", "200", "100"],
            }
        )
        out = akshare_adapter._finalize_hist(frame)
        self.assertEqual(out["日期"].tolist(), ["2024-01-02", "2024-01-03"])
        self.assertEqual(out["收盘"].tolist(), [2.0, 3.0])
        self.assertEqual(out["成交量"].tolist(), [200, 100])

    def test_invalid_primary_rows_fall_back_to_sina(self):
        bad = pd.DataFrame(
            {
                "日期": ["2024-01-02"],
                "开盘": [1],
                "最高": [1],
                "最低": [1],
                "收盘": ["bad"],
                "成交量": [1],
            }
        )
        good = pd.DataFrame(
            {
                "date": ["2024-01-02"],
                "open": [1.0],
                "high": [3.0],
                "low": [1.0],
                "close": [2.0],
                "volume": [100],
            }
        )
        with (
            patch.object(
                akshare_adapter.ak, "stock_zh_a_hist", return_value=bad
            ),
            patch.object(
                akshare_adapter.ak, "stock_zh_a_daily", return_value=good
            ),
        ):
            out = akshare_adapter.get_stock_hist_with_fallback(
                "600519", "20240101", "20240110"
            )
        self.assertFalse(out.empty)
        self.assertEqual(out["收盘"].tolist(), [2.0])

    def test_bse_skips_unsupported_fallbacks(self):
        with (
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_daily",
                side_effect=AssertionError("sina should be skipped for BSE"),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist_tx",
                side_effect=AssertionError("tencent should be skipped for BSE"),
            ),
        ):
            out = akshare_adapter.get_stock_hist_with_fallback(
                "920002", "20240101", "20240110"
            )
        self.assertTrue(out.empty)


class SyntheticDataGuardTests(unittest.TestCase):
    def setUp(self):
        self._original = interface.VENDOR_METHODS["get_indicators"]

    def tearDown(self):
        interface.VENDOR_METHODS["get_indicators"] = self._original
        set_config({"allow_synthetic_data": False})

    def test_synthetic_data_is_rejected_by_default(self):
        interface.VENDOR_METHODS["get_indicators"] = {
            "cache": lambda *args, **kwargs: "# simulated data for testing\n1,2,3"
        }
        set_config({"allow_synthetic_data": False})

        with self.assertRaises(RuntimeError):
            interface.route_to_vendor(
                "get_indicators", "600519", "rsi", "2024-01-01", 5
            )

    def test_synthetic_data_is_marked_when_enabled(self):
        interface.VENDOR_METHODS["get_indicators"] = {
            "cache": lambda *args, **kwargs: "# simulated data for testing\n1,2,3"
        }
        set_config({"allow_synthetic_data": True})

        result = interface.route_to_vendor(
            "get_indicators", "600519", "rsi", "2024-01-01", 5
        )
        self.assertTrue(result.startswith("[SIMULATED DATA]"))


if __name__ == "__main__":
    unittest.main()
