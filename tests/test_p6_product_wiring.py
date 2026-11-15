"""P6：把已有能力接到产品上（评估 / 组合盯市 / 模拟盘）。

这一批不是新增分析算法，而是验证：
* 历史推荐的评估结果能通过 API 返回，并带上报告 id；
* 组合可以按最新价盯市、记录每日快照并计算绩效；
* 模拟盘状态能跨请求持久化，委托能按历史行情撮合。

测试统一使用临时 SQLite，避免污染本地 data/tradingagents.db。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _prices(rows: int = 40, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range("2026-09-01", periods=rows)
    closes = [start + step * index for index in range(rows)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": [100000] * rows,
        }
    )


class TempDbMixin(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_db = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.storage import init_db

        init_db()

    def tearDown(self) -> None:
        if self._old_db is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old_db
        super().tearDown()


class EvaluationServiceTests(TempDbMixin):
    def test_evaluate_reports_prefetches_once_per_ticker_and_keeps_report_id(self):
        from tradingagents.services.evaluation import evaluate_reports

        reports = [
            {"id": 11, "ticker": "600519", "trade_date": "2026-09-01", "decision": "BUY"},
            {"id": 12, "ticker": "600519", "trade_date": "2026-10-01", "decision": "BUY"},
            {"id": 13, "ticker": "000001", "trade_date": "2026-09-01", "decision": "SELL"},
        ]
        calls: list[str] = []

        def loader(ticker, start, end):
            calls.append(ticker)
            return _prices()

        result = evaluate_reports(reports=reports, price_loader=loader)
        # 同一标的多条报告只取一次行情
        self.assertEqual(sorted(calls), ["000001", "600519"])
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["rows"][0]["report_id"], 11)
        self.assertIn("summary", result)

    def test_evaluation_api_returns_rows_and_summary(self):
        client = TestClient(server.app)
        canned = {
            "rows": [{"ticker": "600519", "report_id": 1, "decision": "BUY"}],
            "summary": {"total": 1, "overall": {"hit_rate": 1.0}},
        }
        with patch(
            "tradingagents.services.evaluation.evaluate_reports",
            return_value=canned,
        ):
            resp = client.get("/api/evaluation", params={"ticker": "600519"})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["rows"][0]["report_id"], 1)
        self.assertEqual(data["summary"]["total"], 1)


class PortfolioWiringTests(TempDbMixin):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(server.app)
        from tradingagents.services.portfolio import ensure_account

        ensure_account("默认账户", initial_cash=100000)

    def _add_position(self):
        resp = self.client.post(
            "/api/portfolio/position",
            json={
                "ticker": "600519",
                "quantity": 100,
                "avg_cost": 100.0,
                "name": "贵州茅台",
            },
        )
        self.assertTrue(resp.json()["success"])

    def test_refresh_uses_spot_prices_and_updates_market_value(self):
        self._add_position()
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_spot_prices",
            return_value={"600519": 110.0},
        ):
            resp = self.client.post("/api/portfolio/refresh", json={})
        data = resp.json()
        self.assertTrue(data["success"])
        position = data["portfolio"]["positions"][0]
        self.assertAlmostEqual(position["last_price"], 110.0)
        self.assertAlmostEqual(position["market_value"], 11000.0)
        self.assertAlmostEqual(position["unrealised_pnl"], 1000.0)

    def test_snapshot_and_performance_are_persisted(self):
        self._add_position()
        with patch(
            "tradingagents.dataflows.akshare_adapter.get_akshare_spot_prices",
            return_value={"600519": 110.0},
        ):
            self.client.post("/api/portfolio/refresh", json={})
        resp = self.client.post(
            "/api/portfolio/snapshot", json={"date": "2026-11-10"}
        )
        self.assertTrue(resp.json()["success"])

        performance = self.client.get(
            "/api/portfolio/performance"
        ).json()
        self.assertTrue(performance["success"])
        self.assertGreaterEqual(performance["performance"]["points"], 1)
        self.assertEqual(
            performance["performance"]["equity_curve"][-1]["date"], "2026-11-10"
        )
        self.assertGreater(performance["performance"]["end_equity"], 0)
        # 初始资金 10 万，持仓 100 股 @110 + 现金 10 万 => 权益 11.1 万
        self.assertAlmostEqual(
            performance["performance"]["total_return"], 0.11, places=4
        )

    def test_transactions_endpoint_returns_recorded_fill(self):
        self._add_position()
        resp = self.client.post(
            "/api/portfolio/fill",
            json={
                "ticker": "600519",
                "side": "BUY",
                "quantity": 100,
                "price": 101.0,
                "fee": 5.0,
                "trade_date": "2026-11-11",
            },
        )
        self.assertTrue(resp.json()["success"])
        rows = self.client.get("/api/portfolio/transactions").json()["items"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["side"], "BUY")
        self.assertEqual(rows[0]["trade_date"], "2026-11-11")


class PaperTradingApiTests(TempDbMixin):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(server.app)
        reset = self.client.post(
            "/api/paper/reset", json={"initial_cash": 100000}
        ).json()
        self.assertTrue(reset["success"])
        self.assertEqual(reset["account"]["cash"], 100000)

    def test_order_state_persists_across_requests(self):
        resp = self.client.post(
            "/api/paper/order",
            json={
                "ticker": "600519",
                "side": "buy",
                "quantity": 100,
                "order_type": "market",
            },
        ).json()
        self.assertTrue(resp["success"])
        self.assertEqual(resp["order"]["status"], "pending")

        again = self.client.get("/api/paper").json()
        self.assertTrue(again["success"])
        self.assertEqual(len(again["orders"]), 1)
        self.assertEqual(again["orders"][0]["status"], "pending")

    def test_advance_fills_order_and_persists_position(self):
        self.client.post(
            "/api/paper/order",
            json={
                "ticker": "600519",
                "side": "buy",
                "quantity": 100,
                "order_type": "market",
            },
        )
        frame = _prices(rows=3, start=100.0)
        # 委托提交于今天；回放历史日期不应成交。这里用今天之后的三个交易日，
        # 让委托在“提交日之后”成交，符合实时模拟盘约束。
        future = pd.bdate_range(
            pd.Timestamp.today().normalize() + pd.Timedelta(days=1), periods=3
        )
        frame["Date"] = future
        with patch(
            "tradingagents.services.paper_trading.load_price_frames",
            return_value={"600519": frame},
        ):
            resp = self.client.post(
                "/api/paper/advance",
                json={
                    "start_date": future[0].strftime("%Y-%m-%d"),
                    "end_date": future[-1].strftime("%Y-%m-%d"),
                },
            ).json()
        self.assertTrue(resp["success"])
        report = resp["report"]
        self.assertEqual(len(report["positions"]), 1)
        self.assertEqual(report["positions"][0]["ticker"], "600519")
        self.assertEqual(report["positions"][0]["quantity"], 100)
        self.assertLess(report["account"]["cash"], 100000)

        again = self.client.get("/api/paper").json()
        self.assertEqual(len(again["positions"]), 1)
        self.assertEqual(len(again["equity_curve"]), 3)

    def test_cancel_pending_order(self):
        order = self.client.post(
            "/api/paper/order",
            json={
                "ticker": "600519",
                "side": "buy",
                "quantity": 100,
                "order_type": "limit",
                "limit_price": 90.0,
            },
        ).json()["order"]
        resp = self.client.post(
            "/api/paper/cancel", params={"order_id": order["id"]}
        ).json()
        self.assertTrue(resp["success"])
        self.assertEqual(resp["report"]["orders"][0]["status"], "cancelled")


class SchedulerClosedLoopTests(unittest.TestCase):
    """定时任务默认把组合快照与推荐评估跑起来。"""

    def test_portfolio_snapshot_refreshes_and_records_each_account(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch(
                "tradingagents.storage.list_accounts",
                return_value=[{"name": "默认账户"}],
            ),
            patch(
                "tradingagents.services.portfolio.refresh_prices"
            ) as refresh,
            patch(
                "tradingagents.services.portfolio.record_snapshot"
            ) as snapshot,
        ):
            scheduler._run_portfolio_snapshot_once()
        refresh.assert_called_once_with("默认账户")
        snapshot.assert_called_once_with("默认账户", "2026-11-12")

    def test_evaluation_runs_with_memory_written(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch(
                "tradingagents.services.evaluation.evaluate_reports",
                return_value={
                    "summary": {
                        "raw_count": 3,
                        "effective_sample_size": 3,
                        "overall": {"hit_rate": 0.66},
                    }
                },
            ) as evaluate,
        ):
            scheduler._run_evaluation_once()
        self.assertTrue(evaluate.call_args.kwargs["write_memory"])
        self.assertEqual(evaluate.call_args.kwargs["limit"], 100)


class FrontendWiringTests(unittest.TestCase):
    """新增页面必须真的挂在导航和 switchView 上，而不是只写了 HTML。"""

    def test_portfolio_and_evaluation_views_are_wired(self):
        html = (
            Path(__file__).resolve().parents[1]
            / "webui"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        for view in ("portfolio", "evaluation"):
            self.assertIn(f'data-view="{view}"', html)
            self.assertIn(f'id="view-{view}"', html)
            self.assertIn(f"if (view === '{view}')", html)
        self.assertIn("async function loadPortfolio()", html)
        self.assertIn("async function loadEvaluation()", html)
        self.assertIn("async function loadNorthbound()", html)
        self.assertIn("async function loadEarnings()", html)
        self.assertIn("async function loadRuns()", html)


if __name__ == "__main__":
    unittest.main()
