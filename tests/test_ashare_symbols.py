"""
A 股代码归一化与市场判定的单元测试。

被测模块：tradingagents/dataflows/akshare_adapter.py

这些是数据层最容易出错、也最难靠肉眼发现问题的部分：
代码写法千变万化（600519 / 600519.SH / sh600519 / SH600519），
一旦归一化出错，后续所有行情、财务、新闻接口都会取错标的。

注意：resolve_stock_name 在无法从名称表解析时会走网络请求，
因此本文件只测试不依赖网络的纯函数分支。
"""

import unittest
from unittest.mock import patch

from tradingagents.dataflows.akshare_adapter import (
    is_a_share,
    is_beijing,
    is_shanghai,
    is_shenzhen,
    normalize_symbol,
    resolve_stock_name,
    to_ak_date,
    to_iso_date,
)


class SymbolNormalizationTests(unittest.TestCase):
    """代码归一化测试。"""

    def test_plain_six_digit_code_preserved(self):
        """6 位纯数字代码应保持不变。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("600519"), "600519")
            self.assertEqual(normalize_symbol("000001"), "000001")

    def test_exchange_prefix_stripped(self):
        """带交易所前缀的代码应剥离前缀。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("sh600519"), "600519")
            self.assertEqual(normalize_symbol("sz000001"), "000001")

    def test_non_a_share_symbol_untouched(self):
        """非 A 股代码不应被改动。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("NVDA"), "NVDA")
            self.assertEqual(normalize_symbol("AAPL"), "AAPL")

    def test_whitespace_trimmed(self):
        """前后空白应被清理。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("  600519  "), "600519")

    def test_dotted_exchange_prefix_stripped(self):
        """sh.600519 / bj.830799 这类带点的前缀也应被正确归一化。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("sh.600519"), "600519")
            self.assertEqual(normalize_symbol("sz.000001"), "000001")
            self.assertEqual(normalize_symbol("bj.830799"), "830799")

    def test_exchange_suffix_stripped(self):
        """600519.SH / 600519.SS 等后缀应被剥离。"""
        with patch("tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}):
            self.assertEqual(normalize_symbol("600519.SH"), "600519")
            self.assertEqual(normalize_symbol("600519.SS"), "600519")
            self.assertEqual(normalize_symbol("000001.SZ"), "000001")

    def test_non_cjk_symbol_does_not_hit_network(self):
        """非中文、非 A 股输入不应触发名称表网络请求。"""
        with patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names",
            side_effect=AssertionError("should not be called"),
        ):
            self.assertEqual(normalize_symbol("NVDA"), "NVDA")


class MarketDetectionTests(unittest.TestCase):
    """交易所判定测试。"""

    def setUp(self):
        # 隔离名称表，避免测试触网
        self._patcher = patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names", return_value={}
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_is_a_share(self):
        self.assertTrue(is_a_share("600519"))
        self.assertTrue(is_a_share("sh600519"))
        self.assertFalse(is_a_share("NVDA"))

    def test_is_a_share_rejects_b_shares(self):
        """沪深 B 股（200xxx / 900xxx）不是 A 股。"""
        self.assertFalse(is_a_share("200001"))
        self.assertFalse(is_a_share("900901"))

    def test_is_a_share_accepts_beijing(self):
        """北交所代码（4/8 开头）应被识别为 A 股。"""
        self.assertTrue(is_a_share("830799"))
        self.assertTrue(is_a_share("bj830799"))
        self.assertTrue(is_a_share("430047"))
        self.assertTrue(is_a_share("920002"))

    def test_non_cjk_detection_does_not_hit_network(self):
        with patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names",
            side_effect=AssertionError("should not be called"),
        ):
            self.assertFalse(is_a_share("NVDA"))
            self.assertFalse(is_shanghai("AAPL"))

    def test_is_shanghai(self):
        """沪市：60 开头（主板）与 68 开头（科创板）。"""
        self.assertTrue(is_shanghai("600519"))
        self.assertTrue(is_shanghai("688981"))
        self.assertFalse(is_shanghai("000001"))

    def test_is_shenzhen(self):
        """深市：00 开头（主板）与 30 开头（创业板）。"""
        self.assertTrue(is_shenzhen("000001"))
        self.assertTrue(is_shenzhen("300750"))
        self.assertFalse(is_shenzhen("600519"))

    def test_shanghai_and_shenzhen_are_exclusive(self):
        """沪深两市代码不应重叠。"""
        for code in ("600519", "688981", "000001", "300750"):
            self.assertNotEqual(is_shanghai(code), is_shenzhen(code))

    def test_is_beijing(self):
        """北交所代码应被识别，且不属于沪深。"""
        self.assertTrue(is_beijing("830799"))
        self.assertTrue(is_beijing("bj.430047"))
        self.assertFalse(is_beijing("600519"))
        self.assertFalse(is_shanghai("830799"))
        self.assertFalse(is_shenzhen("830799"))


class DateConversionTests(unittest.TestCase):
    """日期格式转换测试。"""

    def test_iso_to_akshare(self):
        self.assertEqual(to_ak_date("2026-01-15"), "20260115")

    def test_akshare_to_iso(self):
        self.assertEqual(to_iso_date("20260115"), "2026-01-15")

    def test_roundtrip(self):
        """两次转换应回到原点。"""
        original = "2026-01-15"
        self.assertEqual(to_iso_date(to_ak_date(original)), original)


class StockNameResolutionTests(unittest.TestCase):
    """股票名称解析测试（使用模拟名称表，不触网）。"""

    def test_resolve_by_chinese_name(self):
        """中文名应解析为对应代码。"""
        names = {"600519": "贵州茅台"}
        with patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names",
            return_value=names,
        ):
            self.assertEqual(resolve_stock_name("贵州茅台"), "600519")

    def test_resolve_unknown_name_returns_original(self):
        """无法解析的名称应原样返回，而不是抛异常。"""
        with patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names",
            return_value={},
        ):
            self.assertEqual(resolve_stock_name("不存在的股票"), "不存在的股票")

    def test_resolve_passthrough_for_code(self):
        """已经是代码的输入应直接返回。"""
        with patch(
            "tradingagents.dataflows.akshare_adapter._load_akshare_names",
            return_value={},
        ):
            self.assertEqual(resolve_stock_name("600519"), "600519")


if __name__ == "__main__":
    unittest.main()
