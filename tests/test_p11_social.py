"""P11：真实社媒热度与情绪。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from webui.backend import server


def _guba() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "当前排名": 1,
                "代码": "SH600519",
                "股票名称": "贵州茅台",
                "最新价": 1400.0,
                "涨跌额": 20.0,
                "涨跌幅": 1.5,
            },
            {
                "当前排名": 2,
                "代码": "SZ000001",
                "股票名称": "平安银行",
                "最新价": 12.0,
                "涨跌额": -0.1,
                "涨跌幅": -0.8,
            },
        ]
    )


def _keywords() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"时间": "2026-11-15 06:00:00", "股票代码": "SH600519", "概念名称": "白酒", "概念代码": "BK0896", "热度": 6588},
            {"时间": "2026-11-15 06:00:00", "股票代码": "SH600519", "概念名称": "电商概念", "概念代码": "BK0665", "热度": 108},
        ]
    )


def _weibo() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"name": "贵州茅台", "rate": 0.5},
            {"name": "平安银行", "rate": -0.3},
            {"name": "士兰微", "rate": 0.14},
        ]
    )


class TempDbMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("TA_DB_PATH")
        os.environ["TA_DB_PATH"] = str(Path(self._tmp.name) / "test.db")
        from tradingagents.storage import init_db

        init_db()
        from tradingagents.dataflows.social_sentiment import clear_social_cache

        clear_social_cache()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TA_DB_PATH", None)
        else:
            os.environ["TA_DB_PATH"] = self._old
        super().tearDown()


class SocialServiceTests(TempDbMixin):
    def _patched(self):
        from tradingagents.dataflows import akshare_adapter

        return (
            patch.object(
                akshare_adapter.ak, "stock_hot_rank_em", return_value=_guba()
            ),
            patch.object(
                akshare_adapter.ak, "stock_hot_keyword_em", return_value=_keywords()
            ),
            patch.object(
                akshare_adapter.ak,
                "stock_js_weibo_report",
                return_value=_weibo(),
            ),
        )

    def test_social_sentiment(self):
        from tradingagents.dataflows.social_sentiment import get_social_sentiment

        p1, p2, p3 = self._patched()
        with p1, p2, p3:
            result = get_social_sentiment(
                "600519", name="贵州茅台", include_xueqiu=False
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["guba_rank"], 1)
        self.assertGreater(result["heat_score"], 0)
        self.assertAlmostEqual(result["weibo_rate"], 0.5)
        self.assertGreater(result["sentiment_score"], 50)
        self.assertTrue(result["keywords"])
        self.assertIn("社媒趋势", result["summary"])

    def test_historical_date_is_rejected(self):
        from tradingagents.dataflows.social_sentiment import get_social_sentiment

        result = get_social_sentiment("600519", curr_date="2024-01-01")
        self.assertFalse(result["success"])
        self.assertIn("实时", result["message"])

    def test_snapshot_history_and_abnormal_heat(self):
        from tradingagents.dataflows.social_sentiment import get_social_sentiment
        from tradingagents.storage import (
            list_social_snapshots,
            save_social_snapshot,
            social_baseline,
        )

        for day in range(1, 6):
            save_social_snapshot(
                "600519",
                snapshot_date=f"2026-11-0{day}",
                heat_score=20,
                sentiment_score=50,
            )
        p1, p2, p3 = self._patched()
        with p1, p2, p3:
            result = get_social_sentiment(
                "600519", name="贵州茅台", save=True
            )
        self.assertTrue(result["success"])
        self.assertIn("异常升温", result["abnormal"])
        history = list_social_snapshots("600519")
        self.assertEqual(len(history), 6)
        baseline = social_baseline("600519")
        self.assertGreater(baseline["count"], 0)

    def test_social_hot(self):
        from tradingagents.dataflows.social_sentiment import get_social_hot

        p1, p2, p3 = self._patched()
        with p1, p2, p3:
            result = get_social_hot(limit=10)
        self.assertTrue(result["success"])
        self.assertEqual(result["items"][0]["ticker"], "600519")
        self.assertAlmostEqual(result["items"][0]["weibo_rate"], 0.5)


class SocialApiTests(TempDbMixin):
    def setUp(self):
        super().setUp()
        self.client = TestClient(server.app)

    def test_sentiment_and_history_api(self):
        canned = {
            "success": True,
            "ticker": "600519",
            "heat_score": 80,
            "sentiment_score": 70,
            "attention": "过热",
            "sentiment": "乐观",
            "summary": "测试",
            "keywords": [],
            "warnings": [],
        }
        with patch(
            "tradingagents.dataflows.social_sentiment.get_social_sentiment",
            return_value=canned,
        ):
            data = self.client.get(
                "/api/social/sentiment", params={"ticker": "600519"}
            ).json()
        self.assertTrue(data["success"])
        self.assertEqual(data["heat_score"], 80)

        history = self.client.get(
            "/api/social/history", params={"ticker": "600519"}
        ).json()
        self.assertTrue(history["success"])

    def test_snapshot_api(self):
        canned = {
            "success": True,
            "ticker": "600519",
            "heat_score": 80,
            "sentiment_score": 70,
            "summary": "测试",
            "warnings": [],
        }
        with patch(
            "tradingagents.dataflows.social_sentiment.get_social_sentiment",
            return_value=canned,
        ) as mocked:
            data = self.client.post(
                "/api/social/snapshot", json={"ticker": "600519"}
            ).json()
        self.assertTrue(data["success"])
        self.assertTrue(mocked.call_args.kwargs["save"])


class SocialToolTests(unittest.TestCase):
    def test_tool_renders(self):
        from tradingagents.agents.utils.social_sentiment_tools import (
            get_social_sentiment as tool,
        )

        canned = {
            "success": True,
            "ticker": "600519",
            "name": "贵州茅台",
            "as_of": "2026-11-15T10:00:00",
            "summary": "综合热度 80（过热）",
            "keywords": [{"name": "白酒", "heat": 6588}],
            "xueqiu": {},
            "baseline": {"count": 5, "heat_avg": 20, "sentiment_avg": 50},
            "warnings": [],
        }
        with patch(
            "tradingagents.agents.utils.social_sentiment_tools.get_social_sentiment",
            return_value=canned,
        ):
            text = tool.invoke({"ticker": "600519", "name": "贵州茅台"})
        self.assertIn("社媒舆情", text)
        self.assertIn("白酒", text)


class SocialSchedulerTests(unittest.TestCase):
    def test_social_disabled_by_default(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.dataflows.social_sentiment.get_social_sentiment"
            ) as mocked,
        ):
            scheduler._run_social_snapshot_once()
        mocked.assert_not_called()

    def test_social_runs_for_watchlist(self):
        from tradingagents import scheduler

        with (
            patch.dict(os.environ, {"SCHEDULE_SOCIAL": "1"}, clear=True),
            patch(
                "tradingagents.storage.list_watchlist",
                return_value=[{"ticker": "600519"}],
            ),
            patch("tradingagents.storage.list_positions", return_value=[]),
            patch(
                "tradingagents.dataflows.social_sentiment.get_social_sentiment",
                return_value={"success": True},
            ) as mocked,
        ):
            scheduler._run_social_snapshot_once()
        self.assertTrue(mocked.call_args.kwargs["save"])


if __name__ == "__main__":
    unittest.main()
