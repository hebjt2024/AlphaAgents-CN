"""分析任务存储测试。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.storage import (
    create_job,
    delete_job,
    fail_job,
    finish_job,
    get_job,
    list_jobs,
    mark_job_running,
)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "jobs.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_create_run_finish_and_get(self):
        job_id = create_job("600519", "2026-11-11", {"strategies": ["bull_trend"]})
        self.assertTrue(job_id)

        queued = get_job(job_id)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["company"], "600519")
        self.assertEqual(queued["meta"]["strategies"], ["bull_trend"])

        mark_job_running(job_id)
        running = get_job(job_id)
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["started_at"])

        finish_job(job_id, {"decision": "BUY", "reports": {"market_report": "x"}})
        done = get_job(job_id)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result"]["decision"], "BUY")
        self.assertTrue(done["finished_at"])

    def test_failed_job(self):
        job_id = create_job("000001", "2026-11-11")
        fail_job(job_id, "boom")
        job = get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("boom", job["error"])

    def test_list_and_delete(self):
        first = create_job("600519", "2026-11-11")
        second = create_job("000001", "2026-11-11")
        items = list_jobs()
        self.assertEqual(len(items), 2)
        self.assertNotIn("result", items[0])

        self.assertTrue(delete_job(first))
        self.assertIsNone(get_job(first))
        self.assertIsNotNone(get_job(second))


if __name__ == "__main__":
    unittest.main()
