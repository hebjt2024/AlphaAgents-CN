"""P13：组合风险度量、压力测试与再平衡。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _price_frame(seed: int, start: float, drift: float, vol: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=320)
    returns = drift + rng.normal(0, vol, len(dates))
    closes = start * np.cumprod(1 + returns)
    return pd.DataFrame({"Date": dates, "Close": closes})


class TempPortfolioMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.services.portfolio import ensure_account
        from tradingagents.storage import update_account_cash, upsert_position

        ensure_account("默认账户", initial_cash=100000)
        for ticker, qty, cost, price, sector in (
            ("600519", 1000, 100.0, 100.0, "白酒"),
            ("000001", 2000, 50.0, 50.0, "银行"),
            ("300750", 3000, 20.0, 20.0, "电池"),
        ):
            upsert_position(
                "默认账户",
                ticker,
                quantity=qty,
                avg_cost=cost,
                last_price=price,
                sector=sector,
            )
        update_account_cash("默认账户", 100000)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old
        super().tearDown()

    def _patch_prices(self):
        frames = {
            "600519": _price_frame(1, 100.0, 0.0004, 0.012),
            "000001": _price_frame(2, 50.0, 0.0002, 0.010),
            "300750": _price_frame(3, 20.0, 0.0005, 0.016),
        }
        benchmark = _price_frame(9, 4000.0, 0.0002, 0.009)
        return patch(
            "tradingagents.services.portfolio_risk._load_prices",
            side_effect=lambda ticker, start, end: frames[ticker].copy(),
        ), patch(
            "tradingagents.services.portfolio_risk.get_index_history",
            return_value={"success": True, "items": benchmark},
        )


class PortfolioRiskTests(TempPortfolioMixin):
    def test_risk_report_metrics_stress_and_rebalance(self):
        from tradingagents.services.portfolio_risk import portfolio_risk_report

        p1, p2 = self._patch_prices()
        with p1, p2:
            result = portfolio_risk_report("默认账户", lookback=252)
        self.assertTrue(result["success"])
        metrics = result["metrics"]
        self.assertGreater(metrics["annual_volatility"], 0)
        self.assertGreater(metrics["var_95_1d"], 0)
        self.assertGreater(metrics["cvar_95_1d"], 0)
        self.assertIsNotNone(metrics["beta"])
        self.assertTrue(metrics["risk_contributions"])
        self.assertEqual(len(result["stress"]), 6)
        self.assertTrue(result["rebalance"]["success"])
        self.assertTrue(result["rebalance"]["items"])

    def test_weights_use_equity_denominator(self):
        from tradingagents.services.portfolio_risk import _weights

        positions = [
            {"ticker": "600519", "quantity": 100, "last_price": 100.0, "market_value": 10000.0}
        ]
        weights = _weights(positions, 100000.0)
        self.assertAlmostEqual(weights["600519"], 0.1)

    def test_equal_rebalance_respects_total_weight(self):
        from tradingagents.services.portfolio_risk import suggest_rebalance

        result = suggest_rebalance("默认账户", method="equal")
        self.assertTrue(result["success"])
        self.assertLessEqual(result["target_total_weight"], 0.9 + 1e-6)
        actions = {item["action"] for item in result["items"]}
        self.assertTrue(actions & {"买入", "卖出", "不动"})

    def test_risk_parity_rebalance(self):
        from tradingagents.services.portfolio_risk import suggest_rebalance

        result = suggest_rebalance(
            "默认账户",
            method="risk_parity",
            volatility={"600519": 0.25, "000001": 0.15, "300750": 0.40},
        )
        self.assertTrue(result["success"])
        weights = {item["ticker"]: item["target_weight"] for item in result["items"]}
        self.assertGreater(weights["000001"], weights["300750"])

    def test_no_positions_returns_error(self):
        from tradingagents.services.portfolio_risk import portfolio_risk_report
        from tradingagents.storage import delete_position

        for ticker in ("600519", "000001", "300750"):
            delete_position("默认账户", ticker)
        result = portfolio_risk_report("默认账户")
        self.assertFalse(result["success"])


class PortfolioRiskApiTests(TempPortfolioMixin):
    def setUp(self):
        super().setUp()
        self.client = TestClient(server.app)

    def test_risk_api(self):
        canned = {
            "success": True,
            "metrics": {"annual_volatility": 0.2, "var_95_1d": 0.02},
            "stress": [{"scenario": "大盘 -5%"}],
            "rebalance": {"success": True, "items": []},
            "warnings": [],
        }
        with patch(
            "tradingagents.services.portfolio_risk.portfolio_risk_report",
            return_value=canned,
        ):
            data = self.client.get("/api/portfolio/risk").json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["metrics"]["annual_volatility"], 0.2)

    def test_rebalance_api(self):
        canned = {
            "success": True,
            "method": "equal",
            "items": [{"ticker": "600519", "action": "卖出"}],
            "warnings": [],
        }
        with patch(
            "tradingagents.services.portfolio_risk.suggest_rebalance",
            return_value=canned,
        ):
            data = self.client.post(
                "/api/portfolio/rebalance",
                json={"method": "equal", "max_single_weight": 0.2},
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["items"][0]["action"], "卖出")


class PortfolioRiskFrontendTests(unittest.TestCase):
    def test_risk_card_is_wired(self):
        html = (
            Path(__file__).resolve().parents[1]
            / "webui"
            / "frontend"
            / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="portfolioRiskCard"', html)
        self.assertIn("async function loadPortfolioRisk()", html)


if __name__ == "__main__":
    unittest.main()
