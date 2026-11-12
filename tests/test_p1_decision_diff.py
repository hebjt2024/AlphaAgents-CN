"""P1 回归测试：规则初判与 LLM 最终决策的差异必须可见。

背景：规则初判（策略扫描）与组合经理的 LLM 结论是两个**独立来源**。
此前系统只展示最终结论，用户看到"规则说偏多、结论说卖出"时无从察觉 ——
这类分歧恰恰是最值得关注的信号。
"""

import unittest

from tradingagents.services.decision_plan import compare_decisions
from tradingagents.services.fast_preview import preliminary_decision


class PreliminaryDecisionTests(unittest.TestCase):
    def test_buy_requires_enough_independent_families(self):
        single = {
            "net_score": 85,
            "strength": 100,
            "triggered": 1,
            "family_count": 1,
        }
        self.assertNotEqual(preliminary_decision(single), "BUY")

    def test_buy_when_enough_families_agree(self):
        data = {
            "net_score": 85,
            "strength": 100,
            "triggered": 3,
            "family_count": 3,
        }
        self.assertEqual(preliminary_decision(data), "BUY")

    def test_hold_when_net_neutral(self):
        data = {"net_score": 0, "strength": 0, "triggered": 0, "family_count": 0}
        self.assertEqual(preliminary_decision(data), "HOLD")

    def test_sell_direction(self):
        data = {
            "net_score": -85,
            "strength": 100,
            "triggered": 3,
            "family_count": 3,
        }
        self.assertEqual(preliminary_decision(data), "SELL")


class CompareDecisionsTests(unittest.TestCase):
    def test_same_decision_no_diff(self):
        result = compare_decisions("BUY", "BUY")
        self.assertFalse(result["has_diff"])
        self.assertFalse(result["decision_changed"])
        self.assertEqual(result["note"], "")

    def test_different_decision_is_flagged(self):
        result = compare_decisions("BUY", "SELL")
        self.assertTrue(result["has_diff"])
        self.assertTrue(result["decision_changed"])
        self.assertIn("不一致", result["note"])
        self.assertEqual(result["preliminary_decision"], "BUY")
        self.assertEqual(result["final_decision"], "SELL")

    def test_parameter_differences_are_listed(self):
        preliminary_plan = {
            "score": 60,
            "confidence": 40,
            "position_pct": 0.1,
            "risk_reward": 2.0,
            "direction": "偏多",
        }
        final_plan = {
            "score": 75,
            "confidence": 55,
            "position_pct": 0.15,
            "risk_reward": 2.0,
            "direction": "偏多",
        }
        result = compare_decisions(
            "OVERWEIGHT", "BUY", preliminary_plan, final_plan
        )
        self.assertTrue(result["has_diff"])
        self.assertIn("score", result["changed_fields"])
        self.assertIn("confidence", result["changed_fields"])
        self.assertIn("position_pct", result["changed_fields"])
        self.assertNotIn("risk_reward", result["changed_fields"])
        self.assertAlmostEqual(result["score_delta"], 15.0)
        self.assertAlmostEqual(result["position_delta"], 0.05)

    def test_direction_change_is_detected(self):
        preliminary_plan = {"direction": "偏多", "score": 60, "confidence": 40,
                            "position_pct": 0.1, "risk_reward": 2.0}
        final_plan = {"direction": "偏空", "score": 40, "confidence": 40,
                      "position_pct": 0.0, "risk_reward": 2.0}
        result = compare_decisions("BUY", "SELL", preliminary_plan, final_plan)
        self.assertTrue(result["direction_changed"])


if __name__ == "__main__":
    unittest.main()
