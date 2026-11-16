"""数据源韧性测试：熔断冷却 / 结果复用 / 直连备用源。

覆盖三类曾经导致"接口报错刷屏 + 分析缺数据"的根因：

1. 被限流后仍然反复重试，把 5 分钟的封禁拖成 40 分钟 → 熔断冷却；
2. 4 个分析师并发请求同一批数据 → 单次分析内结果复用；
3. 资金流/龙虎榜只有 AkShare 一个源 → 直连备用通道。
"""

import unittest
from unittest.mock import patch

from tradingagents.dataflows import interface, realtime, vendor_health
from tradingagents.dataflows.direct_sources import (
    _parse_tencent_line,
    get_em_capital_flow,
    get_em_dragon_tiger,
)


class VendorHealthTests(unittest.TestCase):
    def setUp(self):
        vendor_health.reset()

    def tearDown(self):
        vendor_health.reset()

    def test_rate_limit_opens_cooldown(self):
        self.assertTrue(vendor_health.is_available("akshare", "get_capital_flow"))
        vendor_health.mark_failure("akshare", "get_capital_flow", "rate_limited")
        self.assertFalse(vendor_health.is_available("akshare", "get_capital_flow"))
        self.assertGreater(
            vendor_health.cooldown_remaining("akshare", "get_capital_flow"), 0
        )

    def test_cooldown_is_scoped_per_method(self):
        """push2 被限流不代表 push2his 也挂了，按数据项隔离。"""
        vendor_health.mark_failure("akshare", "get_capital_flow", "rate_limited")
        self.assertFalse(vendor_health.is_available("akshare", "get_capital_flow"))
        self.assertTrue(vendor_health.is_available("akshare", "get_stock_data"))

    def test_success_clears_cooldown(self):
        vendor_health.mark_failure("akshare", "get_stock_data", "timeout")
        self.assertFalse(vendor_health.is_available("akshare", "get_stock_data"))
        vendor_health.mark_success("akshare", "get_stock_data")
        self.assertTrue(vendor_health.is_available("akshare", "get_stock_data"))

    def test_zero_cooldown_does_not_trip(self):
        with patch.object(vendor_health, "ERROR_COOLDOWN", 0.0):
            vendor_health.mark_failure("akshare", "get_news", "vendor_error")
        self.assertTrue(vendor_health.is_available("akshare", "get_news"))


class RouteResilienceTests(unittest.TestCase):
    def setUp(self):
        interface.reset_route_caches()

    def tearDown(self):
        interface.reset_route_caches()

    def _run(self, method, fn, *args, **kwargs):
        original = interface.VENDOR_METHODS[method]
        try:
            interface.VENDOR_METHODS[method] = fn
            return interface.route_to_vendor(method, *args, **kwargs)
        finally:
            interface.VENDOR_METHODS[method] = original

    def test_skips_vendor_in_cooldown_and_falls_through(self):
        calls = {"akshare": 0}

        def boom(*args, **kwargs):
            calls["akshare"] += 1
            raise interface.AkShareRateLimitError("限流 429")

        vendor_health.mark_failure("akshare", "get_capital_flow", "rate_limited")
        result = self._run(
            "get_capital_flow",
            {"akshare": boom, "eastmoney_direct": lambda *a, **k: "OK 直连数据"},
            "600519",
            "2024-01-02",
            20,
        )
        self.assertEqual(result, "OK 直连数据")
        self.assertEqual(calls["akshare"], 0)  # 冷却期内一次都没打

    def test_rate_limited_vendor_is_tripped(self):
        def boom(*args, **kwargs):
            raise interface.AkShareRateLimitError("限流 429")

        self._run("get_news", {"akshare": boom}, "600519", "2024-01-01", "2024-01-05")
        self.assertFalse(vendor_health.is_available("akshare", "get_news"))

    def test_successful_result_is_memoized(self):
        calls = {"n": 0}

        def impl(*args, **kwargs):
            calls["n"] += 1
            return f"DATA {calls['n']}"

        first = self._run("get_stock_data", {"akshare": impl}, "600519", "2024-01-01", "2024-01-05")
        second = self._run("get_stock_data", {"akshare": impl}, "600519", "2024-01-01", "2024-01-05")
        self.assertEqual(first, second)
        self.assertEqual(calls["n"], 1)

    def test_memo_key_includes_arguments(self):
        calls = {"n": 0}

        def impl(*args, **kwargs):
            calls["n"] += 1
            return f"DATA {calls['n']}"

        self._run("get_stock_data", {"akshare": impl}, "600519", "2024-01-01", "2024-01-05")
        self._run("get_stock_data", {"akshare": impl}, "600519", "2024-02-01", "2024-02-05")
        self.assertEqual(calls["n"], 2)

    def test_failure_is_not_memoized(self):
        calls = {"n": 0}

        def impl(*args, **kwargs):
            calls["n"] += 1
            return f"No data found for '{args[0]}'"

        self._run("get_stock_data", {"akshare": impl}, "600519", "2024-01-01", "2024-01-05")
        self._run("get_stock_data", {"akshare": impl}, "600519", "2024-01-01", "2024-01-05")
        self.assertEqual(calls["n"], 2)


