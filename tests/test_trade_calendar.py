"""A 股交易日历工具测试（不访问网络）。"""

import unittest
from unittest.mock import patch

from tradingagents.dataflows import trade_calendar

TRADING_DAYS = {"2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"}


class TradingDayTests(unittest.TestCase):
    def setUp(self):
        self._patcher = patch.object(
            trade_calendar, "_load_trade_dates", return_value=TRADING_DAYS
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_trading_day(self):
        self.assertTrue(trade_calendar.is_cn_trading_day("2024-01-03"))
        self.assertFalse(trade_calendar.is_cn_trading_day("2024-01-06"))

    def test_previous_trading_day(self):
        self.assertEqual(
            trade_calendar.previous_cn_trading_day("2024-01-08"),
            "2024-01-05",
        )


class WeekdayFallbackTests(unittest.TestCase):
    def setUp(self):
        self._patcher = patch.object(
            trade_calendar, "_load_trade_dates", return_value=set()
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_weekday_rule(self):
        # 2024-01-06 是周六，2024-01-08 是周一
        self.assertFalse(trade_calendar.is_cn_trading_day("2024-01-06"))
        self.assertTrue(trade_calendar.is_cn_trading_day("2024-01-08"))


class ValidateDateTests(unittest.TestCase):
    def setUp(self):
        self._patcher = patch.object(
            trade_calendar, "_load_trade_dates", return_value=TRADING_DAYS
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_a_share_non_trading_day_suggests_previous(self):
        ok, message, adjusted = trade_calendar.validate_analysis_date(
            "600519", "2024-01-06"
        )
        self.assertFalse(ok)
        self.assertEqual(adjusted, "2024-01-05")
        self.assertIn("不是 A 股交易日", message)

    def test_non_a_share_skips_calendar(self):
        ok, message, adjusted = trade_calendar.validate_analysis_date(
            "NVDA", "2024-01-06"
        )
        self.assertTrue(ok)
        self.assertEqual(adjusted, "2024-01-06")

    def test_invalid_date_format(self):
        ok, message, adjusted = trade_calendar.validate_analysis_date(
            "600519", "2024/01/06"
        )
        self.assertFalse(ok)
        self.assertIsNone(adjusted)

    def test_future_date_rejected_for_all_markets(self):
        for ticker in ("600519", "NVDA"):
            ok, message, adjusted = trade_calendar.validate_analysis_date(
                ticker, "2999-01-01"
            )
            self.assertFalse(ok)
            self.assertIn("未来日期", message)
            self.assertEqual(adjusted, trade_calendar.date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
