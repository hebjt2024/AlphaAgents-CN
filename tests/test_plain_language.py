"""人话层与决策解析回归测试。

本文件固化三类曾经出过问题的语义：

1. **决策词误判**：正文里出现"当日不得卖出"时，不能把结论判成 SELL；
   评级小节（Rating）的结论优先于正文用词。
2. **行话解释不能破坏机器可读内容**：报告末尾的 ```json 结构化块
   不能被插入括号解释，否则下游解析不出来。
3. **白话结论卡必须说人话**：结论先行、给出具体动作、把数字翻译成百分比，
   而不是把"综合参考分 50 / 100"原样丢给用户。
"""

import unittest

from tradingagents.services.decision_schema import (
    extract_rating_decision,
    parse_structured_decision,
)
from tradingagents.services.plain_language import (
    build_plain_summary,
    explain_terms,
    extract_summary_bullets,
    find_terms,
    format_plain_summary,
    strip_structured_block,
)

# 一份真实的"被截断"报告：评级是持有，但正文里出现了"卖出"。
TRUNCATED_REPORT = """## 1. 评级（Rating）

**持有（Hold）**

这里的"持有"表示：在当前证据不完整的情况下，不新增仓位。

## 2. 执行摘要（Executive Summary）

- **方向判断**：暂不新增，等待盈利预期、估值和中期趋势形成更明确的验证。
- **仓位规模**：当前新增仓位为 **0%**。

### 3.4 条件化交易框架

- A 股实行 **T+1**：当日买入的 601118 不得在当日卖出。
- 601118 为沪市主板标的，常规涨跌幅限制为 **
"""


class RatingSectionTests(unittest.TestCase):
    def test_extract_rating_decision(self):
        self.assertEqual(extract_rating_decision(TRUNCATED_REPORT), "HOLD")

    def test_extract_rating_decision_without_section(self):
        self.assertEqual(extract_rating_decision("随便一段没有评级的话"), "")

    def test_parse_prefers_rating_section_over_body_text(self):
        """正文里的"卖出"不能盖过评级小节的"持有"。"""
        result = parse_structured_decision(TRUNCATED_REPORT, "HOLD")
        self.assertEqual(result["decision"], "HOLD")
        self.assertEqual(result["decision_source"], "rating_section")

    def test_parse_uses_json_when_present(self):
        text = (
            "## 1. 评级（Rating）\n\n**买入（Buy）**\n\n"
            '```json\n{"decision":"BUY","confidence":72,"summary":"可以买",'
            '"key_points":["趋势向上"],"risk_flags":[],"citations":[]}\n```'
        )
        result = parse_structured_decision(text, "HOLD")
        self.assertEqual(result["decision"], "BUY")
        self.assertEqual(result["decision_source"], "json")
        self.assertTrue(result["structured"])
        self.assertEqual(result["confidence"], 72)

    def test_parse_rejects_copied_enum_template(self):
        """模型把提示词模板整段抄回来时不能当成结论。"""
        text = (
            "## 1. 评级（Rating）\n\n**持有（Hold）**\n\n"
            '```json\n{"decision":"BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL"}\n```'
        )
        result = parse_structured_decision(text, "HOLD")
        self.assertEqual(result["decision"], "HOLD")
        self.assertNotEqual(result["decision_source"], "json")


class ExplainTermsTests(unittest.TestCase):
    def test_explains_jargon_once(self):
        out = explain_terms("A 股实行 T+1，买入后当日不能卖。")
        self.assertIn("T+1（当天买入、当天不能卖）", out)
        self.assertEqual(out.count("（当天买入、当天不能卖）"), 1)

    def test_skips_already_explained(self):
        out = explain_terms("A 股实行 T+1（当天买入、当天不能卖）。")
        self.assertEqual(out.count("当天买入、当天不能卖"), 1)

    def test_strip_structured_block(self):
        text = (
            "## 1. 评级\n\n持有\n\n正文\n\n"
            '```json\n{"decision":"HOLD","confidence":50}\n```\n'
        )
        out = strip_structured_block(text)
        self.assertNotIn("```json", out)
        self.assertIn("持有", out)

    def test_strip_keeps_other_code_blocks(self):
        text = "正文\n\n```python\nprint(1)\n```\n"
        self.assertIn("```python", strip_structured_block(text))

    def test_keeps_json_block_untouched(self):
        """JSON 代码块里不能插括号，否则结构化决策就解析不出来了。"""
        payload = '{"decision":"HOLD","summary":"A 股实行 T+1"}'
        text = f"## 1. 评级\n\n持有\n\n```json\n{payload}\n```"
        out = explain_terms(text)
        self.assertIn(payload, out)

    def test_find_terms(self):
        found = find_terms("当前 T+1 制度下，止损位参考 EMA")
        terms = {item["term"] for item in found}
        self.assertIn("T+1", terms)
        self.assertIn("EMA", terms)


