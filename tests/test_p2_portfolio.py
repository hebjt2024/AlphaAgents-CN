"""P2 组合管理与组合级风控引擎测试。

背景：此前系统的全部风控都停留在散文里 —— 决策卡里的
``risk_per_trade=0.01`` 与 ``max_position=0.20`` 是硬编码常量，
**不知道组合里已经持有什么**。系统可以同时对 10 只股票各给出
"建议仓位 20%"（合计 200%）而没有任何地方拦下来。

本文件固化组合级约束必须生效这一事实。
"""

import os
import tempfile
import unittest
from pathlib import Path

# 必须在导入 storage 之前设置，保证测试使用独立的临时数据库
_TMP_DB = Path(tempfile.mkdtemp(prefix="alphaagents-p2-")) / "test.db"
os.environ["TA_DB_PATH"] = str(_TMP_DB)

from tradingagents.services.portfolio import (  # noqa: E402
    evaluate_plan_for_account,
    load_positions,
    portfolio_snapshot,
    record_fill,
)
from tradingagents.services.risk_engine import (  # noqa: E402
    PortfolioRiskEngine,
    PositionView,
    RiskLimits,
)


def _plan(**overrides):
    plan = {
        "decision": "BUY",
        "direction": "偏多",
        "score": 70,
        "confidence": 60,
        "position_pct": 0.20,
        "risk_pct": 0.04,
        "stop_loss": 96.0,
        "take_profit": 108.0,
        "entry": 100.0,
    }
    plan.update(overrides)
    return plan


def _summary(net=30):
    return {"net_score": net, "strength": 70, "triggered": 3, "family_count": 3}


class RiskLimitsTests(unittest.TestCase):
    def test_single_weight_cap_scales_position_down(self):
        engine = PortfolioRiskEngine(RiskLimits(max_single_weight=0.20))
        result = engine.evaluate(
            plan=_plan(position_pct=0.35),
            summary=_summary(),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["adjusted_position_pct"], 0.20, places=4)
        self.assertIn(
            "single_weight_cap", [f["code"] for f in result["findings"]]
        )

    def test_existing_position_reduces_headroom(self):
        engine = PortfolioRiskEngine(RiskLimits(max_single_weight=0.20))
        positions = [
            PositionView(ticker="600519", quantity=1000, avg_cost=100, last_price=100)
        ]
        result = engine.evaluate(
            plan=_plan(position_pct=0.15),
            summary=_summary(),
            positions=positions,
            equity=100000,
            cash=80000,
            ticker="600519",
        )
        # 已占 100000/100000 = 100%？ 不 —— 权益 10 万，持仓市值 10 万
        # existing_weight = 100000/100000 = 1.0，已严重超限
        self.assertLessEqual(result["adjusted_position_pct"], 0.0)

    def test_total_weight_cap(self):
        engine = PortfolioRiskEngine(RiskLimits(max_total_weight=0.90))
        positions = [
            PositionView(ticker="000001", quantity=8000, avg_cost=10, last_price=10)
        ]  # 市值 80000，占权益 80%
        result = engine.evaluate(
            plan=_plan(position_pct=0.30),
            summary=_summary(),
            positions=positions,
            equity=100000,
            cash=20000,
            ticker="600519",
        )
        codes = [f["code"] for f in result["findings"]]
        self.assertIn("total_weight_cap", codes)
        self.assertLessEqual(result["adjusted_position_pct"], 0.10 + 1e-6)

    def test_sector_weight_cap(self):
        engine = PortfolioRiskEngine(RiskLimits(max_sector_weight=0.40))
        positions = [
            PositionView(
                ticker="000001",
                sector="半导体",
                quantity=3500,
                avg_cost=10,
                last_price=10,
            )
        ]  # 市值 35000 = 35%
        result = engine.evaluate(
            plan=_plan(position_pct=0.30),
            summary=_summary(),
            positions=positions,
            equity=100000,
            cash=65000,
            ticker="600519",
            sector="半导体",
        )
        codes = [f["code"] for f in result["findings"]]
        self.assertIn("sector_weight_cap", codes)

    def test_risk_budget_caps_position(self):
        engine = PortfolioRiskEngine(RiskLimits(risk_per_trade=0.01))
        # 止损距离 20% → 1% / 20% = 5% 仓位上限
        result = engine.evaluate(
            plan=_plan(position_pct=0.30, risk_pct=0.20),
            summary=_summary(),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
        )
        self.assertLessEqual(result["adjusted_position_pct"], 0.05 + 1e-6)
        self.assertIn("risk_budget", [f["code"] for f in result["findings"]])

    def test_cash_constraint(self):
        engine = PortfolioRiskEngine()
        result = engine.evaluate(
            plan=_plan(position_pct=0.50),
            summary=_summary(),
            positions=[],
            equity=100000,
            cash=10000,
            ticker="600519",
        )
        self.assertLessEqual(result["adjusted_position_pct"], 0.10 + 1e-6)
        self.assertIn("insufficient_cash", [f["code"] for f in result["findings"]])

    def test_daily_loss_breaker_blocks(self):
        engine = PortfolioRiskEngine(RiskLimits(daily_loss_limit=0.03))
        result = engine.evaluate(
            plan=_plan(),
            summary=_summary(),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
            daily_pnl_pct=-0.05,
        )
        self.assertFalse(result["allowed"])
        self.assertIn("daily_loss_breaker", result["blocked_reasons"])
        self.assertEqual(result["adjusted_position_pct"], 0.0)

    def test_drawdown_breaker_blocks(self):
        engine = PortfolioRiskEngine(RiskLimits(max_drawdown=0.20))
        result = engine.evaluate(
            plan=_plan(),
            summary=_summary(),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
            drawdown_pct=-0.25,
        )
        self.assertFalse(result["allowed"])
        self.assertIn("drawdown_breaker", result["blocked_reasons"])

    def test_direction_conflict_blocks(self):
        engine = PortfolioRiskEngine()
        result = engine.evaluate(
            plan=_plan(),
            summary=_summary(net=-40),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
        )
        self.assertFalse(result["allowed"])
        self.assertIn("direction_conflict", result["blocked_reasons"])

    def test_hold_never_opens_position(self):
        engine = PortfolioRiskEngine()
        result = engine.evaluate(
            plan=_plan(decision="HOLD", position_pct=0.0),
            summary=_summary(net=0),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["adjusted_position_pct"], 0.0)

    def test_sell_never_increases_position(self):
        engine = PortfolioRiskEngine()
        result = engine.evaluate(
            plan=_plan(decision="SELL", position_pct=0.0),
            summary=_summary(net=-40),
            positions=[],
            equity=100000,
            cash=100000,
            ticker="600519",
        )
        self.assertEqual(result["adjusted_position_pct"], 0.0)


