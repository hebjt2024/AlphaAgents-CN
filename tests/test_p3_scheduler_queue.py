"""任务队列 / 调度器 / 通知可靠性测试。

全部离线运行：不访问网络，不依赖外部服务；等待统一使用短轮询（<=50ms），
避免长时间 sleep。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from unittest.mock import patch

from tradingagents import notify, scheduler
from tradingagents.runtime import ANALYSIS_LOCK, analysis_key
from tradingagents.services.job_queue import JobCancelled, JobQueue
from tradingagents.storage.job_store import (
    create_job,
    get_job,
    mark_job_running,
    reap_stale_jobs,
)
from tradingagents.storage.report_store import _connect
from tradingagents.storage.scheduled_store import (
    acquire_lease,
    get_state_json,
    release_lease,
)

TERMINAL = ("completed", "failed", "cancelled")


def wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """短轮询等待条件成立。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class _FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class QueueTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "queue.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.queues: list[JobQueue] = []

    def tearDown(self):
        for queue in self.queues:
            try:
                queue.shutdown(wait=True, timeout=2.0)
            except Exception:
                pass

    def make_queue(self, **kwargs) -> JobQueue:
        queue = JobQueue(**kwargs)
        self.queues.append(queue)
        return queue


# --------------------------------------------------------------------- 队列


class JobQueueTests(QueueTestBase):
    def test_submitted_job_runs_and_completes(self):
        queue = self.make_queue(worker_count=1)
        seen: dict[str, object] = {}

        def handler(job_id, payload, cancel_event):
            seen["job_id"] = job_id
            seen["payload"] = payload
            return {"decision": "BUY", "report_id": 7}

        queue.register("analysis", handler)
        queue.start()

        job_id = queue.submit(
            kind="analysis",
            company="600519",
            trade_date="2026-11-12",
            payload={"ticker": "600519", "trade_date": "2026-11-12"},
        )
        self.assertTrue(wait_until(lambda: get_job(job_id)["status"] in TERMINAL))
        job = get_job(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["decision"], "BUY")
        self.assertEqual(seen["job_id"], job_id)
        self.assertEqual(seen["payload"]["ticker"], "600519")

    def test_queue_is_bounded_by_worker_count(self):
        queue = self.make_queue(worker_count=1)
        gate = threading.Event()
        started: list[str] = []

        def handler(job_id, payload, cancel_event):
            started.append(job_id)
            gate.wait(3.0)
            return {"ok": True}

        queue.register("analysis", handler)
        queue.start()

        job_ids = [
            queue.submit(kind="analysis", company=str(index), trade_date="2026-11-12")
            for index in range(3)
        ]
        self.assertTrue(wait_until(lambda: len(started) == 1))
        time.sleep(0.05)
        # worker 只有一个：其余任务必须还在排队
        self.assertEqual(len(started), 1)
        self.assertEqual(queue.active_count(), 1)
        self.assertGreaterEqual(queue.pending_count(), 1)

        gate.set()
        self.assertTrue(
            wait_until(lambda: all(get_job(jid)["status"] in TERMINAL for jid in job_ids))
        )
        self.assertEqual(len(started), 3)

    def test_cancel_queued_job_prevents_it_from_running(self):
        queue = self.make_queue(worker_count=1)
        gate = threading.Event()
        started: list[str] = []

        def handler(job_id, payload, cancel_event):
            started.append(job_id)
            gate.wait(3.0)
            return {"ok": True}

        queue.register("analysis", handler)
        queue.start()

        first = queue.submit(kind="analysis", company="a", trade_date="2026-11-12")
        second = queue.submit(kind="analysis", company="b", trade_date="2026-11-12")
        self.assertTrue(wait_until(lambda: len(started) == 1))

        self.assertTrue(queue.cancel(second))
        gate.set()
        self.assertTrue(wait_until(lambda: get_job(first)["status"] in TERMINAL))
        self.assertTrue(wait_until(lambda: get_job(second)["status"] in TERMINAL))
        self.assertEqual(get_job(second)["status"], "cancelled")
        self.assertNotIn(second, started)

    def test_cancel_running_job_stops_at_next_checkpoint(self):
        queue = self.make_queue(worker_count=1)
        checkpoints: list[int] = []

        def handler(job_id, payload, cancel_event):
            index = 0
            while index < 200:
                if cancel_event.is_set():
                    checkpoints.append(index)
                    raise JobCancelled("cancelled at checkpoint")
                index += 1
                checkpoints.append(index)
                time.sleep(0.01)
            return {"ok": True}

        queue.register("analysis", handler)
        queue.start()

        job_id = queue.submit(kind="analysis", company="a", trade_date="2026-11-12")
        self.assertTrue(wait_until(lambda: len(checkpoints) >= 2))
        queue.cancel(job_id)
        self.assertTrue(wait_until(lambda: get_job(job_id)["status"] in TERMINAL))
        self.assertEqual(get_job(job_id)["status"], "cancelled")
        # 必须在检查点处停下，而不是跑完整个循环
        self.assertLess(len(checkpoints), 200)

    def test_graceful_shutdown_waits_for_inflight_job(self):
        queue = self.make_queue(worker_count=1)
        gate = threading.Event()

        def handler(job_id, payload, cancel_event):
            gate.wait(3.0)
            return {"finished": True}

        queue.register("analysis", handler)
        queue.start()
        job_id = queue.submit(kind="analysis", company="a", trade_date="2026-11-12")
        self.assertTrue(wait_until(lambda: queue.active_count() == 1))

        shutdown_done = threading.Event()

        def do_shutdown():
            queue.shutdown(wait=True, timeout=5.0)
            shutdown_done.set()

        thread = threading.Thread(target=do_shutdown, daemon=True)
        thread.start()
        time.sleep(0.05)
        # 优雅退出：必须等当前任务跑完
        self.assertFalse(shutdown_done.is_set())

        gate.set()
        self.assertTrue(wait_until(lambda: shutdown_done.is_set(), timeout=5.0))
        self.assertEqual(get_job(job_id)["status"], "completed")

    def test_queue_scopes_do_not_steal_each_others_jobs(self):
        scope_a = self.make_queue(worker_count=1, scope="scope-a")
        scope_b = self.make_queue(worker_count=1, scope="scope-b")
        ran: list[str] = []

        def handler_a(job_id, payload, cancel_event):
            ran.append(job_id)
            return {"ok": True}

        def handler_b(job_id, payload, cancel_event):
            ran.append(job_id)
            return {"ok": True}

        scope_a.register("analysis", handler_a)
        scope_b.register("analysis", handler_b)
        scope_a.start()
        scope_b.start()

        job_id = scope_a.submit(kind="analysis", company="a", trade_date="2026-11-12")
        self.assertTrue(wait_until(lambda: get_job(job_id)["status"] in TERMINAL))
        self.assertEqual(get_job(job_id)["status"], "completed")
        self.assertEqual(ran, [job_id])

    def test_stale_running_jobs_are_reaped_on_startup(self):
        job_id = create_job("600519", "2026-11-12")
        mark_job_running(job_id)
        stale_time = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
        with _connect() as conn:
            conn.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (stale_time, stale_time, job_id),
            )

        queue = self.make_queue(worker_count=1, stale_after_seconds=600)
        queue.start()

        job = get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("心跳", job["error"])

    def test_reap_stale_jobs_helper_is_idempotent(self):
        job_id = create_job("600519", "2026-11-12")
        mark_job_running(job_id)
        stale_time = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
        with _connect() as conn:
            conn.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (stale_time, stale_time, job_id),
            )
        self.assertEqual(reap_stale_jobs(600), [job_id])
        self.assertEqual(reap_stale_jobs(600), [])


