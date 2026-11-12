"""结构化决策卡测试。

本文件同时承担 P0 回归测试的职责：固化以下曾经出错的语义

- 止损必须放在支撑位**外侧**（做多取 ``min(low20, close - 2*ATR)``），
  而不是贴着 20 日最低点；
- 目标价取**保守**的一侧，不默认"一定能摸到 20 日最高点"；
- BUY 与 OVERWEIGHT 必须给出不同的仓位与评分；
- 单条规则信号不足以给出仓位；
- 规则共识与最终决策方向冲突时不给出仓位；
- A 股不可做空：卖出/低配的建议仓位为 0。
"""

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


def bearish_result():
    data = sample_result()
    data["summary"] = {
        "consensus": "偏空",
        "net_score": -20,
        "strength": 60,
        "triggered": 3,
    }
    return data


class DecisionPlanTests(unittest.TestCase):
    def test_buy_plan(self):
        plan = build_decision_plan(sample_result(), "BUY")
        self.assertEqual(plan["decision"], "BUY")
        self.assertGreater(plan["score"], 50)
        self.assertGreater(plan["confidence"], 0)
        self.assertEqual(plan["entry"], 100.0)
        self.assertLess(plan["stop_loss"], 100.0)
        self.assertGreater(plan["take_profit"], 100.0)
        self.assertGreater(plan["position_pct"], 0)
        self.assertGreater(plan["risk_reward"], 0)

    def test_sell_plan(self):
        plan = build_decision_plan(bearish_result(), "SELL")
        self.assertGreater(plan["stop_loss"], 100.0)
        self.assertLess(plan["take_profit"], 100.0)
        # A 股不可做空 → 卖出对应的建议仓位为 0（含义是减仓/清仓）
        self.assertEqual(plan["position_pct"], 0.0)

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


class StopAndTargetPlacementTests(unittest.TestCase):
    """P0-2：止损/目标的放置方向。"""

    def test_long_stop_is_below_support_not_at_it(self):
        """做多时止损必须低于 20 日最低点，不能正好落在支撑位上。"""
        plan = build_decision_plan(sample_result(), "BUY")
        self.assertLess(
            plan["stop_loss"],
            95.0,
            "止损贴在 20 日最低点上，会把止损位放在止损盘密集区",
        )

    def test_long_target_is_conservative(self):
        """做多目标取保守的一侧：不再默认一定能摸到 20 日最高点。"""
        plan = build_decision_plan(sample_result(), "BUY")
        self.assertLessEqual(plan["take_profit"], 110.0)
        self.assertGreater(plan["take_profit"], 100.0)

    def test_tighter_stop_does_not_inflate_position(self):
        """止损变近不应让仓位变大（此前 stop 被夹紧导致仓位反向放大）。"""
        wide = sample_result()
        wide["price"]["recent_low20"] = 80.0  # 支撑很低 → 止损很远 → 风险大
        narrow = sample_result()
        narrow["price"]["recent_low20"] = 98.0  # 支撑很近 → 止损近 → 风险小

        plan_wide = build_decision_plan(wide, "BUY")
        plan_narrow = build_decision_plan(narrow, "BUY")

        self.assertGreater(plan_wide["risk_pct"], plan_narrow["risk_pct"])
        self.assertLessEqual(
            plan_wide["position_pct"],
            plan_narrow["position_pct"],
            "风险更大时仓位不应更大",
        )


class ConvictionSeparationTests(unittest.TestCase):
    """P0-2：BUY 与 OVERWEIGHT 不能是同一张卡。"""

    def test_buy_and_overweight_differ(self):
        buy = build_decision_plan(sample_result(), "BUY")
        overweight = build_decision_plan(sample_result(), "OVERWEIGHT")
        self.assertNotEqual(buy["score"], overweight["score"])
        self.assertNotEqual(buy["position_pct"], overweight["position_pct"])
        self.assertGreater(buy["position_pct"], overweight["position_pct"])


class PositionGateTests(unittest.TestCase):
    """P0-2：单条信号不给仓位；方向冲突不给仓位。"""

    def test_single_signal_gives_no_position(self):
        data = sample_result()
        data["summary"]["triggered"] = 1
        plan = build_decision_plan(data, "BUY")
        self.assertEqual(plan["position_pct"], 0.0)
        self.assertTrue(
            any("门槛" in reason for reason in plan["reasons"]),
            plan["reasons"],
        )

    def test_single_family_does_not_get_top_score(self):
        """只有一个独立信号族时，综合分不应接近满分。

        net_score 是族内去重后的净分，一个族最多贡献一个族的分数。
        若不按族数折减，"只有趋势族一个信号"也会拿到 95 分，
        而综合分本应表达"多路独立证据的一致程度"。
        """
        data = sample_result()
        data["summary"].update(
            {"net_score": 85, "strength": 100, "triggered": 1, "family_count": 1}
        )
        single = build_decision_plan(data, "BUY")

        data["summary"].update({"triggered": 3, "family_count": 3})
        broad = build_decision_plan(data, "BUY")

        self.assertLess(single["score"], broad["score"])
        self.assertLess(single["score"], 85)
        self.assertLess(single["confidence"], broad["confidence"])

    def test_breadth_monotonic(self):
        scores = []
        for families in (1, 2, 3):
            data = sample_result()
            data["summary"].update(
                {"net_score": 85, "strength": 100, "triggered": families,
                 "family_count": families}
            )
            scores.append(build_decision_plan(data, "BUY")["score"])
        self.assertEqual(scores, sorted(scores))
        self.assertLess(scores[0], scores[-1])

    def test_conflict_between_rules_and_decision(self):
        """规则看多、决策看空 → 标记冲突且不给仓位。"""
        plan = build_decision_plan(sample_result(), "SELL")
        self.assertTrue(plan["conflict"])
        self.assertEqual(plan["position_pct"], 0.0)
        self.assertTrue(any("冲突" in reason for reason in plan["reasons"]))

    def test_aligned_bearish_decision_has_no_conflict(self):
        plan = build_decision_plan(bearish_result(), "SELL")
        self.assertFalse(plan["conflict"])

    def test_direction_field_present(self):
        plan = build_decision_plan(sample_result(), "BUY")
        self.assertEqual(plan["direction"], "偏多")
        self.assertEqual(build_decision_plan(sample_result(), "HOLD")["direction"], "中性")


if __name__ == "__main__":
    unittest.main()