class PortfolioHealthTests(unittest.TestCase):
    def test_health_reports_weights_and_breaches(self):
        engine = PortfolioRiskEngine(RiskLimits(max_single_weight=0.20))
        positions = [
            PositionView(ticker="600519", quantity=3000, avg_cost=100, last_price=100),
        ]  # 市值 300000，权益 100000 → 权重 300%，严重超限
        health = engine.portfolio_health(positions, equity=100000)
        self.assertFalse(health["healthy"])
        self.assertTrue(health["breaches"])
        self.assertAlmostEqual(health["total_weight"], 3.0, places=3)

    def test_health_sector_breakdown(self):
        engine = PortfolioRiskEngine()
        positions = [
            PositionView(
                ticker="000001", sector="半导体", quantity=1000, avg_cost=10, last_price=10
            ),
            PositionView(
                ticker="600519", sector="白酒", quantity=1000, avg_cost=100, last_price=100
            ),
        ]
        health = engine.portfolio_health(positions, equity=100000)
        self.assertIn("半导体", health["sector_weights"])
        self.assertIn("白酒", health["sector_weights"])


class PortfolioServiceTests(unittest.TestCase):
    def test_record_buy_then_sell_updates_cash_and_position(self):
        account = "组合测试-A"
        record_fill(
            account, "600519", "BUY", 100, 100.0, fee=5.0, trade_date="2026-01-05"
        )
        positions = load_positions(account)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 100)

        snapshot = portfolio_snapshot(account)
        self.assertAlmostEqual(snapshot["cash"], 100000 - 10000 - 5.0, places=2)

        record_fill(
            account, "600519", "SELL", 100, 110.0, fee=10.0, trade_date="2026-01-06"
        )
        positions = load_positions(account)
        self.assertEqual(len(positions), 0)

        snapshot = portfolio_snapshot(account)
        # 卖出 110*100 - 10 = 10990
        self.assertAlmostEqual(snapshot["cash"], 100000 - 10005 + 10990, places=2)

    def test_sell_without_position_is_rejected(self):
        with self.assertRaises(ValueError):
            record_fill(
                "组合测试-无持仓", "600519", "SELL", 100, 110.0, fee=10.0
            )

    def test_sell_quantity_cannot_exceed_position(self):
        account = "组合测试-超卖"
        record_fill(account, "600519", "BUY", 100, 100.0, fee=5.0)
        with self.assertRaises(ValueError):
            record_fill(account, "600519", "SELL", 200, 110.0, fee=10.0)

    def test_evaluate_plan_for_account_respects_existing_weight(self):
        account = "组合测试-B"
        record_fill(
            account, "600519", "BUY", 15000, 100.0, fee=50.0, trade_date="2026-01-05"
        )
        # 15000 股 × 100 = 150 万，权益 = 100000 - 1500050 + 1500000 ≈ 9950
        result = evaluate_plan_for_account(
            plan=_plan(position_pct=0.30),
            summary=_summary(),
            account=account,
            ticker="600519",
        )
        self.assertLessEqual(result["adjusted_position_pct"], 0.20 + 1e-6)
        self.assertIn(
            "single_weight_cap", [f["code"] for f in result["findings"]]
        )


if __name__ == "__main__":
    unittest.main()
