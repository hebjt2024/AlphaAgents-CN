"""回归测试：分析流程自动附持仓建议；业绩预告按报告期缓存。

① 持仓建议接进分析流程
   系统产出的是"买不买"，持仓者关心的是"卖不卖"。分析结束后若账户
   持有该标的，应自动给出加/减/清仓建议，而不是让用户再去点一次快捷工具。

② 业绩预告缓存
   ``stock_yjyg_em`` / ``stock_yjkb_em`` 按报告期返回全市场数据，一次查询
   最多扇出 6 个报告期 × 2 个接口。若沿用 30 分钟 TTL，几乎每次分析都要
   重新拉一遍，明显拖慢分析。报告期数据的 TTL 应显著更长。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.services import position_advisor
from tradingagents.services.risk_engine import PositionView

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="alphaagents-advice-"), "a.db")
os.environ.setdefault("TA_DB_PATH", _TMP_DB)


PLAN = {
    "decision": "SELL",
    "direction": "偏空",
    "score": 40,
    "confidence": 60,
    "position_pct": 0.0,
    "risk_pct": 0.085,
    "stop_loss": 1280.0,
    "take_profit": 1100.0,
    "entry": 1402.3,
    "risk_reward": 2.0,
    "conflict": False,
    "triggered": 3,
    "family_count": 3,
}


class AdviceForAnalysisTests(unittest.TestCase):
    def test_returns_none_when_no_position(self):
        with patch(
            "tradingagents.services.portfolio.load_positions", return_value=[]
        ):
            result = position_advisor.advice_for_analysis("600519", PLAN)
        self.assertIsNone(result)

    def test_returns_advice_when_holding(self):
        position = PositionView(
            ticker="600519",
            name="贵州茅台",
            quantity=300,
            avg_cost=1300,
            last_price=1402.3,
            stop_loss=1280,
            take_profit=1550,
        )
        with (
            patch(
                "tradingagents.services.portfolio.load_positions",
                return_value=[position],
            ),
            patch.object(
                position_advisor,
                "advise_position",
                return_value={
                    "success": True,
                    "ticker": "600519",
                    "action": "减仓",
                    "current_qty": 300,
                    "current_weight": 0.18,
                    "target_qty": 200,
                    "target_weight": 0.12,
                    "delta_qty": -100,
                    "unrealised_pnl": 30690,
                    "unrealised_pnl_pct": 0.0787,
                    "stop_loss": 1280,
                    "take_profit": 1550,
                    "alerts": [],
                    "reasons": ["决策卡目标仓位 12%"],
                    "disclaimer": "仅供参考",
                },
            ),
        ):
            result = position_advisor.advice_for_analysis("600519", PLAN)

        self.assertIsNotNone(result)
        self.assertTrue(result["has_position"])
        self.assertEqual(result["advice"]["action"], "减仓")
        self.assertIn("持仓建议", result["markdown"])
        self.assertIn("卖出 100 股", result["markdown"])

    def test_load_failure_does_not_break_analysis(self):
        with patch(
            "tradingagents.services.portfolio.load_positions",
            side_effect=RuntimeError("db locked"),
        ):
            self.assertIsNone(position_advisor.advice_for_analysis("600519", PLAN))

    def test_markdown_contains_alerts_and_reasons(self):
        advice = {
            "success": True,
            "action": "清仓",
            "current_qty": 100,
            "current_weight": 0.05,
            "target_qty": 0,
            "target_weight": 0.0,
            "delta_qty": -100,
            "unrealised_pnl": -500,
            "unrealised_pnl_pct": -0.05,
            "stop_loss": 1280,
            "take_profit": 1550,
            "alerts": [
                {"code": "stop_loss_hit", "level": "danger",
                 "message": "跌破止损位 1280"}
            ],
            "reasons": ["规则与决策方向冲突"],
            "disclaimer": "仅供参考",
        }
        text = position_advisor.format_position_advice(advice)
        self.assertIn("卖出 100 股", text)
        self.assertIn("跌破止损位", text)
        self.assertIn("规则与决策方向冲突", text)

    def test_format_returns_empty_for_failed_advice(self):
        self.assertEqual(
            position_advisor.format_position_advice({"success": False}), ""
        )
        self.assertEqual(position_advisor.format_position_advice({}), "")


class EarningsCacheTests(unittest.TestCase):
    def test_period_cache_ttl_is_longer_than_default(self):
        """报告期数据的缓存 TTL 必须显著长于普通全市场快照。"""
        from tradingagents.dataflows import ashare_events

        self.assertGreater(
            ashare_events._PERIOD_MARKET_TTL,
            ashare_events._MARKET_TTL * 4,
            "报告期数据 TTL 应远大于 30 分钟，否则每次分析都会重新拉取",
        )

    def test_fetch_market_frame_respects_custom_ttl(self):
        from tradingagents.dataflows import ashare_events

        calls = {"n": 0}

        def fake_func(**kwargs):
            calls["n"] += 1
            import pandas as pd

            return pd.DataFrame([{"股票代码": "600519"}])

        ashare_events.clear_caches()
        kwargs = {"date": "20250930"}
        first = ashare_events._fetch_market_frame(
            "yjyg", fake_func, ttl=ashare_events._PERIOD_MARKET_TTL, **kwargs
        )
        second = ashare_events._fetch_market_frame(
            "yjyg", fake_func, ttl=ashare_events._PERIOD_MARKET_TTL, **kwargs
        )
        self.assertEqual(calls["n"], 1, "相同报告期不应重复请求")
        self.assertEqual(len(first), len(second))
        ashare_events.clear_caches()

    def test_ttl_is_not_part_of_cache_key(self):
        """同一 (接口, 参数) 即使传入不同 ttl 也必须命中同一缓存条目。"""
        from tradingagents.dataflows import ashare_events

        calls = {"n": 0}

        def fake_func(**kwargs):
            calls["n"] += 1
            import pandas as pd

            return pd.DataFrame()

        ashare_events.clear_caches()
        ashare_events._fetch_market_frame("fhps", fake_func, ttl=60, date="20250630")
        ashare_events._fetch_market_frame("fhps", fake_func, ttl=99999, date="20250630")
        self.assertEqual(calls["n"], 1)
        ashare_events.clear_caches()


if __name__ == "__main__":
    unittest.main()
