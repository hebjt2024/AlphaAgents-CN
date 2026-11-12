"""P1 回归测试：策略族去重。

背景：15 个策略并非 15 个独立假设（均线金叉与 MACD 金叉触发集合完全
相同；平台突破是放量突破的严格子集；龙头策略是多头趋势的严格子集；
箱体震荡与 RSI 反转高度重合）。旧实现直接把这些相关信号的分数相加，
等于把同一个假设数了多遍，系统性高估共识。
"""

import unittest

from tradingagents.strategies.base import StrategySignal
from tradingagents.strategies.families import (
    STRATEGY_FAMILIES,
    aggregate_by_family,
    family_label,
    family_of,
)


def _sig(key, direction="bullish", score=80, triggered=True):
    return StrategySignal(
        key=key,
        name=key,
        category="test",
        triggered=triggered,
        direction=direction,
        score=score,
        summary="",
    )


class FamilyMappingTests(unittest.TestCase):
    def test_every_strategy_is_registered(self):
        from tradingagents.strategies.classic import ALL_STRATEGIES

        keys = {func.__name__ for func in ALL_STRATEGIES}
        missing = keys - set(STRATEGY_FAMILIES)
        self.assertEqual(missing, set(), f"未归族的策略：{missing}")

    def test_confirmed_overlaps_share_a_family(self):
        # 触发集合完全相同 / 严格子集的关系必须归入同一族
        self.assertEqual(family_of("ma_golden_cross"), family_of("macd_cross"))
        self.assertEqual(family_of("volume_breakout"), family_of("platform_breakout"))
        self.assertEqual(family_of("bull_trend"), family_of("dragon_head"))
        self.assertEqual(family_of("box_oscillation"), family_of("rsi_reversal"))

    def test_labels_are_chinese(self):
        for family in set(STRATEGY_FAMILIES.values()):
            self.assertTrue(family_label(family))


class AggregateByFamilyTests(unittest.TestCase):
    def test_same_family_counts_once(self):
        """同族 3 个信号只算一票。"""
        result = aggregate_by_family(
            [
                _sig("ma_golden_cross", score=90),
                _sig("macd_cross", score=80),
                _sig("bull_trend", score=85),
            ]
        )
        self.assertEqual(result["family_count"], 1)
        self.assertEqual(result["net_score"], 90)  # 族内取最高分
        self.assertEqual(result["raw_net_score"], 255)

    def test_different_families_count_separately(self):
        result = aggregate_by_family(
            [
                _sig("bull_trend", score=90),      # 趋势族
                _sig("volume_breakout", score=80),  # 突破族
                _sig("rsi_reversal", score=70),     # 反转族
            ]
        )
        self.assertEqual(result["family_count"], 3)
        self.assertEqual(result["net_score"], 240)

    def test_mixed_directions_within_family(self):
        """同族多空对冲时，族内取各自最高分后相减。"""
        result = aggregate_by_family(
            [
                _sig("bull_trend", "bullish", 90),
                _sig("macd_cross", "bearish", 60),
            ]
        )
        self.assertEqual(result["family_count"], 1)
        self.assertEqual(result["net_score"], 30)

    def test_untriggered_signals_are_ignored(self):
        result = aggregate_by_family(
            [
                _sig("bull_trend", score=90, triggered=False),
                _sig("volume_breakout", score=80),
            ]
        )
        self.assertEqual(result["family_count"], 1)
        self.assertEqual(result["net_score"], 80)

    def test_deduplication_never_inflates_net_score(self):
        """去重后的净分必须不大于原始求和。"""
        signals = [
            _sig("ma_golden_cross", score=90),
            _sig("macd_cross", score=85),
            _sig("bull_trend", score=80),
            _sig("dragon_head", score=75),
        ]
        result = aggregate_by_family(signals)
        self.assertLessEqual(result["net_score"], result["raw_net_score"])

    def test_families_detail_is_returned(self):
        result = aggregate_by_family([_sig("bull_trend", score=90)])
        families = result["families"]
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0]["family"], "trend")
        self.assertEqual(families[0]["direction"], "bullish")
        self.assertEqual(len(families[0]["bull_signals"]), 1)


if __name__ == "__main__":
    unittest.main()
