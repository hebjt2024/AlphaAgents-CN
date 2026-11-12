"""WebUI 新增 API（资金流 / 北向 / 回测 / 鉴权预检）测试。"""

import os
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server

CAPITAL_FLOW_TEXT = """# Capital Flow data for 600519 (A-share main capital flow)
# 最近2个交易日主力净流入合计: 3.00 亿元
# Unit: CNY.

日期,收盘价,涨跌幅,主力净流入-净额,主力净流入-净占比
2024-01-05,1700.0,1.2,500000000.0,3.5
2024-01-04,1680.0,-0.5,-200000000.0,-1.2
"""

NORTHBOUND_TEXT = """# Northbound Capital Flow (沪深股通)
# 最近2个交易日北向净流入合计: 3.00 亿元

日期,当日成交净买额,当日资金流入
2026-11-10,5.0,6.0
2026-11-07,-2.0,-1.0
"""


class MarketApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_capital_flow_endpoint(self):
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_capital_flow",
            return_value=CAPITAL_FLOW_TEXT,
        ):
            resp = self.client.get(
                "/api/market/capital-flow", params={"ticker": "600519", "days": 10}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["items"]), 2)
        # 图表需要旧→新
        self.assertEqual(data["items"][0]["date"], "2024-01-04")
        self.assertEqual(data["items"][1]["main_net"], 500000000.0)

    def test_capital_flow_failure_message(self):
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_capital_flow",
            return_value="No capital flow data found for '600519'",
        ):
            resp = self.client.get(
                "/api/market/capital-flow", params={"ticker": "600519"}
            )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("No capital flow", data["message"])

    def test_northbound_endpoint(self):
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_northbound_flow",
            return_value=NORTHBOUND_TEXT,
        ):
            resp = self.client.get("/api/market/northbound", params={"days": 5})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][1]["net"], 5.0)

    def test_analyze_requires_api_key(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            # 避免测试机上的 .env 真实密钥影响预检结果
            patch.object(server, "load_dotenv", lambda *a, **k: None),
            patch.object(
                server,
                "validate_analysis_date",
                return_value=(True, "", "2024-01-02"),
            ),
        ):
            resp = self.client.post(
                "/api/analyze",
                json={
                    "company": "600519",
                    "date": "2024-01-02",
                    "llm_provider": "anthropic",
                    "deep_think_llm": "MiniMax-M3",
                    "quick_think_llm": "MiniMax-M2.5",
                },
            )
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("API Key", data["message"])

    def test_kline_range_and_end_date(self):
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2024-01-08", "2024-01-09", "2024-01-10"]
                ),
                "Open": [10.0, 11.0, 12.0],
                "High": [11.0, 12.0, 13.0],
                "Low": [9.0, 10.0, 11.0],
                "Close": [10.5, 11.5, 12.5],
                "Volume": [100, 200, 300],
            }
        )
        with patch.object(
            server, "_load_prices", return_value=frame
        ) as mocked:
            resp = self.client.get(
                "/api/market/kline",
                params={
                    "ticker": "600519",
                    "range": "all",
                    "end_date": "2024-01-10",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["range"], "all")
        self.assertEqual(data["bars"], 3)
        # 历史分析时 K 线必须截止到分析日
        self.assertEqual(mocked.call_args[0][2], "2024-01-10")

    def test_test_llm_endpoint(self):
        class FakeLLM:
            def invoke(self, *args, **kwargs):
                return type("R", (), {"content": "OK"})()

        class FakeClient:
            def get_llm(self):
                return FakeLLM()

        with (
            patch(
                "tradingagents.llm_clients.auth.ensure_provider_credentials",
                return_value="key",
            ),
            patch(
                "tradingagents.llm_clients.factory.create_llm_client",
                return_value=FakeClient(),
            ),
        ):
            resp = self.client.post(
                "/api/test-llm",
                json={
                    "llm_provider": "anthropic",
                    "model": "test-model",
                    "backend_url": "https://api.minimax.cn/anthropic",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("连接成功", data["message"])

    def test_analyze_request_accepts_strategy_selection(self):
        req = server.AnalyzeRequest(
            strategies=["bull_trend", "volume_breakout"]
        )
        self.assertEqual(
            req.strategies, ["bull_trend", "volume_breakout"]
        )

    def test_strategy_backtest_endpoint(self):
        fake = {
            "success": True,
            "ticker": "600519",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "bars": 200,
            "benchmark_return": 0.1,
            "strategies": [],
        }
        with patch(
            "tradingagents.services.strategy_backtest.run_strategy_backtest",
            return_value=fake,
        ):
            resp = self.client.get(
                "/api/strategy-backtest",
                params={
                    "ticker": "600519",
                    "end_date": "2024-12-31",
                    "strategies": "bull_trend",
                    "days": 120,
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ticker"], "600519")

    def test_strategies_endpoint(self):
        fake = {
            "success": True,
            "ticker": "600519",
            "as_of": "2026-01-02",
            "bars": 120,
            "price": {"close": 10.0},
            "summary": {"consensus": "偏多", "triggered": 1},
            "signals": [],
        }
        with patch(
            "tradingagents.strategies.scanner.scan_all_strategies",
            return_value=fake,
        ):
            resp = self.client.get(
                "/api/strategies",
                params={"ticker": "600519", "end_date": "2026-01-02"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ticker"], "600519")

    def test_strategies_watchlist_endpoint(self):
        fake_items = [
            {
                "ticker": "600519",
                "consensus": "偏多",
                "strength": 80,
                "triggered": 2,
            }
        ]
        with (
            patch.object(server, "list_watchlist", return_value=[{"ticker": "600519"}]),
            patch(
                "tradingagents.strategies.scanner.scan_watchlist",
                return_value=fake_items,
            ),
        ):
            resp = self.client.get("/api/strategies/watchlist")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "600519")

    def test_analyze_returns_job_id(self):
        """分析任务改为走有界队列，job_id 由队列分配。"""

        class _FakeQueue:
            def __init__(self):
                self.submitted = []

            def active_count(self):
                return 0

            def pending_count(self):
                return 0

            def submit(self, **kwargs):
                self.submitted.append(kwargs)
                return "job123"

        fake_queue = _FakeQueue()
        with (
            patch.object(
                server,
                "validate_analysis_date",
                return_value=(True, "", "2026-11-11"),
            ),
            patch(
                "tradingagents.llm_clients.auth.ensure_provider_credentials"
            ),
            patch.object(server, "ensure_job_queue", return_value=fake_queue),
        ):
            resp = self.client.post(
                "/api/analyze",
                json={"company": "600519", "date": "2026-11-11"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["job_id"], "job123")
        # 任务被提交到队列，而不是直接起线程
        self.assertEqual(len(fake_queue.submitted), 1)
        self.assertEqual(fake_queue.submitted[0]["company"], "600519")
        self.assertEqual(fake_queue.submitted[0]["kind"], "analysis")

    def test_job_list_detail_result_endpoints(self):
        item = {
            "job_id": "job1",
            "company": "600519",
            "trade_date": "2026-11-11",
            "status": "completed",
            "result": {"decision": "BUY"},
        }
        with patch.object(server, "list_jobs", return_value=[item]):
            resp = self.client.get("/api/jobs")
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["items"][0]["job_id"], "job1")

        with patch.object(server, "get_job", return_value=item):
            resp = self.client.get("/api/jobs/job1")
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["job"]["company"], "600519")

        with patch.object(server, "get_job", return_value=item):
            resp = self.client.get("/api/jobs/job1/result")
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["result"]["decision"], "BUY")

    def test_job_result_running_endpoint(self):
        item = {
            "job_id": "job2",
            "status": "running",
            "result": None,
        }
        with patch.object(server, "get_job", return_value=item):
            resp = self.client.get("/api/jobs/job2/result")
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["status"], "running")

    def test_job_cancel_and_delete_endpoints(self):
        with (
            patch.object(
                server,
                "get_job",
                return_value={"job_id": "job3", "status": "running"},
            ),
            patch.dict(server.analysis_status, {"job_id": "job3"}),
        ):
            resp = self.client.post("/api/jobs/job3/cancel")
        self.assertTrue(resp.json()["success"])
        server.analysis_cancel_event.clear()

        with (
            patch.object(
                server,
                "get_job",
                return_value={"job_id": "job3", "status": "completed"},
            ),
            patch.object(server, "delete_job", return_value=True),
        ):
            resp = self.client.delete("/api/jobs/job3")
        self.assertTrue(resp.json()["success"])

    def test_job_missing_endpoint(self):
        with patch.object(server, "get_job", return_value=None):
            resp = self.client.get("/api/jobs/missing")
        self.assertFalse(resp.json()["success"])

    def test_scheduled_crud_endpoints(self):
        item = {
            "id": 1,
            "ticker": "600519",
            "schedule_time": "18:00",
            "enabled": True,
            "last_run_date": "",
        }
        with patch.object(server, "list_scheduled_jobs", return_value=[item]):
            resp = self.client.get("/api/scheduled")
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["items"][0]["id"], 1)

        with patch.object(server, "add_scheduled_job", return_value=item):
            resp = self.client.post(
                "/api/scheduled",
                json={"ticker": "600519", "schedule_time": "18:00"},
            )
        self.assertTrue(resp.json()["success"])

        with patch.object(server, "update_scheduled_job", return_value=item):
            resp = self.client.patch(
                "/api/scheduled/1", json={"enabled": False}
            )
        self.assertTrue(resp.json()["success"])

        with patch.object(server, "delete_scheduled_job", return_value=True):
            resp = self.client.delete("/api/scheduled/1")
        self.assertTrue(resp.json()["success"])

    def test_scheduled_missing_update(self):
        with patch.object(server, "update_scheduled_job", return_value=None):
            resp = self.client.patch(
                "/api/scheduled/999", json={"enabled": False}
            )
        self.assertFalse(resp.json()["success"])

    def test_fast_preview_endpoint(self):
        fake = {
            "success": True,
            "ticker": "600519",
            "preliminary_decision": "BUY",
            "markdown": "## 快速预览",
        }
        with patch(
            "tradingagents.services.fast_preview.build_fast_preview",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/preview",
                params={
                    "ticker": "600519",
                    "end_date": "2026-11-11",
                    "strategies": "volume_breakout",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["preliminary_decision"], "BUY")
        mocked.assert_called_once_with(
            "600519", "2026-11-11", ["volume_breakout"]
        )

    def test_market_review_endpoint(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "indices": [{"name": "上证指数", "close": 3200.0}],
            "breadth": {"up": 3000, "down": 1500},
            "sectors": {"top": [], "bottom": []},
            "summary": {"tone": "偏强", "text": "市场偏强"},
        }
        with patch(
            "tradingagents.services.market_review.build_market_review",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/market/review", params={"date": "2026-11-11"}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["indices"][0]["name"], "上证指数")
        mocked.assert_called_once_with("2026-11-11")

    def test_market_review_notify_endpoint(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "notify": {"wecom": {"ok": True}},
        }
        with patch(
            "tradingagents.services.market_review.send_market_review",
            return_value=fake,
        ) as mocked:
            resp = self.client.post(
                "/api/market/review/notify",
                json={"date": "2026-11-11"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["notify"]["wecom"]["ok"], True)
        mocked.assert_called_once_with("2026-11-11")

    def test_vendor_assets_are_served_locally(self):
        for name in ("marked.min.js", "purify.min.js", "echarts.min.js"):
            resp = self.client.get(f"/static/vendor/{name}")
            self.assertEqual(resp.status_code, 200)
            self.assertGreater(len(resp.content), 1000)

    def test_stock_search_endpoint(self):
        fake = {
            "success": True,
            "query": "茅台",
            "items": [{"ticker": "600519", "name": "贵州茅台"}],
        }
        with patch(
            "tradingagents.services.market_search.search_stocks",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/market/stock-search", params={"q": "茅台", "limit": 5}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "600519")
        mocked.assert_called_once_with("茅台", 5)

    def test_hot_stocks_endpoint(self):
        fake = {
            "success": True,
            "date": "2026-11-11",
            "items": [{"ticker": "002160", "streak": 2}],
        }
        with patch(
            "tradingagents.services.market_search.get_hot_stocks",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/market/hot-stocks",
                params={"limit": 10, "date": "2026-11-11"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "002160")
        mocked.assert_called_once_with(10, "2026-11-11")

    def test_market_sectors_endpoint(self):
        with patch(
            "tradingagents.services.sector_history.list_sector_names",
            return_value=[{"name": "半导体", "code": "881121"}],
        ):
            resp = self.client.get("/api/market/sectors")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["name"], "半导体")

    def test_sector_history_endpoint(self):
        fake = {
            "success": True,
            "name": "半导体",
            "change_5d": 4.2,
            "series": [{"date": "2026-11-11", "close": 124.0}],
        }
        with patch(
            "tradingagents.services.sector_history.get_sector_history",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/market/sector-history",
                params={"name": "半导体", "days": 30},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["change_5d"], 4.2)
        mocked.assert_called_once_with("半导体", 30)

    def test_daily_briefing_endpoint(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "summary": {"tone": "偏强", "alerts": 1},
            "markdown": "# 每日盘后投研简报",
        }
        with patch(
            "tradingagents.services.daily_briefing.build_daily_briefing",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/daily-briefing", params={"date": "2026-11-11"}
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["summary"]["alerts"], 1)
        mocked.assert_called_once_with("2026-11-11")

    def test_daily_briefing_notify_endpoint(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "notify": {"wecom": {"ok": True}},
        }
        with patch(
            "tradingagents.services.daily_briefing.send_daily_briefing",
            return_value=fake,
        ) as mocked:
            resp = self.client.post(
                "/api/daily-briefing/notify",
                json={"date": "2026-11-11"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["notify"]["wecom"]["ok"], True)
        mocked.assert_called_once_with("2026-11-11", "webui")

    def test_daily_briefing_archive_endpoint(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "briefing_id": 3,
        }
        with patch(
            "tradingagents.services.daily_briefing.archive_daily_briefing",
            return_value=fake,
        ) as mocked:
            resp = self.client.post(
                "/api/daily-briefing/archive",
                json={"date": "2026-11-11"},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["briefing_id"], 3)
        mocked.assert_called_once_with("2026-11-11", "webui")

    def test_briefings_list_and_detail_endpoints(self):
        item = {
            "id": 1,
            "briefing_date": "2026-11-11",
            "summary": {"tone": "偏强"},
        }
        with patch.object(server, "list_briefings", return_value=[item]):
            resp = self.client.get("/api/briefings")
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["items"][0]["id"], 1)

        with patch.object(
            server,
            "get_briefing",
            return_value={**item, "markdown": "# 简报"},
        ):
            resp = self.client.get("/api/briefings/1")
        self.assertTrue(resp.json()["success"])
        self.assertIn("markdown", resp.json()["briefing"])

        with patch.object(server, "get_briefing", return_value=None):
            resp = self.client.get("/api/briefings/999")
        self.assertFalse(resp.json()["success"])

    def test_briefings_delete_endpoint(self):
        with patch.object(server, "delete_briefing", return_value=True):
            resp = self.client.delete("/api/briefings/1")
        self.assertTrue(resp.json()["success"])

        with patch.object(server, "delete_briefing", return_value=False):
            resp = self.client.delete("/api/briefings/999")
        self.assertFalse(resp.json()["success"])

    def test_strategy_alerts_endpoint(self):
        fake = {
            "success": True,
            "items": [{"ticker": "600519", "consensus": "偏多", "triggered": 2}],
            "summary": {"total": 2, "triggered": 1},
        }
        with patch(
            "tradingagents.services.strategy_alerts.build_strategy_alerts",
            return_value=fake,
        ) as mocked:
            resp = self.client.get(
                "/api/strategy-alerts",
                params={
                    "tickers": "600519,000001",
                    "strategies": "bull_trend",
                    "only_triggered": "true",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "600519")
        mocked.assert_called_once_with(
            ["600519", "000001"], None, ["bull_trend"], True
        )

    def test_strategy_alerts_notify_endpoint(self):
        fake = {
            "success": True,
            "items": [],
            "summary": {},
            "notify": {"wecom": {"ok": True}},
        }
        with patch(
            "tradingagents.services.strategy_alerts.send_strategy_alerts",
            return_value=fake,
        ) as mocked:
            resp = self.client.post(
                "/api/strategy-alerts/notify",
                json={"tickers": ["600519"], "strategies": ["bull_trend"]},
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["notify"]["wecom"]["ok"], True)
        mocked.assert_called_once_with(
            ["600519"], None, ["bull_trend"], True, False
        )

    def test_config_defaults_endpoint(self):
        resp = self.client.get("/api/config/defaults")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("llm_provider", data)
        self.assertIn("deep_think_llm", data)
        self.assertIn("quick_think_llm", data)
        self.assertIn("backend_url", data)
        self.assertIn("provider_models", data)

    def test_pipeline_presets_endpoint(self):
        resp = self.client.get("/api/pipeline/presets")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("standard", data["presets"])
        self.assertTrue(any(a["key"] == "market" for a in data["analysts"]))

    def test_settings_status_endpoint(self):
        with patch.dict(
            os.environ,
            {"MINIMAX_API_KEY": "test-key", "TAVILY_API_KEY": "tv-key"},
            clear=True,
        ):
            resp = self.client.get("/api/settings/status")
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["minimax_configured"])
        self.assertTrue(data["tavily_configured"])
        providers = {p["provider"]: p["configured"] for p in data["providers"]}
        self.assertTrue(providers["ollama"])
        # MINIMAX_API_KEY 也算作 anthropic 兼容端点已配置
        self.assertTrue(providers["anthropic"])

    def test_backtest_endpoint(self):
        fake = {
            "ticker": "600519",
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "strategy": "ma_cross",
            "total_return": 0.1,
            "equity_curve": [{"date": "2024-01-02", "equity": 100000.0}],
        }
        with patch.object(server, "run_backtest", return_value=fake):
            resp = self.client.post(
                "/api/backtest",
                json={
                    "ticker": "600519",
                    "start_date": "2024-01-01",
                    "end_date": "2024-06-30",
                },
            )
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["ticker"], "600519")


if __name__ == "__main__":
    unittest.main()
