"""P0 回归测试：最终决策必须是可枚举、可归一化的。

背景：此前 ``SignalProcessor.process_signal`` 直接返回 ``result.strip()``，
于是 ``"BUY."``、``"**BUY**"``、``"买入"`` 都会原样透传。下游决策卡按精确
匹配解析，这类字符串会被**静默降级为"持有"且建议仓位归零**，而界面仍
显示模型的原始字符串 —— 结论与呈现不一致，且没有任何提示。
"""

import unittest

from tradingagents.graph.signal_processing import (
    ALLOWED_DECISIONS,
    normalize_decision,
)


class NormalizeDecisionTests(unittest.TestCase):
    def test_exact_english_decisions(self):
        for decision in ALLOWED_DECISIONS:
            self.assertEqual(normalize_decision(decision), (decision, True))

    def test_strips_markdown_and_punctuation(self):
        for raw in ("**BUY**", "BUY.", "  buy  ", "`SELL`", "HOLD!!!", "**overweight**"):
            decision, ok = normalize_decision(raw)
            self.assertTrue(ok, raw)
            self.assertIn(decision, ALLOWED_DECISIONS, raw)

    def test_chinese_variants(self):
        cases = {
            "买入": "BUY",
            "增持": "BUY",
            "强烈买入": "BUY",
            "超配": "OVERWEIGHT",
            "持有": "HOLD",
            "观望": "HOLD",
            "中性": "HOLD",
            "低配": "UNDERWEIGHT",
            "减配": "UNDERWEIGHT",
            "卖出": "SELL",
            "清仓": "SELL",
        }
        for raw, expected in cases.items():
            decision, ok = normalize_decision(raw)
            self.assertTrue(ok, raw)
            self.assertEqual(decision, expected, raw)

    def test_extracts_from_sentence(self):
        decision, ok = normalize_decision(
            "综合以上分析，我给出评级：买入。目标价 200 元。"
        )
        self.assertTrue(ok)
        self.assertEqual(decision, "BUY")

    def test_unknown_falls_back_to_hold_and_reports_failure(self):
        for raw in ("", "   ", None, "无法判断", "???"):
            decision, ok = normalize_decision(raw)
            self.assertFalse(ok, repr(raw))
            self.assertEqual(decision, "HOLD")

    def test_never_returns_non_enum(self):
        for raw in ("BUY.", "**SELL**", "买入", "随便一段话", "OVERWEIGHT"):
            decision, _ = normalize_decision(raw)
            self.assertIn(decision, ALLOWED_DECISIONS, repr(raw))


if __name__ == "__main__":
    unittest.main()
