"""回归测试：启动不得自动跑分析；只做 A 股；数据源配置即白名单。

背景（用户实测反馈）：

1. 打开 localhost:8000 就看到系统在自动分析贵州茅台。
   根因是 WebUI 启动时无条件拉起调度线程，且任务队列启动时会
   把数据库里遗留的 queued 任务重新排队执行 —— 用户只是打开页面，
   系统却自己开始干活。

2. 后台日志充斥 yfinance 报错（YFRateLimitError / Too Many Requests），
   还出现了 NVDA / AAPL 的资料请求。
   根因是降级链会把**未配置**的供应商（yfinance / alpha_vantage）
   无条件追加到末尾，于是 A 股取数失败后仍会去打 Yahoo；
   同时 AnalyzeRequest 的默认值是 company="NVDA"、date="2024-05-10"，
   任何空 body 请求都会静默分析 NVDA。
"""

import os
import unittest
from unittest.mock import patch

from tradingagents.services.job_queue import JobQueue


class StartupDoesNotAutoRunTests(unittest.TestCase):
    """启动时不得自动执行任何分析。"""

    def test_scheduler_is_not_started_by_default(self):
        """未配置 SCHEDULE_ENABLED 时，不应启动调度线程。"""
        from webui.backend import server

        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(server, "start_scheduler") as start,
        ):
            os.environ.pop("SCHEDULE_ENABLED", None)
            import asyncio

            async def _run():
                async with server.lifespan(server.create_app()):
                    return None

            asyncio.run(_run())
        start.assert_not_called()

    def test_scheduler_starts_when_explicitly_enabled(self):
        from webui.backend import server

        with (
            patch.dict(os.environ, {"SCHEDULE_ENABLED": "1"}, clear=False),
            patch.object(server, "start_scheduler") as start,
            patch.object(server, "stop_scheduler"),
        ):

            async def _run():
                async with server.lifespan(server.create_app()):
                    return None

            import asyncio

            asyncio.run(_run())
        start.assert_called_once()

    def test_job_queue_does_not_recover_pending_by_default(self):
        """队列默认不恢复历史排队任务（重启不应自动开始干活）。"""
        queue = JobQueue(worker_count=1, name="test-no-recover")
        self.assertFalse(queue.recover_pending)

    def test_job_queue_can_opt_in_to_recovery(self):
        queue = JobQueue(
            worker_count=1, name="test-recover", recover_pending=True
        )
        self.assertTrue(queue.recover_pending)


class AShareOnlyTests(unittest.TestCase):
    """只做 A 股：非 A 股标的必须被拒绝，且不会触达任何数据源。"""

    def setUp(self):
        from tradingagents.dataflows import config as df_config

        self._config = df_config

    def test_non_a_share_is_rejected_before_fetching(self):
        from tradingagents.dataflows import interface

        calls: list[str] = []
        original = interface.VENDOR_METHODS.get("get_stock_data")
        try:
            interface.VENDOR_METHODS["get_stock_data"] = {
                "akshare": lambda *a, **k: calls.append("akshare") or "x",
                "yfinance": lambda *a, **k: calls.append("yfinance") or "y",
            }
            with patch.dict(os.environ, {"A_SHARE_ONLY": "1"}, clear=False):
                result = interface.route_to_vendor(
                    "get_stock_data", "NVDA", "2024-01-01", "2024-12-31"
                )
        finally:
            if original is not None:
                interface.VENDOR_METHODS["get_stock_data"] = original

        # 应当在碰任何供应商之前就返回
        self.assertEqual(calls, [])
        self.assertIn("已拒绝非 A 股标的", result)
        self.assertIn("NVDA", result)
        self.assertTrue(interface.is_error_result(result)[0])

    def test_a_share_symbols_are_allowed(self):
        from tradingagents.dataflows import interface

        for symbol in ("600519", "sh600519", "600519.SH", "贵州茅台", "000001"):
            self.assertTrue(
                interface._looks_like_a_share(symbol), symbol
            )

    def test_non_a_share_symbols_are_detected(self):
        from tradingagents.dataflows import interface

        for symbol in ("NVDA", "AAPL", "0700.HK", "BRK.B"):
            self.assertFalse(
                interface._looks_like_a_share(symbol), symbol
            )

    def test_disable_a_share_only_restores_multi_market(self):
        from tradingagents.dataflows import interface

        with patch.dict(os.environ, {"A_SHARE_ONLY": "0"}, clear=False):
            self.assertFalse(interface._is_a_share_only())


class VendorWhitelistTests(unittest.TestCase):
    """data_vendors 即白名单：未配置的供应商不得被自动追加。"""

    def test_unconfigured_vendor_is_not_appended(self):
        from tradingagents.dataflows import interface

        calls: list[str] = []

        original = interface.VENDOR_METHODS.get("get_stock_data")
        try:
            interface.VENDOR_METHODS["get_stock_data"] = {
                "akshare": lambda *a, **k: calls.append("akshare")
                or "No data found for symbol",
                "yfinance": lambda *a, **k: calls.append("yfinance")
                or "should-not-be-called",
            }
            with (
                patch.object(
                    interface, "get_vendor", return_value="akshare"
                ),
                patch.dict(
                    os.environ, {"A_SHARE_ONLY": "0"}, clear=False
                ),
                patch.object(
                    interface,
                    "get_config",
                    return_value={
                        "allow_unconfigured_vendors": False,
                        "allow_synthetic_data": False,
                    },
                ),
            ):
                interface.route_to_vendor(
                    "get_stock_data", "600519", "2024-01-01", "2024-02-01"
                )
        finally:
            if original is not None:
                interface.VENDOR_METHODS["get_stock_data"] = original

        self.assertIn("akshare", calls)
        self.assertNotIn("yfinance", calls)

    def test_opt_in_appends_unconfigured_vendor(self):
        from tradingagents.dataflows import interface

        calls: list[str] = []
        original = interface.VENDOR_METHODS.get("get_stock_data")
        try:
            interface.VENDOR_METHODS["get_stock_data"] = {
                "akshare": lambda *a, **k: calls.append("akshare")
                or "No data found for symbol",
                "yfinance": lambda *a, **k: calls.append("yfinance")
                or "fallback-data",
            }
            with (
                patch.object(
                    interface, "get_vendor", return_value="akshare"
                ),
                patch.dict(
                    os.environ, {"A_SHARE_ONLY": "0"}, clear=False
                ),
                patch.object(
                    interface,
                    "get_config",
                    return_value={
                        "allow_unconfigured_vendors": True,
                        "allow_synthetic_data": False,
                    },
                ),
            ):
                result = interface.route_to_vendor(
                    "get_stock_data", "600519", "2024-01-01", "2024-02-01"
                )
        finally:
            if original is not None:
                interface.VENDOR_METHODS["get_stock_data"] = original

        self.assertIn("yfinance", calls)
        self.assertEqual(result, "fallback-data")


if __name__ == "__main__":
    unittest.main()