class AnalysisLockTests(unittest.TestCase):
    def test_analysis_lock_is_context_manager(self):
        self.assertFalse(ANALYSIS_LOCK.locked())
        with ANALYSIS_LOCK:
            self.assertTrue(ANALYSIS_LOCK.locked())
        self.assertFalse(ANALYSIS_LOCK.locked())

    def test_analysis_lock_excludes_other_threads(self):
        acquired = threading.Event()
        released = threading.Event()

        def holder():
            with ANALYSIS_LOCK:
                acquired.set()
                released.wait(2.0)

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(2.0))
        self.assertFalse(ANALYSIS_LOCK.acquire(blocking=False))
        released.set()
        thread.join(2.0)
        self.assertFalse(ANALYSIS_LOCK.locked())

    def test_analysis_lock_is_reentrant(self):
        with ANALYSIS_LOCK:
            with ANALYSIS_LOCK:
                self.assertTrue(ANALYSIS_LOCK.locked())
        self.assertFalse(ANALYSIS_LOCK.locked())

    def test_different_analysis_keys_do_not_block_each_other(self):
        holder_ready = threading.Event()
        release = threading.Event()

        def holder():
            with analysis_key("config-a"):
                with ANALYSIS_LOCK:
                    holder_ready.set()
                    release.wait(2.0)

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        self.assertTrue(holder_ready.wait(2.0))
        with analysis_key("config-b"):
            self.assertTrue(ANALYSIS_LOCK.acquire(blocking=False))
        release.set()
        thread.join(2.0)