class PlainSummaryTests(unittest.TestCase):
    def _plan(self):
        return {
            "decision": "HOLD",
            "direction": "中性",
            "score": 50,
            "confidence": 0,
            "consensus": "中性",
            "conflict": False,
            "entry": 5.32,
            "stop_loss": 4.59,
            "take_profit": 6.37,
            "risk_reward": 1.44,
            "position_pct": 0.0,
            "risk_pct": 0.137,
            "triggered": 0,
            "family_count": 0,
            "reasons": ["策略共识：中性（净分 0，强度 0%）"],
        }

    def test_build_plain_summary_hold(self):
        summary = build_plain_summary(
            ticker="601118",
            decision="HOLD",
            plan=self._plan(),
            structured=parse_structured_decision(TRUNCATED_REPORT, "HOLD"),
            decision_text=TRUNCATED_REPORT,
        )
        self.assertEqual(summary["decision"], "HOLD")
        self.assertIn("601118", summary["headline"])
        self.assertIn("先不动", summary["headline"])
        self.assertTrue(summary["do_list"])
        self.assertTrue(summary["risks"])

    def test_price_numbers_are_translated_to_percent(self):
        summary = build_plain_summary(
            ticker="601118",
            decision="HOLD",
            plan=self._plan(),
            structured=parse_structured_decision(TRUNCATED_REPORT, "HOLD"),
            decision_text=TRUNCATED_REPORT,
        )
        numbers = {item["label"]: item for item in summary["numbers"]}
        stop = numbers["止损位（认亏离场价）"]
        self.assertIn("13.7%", stop["plain"])
        target = numbers["目标位（可以了结的价）"]
        self.assertIn("19.7%", target["plain"])

    def test_buy_summary_gives_actionable_steps(self):
        plan = dict(self._plan())
        plan.update({"decision": "BUY", "position_pct": 0.1, "score": 70})
        summary = build_plain_summary(
            ticker="601118",
            decision="BUY",
            plan=plan,
            structured={"structured": True, "confidence": 70, "key_points": ["趋势向上"]},
            decision_text=TRUNCATED_REPORT,
        )
        self.assertEqual(summary["decision"], "BUY")
        self.assertIn("买", summary["action"])
        self.assertTrue(any("5.32" in item for item in summary["do_list"]))

    def test_missing_data_does_not_raise(self):
        summary = build_plain_summary()
        self.assertIn("headline", summary)
        self.assertEqual(summary["decision"], "HOLD")
        self.assertTrue(summary["disclaimer"])

    def test_non_dict_plan_does_not_raise(self):
        """历史报告里 decision_plan 存的是 Markdown 字符串，不能当字典用。"""
        summary = build_plain_summary(
            ticker="601118",
            decision="HOLD",
            plan="### 结构化决策卡\n- **决策**：HOLD",
            structured="**决策**：HOLD",  # 同一个坑的另一处
            decision_text=TRUNCATED_REPORT,
        )
        self.assertEqual(summary["decision"], "HOLD")
        self.assertTrue(summary["why"])

    def test_bearish_summary_does_not_mislabel_price_levels(self):
        """看空/减仓时止损位在现价上方，不能说成"比现价低"。"""
        plan = dict(self._plan())
        plan.update({"decision": "SELL", "stop_loss": 5.9, "take_profit": 4.2})
        summary = build_plain_summary(
            ticker="601118", decision="SELL", plan=plan, decision_text=TRUNCATED_REPORT
        )
        numbers = summary["numbers"]
        labels = [item["label"] for item in numbers]
        self.assertNotIn("止损位（认亏离场价）", labels)
        self.assertEqual(summary["decision"], "SELL")

    def test_long_reason_is_shortened(self):
        long_bullet = "- " + "很长的理由" * 40
        text = "## 2. 执行摘要（Executive Summary）\n\n" + long_bullet
        summary = build_plain_summary(
            ticker="601118", decision="HOLD", decision_text=text
        )
        self.assertTrue(summary["why"])
        self.assertLessEqual(len(summary["why"][0]), 145)

    def test_format_plain_summary_markdown(self):
        summary = build_plain_summary(
            ticker="601118",
            decision="HOLD",
            plan=self._plan(),
            structured=parse_structured_decision(TRUNCATED_REPORT, "HOLD"),
            decision_text=TRUNCATED_REPORT,
        )
        md = format_plain_summary(summary)
        self.assertIn("### 人话结论", md)
        self.assertIn("该怎么做", md)
        self.assertIn("术语对照", md)

    def test_rewrite_returns_empty_without_llm(self):
        from tradingagents.services.plain_language import (
            rewrite_in_plain_language,
        )

        self.assertEqual(rewrite_in_plain_language("原文", None), "")

    def test_rewrite_uses_llm_and_survives_failure(self):
        from tradingagents.services.plain_language import (
            rewrite_in_plain_language,
        )

        class _Resp:
            content = "白话版正文"

        class _Good:
            def invoke(self, prompt):
                return _Resp()

        class _Bad:
            def invoke(self, prompt):
                raise RuntimeError("boom")

        self.assertEqual(
            rewrite_in_plain_language("原文", _Good(), ticker="601118"), "白话版正文"
        )
        self.assertEqual(rewrite_in_plain_language("原文", _Bad()), "")

    def test_extract_summary_bullets(self):
        bullets = extract_summary_bullets(TRUNCATED_REPORT)
        self.assertTrue(bullets)
        self.assertTrue(bullets[0].startswith("方向判断"))
        self.assertNotIn("**", bullets[0])


if __name__ == "__main__":
    unittest.main()
