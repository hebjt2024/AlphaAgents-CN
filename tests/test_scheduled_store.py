"""定时分析任务存储测试。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.storage import (
    add_scheduled_job,
    delete_scheduled_job,
    due_scheduled_jobs,
    get_scheduled_job,
    list_scheduled_jobs,
    mark_scheduled_run,
    update_scheduled_job,
)


class ScheduledStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "scheduled.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_add_list_update_delete(self):
        item = add_scheduled_job("600519", "贵州茅台", "18:00")
        self.assertGreater(item["id"], 0)
        self.assertEqual(item["schedule_time"], "18:00")
        self.assertTrue(item["enabled"])

        items = list_scheduled_jobs()
        self.assertEqual(len(items), 1)

        updated = update_scheduled_job(
            item["id"], schedule_time="9:5", enabled=False
        )
        self.assertEqual(updated["schedule_time"], "09:05")
        self.assertFalse(updated["enabled"])

        self.assertTrue(delete_scheduled_job(item["id"]))
        self.assertIsNone(get_scheduled_job(item["id"]))

    def test_due_jobs_and_mark_run(self):
        first = add_scheduled_job("600519", "", "18:00")
        second = add_scheduled_job("000001", "", "19:00")

        due = due_scheduled_jobs("18:30", "2026-11-11")
        self.assertEqual([item["id"] for item in due], [first["id"]])

        mark_scheduled_run(first["id"], "2026-11-11")
        due_after = due_scheduled_jobs("18:30", "2026-11-11")
        self.assertEqual(due_after, [])

        due_next_day = due_scheduled_jobs("18:30", "2026-11-12")
        self.assertIn(first["id"], [item["id"] for item in due_next_day])

        late = due_scheduled_jobs("19:30", "2026-11-11")
        self.assertIn(second["id"], [item["id"] for item in late])

    def test_same_ticker_and_time_is_upserted(self):
        first = add_scheduled_job("600519", "", "18:00")
        second = add_scheduled_job("600519", "贵州茅台", "18:00")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list_scheduled_jobs()), 1)
        self.assertEqual(second["name"], "贵州茅台")


if __name__ == "__main__":
    unittest.main()
