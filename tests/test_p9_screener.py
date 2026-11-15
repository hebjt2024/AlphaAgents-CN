"""P9：全市场多因子选股与每日候选池。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _snapshot() -> pd.DataFrame:
    rows = [
        {
            "code": "600519",
            "name": "贵州茅台",
            "price": 1500.0,
            "change_pct": 1.5,
            "change": 22.0,
            "open": 1480.0,
            "prev_close": 1478.0,
            "high": 1510.0,
            "low": 1470.0,
            "volume": 30000,
            "amount": 5.0e9,
            "amplitude": 2.7,
            "volume_ratio": 1.2,
            "turnover": 0.5,
            "pe_ttm": 25.0,
            "pb": 8.0,
            "market_cap": 1.9e12,
            "float_market_cap": 1.9e12,
            "speed": 0.1,
            "change_60d": 12.0,
            "change_ytd": 20.0,
        },
        {
            "code": "000001",
            "name": "平安银行",
            "price": 12.0,
            "change_pct": 0.5,
            "change": 0.06,
            "open": 11.9,
            "prev_close": 11.94,
            "high": 12.1,
            "low": 11.8,
            "volume": 1000000,
            "amount": 2.0e9,
            "amplitude": 2.5,
            "volume_ratio": 0.9,
            "turnover": 1.2,
            "pe_ttm": 5.0,
            "pb": 0.6,
            "market_cap": 2.0e11,
            "float_market_cap": 1.9e11,
            "speed": 0.0,
            "change_60d": 5.0,
            "change_ytd": 10.0,
        },
        {
            "code": "300750",
            "name": "宁德时代",
            "price": 220.0,
            "change_pct": 3.0,
            "change": 6.4,
            "open": 214.0,
            "prev_close": 213.6,
            "high": 222.0,
            "low": 213.0,
            "volume": 500000,
            "amount": 3.0e9,
            "amplitude": 4.2,
            "volume_ratio": 2.2,
            "turnover": 2.5,
            "pe_ttm": 30.0,
            "pb": 5.0,
            "market_cap": 1.0e12,
            "float_market_cap": 8.0e11,
            "speed": 0.2,
            "change_60d": 25.0,
            "change_ytd": 40.0,
        },
        {
            "code": "600001",
            "name": "*ST测试",
            "price": 3.0,
            "change_pct": 1.0,
            "change": 0.03,
            "open": 3.0,
            "prev_close": 2.97,
            "high": 3.05,
            "low": 2.95,
            "volume": 100000,
            "amount": 2.0e9,
            "amplitude": 3.0,
            "volume_ratio": 1.0,
            "turnover": 1.0,
            "pe_ttm": 10.0,
            "pb": 1.0,
            "market_cap": 2.0e10,
            "float_market_cap": 2.0e10,
            "speed": 0.0,
            "change_60d": 1.0,
            "change_ytd": 2.0,
        },
        {
            "code": "000002",
            "name": "低成交",
            "price": 8.0,
            "change_pct": 0.2,
            "change": 0.02,
            "open": 8.0,
            "prev_close": 7.98,
            "high": 8.1,
            "low": 7.9,
            "volume": 10000,
            "amount": 1.0e7,
            "amplitude": 2.0,
            "volume_ratio": 0.5,
            "turnover": 0.1,
            "pe_ttm": 8.0,
            "pb": 0.8,
            "market_cap": 1.0e10,
            "float_market_cap": 9.0e9,
            "speed": 0.0,
            "change_60d": 0.0,
            "change_ytd": 1.0,
        },
        {
            "code": "688001",
            "name": "科创涨停",
            "price": 60.0,
            "change_pct": 20.0,
            "change": 10.0,
            "open": 52.0,
            "prev_close": 50.0,
            "high": 60.0,
            "low": 51.0,
            "volume": 200000,
            "amount": 1.2e9,
            "amplitude": 18.0,
            "volume_ratio": 3.0,
            "turnover": 5.0,
            "pe_ttm": 40.0,
            "pb": 6.0,
            "market_cap": 3.0e10,
            "float_market_cap": 2.0e10,
            "speed": 0.0,
            "change_60d": 30.0,
            "change_ytd": 50.0,
        },
        {
            "code": "000003",
            "name": "亏损公司",
            "price": 15.0,
            "change_pct": 0.8,
            "change": 0.12,
            "open": 14.9,
            "prev_close": 14.88,
            "high": 15.2,
            "low": 14.8,
            "volume": 300000,
            "amount": 2.0e9,
            "amplitude": 2.7,
            "volume_ratio": 1.1,
            "turnover": 1.5,
            "pe_ttm": -5.0,
            "pb": 2.0,
            "market_cap": 5.0e10,
            "float_market_cap": 4.0e10,
            "speed": 0.0,
            "change_60d": -8.0,
            "change_ytd": -12.0,
        },
    ]
    return pd.DataFrame(rows)


def _financials() -> pd.DataFrame:
    rows = [
        {
            "code": "600519",
            "name_fin": "贵州茅台",
            "eps": 50.0,
            "revenue": 1.0e11,
            "revenue_growth": 15.0,
            "revenue_qoq": 3.0,
            "net_profit": 5.0e10,
            "profit_growth": 18.0,
            "profit_qoq": 4.0,
            "bps": 150.0,
            "roe": 30.0,
            "ocf_ps": 40.0,
            "gross_margin": 90.0,
            "industry": "白酒",
            "announce_date": "2026-10-25",
        },
        {
            "code": "000001",
            "name_fin": "平安银行",
            "eps": 1.5,
            "revenue": 1.0e11,
            "revenue_growth": 5.0,
            "revenue_qoq": 1.0,
            "net_profit": 3.0e10,
            "profit_growth": 8.0,
            "profit_qoq": 2.0,
            "bps": 20.0,
            "roe": 12.0,
            "ocf_ps": 3.0,
            "gross_margin": 40.0,
            "industry": "银行",
            "announce_date": "2026-10-25",
        },
        {
            "code": "300750",
            "name_fin": "宁德时代",
            "eps": 8.0,
            "revenue": 4.0e11,
            "revenue_growth": 30.0,
            "revenue_qoq": 8.0,
            "net_profit": 5.0e10,
            "profit_growth": 35.0,
            "profit_qoq": 9.0,
            "bps": 50.0,
            "roe": 22.0,
            "ocf_ps": 9.0,
            "gross_margin": 25.0,
            "industry": "电池",
            "announce_date": "2026-10-25",
        },
        {
            "code": "600001",
            "name_fin": "*ST测试",
            "eps": 0.2,
            "revenue": 1.0e9,
            "revenue_growth": 2.0,
            "revenue_qoq": 0.0,
            "net_profit": 1.0e8,
            "profit_growth": 3.0,
            "profit_qoq": 0.0,
            "bps": 3.0,
            "roe": 5.0,
            "ocf_ps": 0.3,
            "gross_margin": 20.0,
            "industry": "测试",
            "announce_date": "2026-10-25",
        },
        {
            "code": "688001",
            "name_fin": "科创涨停",
            "eps": 0.5,
            "revenue": 1.0e9,
            "revenue_growth": 20.0,
            "revenue_qoq": 5.0,
            "net_profit": 1.0e8,
            "profit_growth": 25.0,
            "profit_qoq": 6.0,
            "bps": 8.0,
            "roe": 10.0,
            "ocf_ps": 0.6,
            "gross_margin": 30.0,
            "industry": "半导体",
            "announce_date": "2026-10-25",
        },
    ]
    return pd.DataFrame(rows)


class ScreenerServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_db = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.storage import init_db

        init_db()

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old_db

    def _run(self, **kwargs):
        from tradingagents.services.screener import run_screener

        with patch(
            "tradingagents.services.screener.get_universe_snapshot",
            return_value=_snapshot(),
        ), patch(
            "tradingagents.services.screener.get_financial_snapshot",
            return_value=_financials(),
        ), patch(
            "tradingagents.services.screener.get_fund_flow_snapshot",
            return_value=pd.DataFrame(
                columns=["code", "main_net_ratio", "main_net_amount"]
            ),
        ):
            return run_screener(**kwargs)

    def test_filters_and_scores(self):
        result = self._run(
            top=10,
            min_amount_yi=1.0,
            min_market_cap_yi=10.0,
            exclude_st=True,
            exclude_limit=True,
            include_financials=True,
            save=False,
        )
        self.assertTrue(result["success"])
        codes = {item["ticker"] for item in result["items"]}
        self.assertNotIn("600001", codes)  # ST
        self.assertNotIn("000002", codes)  # 成交额不足
        self.assertNotIn("688001", codes)  # 接近涨停
        self.assertIn("600519", codes)
        self.assertEqual(result["summary"]["universe"], 7)
        self.assertEqual(result["summary"]["after_filters"], 4)
        for item in result["items"]:
            self.assertGreaterEqual(item["score"], 0)
            self.assertLessEqual(item["score"], 100)
            self.assertEqual(
                set(item["factor_scores"]),
                {"value", "quality", "growth", "momentum", "liquidity", "capital"},
            )
            self.assertTrue(item["reasons"])

    def test_industry_neutral_adjustment(self):
        result = self._run(
            top=10,
            min_amount_yi=1.0,
            min_market_cap_yi=10.0,
            exclude_st=True,
            exclude_limit=True,
            include_financials=True,
            industry_neutral=True,
            save=False,
        )
        self.assertTrue(result["summary"]["industry_neutral"])
        for item in result["items"]:
            self.assertGreaterEqual(item["score"], 0)
            self.assertLessEqual(item["score"], 100)

    def test_momentum_and_liquidity_are_not_diluted(self):
        from tradingagents.services.screener import _factor_scores

        frame = _snapshot()
        factors = _factor_scores(frame)
        self.assertGreater(float(factors["momentum"].max()), 33.5)
        self.assertGreater(float(factors["liquidity"].max()), 33.5)

    def test_missing_pe_keeps_value_nan(self):
        from tradingagents.services.screener import _factor_scores

        frame = _snapshot().head(2).copy()
        frame.loc[0, "pe_ttm"] = float("nan")
        factors = _factor_scores(frame)
        self.assertTrue(pd.isna(factors.loc[0, "value"]) or factors.loc[0, "value"] == 50)
        # 缺失 PE 不应被当成 0 分
        self.assertNotEqual(factors.loc[0, "value"], 0.0)

    def test_loss_making_stock_value_score_is_zero(self):
        result = self._run(
            top=10,
            min_amount_yi=1.0,
            min_market_cap_yi=10.0,
            exclude_st=True,
            exclude_limit=True,
            include_financials=True,
            save=False,
        )
        loss = next(
            item for item in result["items"] if item["ticker"] == "000003"
        )
        self.assertEqual(loss["factor_scores"]["value"], 0.0)
        self.assertIn("亏损", loss["flags"])

    def test_run_is_archived_and_retrievable(self):
        from tradingagents.storage import (
            get_latest_screener_run,
            get_screener_run,
            list_screener_runs,
        )

        result = self._run(top=5, include_financials=True, save=True)
        self.assertIsNotNone(result["run_id"])
        latest = get_latest_screener_run()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["style"], "balanced")
        self.assertTrue(latest["items"])
        detail = get_screener_run(result["run_id"])
        self.assertEqual(len(detail["items"]), len(result["items"]))
        self.assertEqual(len(list_screener_runs()), 1)


class ScreenerApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_db = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.storage import init_db

        init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old_db

    def test_run_and_latest_api(self):
        canned = {
            "success": True,
            "run_id": 1,
            "run_date": "2026-11-14",
            "params": {"style": "balanced"},
            "summary": {"universe": 10, "after_filters": 5},
            "items": [{"ticker": "600519", "score": 80}],
        }
        with patch(
            "tradingagents.services.screener.run_screener",
            return_value=canned,
        ):
            data = self.client.post(
                "/api/screener/run", json={"top": 10}
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["ticker"], "600519")

        from tradingagents.storage import save_screener_run

        save_screener_run(
            "balanced",
            {"top": 10},
            {"universe": 10},
            [{"ticker": "600519", "score": 80}],
            run_date="2026-11-14",
        )
        latest = self.client.get("/api/screener/latest").json()
        self.assertTrue(latest["success"])
        self.assertEqual(latest["run"]["style"], "balanced")

    def test_styles_api(self):
        data = self.client.get("/api/screener/styles").json()
        self.assertTrue(data["success"])
        self.assertIn("balanced", data["items"])
        self.assertIn("value", data["items"])


class ScreenerSchedulerTests(unittest.TestCase):
    def test_screener_disabled_by_default(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.services.screener.run_screener"
            ) as mocked,
        ):
            scheduler._run_screener_once()
        mocked.assert_not_called()

    def test_screener_runs_when_enabled(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {"SCHEDULE_SCREENER": "1"}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-14"),
            patch(
                "tradingagents.services.screener.run_screener",
                return_value={"items": [{"ticker": "600519"}], "summary": {}},
            ) as mocked,
        ):
            scheduler._run_screener_once()
        self.assertTrue(mocked.call_args.kwargs["save"])


class ScreenerFrontendTests(unittest.TestCase):
    def test_view_and_functions_are_wired(self):
        html = (
            Path(__file__).resolve().parents[1]
            / "webui"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-view="screener"', html)
        self.assertIn('id="view-screener"', html)
        self.assertIn("async function runScreener()", html)
        self.assertIn("async function loadLatestScreener()", html)
        self.assertIn("apiFetch('/api/watchlist'", html)
        self.assertNotIn("apiFetch('/api/watch'", html)


if __name__ == "__main__":
    unittest.main()
