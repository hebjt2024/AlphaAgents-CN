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
    purge_all_job_records,
    purge_jobs,
    summarize_jobs,
)
from tradingagents.storage.job_store import (
    cancel_requested_queued_jobs,
    claim_job,
    claim_next_job,
    mark_job_cancelled,
    request_cancel,
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


class JobTerminalStateTests(unittest.TestCase):
    """终态不可被迟到的结果/失败/取消覆盖，取消请求会阻止认领。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "jobs.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _running_job(self, status="completed"):
        job_id = create_job("600519", "2026-11-11")
        mark_job_running(job_id)
        if status == "completed":
            finish_job(job_id, {"decision": "BUY"})
        else:
            mark_job_cancelled(job_id, "用户取消")
        return job_id

    def test_finish_job_only_moves_running_to_terminal(self):
        job_id = create_job("600519", "2026-11-11")
        # queued 任务不能被 finish_job 直接写成 completed
        finish_job(job_id, {"decision": "BUY"})
        self.assertEqual(get_job(job_id)["status"], "queued")

    def test_completed_job_is_not_overwritten_by_late_finish(self):
        job_id = self._running_job("completed")
        finish_job(job_id, {"decision": "SELL"}, status="failed")
        job = get_job(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["decision"], "BUY")

    def test_cancelled_job_is_not_overwritten_by_failure(self):
        job_id = self._running_job("cancelled")
        fail_job(job_id, "late failure")
        job = get_job(job_id)
        self.assertEqual(job["status"], "cancelled")
        self.assertNotIn("late failure", job["error"])

    def test_failed_job_is_not_overwritten_by_cancel(self):
        job_id = create_job("000001", "2026-11-11")
        mark_job_running(job_id)
        fail_job(job_id, "boom")
        mark_job_cancelled(job_id, "late cancel")
        job = get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("boom", job["error"])

    def test_cancel_requested_job_cannot_be_claimed(self):
        job_id = create_job("600519", "2026-11-11")
        self.assertTrue(request_cancel(job_id))
        self.assertFalse(claim_job(job_id))
        self.assertIsNone(claim_next_job())
        self.assertEqual(get_job(job_id)["status"], "queued")

    def test_cancel_requested_queued_jobs_are_marked_cancelled(self):
        job_id = create_job("600519", "2026-11-11")
        request_cancel(job_id)
        self.assertEqual(cancel_requested_queued_jobs(), [job_id])
        job = get_job(job_id)
        self.assertEqual(job["status"], "cancelled")
        # 幂等：再次清理不会重复返回
        self.assertEqual(cancel_requested_queued_jobs(), [])

    def test_finish_job_rejects_illegal_terminal_status(self):
        job_id = create_job("600519", "2026-11-11")
        mark_job_running(job_id)
        with self.assertRaises(ValueError):
            finish_job(job_id, {}, status="running")

    def test_queue_cancel_marks_db_queued_job_cancelled(self):
        from tradingagents.services.job_queue import JobQueue

        job_id = create_job("600519", "2026-11-11")
        queue = JobQueue(worker_count=1)
        self.assertTrue(queue.cancel(job_id))
        job = get_job(job_id)
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse(claim_job(job_id))


class JobPurgeTests(unittest.TestCase):
    """批量清理：默认只动终态任务，绝不误删排队/运行中的任务。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "jobs.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _seed(self):
        completed = create_job("600519", "2026-11-10")
        mark_job_running(completed)
        finish_job(completed, {"decision": "BUY"})

        failed = create_job("000001", "2026-11-10")
        mark_job_running(failed)
        fail_job(failed, "boom")

        queued = create_job("600519", "2026-11-11")  # 保持 queued

        running = create_job("601118", "2026-11-11")
        mark_job_running(running)

        return queued, running

    def test_default_purge_keeps_active_jobs(self):
        queued, running = self._seed()

        result = purge_jobs()
        self.assertEqual(result["deleted"], 2)  # 只删 completed + failed

        remaining = {item["job_id"]: item["status"] for item in list_jobs()}
        self.assertIn(queued, remaining)
        self.assertIn(running, remaining)
        self.assertEqual(remaining[queued], "queued")
        self.assertEqual(remaining[running], "running")

    def test_dry_run_does_not_delete(self):
        self._seed()
        result = purge_jobs(dry_run=True)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(result["dry_run"])
        # total 表示"按当前过滤条件会命中多少"，默认只含终态任务
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["by_status"], {"completed": 1, "failed": 1})
        # 一条都没删
        self.assertEqual(len(list_jobs()), 4)

    def test_before_date_filter(self):
        self._seed()
        # 所有任务都是今天创建的 → "未来日期之前" 全部命中
        future = "2099-01-01"
        result = purge_jobs(before_date=future)
        self.assertEqual(result["deleted"], 2)
        # 极早日期之前 → 一个都不命中
        self.assertEqual(purge_jobs(before_date="2000-01-01")["deleted"], 0)

    def test_company_filter(self):
        self._seed()
        result = purge_jobs(company="600519")
        # 600519 有 1 个 completed + 1 个 queued；默认只删终态那个
        self.assertEqual(result["deleted"], 1)
        remaining = [item["company"] for item in list_jobs()]
        self.assertIn("600519", remaining)  # queued 的那个还在

    def test_explicitly_purging_queued_removes_it(self):
        queued, running = self._seed()
        result = purge_jobs(statuses=["queued"])
        self.assertEqual(result["deleted"], 1)
        self.assertIsNone(get_job(queued))
        self.assertIsNotNone(get_job(running))

    def test_summarize_reports_status_breakdown(self):
        self._seed()
        summary = summarize_jobs()
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["by_status"].get("completed"), 1)
        self.assertEqual(summary["by_status"].get("failed"), 1)
        self.assertEqual(summary["by_status"].get("queued"), 1)
        self.assertEqual(summary["by_status"].get("running"), 1)

    def test_purge_all_respects_include_active(self):
        queued, running = self._seed()

        first = purge_all_job_records(include_active=False)
        self.assertEqual(first["deleted"], 2)
        self.assertIsNotNone(get_job(queued))

        second = purge_all_job_records(include_active=True)
        self.assertEqual(second["deleted"], 2)
        self.assertIsNone(get_job(queued))
        self.assertIsNone(get_job(running))
        self.assertEqual(list_jobs(), [])


if __name__ == "__main__":
    unittest.main()
