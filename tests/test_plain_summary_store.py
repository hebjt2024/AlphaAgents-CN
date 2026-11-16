"""报告中心与 WebUI 结果的"人话结论"落库/回读测试。

验证三件事：

1. 报告里的 ``plain_summary`` 会随 reports 一起持久化，读者从报告中心
   点开就能看到人话结论（而不是只有行话原文）。
2. ``GET /api/reports/{id}`` 原样返回 ``plain_summary`` 段。
3. 落库后再解析决策词，仍然以评级小节为准（不会被正文里的"卖出"带偏）。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from tradingagents.services.decision_schema import parse_structured_decision
from tradingagents.services.plain_language import (
    build_plain_summary,
    format_plain_summary,
)
from tradingagents.storage import get_report, save_report

REPORT_TEXT = """## 1. 评级（Rating）

**持有（Hold）**

## 2. 执行摘要（Executive Summary）

- **方向判断**：暂不新增，等待数据验证。

## 4. 执行纪律

- A 股实行 **T+1**：当日买入的不得在当日卖出。
"""


class ReportPlainSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "reports.db")
        self.patch = patch.dict(os.environ, {"TA_DB_PATH": self.db}, clear=False)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _save(self):
        structured = parse_structured_decision(REPORT_TEXT, "HOLD")
        plan = {
            "decision": "HOLD",
            "direction": "中性",
            "score": 50,
            "confidence": 0,
            "consensus": "中性",
            "entry": 5.32,
            "stop_loss": 4.59,
            "take_profit": 6.37,
            "risk_reward": 1.44,
            "position_pct": 0.0,
            "reasons": [],
        }
        plain = build_plain_summary(
            ticker="601118",
            decision="HOLD",
            plan=plan,
            structured=structured,
            decision_text=REPORT_TEXT,
        )
        return save_report(
            ticker="601118",
            trade_date="2026-11-16",
            decision="HOLD",
            decision_text=REPORT_TEXT,
            reports={
                "plain_summary": format_plain_summary(plain),
                "final_trade_decision": REPORT_TEXT,
            },
            source="test",
            meta={"plain_summary": plain},
        )

    def test_plain_summary_persisted(self):
        report_id = self._save()
        report = get_report(report_id)
        self.assertIsNotNone(report)
        self.assertIn("人话结论", report["reports"]["plain_summary"])
        self.assertIn("先不动", report["reports"]["plain_summary"])

    def test_report_detail_api_returns_plain_summary(self):
        from webui.backend import server

        report_id = self._save()
        client = TestClient(server.app)
        resp = client.get(f"/api/reports/{report_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        reports = body["report"]["reports"]
        self.assertIn("plain_summary", reports)
        self.assertIn("人话结论", reports["plain_summary"])

    def test_decision_still_hold_after_roundtrip(self):
        report_id = self._save()
        report = get_report(report_id)
        parsed = parse_structured_decision(
            report["reports"]["final_trade_decision"], "HOLD"
        )
        self.assertEqual(parsed["decision"], "HOLD")


if __name__ == "__main__":
    unittest.main()
