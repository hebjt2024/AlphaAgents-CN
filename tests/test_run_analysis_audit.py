"""run_analysis 审计事件单元测试。

覆盖：
1. 成功运行写入 success 审计事件（detail 含 report_id / decision / validation_level）；
2. 运行失败写入 failure 审计事件并重新抛出原异常。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tradingagents.storage.audit_store import list_audit_events


def _events_for(ticker: str) -> list[dict]:
    return [
        e
        for e in list_audit_events(limit=50)
        if ticker in (e.get("action") or "")
    ]


class RunAnalysisAuditTests(unittest.TestCase):
    def _final_state(self) -> dict:
        return {
            "final_trade_decision": "BUY",
            "market_report": "",
            "fundamentals_report": "",
            "news_report": "",
            "sentiment_report": "",
            "strategy_report": "",
            "trader_investment_plan": "",
        }

    def test_success_appends_audit_event(self):
        from tradingagents.services.runner import run_analysis

        fake_graph = MagicMock()
        fake_graph.propagate.return_value = (self._final_state(), "BUY")
        with patch(
            "tradingagents.graph.trading_graph.TradingAgentsGraph",
            return_value=fake_graph,
        ):
            result = run_analysis("TESTRUN", "2026-11-24", notify=False)

        self.assertEqual(result["decision"], "BUY")
        events = _events_for("TESTRUN")
        self.assertTrue(events, "成功运行应写入审计事件")
        self.assertEqual(events[0]["status"], 200)
        detail = events[0].get("detail") or {}
        self.assertEqual(detail.get("report_id"), result["report_id"])
        self.assertEqual(detail.get("decision"), "BUY")

    def test_failure_appends_audit_event_and_raises(self):
        from tradingagents.services.runner import run_analysis

        fake_graph = MagicMock()
        fake_graph.propagate.side_effect = RuntimeError("mock boom")
        with patch(
            "tradingagents.graph.trading_graph.TradingAgentsGraph",
            return_value=fake_graph,
        ):
            with self.assertRaises(RuntimeError):
                run_analysis("TESTRUNFAIL", "2026-11-24", notify=False)

        events = _events_for("TESTRUNFAIL")
        self.assertTrue(events, "失败运行应写入审计事件")
        self.assertEqual(events[0]["status"], 500)
        detail = events[0].get("detail") or {}
        self.assertIn("mock boom", detail.get("error", ""))


if __name__ == "__main__":
    unittest.main()
