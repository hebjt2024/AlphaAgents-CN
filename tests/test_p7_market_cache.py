"""P7：本地行情仓库、本地优先取数与数据质量检查。

测试使用独立市场库（``TA_MARKET_DB``），并显式开启缓存
（``TA_MARKET_CACHE=1``），避免与 conftest 的默认关闭策略冲突。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server

CSV = """Date,Open,High,Low,Close,Volume
2024-01-02,10.0,10.5,9.8,10.2,10000
2024-01-03,10.2,10.8,10.0,10.6,12000
2024-01-04,10.6,11.0,10.4,10.9,15000
"""


def _frame(rows: int = 20, start: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    closes = [start + index * 0.1 for index in range(rows)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": closes,
            "High": [value + 0.2 for value in closes],
            "Low": [value - 0.2 for value in closes],
            "Close": closes,
            "Volume": [10000] * rows,
            "Amount": [value * 10000 for value in closes],
        }
    )


class MarketStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_market_db = os.environ.get("TA_MARKET_DB")
        self._old_market_cache = os.environ.get("TA_MARKET_CACHE")
        os.environ["TA_MARKET_DB"] = str(Path(self._tmp.name) / "market.db")
        os.environ["TA_MARKET_CACHE"] = "1"
        from tradingagents.storage import market_store

        market_store.delete_market_data()

    def tearDown(self):
        for name, value in (
            ("TA_MARKET_DB", self._old_market_db),
            ("TA_MARKET_CACHE", self._old_market_cache),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_save_and_load_roundtrip(self):
        from tradingagents.storage.market_store import (
            covered_range,
            load_daily_bars,
            save_daily_bars,
        )

        written = save_daily_bars("600519", _frame(5), source="test")
        self.assertEqual(written, 5)
        loaded = load_daily_bars("600519", "2024-01-01", "2024-01-31")
        self.assertEqual(len(loaded), 5)
        self.assertEqual(str(loaded["Date"].iloc[0])[:10], "2024-01-02")
        self.assertAlmostEqual(float(loaded["Close"].iloc[-1]), 10.4)
        self.assertEqual(covered_range("600519"), ("2024-01-02", "2024-01-08"))

    def test_missing_ranges_only_covers_edges(self):
        from tradingagents.storage.market_store import (
            missing_ranges,
            save_daily_bars,
        )

        save_daily_bars("600519", _frame(5), source="test")
        self.assertEqual(
            missing_ranges("600519", "2024-01-01", "2024-01-31"),
            [
                ("2024-01-01", "2024-01-01"),
                ("2024-01-09", "2024-01-31"),
            ],
        )
        self.assertEqual(
            missing_ranges("600519", "2023-12-01", "2024-01-04"),
            [("2023-12-01", "2024-01-01")],
        )

    def test_market_data_fetches_only_once(self):
        from tradingagents.services.market_data import load_prices

        calls = []

        def fetch(code, start, end):
            calls.append((code, start, end))
            return CSV

        first = load_prices(
            "600519", "2024-01-02", "2024-01-04", fetch=fetch
        )
        second = load_prices(
            "600519", "2024-01-02", "2024-01-04", fetch=fetch
        )
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertEqual(len(calls), 1, "第二次应命中本地缓存，不再请求数据源")

    def test_market_data_falls_back_to_local_when_fetch_fails(self):
        from tradingagents.services.market_data import load_prices
        from tradingagents.storage.market_store import save_daily_bars

        save_daily_bars("600519", _frame(5), source="test")

        def broken(code, start, end):
            raise RuntimeError("network down")

        frame = load_prices(
            "600519", "2024-01-02", "2024-01-31", fetch=broken
        )
        self.assertEqual(len(frame), 5)
        self.assertTrue(frame.attrs.get("warnings"))

    def test_backtest_loader_uses_cache(self):
        from tradingagents.services import backtest

        with patch.object(
            backtest, "route_to_vendor", wraps=backtest.route_to_vendor
        ) as mocked:
            mocked.return_value = CSV
            first = backtest._load_prices("600519", "2024-01-02", "2024-01-04")
            second = backtest._load_prices("600519", "2024-01-02", "2024-01-04")
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertEqual(mocked.call_count, 1)


class DataQualityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("TA_MARKET_DB")
        os.environ["TA_MARKET_DB"] = str(Path(self._tmp.name) / "market.db")
        from tradingagents.storage import market_store

        market_store.delete_market_data()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TA_MARKET_DB", None)
        else:
            os.environ["TA_MARKET_DB"] = self._old

    def test_check_frame_flags_inconsistent_ohlc_and_extreme_return(self):
        from tradingagents.services.data_quality import check_frame

        frame = _frame(4)
        frame.loc[1, "High"] = 1.0  # 最高价低于收盘价
        frame.loc[2, "Close"] = frame.loc[1, "Close"] * 2  # 单日翻倍
        result = check_frame(frame)
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("ohlc_inconsistent", codes)
        self.assertIn("extreme_return", codes)
        self.assertFalse(result["healthy"])

    def test_quality_report_marks_missing_local_data(self):
        from tradingagents.services.data_quality import quality_report

        result = quality_report("600519")
        self.assertTrue(result["success"] if "success" in result else True)
        self.assertFalse(result["healthy"])
        self.assertIn(
            "no_local_data", {item["code"] for item in result["issues"]}
        )

    def test_quality_report_reads_saved_data(self):
        from tradingagents.services.data_quality import quality_report
        from tradingagents.storage.market_store import save_daily_bars

        save_daily_bars("600519", _frame(30), source="test")
        result = quality_report("600519", "2024-01-01", "2024-02-29")
        self.assertEqual(result["rows"], 30)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["last_date"], "2024-02-12")


class DataApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("TA_MARKET_DB")
        self._old_cache = os.environ.get("TA_MARKET_CACHE")
        os.environ["TA_MARKET_DB"] = str(Path(self._tmp.name) / "market.db")
        os.environ["TA_MARKET_CACHE"] = "1"
        self.client = TestClient(server.app)

    def tearDown(self):
        for name, value in (
            ("TA_MARKET_DB", self._old),
            ("TA_MARKET_CACHE", self._old_cache),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_status_and_sync_endpoints(self):
        with patch(
            "tradingagents.dataflows.interface.route_to_vendor",
            return_value=CSV,
        ):
            resp = self.client.post(
                "/api/data/sync",
                json={
                    "ticker": "600519",
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-10",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "600519")
        self.assertEqual(data["items"][0]["written"], 3)

        status = self.client.get("/api/data/status", params={"ticker": "600519"}).json()
        self.assertTrue(status["success"])
        self.assertEqual(status["cache"]["symbols"], 1)
        self.assertEqual(status["ticker"]["first_date"], "2024-01-02")

        quality = self.client.get(
            "/api/data/quality", params={"ticker": "600519"}
        ).json()
        self.assertTrue(quality["success"])
        self.assertEqual(quality["rows"], 3)


if __name__ == "__main__":
    unittest.main()