# ----------------------------------------------------------------- 调度器


class SchedulerStateTests(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.state = patch.dict(
            os.environ,
            {
                "TA_DB_PATH": os.path.join(self.tmp.name, "queue.db"),
                "SCHEDULE_ENABLED": "1",
                "SCHEDULE_TIME": "18:00",
                "SCHEDULE_STOCKS": "600519",
                # 隔离租约/状态键，避免与同进程其它调度器实例互相影响
                "SCHEDULE_LEASE_KEY": "daily_batch_test",
                "SCHEDULE_STATE_KEY": "daily_batch_test",
            },
        )
        self.state.start()
        self.addCleanup(self.state.stop)

    def test_daily_batch_state_survives_restart(self):
        today = "2026-11-12"
        scheduler._record_batch_result(today, [])
        # "重启"：重新从数据库读取，内存里没有任何缓存
        state = scheduler._load_batch_state()
        self.assertEqual(state.get("last_run_date"), today)

        # 同一天再次调用不应重复执行
        with patch.object(scheduler, "_run_once") as run_once:
            now = datetime(2026, 11, 12, 19, 0)
            self.assertFalse(scheduler._run_daily_batch_if_due(now))
        run_once.assert_not_called()

    def test_failed_batch_is_retryable_and_not_marked_done(self):
        today = "2026-11-12"
        scheduler._record_batch_result(today, ["600519: boom"])
        state = scheduler._load_batch_state()
        self.assertNotEqual(state.get("last_run_date"), today)
        self.assertEqual(state.get("attempts"), 1)
        self.assertIn("boom", state.get("last_error", ""))
        self.assertTrue(state.get("next_retry_at"))

        # 未到重试时间不重跑
        scheduler._save_batch_state(
            {
                "last_run_date": "",
                "attempts": 1,
                "attempts_date": today,
                "next_retry_at": "2026-11-12T23:00:00",
            }
        )
        with patch.object(scheduler, "_run_once") as run_once:
            now = datetime(2026, 11, 12, 19, 0)
            self.assertFalse(scheduler._run_daily_batch_if_due(now))
        run_once.assert_not_called()

        # 退避时间过去之后重新可跑
        scheduler._save_batch_state(
            {
                "last_run_date": "",
                "attempts": 1,
                "attempts_date": today,
                "next_retry_at": "2026-11-12T18:00:00",
            }
        )
        with patch.object(scheduler, "_run_once") as run_once:
            now = datetime(2026, 11, 12, 19, 0)
            self.assertTrue(scheduler._run_daily_batch_if_due(now))
        run_once.assert_called_once()

    def test_lease_prevents_two_processes_from_running_daily_batch(self):
        key = "daily_batch_test"
        self.assertTrue(acquire_lease(key, "owner-a"))
        self.assertFalse(acquire_lease(key, "owner-b"))
        self.assertTrue(release_lease(key, "owner-a"))
        self.assertTrue(acquire_lease(key, "owner-b"))
        release_lease(key, "owner-b")

    def test_daily_batch_is_skipped_when_lease_held_by_other_owner(self):
        acquire_lease("daily_batch_test", "someone-else")
        try:
            with patch.object(scheduler, "_run_once") as run_once:
                now = datetime(2026, 11, 12, 19, 0)
                self.assertFalse(scheduler._run_daily_batch_if_due(now))
            run_once.assert_not_called()
        finally:
            release_lease("daily_batch_test", "someone-else")

    def test_catch_up_runs_missed_batch_after_schedule_time(self):
        # 状态显示"昨天跑过"，今天还没跑
        scheduler._save_batch_state({"last_run_date": "2026-11-11", "attempts": 0})
        with patch.object(scheduler, "_run_once") as run_once:
            now = datetime(2026, 11, 12, 20, 30)
            self.assertTrue(scheduler._run_daily_batch_if_due(now))
        run_once.assert_called_once()

    def test_catch_up_not_run_before_schedule_time(self):
        scheduler._save_batch_state({"last_run_date": "2026-11-11", "attempts": 0})
        with patch.object(scheduler, "_run_once") as run_once:
            now = datetime(2026, 11, 12, 10, 0)
            self.assertFalse(scheduler._run_daily_batch_if_due(now))
        run_once.assert_not_called()


class SchedulerJobTests(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(
            os.environ,
            {
                "TA_DB_PATH": os.path.join(self.tmp.name, "queue.db"),
                "SCHEDULE_ENABLED": "1",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _patch_scheduled(self, jobs):
        return (
            patch("tradingagents.storage.due_scheduled_jobs", return_value=jobs),
            patch("tradingagents.storage.mark_scheduled_run"),
        )

    def test_failed_scheduled_job_is_not_marked_as_done(self):
        job = {"id": 1, "ticker": "600519", "schedule_time": "18:00"}
        due_patch, mark_patch = self._patch_scheduled([job])
        with (
            due_patch,
            mark_patch as marked,
            patch("tradingagents.storage.scheduled_store.mark_scheduled_failure") as failed,
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "datetime") as dt,
            patch.object(
                scheduler,
                "run_analysis",
                side_effect=RuntimeError("boom"),
            ),
        ):
            dt.now.return_value = datetime(2026, 11, 12, 18, 30)
            scheduler._run_scheduled_jobs()

        marked.assert_not_called()
        failed.assert_called_once()
        self.assertIn("boom", failed.call_args[0][1])

    def test_successful_scheduled_job_is_marked_as_done(self):
        job = {"id": 2, "ticker": "600519", "schedule_time": "18:00"}
        due_patch, mark_patch = self._patch_scheduled([job])
        with (
            due_patch,
            mark_patch as marked,
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "datetime") as dt,
            patch.object(
                scheduler,
                "run_analysis",
                return_value={"decision": "BUY", "report_id": 9},
            ) as run,
        ):
            dt.now.return_value = datetime(2026, 11, 12, 18, 30)
            scheduler._run_scheduled_jobs()

        run.assert_called_once_with(
            "600519", "2026-11-12", source="scheduled_job", notify=True
        )
        marked.assert_called_once_with(2, "2026-11-12")


class SchemaMigrationTests(unittest.TestCase):
    """首次 schema 迁移必须幂等，且能作用于已存在的老库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "legacy.db")
        self.env = patch.dict(os.environ, {"TA_DB_PATH": self.db})
        self.env.start()
        self.addCleanup(self.env.stop)
        # 手工建一个"老版本"表：缺少 heartbeat_at / updated_at / scope 等列
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    meta_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE scheduled_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    schedule_time TEXT NOT NULL DEFAULT '18:00',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(ticker, schedule_time)
                )
                """
            )
            conn.execute(
                "INSERT INTO jobs (id, company, trade_date, status, created_at) "
                "VALUES ('legacy-1', '600519', '2026-11-12', 'queued', '2026-11-12T10:00:00')"
            )

    def test_migration_adds_columns_and_keeps_old_rows(self):
        from tradingagents.storage.job_store import (
            claim_next_job,
            list_queued_jobs,
            reap_stale_jobs,
        )
        from tradingagents.storage.scheduled_store import (
            acquire_lease,
            add_scheduled_job,
            claim_scheduled_job,
        )

        # 触发迁移（任意一次 store 调用都会执行）
        job = get_job("legacy-1")
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")

        with _connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name in ("heartbeat_at", "updated_at", "kind", "attempts",
                     "cancel_requested", "scope"):
            self.assertIn(name, columns)

        # 老数据仍然可以被队列认领
        claimed = claim_next_job()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["job_id"], "legacy-1")

        # 幂等：重复执行迁移不会报错
        self.assertEqual(list_queued_jobs(), [])
        self.assertEqual(reap_stale_jobs(60), [])

        item = add_scheduled_job("600519", "", "18:00")
        self.assertTrue(claim_scheduled_job(item["id"], "owner-x"))
        self.assertTrue(acquire_lease("k", "owner-x"))

        with _connect() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(scheduled_jobs)")
            }
        for name in ("claimed_at", "claimed_by", "retry_count", "last_error"):
            self.assertIn(name, columns)
    def test_migration_is_idempotent_across_calls(self):
        from tradingagents.storage.job_store import create_job

        for _ in range(3):
            create_job("600519", "2026-11-12")
        with _connect() as conn:
            conn.execute("PRAGMA user_version")
            rows = conn.execute("PRAGMA table_info(jobs)").fetchall()
        names = [row["name"] for row in rows]
        self.assertEqual(len(names), len(set(names)))


class SchedulerStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "state.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_state_roundtrip(self):
        scheduler._save_batch_state({"last_run_date": "2026-11-12", "attempts": 2})
        self.assertEqual(
            get_state_json("daily_batch", {}),
            {"last_run_date": "2026-11-12", "attempts": 2},
        )

# ----------------------------------------------------------------- 通知


class NotifyTests(unittest.TestCase):
    SECRET = "s3cr3t-webhook-token"

    def setUp(self):
        self.url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.SECRET}"
        self.env = patch.dict(
            os.environ,
            {
                "WECOM_WEBHOOK_URL": self.url,
                "FEISHU_WEBHOOK_URL": "",
                "GENERIC_WEBHOOK_URL": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_retries_transient_failure_and_succeeds_on_second_attempt(self):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("temporary network failure")
            return _FakeResponse(200, json.dumps({"errcode": 0, "errmsg": "ok"}))

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            result = notify.send_notification(
                "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
            )

        self.assertEqual(calls["count"], 2)
        self.assertTrue(result["wecom"]["ok"])
        self.assertEqual(result["wecom"]["attempts"], 2)
        self.assertEqual(result["wecom"]["status"], 200)
        self.assertEqual(result["wecom"]["code"], 0)

    def test_business_error_code_is_failure_despite_http_200(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                200, json.dumps({"errcode": 93000, "errmsg": "invalid webhook"})
            )

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            result = notify.send_notification(
                "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
            )

        self.assertFalse(result["wecom"]["ok"])
        self.assertEqual(result["wecom"]["code"], 93000)
        self.assertEqual(result["wecom"]["status"], 200)

    def test_feishu_business_error_code_is_failure(self):
        env = patch.dict(
            os.environ,
            {
                "WECOM_WEBHOOK_URL": "",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            },
        )
        env.start()
        self.addCleanup(env.stop)

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(200, json.dumps({"code": 19024, "msg": "bad sign"}))

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            result = notify.send_notification(
                "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
            )

        self.assertFalse(result["feishu"]["ok"])
        self.assertEqual(result["feishu"]["code"], 19024)

    def test_4xx_is_not_retried(self):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(
                self.url, 400, "Bad Request", {}, None
            )

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            result = notify.send_notification(
                "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
            )

        self.assertEqual(calls["count"], 1)
        self.assertFalse(result["wecom"]["ok"])
        self.assertEqual(result["wecom"]["attempts"], 1)
        self.assertEqual(result["wecom"]["status"], 400)

    def test_webhook_url_never_leaks(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(f"failed to connect to {self.url}")

        logger = logging.getLogger("tradingagents.notify")
        with self.assertLogs(logger, level="DEBUG") as captured:
            with patch.object(urllib.request, "urlopen", fake_urlopen):
                result = notify.send_notification(
                    "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
                )

        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(self.SECRET, blob)
        self.assertNotIn(self.url, blob)
        self.assertNotIn(self.SECRET, str(result))
        logs = "\n".join(captured.output)
        self.assertNotIn(self.SECRET, logs)
        self.assertNotIn(self.url, logs)
        self.assertFalse(result["wecom"]["ok"])

    def test_failure_alert_hook_is_called(self):
        seen: list[dict] = []

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(200, json.dumps({"errcode": 93000}))

        notify.register_failure_handler(seen.append)
        self.addCleanup(notify.unregister_failure_handler, seen.append)
        with patch.object(urllib.request, "urlopen", fake_urlopen):
            notify.send_notification(
                "标题", "正文", sleep=lambda _s: None, jitter_fn=lambda a, b: 0.0
            )

        self.assertTrue(seen)
        self.assertEqual(seen[0]["channel"], "wecom")
        self.assertNotIn(self.SECRET, json.dumps(seen[0], ensure_ascii=False))

    def test_redact_url_masks_secret(self):
        redacted = notify.redact_url(self.url)
        self.assertNotIn(self.SECRET, redacted)
        self.assertIn("qyapi.weixin.qq.com", redacted)

    def test_channel_rate_limit_throttles_consecutive_sends(self):
        waits: list[float] = []

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(200, json.dumps({"errcode": 0}))

        with patch.object(urllib.request, "urlopen", fake_urlopen):
            for _ in range(2):
                notify.send_notification(
                    "标题",
                    "正文",
                    sleep=lambda seconds: waits.append(seconds),
                    jitter_fn=lambda a, b: 0.0,
                )
        # 默认不限流
        self.assertEqual(waits, [])

        with patch.dict(os.environ, {"NOTIFY_MIN_INTERVAL_SECONDS": "1"}):
            waits.clear()
            with patch.object(urllib.request, "urlopen", fake_urlopen):
                for _ in range(2):
                    notify.send_notification(
                        "标题",
                        "正文",
                        sleep=lambda seconds: waits.append(seconds),
                        jitter_fn=lambda a, b: 0.0,
                    )
        self.assertTrue(waits)
        self.assertLessEqual(max(waits), 1.0)


if __name__ == "__main__":
    unittest.main()
