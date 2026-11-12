"""P1 市场情绪历史测试：落库、upsert、分位 / 趋势 / 分级。

使用临时 ``TA_DB_PATH``，不触达网络。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.services.market_sentiment import (
    backfill_sentiment,
    get_sentiment_history,
    snapshot_market_sentiment,
)
from tradingagents.storage import (
    delete_sentiment_snapshot,
    get_sentiment_snapshot,
    list_sentiment_snapshots,
    save_sentiment_snapshot,
)


def _payload(up, down, limit_up=10, limit_down=5, tone="震荡", total=1000.0):
    return {
        "as_of": "2026-01-01",
        "breadth": {
            "up": up,
            "down": down,
            "flat": 0,
            "limit_up": limit_up,
            "limit_down": limit_down,
        },
        "indices": [{"code": "sh000001", "turnover_yi": total}],
        "summary": {"tone": tone, "text": "测试"},
        "warnings": [],
    }


class SentimentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "sentiment.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_same_day_write_is_upsert(self):
        save_sentiment_snapshot("2026-11-12", _payload(100, 200))
        save_sentiment_snapshot(
            "2026-11-12", _payload(300, 50, limit_up=30, tone="偏强")
        )

        rows = list_sentiment_snapshots(60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["up_count"], 300)
        self.assertEqual(rows[0]["down_count"], 50)
        self.assertEqual(rows[0]["limit_up"], 30)
        self.assertEqual(rows[0]["tone"], "偏强")

        snapshot = get_sentiment_snapshot("2026-11-12")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["up_count"], 300)
        self.assertAlmostEqual(snapshot["total_amount"], 1000.0)

    def test_list_is_ascending_and_limited(self):
        for day in range(1, 6):
            save_sentiment_snapshot(
                f"2026-01-0{day}", _payload(up=day, down=10)
            )

        rows = list_sentiment_snapshots(days=3)
        self.assertEqual(
            [row["trade_date"] for row in rows],
            ["2026-01-03", "2026-01-04", "2026-01-05"],
        )

        everything = list_sentiment_snapshots(days=60)
        self.assertEqual(len(everything), 5)

    def test_get_and_delete(self):
        save_sentiment_snapshot("2026-02-01", _payload(10, 20))
        self.assertIsNotNone(get_sentiment_snapshot("2026-02-01"))
        self.assertTrue(delete_sentiment_snapshot("2026-02-01"))
        self.assertIsNone(get_sentiment_snapshot("2026-02-01"))
        self.assertFalse(delete_sentiment_snapshot("2026-02-01"))

    def test_days_validation(self):
        save_sentiment_snapshot("2026-03-01", _payload(10, 20))
        self.assertEqual(len(list_sentiment_snapshots(days=0)), 1)

    def test_history_percentile_trend_and_level(self):
        for index in range(1, 21):
            up = index * 5
            save_sentiment_snapshot(
                f"2026-04-{index:02d}",
                _payload(
                    up=up,
                    down=100 - up,
                    limit_up=10 + index,
                    limit_down=5,
                ),
            )

        history = get_sentiment_history(days=60)
        self.assertTrue(history["success"])
        self.assertEqual(len(history["series"]), 20)
        self.assertEqual(history["series"][-1]["up_ratio"], 1.0)
        self.assertEqual(history["stats"]["samples"], 20)
        self.assertEqual(history["stats"]["percentile"], 97.5)
        self.assertEqual(history["level"], "高潮")
        self.assertEqual(history["trend"], "回暖")
        self.assertEqual(history["stats"]["avg5"]["limit_up"], 28.0)
        self.assertEqual(history["stats"]["avg20"]["limit_up"], 20.5)
        self.assertEqual(history["warnings"], [])
        self.assertIn("高潮", history["summary"])

    def test_history_warns_on_small_sample(self):
        for day in range(1, 4):
            save_sentiment_snapshot(
                f"2026-05-0{day}", _payload(up=day * 10, down=10)
            )

        history = get_sentiment_history(days=60)
        self.assertTrue(history["success"])
        self.assertEqual(len(history["warnings"]), 1)
        self.assertIn("样本不足", history["warnings"][0])
        self.assertEqual(history["trend"], "持平")

    def test_history_empty(self):
        history = get_sentiment_history(days=60)
        self.assertFalse(history["success"])
        self.assertEqual(history["series"], [])
        self.assertTrue(history["warnings"])

    def test_backfill_is_refused(self):
        result = backfill_sentiment(30)
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "复盘数据源为实时快照，无法回溯历史")
        self.assertEqual(result["requested_days"], 30)


class SnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "sentiment.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_snapshot_persists_patched_review(self):
        fake = {
            "success": True,
            "as_of": "2026-11-12",
            "breadth": {
                "up": 2500,
                "down": 2200,
                "flat": 100,
                "limit_up": 40,
                "limit_down": 8,
                "up_ratio": 0.52,
            },
            "indices": [
                {"code": "sh000001", "close": 3200.0, "turnover_yi": 5000.0}
            ],
            "summary": {"tone": "震荡", "text": "市场震荡。"},
            "warnings": [],
        }
        with patch(
            "tradingagents.services.market_sentiment.build_market_review",
            return_value=fake,
        ):
            result = snapshot_market_sentiment()

        self.assertTrue(result["success"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["trade_date"], "2026-11-12")

        row = get_sentiment_snapshot("2026-11-12")
        self.assertIsNotNone(row)
        self.assertEqual(row["up_count"], 2500)
        self.assertEqual(row["down_count"], 2200)
        self.assertEqual(row["limit_up"], 40)
        self.assertEqual(row["tone"], "震荡")
        self.assertAlmostEqual(row["total_amount"], 5000.0)

    def test_snapshot_without_data_is_not_saved(self):
        with patch(
            "tradingagents.services.market_sentiment.build_market_review",
            return_value={"success": False, "as_of": "2026-11-13"},
        ):
            result = snapshot_market_sentiment()

        self.assertFalse(result["success"])
        self.assertFalse(result["saved"])
        self.assertIsNone(get_sentiment_snapshot("2026-11-13"))

    def test_small_sample_is_flagged_unreliable(self):
        """样本不足时必须显式标注判定不可靠。

        WHY：3 天数据算出的 0% 分位会被读成"历史冰点"，但实际只是
        这三天的极小值。不标注就会误导重仓决策。
        """
        for offset in range(3):
            save_sentiment_snapshot(
                f"2026-11-{10 + offset:02d}",
                {
                    "breadth": {
                        "up_count": 1000 + offset * 100,
                        "down_count": 3500 - offset * 100,
                        "limit_up": 20,
                        "limit_down": 10,
                    }
                },
            )

        history = get_sentiment_history(days=60)
        self.assertFalse(history["reliable"])
        self.assertFalse(history["level_reliable"])
        self.assertEqual(history["min_samples"], 20)
        self.assertIn("样本不足", history["summary"])
        self.assertIn("不可靠", history["summary"])
        self.assertFalse(history["stats"]["reliable"])

    def test_enough_samples_is_marked_reliable(self):
        for offset in range(20):
            save_sentiment_snapshot(
                f"2026-10-{offset + 1:02d}",
                {
                    "breadth": {
                        "up_count": 1500 + (offset % 5) * 200,
                        "down_count": 3000 - (offset % 5) * 200,
                        "limit_up": 30,
                        "limit_down": 8,
                    }
                },
            )

        history = get_sentiment_history(days=60)
        self.assertTrue(history["reliable"])
        self.assertTrue(history["level_reliable"])
        self.assertNotIn("样本不足", history["summary"])


if __name__ == "__main__":
    unittest.main()
