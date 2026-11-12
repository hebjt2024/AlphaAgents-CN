"""结构化决策卡测试。"""

import unittest

from tradingagents.services.decision_plan import (
    build_decision_plan,
    format_decision_plan,
)


def sample_result():
    return {
        "price": {
            "close": 100.0,
            "atr": 2.0,
            "recent_high20": 110.0,
            "recent_low20": 95.0,
        },
        "summary": {
            "consensus": "偏多",
            "net_score": 20,
            "strength": 60,
            "triggered": 3,
        },
    }


class DecisionPlanTests(unittest.TestCase):
    def test_buy_plan(self):
        plan = build_decision_plan(sample_result(), "BUY")
        self.assertEqual(plan["decision"], "BUY")
        self.assertGreater(plan["score"], 50)
        self.assertGreater(plan["confidence"], 0)
        self.assertEqual(plan["entry"], 100.0)
        self.assertLess(plan["stop_loss"], 100.0)
        self.assertGreaterEqual(plan["take_profit"], 110.0)
        self.assertGreater(plan["position_pct"], 0)
        self.assertGreater(plan["risk_reward"], 0)

    def test_sell_plan(self):
        plan = build_decision_plan(sample_result(), "SELL")
        self.assertGreater(plan["stop_loss"], 100.0)
        self.assertLess(plan["take_profit"], 100.0)
        self.assertGreater(plan["position_pct"], 0)

    def test_hold_plan_has_zero_position(self):
        plan = build_decision_plan(sample_result(), "HOLD")
        self.assertEqual(plan["position_pct"], 0.0)
        self.assertLess(plan["stop_loss"], 100.0)
        self.assertGreater(plan["take_profit"], 100.0)

    def test_missing_price_is_safe(self):
        plan = build_decision_plan({}, "BUY")
        self.assertEqual(plan["entry"], 0.0)
        self.assertEqual(plan["stop_loss"], 0.0)

    def test_format(self):
        text = format_decision_plan(build_decision_plan(sample_result(), "BUY"))
        self.assertIn("结构化决策卡", text)
        self.assertIn("止损参考", text)


if __name__ == "__main__":
    unittest.main()
