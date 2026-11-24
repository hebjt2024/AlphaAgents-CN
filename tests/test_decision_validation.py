"""投资结论校验（决策对齐）单元测试。

覆盖：
1. 各来源一致 → ok；
2. 方向矛盾（最终 vs 结构化决策卡 / 结构化决策 / 规则初判）→ contradicted；
3. 规则初判与最终差异（方向一致）→ warning；
4. 置信度偏低 → warning；
5. 最终决策词非法 → contradicted；
6. 与上一次结论方向相反 → warning；
7. get_stored_decision：有持久化结论返回，无则 None。
"""

from __future__ import annotations

import unittest

from tradingagents.services.decision_schema import ALLOWED_DECISIONS
from tradingagents.services.decision_validation import (
    direction_of,
    get_stored_decision,
    validate_investment_conclusion,
)
from tradingagents.storage import delete_report, list_reports, save_report


class ValidateInvestmentConclusionTests(unittest.TestCase):
    def test_aligned_sources_ok(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY", "confidence": 60},
            structured_decision={"decision": "BUY", "confidence": 65},
            decision_diff={"has_diff": False, "note": ""},
            stored_decision="HOLD",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["level"], "ok")
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["warnings"], [])

    def test_neutral_differs_is_not_contradiction(self):
        result = validate_investment_conclusion(
            decision="HOLD",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "HOLD"},
            decision_diff={"has_diff": True, "preliminary_decision": "BUY"},
        )
        # final 为中性，不算矛盾；但差异本身会产生 warning
        self.assertEqual(result["mismatches"], [])
        self.assertTrue(result["warnings"])
        self.assertFalse(result["ok"])

    def test_contradiction_final_vs_structured(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "SELL"},
            decision_diff={"has_diff": False, "note": ""},
        )
        self.assertEqual(result["level"], "contradicted")
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("结构化决策" in m for m in result["mismatches"]), result["mismatches"]
        )

    def test_contradiction_final_vs_decision_plan(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "SELL"},
            structured_decision={"decision": "BUY"},
            decision_diff={"has_diff": False, "note": ""},
        )
        self.assertEqual(result["level"], "contradicted")
        self.assertTrue(
            any("结构化决策卡" in m for m in result["mismatches"]), result["mismatches"]
        )

    def test_contradiction_final_vs_preliminary(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "BUY"},
            decision_diff={"has_diff": True, "preliminary_decision": "SELL"},
        )
        self.assertEqual(result["level"], "contradicted")
        self.assertTrue(
            any("规则初判" in m for m in result["mismatches"]), result["mismatches"]
        )

    def test_same_group_is_not_contradiction(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "OVERWEIGHT"},
            structured_decision={"decision": "OVERWEIGHT"},
            decision_diff={"has_diff": False, "note": ""},
        )
        self.assertEqual(result["mismatches"], [])
        self.assertTrue(result["ok"])

    def test_rule_diff_warning(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "BUY"},
            decision_diff={
                "has_diff": True,
                "preliminary_decision": "HOLD",
                "note": "规则初判为 HOLD，最终决策为 BUY，两者方向不一致，请重点核对分歧原因。",
            },
        )
        self.assertTrue(result["warnings"])
        self.assertEqual(result["mismatches"], [])

    def test_low_confidence_warning(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "BUY", "confidence": 30},
            decision_diff={"has_diff": False, "note": ""},
        )
        self.assertTrue(any("置信度" in w for w in result["warnings"]))
        self.assertFalse(result["ok"])

    def test_invalid_decision_contradicted(self):
        result = validate_investment_conclusion(decision="SOMETHING_WRONG")
        self.assertEqual(result["level"], "contradicted")
        self.assertTrue(result["mismatches"])

    def test_stored_direction_flip_warning(self):
        result = validate_investment_conclusion(
            decision="BUY",
            decision_plan={"decision": "BUY"},
            structured_decision={"decision": "BUY"},
            decision_diff={"has_diff": False, "note": ""},
            stored_decision="SELL",
        )
        self.assertTrue(any("上一次结论" in w for w in result["warnings"]))
        # 方向翻转是 warning（ok=False），但不算矛盾
        self.assertFalse(result["ok"])
        self.assertEqual(result["mismatches"], [])

    def test_direction_of(self):
        self.assertEqual(direction_of("BUY"), "多")
        self.assertEqual(direction_of("OVERWEIGHT"), "多")
        self.assertEqual(direction_of("SELL"), "空")
        self.assertEqual(direction_of("UNDERWEIGHT"), "空")
        self.assertEqual(direction_of("HOLD"), "中")
        self.assertEqual(direction_of(""), "中")

    def test_get_stored_decision_persisted(self):
        report_id = save_report(
            ticker="TESTVALD",
            trade_date="2026-11-24",
            decision="BUY",
            decision_text="test",
            reports={},
            source="test",
        )
        try:
            self.assertEqual(get_stored_decision("TESTVALD"), "BUY")
        finally:
            delete_report(report_id)

    def test_get_stored_decision_none_when_missing(self):
        # 假定该代码一直没有持久化过
        existing = [
            r["ticker"] for r in list_reports(limit=50) if r["ticker"] == "TESTNODEC"
        ]
        if existing:
            self.skipTest("旧数据中已有 TESTNODEC 结论，跳过")
        self.assertIsNone(get_stored_decision("TESTNODEC"))

    def test_validation_result_keys(self):
        result = validate_investment_conclusion(decision="BUY")
        self.assertIn("ok", result)
        self.assertIn("level", result)
        self.assertIn("mismatches", result)
        self.assertIn("warnings", result)
        self.assertIn("summary", result)
        self.assertEqual(result["level"], "ok")


if __name__ == "__main__":
    unittest.main()
