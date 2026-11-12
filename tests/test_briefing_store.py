"""每日简报归档存储测试。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.storage import (
    delete_briefing,
    get_briefing,
    list_briefings,
    save_briefing,
)


def sample_briefing(markdown: str = "# 简报\n\n市场偏强"):
    return {
        "as_of": "2026-11-11",
        "summary": {"tone": "偏强", "alerts": 2, "signals": 3},
        "markdown": markdown,
    }


class BriefingStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "test.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_save_list_get_delete(self):
        briefing_id = save_briefing(sample_briefing(), source="test")
        self.assertGreater(briefing_id, 0)

        items = list_briefings()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["briefing_date"], "2026-11-11")
        self.assertEqual(items[0]["summary"]["tone"], "偏强")
        self.assertEqual(items[0]["source"], "test")

        full = get_briefing(briefing_id)
        self.assertIn("市场偏强", full["markdown"])

        self.assertTrue(delete_briefing(briefing_id))
        self.assertEqual(list_briefings(), [])
        self.assertIsNone(get_briefing(briefing_id))

    def test_same_date_upserts(self):
        first = save_briefing(sample_briefing("旧内容"))
        second = save_briefing(sample_briefing("新内容"), source="updated")

        self.assertEqual(first, second)
        items = list_briefings()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "updated")
        full = get_briefing(second)
        self.assertIn("新内容", full["markdown"])
        self.assertNotIn("旧内容", full["markdown"])

    def test_get_missing(self):
        self.assertIsNone(get_briefing(9999))
        self.assertFalse(delete_briefing(9999))


if __name__ == "__main__":
    unittest.main()
