"""P0 持仓顾问测试：六种动作、A 股约束与持仓提醒。

全部使用显式的 ``PositionView`` 与 ``equity`` / ``cash``，不触达数据库、
网络或 LLM。
"""

import unittest

from tradingagents.services.position_advisor import (
    advise_position,
    check_position_alerts,
    summarize_alerts,
)
from tradingagents.services.risk_engine import PositionView


def _plan(**overrides):
    plan = {
        "decision": "BUY",
        "direction": "偏多",
        "score": 72,
        "confidence": 65,
        "position_pct": 0.20,
        "risk_pct": 0.04,
        "stop_loss": 96.0,
        "take_profit": 120.0,
        "entry": 100.0,
        "risk_reward": 2.0,
        "conflict": False,
        "triggered": 3,
        "family_count": 3,
    }
    plan.update(overrides)
    return plan


def _position(**overrides):
    values = {
        "ticker": "600519",
        "name": "贵州茅台",
        "quantity": 0.0,
        "avg_cost": 0.0,
        "last_price": 100.0,
    }
    values.update(overrides)
    return PositionView(**values)


class PositionActionTests(unittest.TestCase):
    def test_open_position(self):
        result = advise_position(
            "600519",
            _plan(),
            position=_position(quantity=0, last_price=100.0),
            equity=100000,
            cash=100000,
        )
        self.assertEqual(result["action"], "建仓")
        self.assertEqual(result["current_qty"], 0)
        self.assertEqual(result["target_qty"], 200)
        self.assertGreater(result["delta_qty"], 0)

    def test_add_position_is_whole_lot(self):
        result = advise_position(
            "000001",
            _plan(),
            position=_position(
                ticker="000001", quantity=500, avg_cost=9.0, last_price=10.0
            ),
            equity=100000,
            cash=95000,
        )
        self.assertEqual(result["action"], "加仓")
        self.assertEqual(result["target_qty"], 2000)
        self.assertEqual(result["delta_qty"], 1500)
        self.assertEqual(result["delta_qty"] % 100, 0)
        self.assertIn("整手", "".join(result["reasons"]))

    def test_reduce_position(self):
        result = advise_position(
            "000001",
            _plan(position_pct=0.03),
            position=_position(
                ticker="000001", quantity=500, avg_cost=9.0, last_price=10.0
            ),
            equity=100000,
            cash=95000,
        )
        self.assertEqual(result["action"], "减仓")
        self.assertEqual(result["target_qty"], 300)
        self.assertEqual(result["delta_qty"], -200)
        self.assertEqual(result["delta_lots"], -2)
        self.assertIn("T+1", "".join(result["reasons"]))

    def test_close_position(self):
        result = advise_position(
            "600519",
            _plan(decision="SELL", position_pct=0.0),
            position=_position(quantity=500, avg_cost=90.0, last_price=100.0),
            equity=100000,
            cash=50000,
        )
        self.assertEqual(result["action"], "清仓")
        self.assertEqual(result["target_qty"], 0)
        self.assertEqual(result["target_weight"], 0)
        self.assertLess(result["delta_qty"], 0)
        self.assertIn("清仓", "".join(result["reasons"]))

    def test_hold_position(self):
        result = advise_position(
            "000001",
            _plan(position_pct=0.05),
            position=_position(
                ticker="000001", quantity=500, avg_cost=9.0, last_price=10.0
            ),
            equity=100000,
            cash=95000,
        )
        self.assertEqual(result["action"], "持有")
        self.assertEqual(result["delta_qty"], 0)
        self.assertEqual(result["target_qty"], result["current_qty"])

    def test_watch_without_position(self):
        result = advise_position(
            "600519",
            _plan(decision="HOLD", position_pct=0.0),
            position=_position(quantity=0, last_price=100.0),
            equity=100000,
            cash=100000,
        )
        self.assertEqual(result["action"], "观望")
        self.assertEqual(result["target_qty"], 0)

    def test_sell_never_returns_negative_quantity(self):
        result = advise_position(
            "600519",
            _plan(decision="SELL", position_pct=0.0),
            position=_position(quantity=300, avg_cost=90.0, last_price=100.0),
            equity=100000,
            cash=50000,
        )
        self.assertIn(result["action"], ("减仓", "清仓"))
        self.assertGreaterEqual(result["target_qty"], 0)
        self.assertGreaterEqual(result["current_qty"], 0)
        self.assertEqual(result["action"], "清仓")

    def test_blocked_position_pct_maintains_existing_position(self):
        """决策未给出仓位（门槛未过）时，语义是"不新增"，不是"清仓"。

        此前会被当成目标 0 → 清仓，对已持仓者是非常过强的错误指令。
        """
        result = advise_position(
            "600519",
            _plan(position_pct=0.0),
            position=_position(quantity=300, avg_cost=90.0, last_price=100.0),
            equity=100000,
            cash=50000,
        )
        self.assertAlmostEqual(result["target_weight"], result["current_weight"])
        self.assertEqual(result["target_qty"], result["current_qty"])
        self.assertEqual(result["action"], "持有")
        self.assertEqual(result["delta_qty"], 0)
        self.assertIn("维持", "".join(result["reasons"]))

    def test_blocked_position_pct_without_position_stays_flat(self):
        result = advise_position(
            "600519",
            _plan(position_pct=0.0),
            position=_position(quantity=0, avg_cost=0.0, last_price=100.0),
            equity=100000,
            cash=100000,
        )
        self.assertEqual(result["target_weight"], 0)
        self.assertEqual(result["target_qty"], 0)
        self.assertEqual(result["action"], "观望")

    def test_conflict_maintains_existing_position(self):
        """规则与决策方向冲突时，正确姿态是"不动"，而不是清仓。"""
        result = advise_position(
            "600519",
            _plan(conflict=True),
            position=_position(quantity=300, avg_cost=90.0, last_price=100.0),
            equity=100000,
            cash=50000,
        )
        self.assertAlmostEqual(result["target_weight"], result["current_weight"])
        self.assertEqual(result["target_qty"], result["current_qty"])
        self.assertEqual(result["action"], "持有")
        self.assertEqual(result["delta_qty"], 0)
        text = "".join(result["reasons"])
        self.assertIn("冲突", text)
        self.assertIn("暂不调整", text)

    def test_conflict_without_position_stays_flat(self):
        result = advise_position(
            "600519",
            _plan(conflict=True),
            position=_position(quantity=0, avg_cost=0.0, last_price=100.0),
            equity=100000,
            cash=100000,
        )
        self.assertEqual(result["target_weight"], 0)
        self.assertEqual(result["action"], "观望")

    def test_bearish_decision_overrides_conflict_and_liquidates(self):
        """看空决策优先：即使存在冲突，也按减仓/清仓处理。"""
        result = advise_position(
            "600519",
            _plan(decision="SELL", conflict=True),
            position=_position(quantity=300, avg_cost=90.0, last_price=100.0),
            equity=100000,
            cash=50000,
        )
        self.assertEqual(result["target_weight"], 0)
        self.assertEqual(result["action"], "清仓")

    def test_returned_keys(self):
        result = advise_position(
            "600519",
            _plan(),
            position=_position(quantity=0, last_price=100.0),
            equity=100000,
            cash=100000,
        )
        for key in (
            "success",
            "ticker",
            "action",
            "current_qty",
            "current_weight",
            "target_weight",
            "target_qty",
            "delta_qty",
            "delta_lots",
            "latest_price",
            "avg_cost",
            "unrealised_pnl",
            "unrealised_pnl_pct",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "decision",
            "confidence",
            "alerts",
            "reasons",
            "disclaimer",
        ):
            self.assertIn(key, result)


