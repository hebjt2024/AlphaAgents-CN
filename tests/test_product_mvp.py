"""Fix 6 MVP 回归测试：自选股、意图解析、回测。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.graph.intent_parser import parse_intent
from tradingagents.services import backtest as backtest_module
from tradingagents.storage import add_watch, list_watchlist, remove_watch


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        fd, self._db_file = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self._db_file)
        self._old = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = self._db_file

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old
        for suffix in ("", "-wal", "-shm"):
            path = self._db_file + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_add_list_remove(self):
        add_watch("600519", "贵州茅台")
        add_watch("000001", "平安银行")
        items = list_watchlist()
        self.assertEqual({item["ticker"] for item in items}, {"600519", "000001"})

        self.assertTrue(remove_watch("600519"))
        self.assertEqual({item["ticker"] for item in list_watchlist()}, {"000001"})


class IntentParserTests(unittest.TestCase):
    def test_chinese_intent(self):
        intent = parse_intent("帮我短线看看贵州茅台的技术面和资金面")
        self.assertEqual(intent["ticker"], "600519")
        self.assertEqual(intent["horizons"], ["short"])
        self.assertIn("technical", intent["focus_areas"])
        self.assertIn("capital", intent["focus_areas"])

    def test_english_intent(self):
        intent = parse_intent("NVDA long term fundamentals")
        self.assertEqual(intent["ticker"], "NVDA")
        self.assertIn("long", intent["horizons"])
        self.assertIn("fundamental", intent["focus_areas"])

    def test_unknown_chinese_name_resolves_from_name_table(self):
        with patch(
            "tradingagents.graph.intent_parser._load_akshare_names",
            return_value={"苏垦农发": "601952"},
        ):
            intent = parse_intent("帮我看看苏垦农发短线技术面")
        self.assertEqual(intent["ticker"], "601952")
        self.assertIn("short", intent["horizons"])


class BacktestTests(unittest.TestCase):
    def test_ma_cross_backtest_with_mocked_prices(self):
        from datetime import date, timedelta

        rows = ["Date,Open,High,Low,Close,Volume"]
        price = 10.0
        current = date(2025, 1, 1)
        for _ in range(120):
            price += 0.1
            rows.append(
                f"{current.isoformat()}, {price}, {price + 0.2}, "
                f"{price - 0.2}, {price}, 1000"
            )
            current += timedelta(days=1)
        csv_text = "\n".join(rows)

        with patch.object(
            backtest_module, "route_to_vendor", return_value=csv_text
        ):
            result = backtest_module.run_backtest(
                "600519", "2025-01-01", "2025-04-30"
            )

        self.assertEqual(result["ticker"], "600519")
        self.assertGreater(result["bars"], 100)
        self.assertIn("total_return", result)
        self.assertIn("max_drawdown", result)
        self.assertIsInstance(result["equity_curve"], list)


if __name__ == "__main__":
    unittest.main()