class DirectSourceParsingTests(unittest.TestCase):
    def test_tencent_line_parsing(self):
        line = (
            'v_sh601118="1~海南橡胶~601118~5.31~5.45~5.41~513158~214047~299110'
            "~5.31~63~5.30~5290~5.29~1232~5.28~5519~5.27~1771~5.32~344~5.33~682"
            "~5.34~1138~5.35~2464~5.36~3162~~20261116135317~-0.14~-2.57~5.43"
            '~5.23~5.31/513158/27843~513158~27843~1.23~-573.96~~5.43~5.23~3.67'
            '~226.81~226.81~2.42~5.99~4.90~1.05";'
        )
        parsed = _parse_tencent_line(line)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["ticker"], "601118")
        self.assertEqual(parsed["name"], "海南橡胶")
        self.assertAlmostEqual(parsed["price"], 5.31)
        self.assertAlmostEqual(parsed["change_pct"], -2.57)

    def test_tencent_line_rejects_garbage(self):
        self.assertIsNone(_parse_tencent_line("not a quote line"))

    def test_capital_flow_formats_rows(self):
        payload = {
            "data": {
                "klines": [
                    "2026-11-13,1000000.0,-100.0,200.0,300000.0,700000.0,1.1,0.1,0.2,0.3,0.4,5.30,1.2",
                    "2026-11-12,-500000.0,100.0,200.0,-200000.0,-300000.0,-0.8,0.1,0.2,-0.1,-0.2,5.28,-0.5",
                ]
            }
        }

        class _Resp:
            def json(self):
                return payload

        with patch("tradingagents.dataflows.direct_sources._get", return_value=_Resp()):
            text = get_em_capital_flow("601118", "2026-11-13", 5)
        self.assertIn("Capital Flow data for 601118", text)
        self.assertIn("主力净流入-净额", text)
        self.assertIn("2026-11-13", text)

    def test_capital_flow_filters_future_rows(self):
        payload = {
            "data": {
                "klines": [
                    "2026-11-20,1000000.0,0,0,0,0,0,0,0,0,0,5.30,1.2",
                ]
            }
        }

        class _Resp:
            def json(self):
                return payload

        with patch("tradingagents.dataflows.direct_sources._get", return_value=_Resp()):
            text = get_em_capital_flow("601118", "2026-11-13", 5)
        self.assertIn("No capital flow data found", text)

    def test_dragon_tiger_formats_rows(self):
        payload = {
            "result": {
                "data": [
                    {
                        "TRADE_DATE": "2026-10-15 00:00:00",
                        "SECURITY_CODE": "601118",
                        "SECURITY_NAME_ABBR": "海南橡胶",
                        "CLOSE_PRICE": 6.2,
                        "CHANGE_RATE": -10.01,
                        "BILLBOARD_NET_AMT": 12101339.34,
                        "BILLBOARD_BUY_AMT": 183478182.9,
                        "BILLBOARD_SELL_AMT": 171376843.56,
                        "TURNOVERRATE": 4.45,
                        "EXPLANATION": "跌幅偏离值达到7%的前五只证券",
                        "D1_CLOSE_ADJCHRATE": 8.87,
                        "D5_CLOSE_ADJCHRATE": -1.13,
                        "D10_CLOSE_ADJCHRATE": -17.9,
                    }
                ]
            }
        }

        class _Resp:
            def json(self):
                return payload

        with patch("tradingagents.dataflows.direct_sources._get", return_value=_Resp()):
            text = get_em_dragon_tiger("601118", "2026-11-16", 90)
        self.assertIn("Dragon-Tiger (LHB) data for 601118", text)
        self.assertIn("龙虎榜净买额", text)
        self.assertIn("2026-10-15", text)


class RealtimeFallbackTests(unittest.TestCase):
    def test_falls_back_to_tencent_when_eastmoney_down(self):
        def _boom():
            raise RuntimeError("push2 断开连接")

        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_spot_snapshot",
            side_effect=_boom,
        ), patch(
            "tradingagents.dataflows.direct_sources.get_tencent_quotes",
            return_value={
                "601118": {"ticker": "601118", "price": 5.31, "source": "tencent"}
            },
        ):
            quotes = realtime.get_realtime_quotes(["601118"])
        self.assertEqual(quotes["601118"]["price"], 5.31)


if __name__ == "__main__":
    unittest.main()