class PositionAlertTests(unittest.TestCase):
    def test_stop_loss_hit(self):
        alerts = check_position_alerts(
            [
                _position(
                    quantity=100,
                    avg_cost=100.0,
                    last_price=90.0,
                    stop_loss=95.0,
                )
            ]
        )
        self.assertEqual([item["code"] for item in alerts], ["stop_loss_hit"])
        self.assertEqual(alerts[0]["level"], "danger")

    def test_stop_loss_near(self):
        alerts = check_position_alerts(
            [
                _position(
                    quantity=100,
                    avg_cost=100.0,
                    last_price=96.0,
                    stop_loss=95.0,
                )
            ]
        )
        self.assertEqual([item["code"] for item in alerts], ["stop_loss_near"])
        self.assertEqual(alerts[0]["level"], "warn")

    def test_take_profit_hit(self):
        alerts = check_position_alerts(
            [
                _position(
                    quantity=100,
                    avg_cost=100.0,
                    last_price=110.0,
                    stop_loss=90.0,
                    take_profit=105.0,
                )
            ]
        )
        self.assertEqual([item["code"] for item in alerts], ["take_profit_hit"])
        self.assertEqual(alerts[0]["level"], "info")

    def test_deep_loss_without_stop(self):
        alerts = check_position_alerts(
            [
                _position(
                    quantity=100,
                    avg_cost=100.0,
                    last_price=80.0,
                )
            ]
        )
        codes = {item["code"] for item in alerts}
        self.assertIn("deep_loss", codes)
        self.assertIn("no_stop_loss", codes)

    def test_missing_stop_loss(self):
        alerts = check_position_alerts(
            [
                _position(
                    quantity=100,
                    avg_cost=100.0,
                    last_price=100.0,
                )
            ]
        )
        self.assertEqual([item["code"] for item in alerts], ["no_stop_loss"])
        self.assertEqual(alerts[0]["level"], "warn")

    def test_alerts_sorted_by_severity(self):
        alerts = check_position_alerts(
            [
                _position(
                    ticker="000001",
                    quantity=100,
                    avg_cost=100.0,
                    last_price=110.0,
                    stop_loss=90.0,
                    take_profit=105.0,
                ),
                _position(
                    ticker="600519",
                    quantity=100,
                    avg_cost=100.0,
                    last_price=80.0,
                    stop_loss=95.0,
                ),
                _position(
                    ticker="300750",
                    quantity=100,
                    avg_cost=100.0,
                    last_price=80.0,
                ),
            ]
        )
        self.assertEqual(
            [item["level"] for item in alerts],
            ["danger", "warn", "warn", "info"],
        )
        self.assertEqual(alerts[0]["ticker"], "600519")
        self.assertEqual(alerts[-1]["ticker"], "000001")

    def test_summarize_alerts(self):
        alerts = [
            {
                "ticker": "600519",
                "name": "",
                "code": "stop_loss_hit",
                "level": "danger",
                "message": "",
                "detail": {},
            },
            {
                "ticker": "000001",
                "name": "",
                "code": "stop_loss_near",
                "level": "warn",
                "message": "",
                "detail": {},
            },
            {
                "ticker": "300750",
                "name": "",
                "code": "stop_loss_near",
                "level": "warn",
                "message": "",
                "detail": {},
            },
        ]
        summary = summarize_alerts(alerts)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["danger"], 1)
        self.assertEqual(summary["warn"], 2)
        self.assertEqual(summary["info"], 0)
        self.assertEqual(
            summary["headline"], "3 条提醒：1 条止损触发、2 条接近止损"
        )

    def test_summarize_empty_alerts(self):
        summary = summarize_alerts([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["headline"], "暂无持仓提醒")


if __name__ == "__main__":
    unittest.main()
