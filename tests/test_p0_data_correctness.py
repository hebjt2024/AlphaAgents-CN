"""P0 数据正确性回归测试（不访问网络）。

覆盖数据获取层的六个关键缺陷：
  FIX 1 腾讯备用源的数据口径污染（不复权 + 成交额冒充成交量）
  FIX 2 单日指标 NaN 以字符串 "nan" 泄漏给 LLM
  FIX 3 交易日历抓取失败被当成"有效日历"缓存 24 小时
  FIX 4 离线演示数据靠字符串嗅探识别（误伤真实数据 / 漏判伪造数据）
  FIX 5 "没有数据"被改写成笼统的基础设施错误
  FIX 6 资金流向类数据项没有备选数据源却伪装成"供应商链耗尽"
  FIX 7 AkShare 节流非线程安全 + 限流异常从不抛出
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import (
    akshare_adapter,
    cache_provider,
    interface,
    stockstats_utils,
    trade_calendar,
)
from tradingagents.dataflows.config import get_config, set_config

# ============================================================================
# FIX 1：腾讯备用源（不复权 + 成交额冒充成交量）
# ============================================================================


def _tencent_frame(with_volume: bool = True) -> pd.DataFrame:
    """构造一份腾讯源风格的返回数据。

    腾讯接口返回：date/open/close/high/low/volume(成交量)/turnover/amount(成交额)。
    这里 amount 故意取一个与 volume 差几个数量级的值，
    以便断言"成交额绝不能写进成交量列"。
    """
    data = {
        "date": ["2024-01-02", "2024-01-03"],
        "open": [10.0, 10.2],
        "close": [10.5, 10.4],
        "high": [10.6, 10.5],
        "low": [9.9, 10.1],
        "amount": [1_000_000_000.0, 1_200_000_000.0],  # 成交额（元）
    }
    if with_volume:
        data["volume"] = [100_000.0, 120_000.0]  # 成交量（股/手）
    return pd.DataFrame(data)


class TencentFallbackTests(unittest.TestCase):
    """FIX 1：降级到腾讯源时，数据口径必须被如实标记。"""

    def _fallback_frame(self, with_volume: bool = True) -> pd.DataFrame:
        with (
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_daily",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist_tx",
                return_value=_tencent_frame(with_volume),
            ),
        ):
            return akshare_adapter.get_stock_hist_with_fallback(
                "600519", "20240101", "20240110"
            )

    def test_amount_is_not_written_into_volume_column(self):
        """成交额（单位：元）绝不能覆盖成交量列。"""
        out = self._fallback_frame()
        self.assertFalse(out.empty)
        self.assertIn("成交额", out.columns)
        self.assertEqual(out["成交额"].tolist(), [1_000_000_000.0, 1_200_000_000.0])
        # 成交量必须是真实成交量，而不是成交额
        self.assertEqual(out["成交量"].tolist(), [100_000.0, 120_000.0])

    def test_missing_volume_is_marked_in_note(self):
        """腾讯源没有成交量字段时，宁可缺失也不能用成交额冒充。"""
        out = self._fallback_frame(with_volume=False)
        self.assertFalse(out.empty)
        self.assertNotIn("成交量", out.columns)
        notes = akshare_adapter._get_source_notes(out)
        self.assertIn("成交量", notes)

    def test_unadjusted_note_is_attached_to_frame(self):
        """腾讯源为不复权数据，必须在数据上打标记。"""
        out = self._fallback_frame()
        notes = akshare_adapter._get_source_notes(out)
        self.assertIn("未复权", notes)
        self.assertIn("前复权", notes)
        self.assertTrue(
            akshare_adapter._UNADJUSTED_SOURCE_NOTE.startswith("# 注意:")
        )

    def test_stock_output_header_contains_source_note(self):
        """最终给 LLM 的文本头部必须包含口径提示。"""
        with (
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_daily",
                return_value=pd.DataFrame(),
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_zh_a_hist_tx",
                return_value=_tencent_frame(),
            ),
            patch.object(
                akshare_adapter,
                "_missing_trading_days_note",
                return_value="",
            ),
        ):
            output = akshare_adapter.get_akshare_stock(
                "600519", "2024-01-01", "2024-01-10"
            )
        self.assertIn("未复权", output)
        self.assertIn("成交额", output)


# ============================================================================
# FIX 2：单日指标 NaN 泄漏为字符串 "nan"
# ============================================================================


class SingleDateIndicatorNanTests(unittest.TestCase):
    """FIX 2：单日指标查询必须把 NaN 转成明确的缺失标记。"""

    def test_nan_indicator_is_not_returned_as_nan_string(self):
        original_cache_dir = get_config().get("data_cache_dir")
        with tempfile.TemporaryDirectory() as tmpdir:
            set_config({"data_cache_dir": tmpdir})
            try:
                today = pd.Timestamp.today()
                start = today - pd.DateOffset(years=15)
                symbol = "600519.SS"
                data_file = Path(tmpdir) / (
                    f"{symbol}-YFin-data-"
                    f"{start.strftime('%Y-%m-%d')}-{today.strftime('%Y-%m-%d')}.csv"
                )
                # 只有 3 行数据 → 首行的 rsi 必然是 NaN
                pd.DataFrame(
                    {
                        "Date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                        "Open": [10.0, 10.2, 10.1],
                        "High": [10.6, 10.5, 10.4],
                        "Low": [9.9, 10.0, 9.8],
                        "Close": [10.5, 10.4, 10.3],
                        "Volume": [1000.0, 1100.0, 1200.0],
                    }
                ).to_csv(data_file, index=False)

                result = stockstats_utils.StockstatsUtils.get_stock_stats(
                    "600519", "rsi", "2024-01-02"
                )
            finally:
                set_config({"data_cache_dir": original_cache_dir})

        self.assertNotEqual(str(result).strip().lower(), "nan")
        self.assertIn("N/A", str(result))


# ============================================================================
# FIX 3：交易日历抓取失败不得被缓存成"有效日历"
# ============================================================================


class TradeCalendarDegradationTests(unittest.TestCase):
    """FIX 3：区分"日历不可用"与"没有节假日"。"""

    def setUp(self):
        self._saved = (
            trade_calendar._cached_dates,
            trade_calendar._cache_ts,
            trade_calendar._calendar_unavailable,
            trade_calendar._calendar_retry_ts,
        )
        trade_calendar._cached_dates = None
        trade_calendar._cache_ts = 0.0
        trade_calendar._calendar_unavailable = False
        trade_calendar._calendar_retry_ts = 0.0

    def tearDown(self):
        (
            trade_calendar._cached_dates,
            trade_calendar._cache_ts,
            trade_calendar._calendar_unavailable,
            trade_calendar._calendar_retry_ts,
        ) = self._saved

    def test_failed_fetch_is_not_cached_as_valid_calendar(self):
        with patch.object(
            akshare_adapter.ak,
            "tool_trade_date_hist_sina",
            side_effect=RuntimeError("network down"),
        ):
            dates = trade_calendar._load_trade_dates()

        self.assertEqual(dates, set())
        # 关键：不能把空集合缓存成"有效交易日历"
        self.assertIsNone(trade_calendar._cached_dates)
        self.assertTrue(trade_calendar.calendar_degraded())

    def test_successful_fetch_clears_degraded_state(self):
        with patch.object(
            akshare_adapter.ak,
            "tool_trade_date_hist_sina",
            side_effect=RuntimeError("network down"),
        ):
            trade_calendar._load_trade_dates()
        self.assertTrue(trade_calendar.calendar_degraded())

        good = pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03"]})
        # 取消降级重试的退避间隔，模拟"稍后重试成功"
        with (
            patch.object(trade_calendar, "_CALENDAR_RETRY_INTERVAL", 0.0),
            patch.object(
                akshare_adapter.ak,
                "tool_trade_date_hist_sina",
                return_value=good,
            ),
        ):
            dates = trade_calendar._load_trade_dates()

        self.assertIn("2024-01-02", dates)
        self.assertFalse(trade_calendar.calendar_degraded())

    def test_validate_analysis_date_surfaces_degradation(self):
        with patch.object(
            akshare_adapter.ak,
            "tool_trade_date_hist_sina",
            side_effect=RuntimeError("network down"),
        ):
            ok, message, _ = trade_calendar.validate_analysis_date(
                "600519", "2024-01-03"
            )
        self.assertTrue(ok)
        self.assertIn("交易日历不可用", message)


# ============================================================================
# FIX 4：离线演示数据的识别机制
# ============================================================================


class SyntheticPayloadTests(unittest.TestCase):
    """FIX 4：用显式标记替代脆弱的字符串嗅探。"""

    def test_cache_provider_returns_marked_payloads(self):
        payload = cache_provider.get_cached_stock_data(
            "600519", "2024-01-01", "2024-01-05"
        )
        self.assertIsInstance(payload, cache_provider.SyntheticPayload)
        self.assertTrue(interface._is_synthetic_payload(payload))

    def test_cache_vendor_payload_is_refused_by_default(self):
        """来自 "cache" 供应商的载荷按定义就是伪造数据，默认必须拒绝。"""
        original = interface.VENDOR_METHODS["get_indicators"]
        try:
            interface.VENDOR_METHODS["get_indicators"] = {
                "cache": lambda *args, **kwargs: "Date,Close\n2024-01-02,10.5\n"
            }
            set_config({"allow_synthetic_data": False})
            result = interface.route_to_vendor(
                "get_indicators", "600519", "rsi", "2024-01-01", 5
            )
        finally:
            interface.VENDOR_METHODS["get_indicators"] = original
            set_config({"allow_synthetic_data": False})

        self.assertNotIn("2024-01-02,10.5", result)
        self.assertTrue(interface.is_error_result(result)[0])

    def test_synthetic_payload_from_real_vendor_raises(self):
        """真实供应商返回合成载荷时必须显式报错（fail-closed）。"""
        original = interface.VENDOR_METHODS["get_indicators"]
        try:
            # 降级链现在以 data_vendors 配置为白名单，因此这里显式把
            # get_indicators 的供应商指向被测的 mock 实现。
            interface.VENDOR_METHODS["get_indicators"] = {
                "yfinance": lambda *args, **kwargs: cache_provider.SyntheticPayload(
                    "Date,Close\n2024-01-02,10.5\n"
                )
            }
            set_config({"allow_synthetic_data": False})
            with (
                patch.object(interface, "get_vendor", return_value="yfinance"),
                self.assertRaises(interface.SyntheticDataRefusedError),
            ):
                interface.route_to_vendor(
                    "get_indicators", "600519", "rsi", "2024-01-01", 5
                )
        finally:
            interface.VENDOR_METHODS["get_indicators"] = original
            set_config({"allow_synthetic_data": False})

    def test_legitimate_payload_is_not_rejected(self):
        """真实数据里含 "(cached)" 子串也不能被误判为伪造数据。"""
        original = interface.VENDOR_METHODS["get_indicators"]
        payload = "# report (cached)\nDate,Close\n2024-01-02,10.5\n"
        try:
            interface.VENDOR_METHODS["get_indicators"] = {
                "yfinance": lambda *args, **kwargs: payload
            }
            set_config({"allow_synthetic_data": False})
            with patch.object(interface, "get_vendor", return_value="yfinance"):
                result = interface.route_to_vendor(
                    "get_indicators", "600519", "rsi", "2024-01-01", 5
                )
        finally:
            interface.VENDOR_METHODS["get_indicators"] = original
            set_config({"allow_synthetic_data": False})

        self.assertEqual(result, payload)
        self.assertFalse(interface.is_error_result(result)[0])


# ============================================================================
# FIX 5：失败语义（"没有数据" vs "数据源故障"）
# ============================================================================


class VendorFailureSemanticsTests(unittest.TestCase):
    """FIX 5：失败时必须保留数据源给出的原始原因。"""

    def test_no_data_is_preserved_in_failure_message(self):
        original = interface.VENDOR_METHODS["get_stock_data"]
        message = (
            "No data found for symbol '600519' between 2024-01-01 and 2024-01-05"
        )
        try:
            interface.VENDOR_METHODS["get_stock_data"] = {
                "akshare": lambda *args, **kwargs: message
            }
            result = interface.route_to_vendor(
                "get_stock_data", "600519", "2024-01-01", "2024-01-05"
            )
        finally:
            interface.VENDOR_METHODS["get_stock_data"] = original

        self.assertIn("No data found for symbol '600519'", result)
        self.assertIn("未获取到", result)
        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_NO_DATA)

    def test_vendor_error_is_distinguished_from_no_data(self):
        original = interface.VENDOR_METHODS["get_stock_data"]

        def _boom(*args, **kwargs):
            raise RuntimeError("connection reset by peer")

        try:
            interface.VENDOR_METHODS["get_stock_data"] = {"akshare": _boom}
            result = interface.route_to_vendor(
                "get_stock_data", "600519", "2024-01-01", "2024-01-05"
            )
        finally:
            interface.VENDOR_METHODS["get_stock_data"] = original

        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_VENDOR_ERROR)
        self.assertIn("connection reset by peer", result)

    def test_is_error_result_classifies_reasons(self):
        cases = {
            "No data found for symbol 'X'": interface.REASON_NO_DATA,
            "Error: upstream failed": interface.REASON_VENDOR_ERROR,
            "龙虎榜接口不可用（缺少接口）": interface.REASON_UNAVAILABLE,
            "[不适用] 'AAPL' 不是 A 股标的": interface.REASON_NOT_APPLICABLE,
            "# Stock data for 600519": "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                failed, reason = interface.is_error_result(text)
                self.assertEqual(failed, bool(expected))
                self.assertEqual(reason, expected)


# ============================================================================
# FIX 6：资金流向类数据项没有备选数据源
# ============================================================================


class CapitalFlowFallbackTests(unittest.TestCase):
    """FIX 6：如实说明"没有可用数据源"，并正确识别"不适用"。"""

    def test_capital_flow_methods_have_no_fallback_vendor(self):
        for method in ("get_capital_flow", "get_northbound_flow", "get_dragon_tiger"):
            with self.subTest(method=method):
                self.assertIn(method, interface.NO_FALLBACK_METHODS)
                self.assertEqual(
                    list(interface.VENDOR_METHODS[method].keys()), ["akshare"]
                )

    def test_unavailable_message_is_explicit(self):
        original = interface.VENDOR_METHODS["get_capital_flow"]

        def _boom(*args, **kwargs):
            raise RuntimeError("akshare 接口不可用")

        try:
            interface.VENDOR_METHODS["get_capital_flow"] = {"akshare": _boom}
            result = interface.route_to_vendor(
                "get_capital_flow", "600519", "2024-01-02", 20
            )
        finally:
            interface.VENDOR_METHODS["get_capital_flow"] = original

        self.assertIn("该数据项当前没有可用数据源", result)
        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_UNAVAILABLE)

    def test_non_a_share_message_is_not_treated_as_data(self):
        """非 A 股标的的提示语不能被当成"成功取到数据"。"""
        result = akshare_adapter.get_akshare_capital_flow("AAPL")
        self.assertTrue(result.startswith(akshare_adapter.NOT_APPLICABLE_MARKER))
        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_NOT_APPLICABLE)

    def test_non_a_share_dragon_tiger_message_is_marked(self):
        result = akshare_adapter.get_akshare_dragon_tiger("AAPL")
        self.assertTrue(result.startswith(akshare_adapter.NOT_APPLICABLE_MARKER))
        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_NOT_APPLICABLE)


# ============================================================================
# FIX 7：节流线程安全 + 限流异常
# ============================================================================


class ThrottleAndRateLimitTests(unittest.TestCase):
    """FIX 7：并发请求必须真的被节流；限流必须能被识别。"""

    def test_throttle_serialises_parallel_requests(self):
        original_interval = akshare_adapter.AKSHARE_REQUEST_INTERVAL
        akshare_adapter.AKSHARE_REQUEST_INTERVAL = 0.05
        try:
            akshare_adapter._last_request_time = 0.0
            start = time.time()
            threads = [
                threading.Thread(target=akshare_adapter._throttle_request)
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            elapsed = time.time() - start
        finally:
            akshare_adapter.AKSHARE_REQUEST_INTERVAL = original_interval
            akshare_adapter._last_request_time = 0.0

        # 4 个并发请求必须被串行化，总耗时至少 3 个间隔
        self.assertGreaterEqual(elapsed, 0.15)

    def test_rate_limit_error_is_raised_after_retries(self):
        calls = {"n": 0}

        def _limited():
            calls["n"] += 1
            raise RuntimeError("HTTP 429 Too Many Requests")

        with self.assertRaises(akshare_adapter.AkShareRateLimitError):
            akshare_adapter.retry_with_backoff(
                _limited, max_retries=1, base_delay=0.0, max_delay=0.0
            )
        self.assertEqual(calls["n"], 2)

    def test_non_rate_limit_error_is_not_converted(self):
        def _broken():
            raise ValueError("bad payload")

        with self.assertRaises(ValueError):
            akshare_adapter.retry_with_backoff(
                _broken, max_retries=1, base_delay=0.0, max_delay=0.0
            )

    def test_router_distinguishes_rate_limit_from_hard_error(self):
        """限流异常必须能被路由层识别为 rate_limited（而不是普通故障）。"""
        original = interface.VENDOR_METHODS["get_capital_flow"]

        def _limited(*args, **kwargs):
            raise akshare_adapter.AkShareRateLimitError(
                "AkShare API 连续 3 次触发限流：HTTP 429"
            )

        try:
            interface.VENDOR_METHODS["get_capital_flow"] = {"akshare": _limited}
            result = interface.route_to_vendor(
                "get_capital_flow", "600519", "2024-01-02", 20
            )
        finally:
            interface.VENDOR_METHODS["get_capital_flow"] = original

        failed, reason = interface.is_error_result(result)
        self.assertTrue(failed)
        self.assertEqual(reason, interface.REASON_RATE_LIMITED)


if __name__ == "__main__":
    unittest.main()
