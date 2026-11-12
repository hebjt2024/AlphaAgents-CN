"""A 股交易规则模块的单元测试（纯函数，不访问网络）。"""

import unittest

from tradingagents.dataflows.ashare_rules import (
    build_ashare_context,
    contains_cjk,
    exchange_of,
    is_a_share_code,
    is_beijing,
    is_st,
    normalize_code,
    price_limit_pct,
    report_available_date,
    risk_warnings,
)


class NormalizeCodeTests(unittest.TestCase):
    def test_common_formats(self):
        self.assertEqual(normalize_code("600519"), "600519")
        self.assertEqual(normalize_code("  600519  "), "600519")
        self.assertEqual(normalize_code("sh600519"), "600519")
        self.assertEqual(normalize_code("SZ000001"), "000001")
        self.assertEqual(normalize_code("bj830799"), "830799")
        self.assertEqual(normalize_code("sh.600519"), "600519")
        self.assertEqual(normalize_code("600519.SH"), "600519")
        self.assertEqual(normalize_code("600519.SS"), "600519")
        self.assertEqual(normalize_code("000001.XSHE"), "000001")

    def test_non_a_share_returns_empty(self):
        self.assertEqual(normalize_code("NVDA"), "")
        self.assertEqual(normalize_code("0700.HK"), "")
        self.assertEqual(normalize_code(""), "")


class ExchangeTests(unittest.TestCase):
    def test_exchange(self):
        self.assertEqual(exchange_of("600519"), "sh")
        self.assertEqual(exchange_of("688981"), "sh")
        self.assertEqual(exchange_of("000001"), "sz")
        self.assertEqual(exchange_of("300750"), "sz")
        self.assertEqual(exchange_of("830799"), "bj")
        self.assertEqual(exchange_of("920002"), "bj")
        self.assertEqual(exchange_of("NVDA"), "")

    def test_is_beijing(self):
        self.assertTrue(is_beijing("830799"))
        self.assertTrue(is_beijing("bj.430047"))
        self.assertTrue(is_beijing("920002"))
        self.assertFalse(is_beijing("600519"))

    def test_b_share_is_not_a_share(self):
        self.assertFalse(is_a_share_code("200001"))  # 深市 B 股
        self.assertFalse(is_a_share_code("900901"))  # 沪市 B 股
        self.assertTrue(is_a_share_code("600519"))
        self.assertTrue(is_a_share_code("300750"))
        self.assertTrue(is_a_share_code("830799"))

    def test_contains_cjk(self):
        self.assertTrue(contains_cjk("贵州茅台"))
        self.assertFalse(contains_cjk("600519"))


class PriceLimitTests(unittest.TestCase):
    def test_board_limits(self):
        self.assertAlmostEqual(price_limit_pct("600519"), 0.10)
        self.assertAlmostEqual(price_limit_pct("300750"), 0.20)
        self.assertAlmostEqual(price_limit_pct("688981"), 0.20)
        self.assertAlmostEqual(price_limit_pct("830799"), 0.30)
        self.assertAlmostEqual(price_limit_pct("920002"), 0.30)

    def test_st_limit(self):
        self.assertTrue(is_st("*ST海航"))
        self.assertAlmostEqual(price_limit_pct("600519", "*ST测试"), 0.05)


class ReportAvailabilityTests(unittest.TestCase):
    def test_disclosure_deadlines(self):
        self.assertEqual(report_available_date("20231231"), "2024-04-30")
        self.assertEqual(report_available_date("20240331"), "2024-04-30")
        self.assertEqual(report_available_date("20240630"), "2024-08-31")
        self.assertEqual(report_available_date("20240930"), "2024-10-31")
        self.assertEqual(report_available_date(2023), "2024-04-30")

    def test_invalid_period(self):
        self.assertEqual(report_available_date(""), "")
        self.assertEqual(report_available_date("abc"), "")


class RiskAndContextTests(unittest.TestCase):
    def test_risk_warnings(self):
        warnings = risk_warnings("830799")
        self.assertTrue(any("北交所" in w for w in warnings))

        st_warnings = risk_warnings("600519", "*ST测试")
        self.assertTrue(any("ST" in w for w in st_warnings))

    def test_build_context_for_a_share(self):
        text = build_ashare_context("600519")
        self.assertIn("T+1", text)
        self.assertIn("涨跌幅限制", text)

    def test_build_context_skips_non_a_share(self):
        self.assertEqual(build_ashare_context("NVDA"), "")


if __name__ == "__main__":
    unittest.main()
