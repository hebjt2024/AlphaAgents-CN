"""有界任务队列（SQLite 持久化 + 固定 worker 池）。

设计目标
--------
替换"一把全局锁 + 调用方自己起线程"的旧模型：

* **有界并发**：固定数量的 worker 线程（可配置），提交再多的任务也只会同时
  跑 ``worker_count`` 个；每个任务彼此隔离，没有全局互斥锁。
* **持久化**：待执行任务落在 ``jobs`` 表里（``status='queued'``），进程重启后
  可以恢复，不会丢任务。
* **原子认领**：``claim_job`` / ``claim_next_job`` 用条件 UPDATE 保证一个任务
  只被一个 worker 执行，跨进程同样成立。
* **取消**：``cancel(job_id)`` 对排队中的任务直接摘除；对运行中的任务设置
  ``threading.Event``，handler 在"检查点"（例如图节点分块之间）检查该事件
  即可优雅退出。
* **僵尸回收**：进程被 kill 后 ``jobs`` 行会永远停在 ``running``；启动时把心跳
  超时的 running 任务标记为 failed 并写明原因。
* **优雅退出**：``shutdown(wait=True)`` 等当前任务跑完再退出，而不是直接 kill。

典型用法::

    from tradingagents.services.job_queue import JobQueue

    queue = JobQueue(worker_count=2)
    queue.register("analysis", my_handler)   # (job_id, payload, cancel_event)
    queue.start()

    job_id = queue.submit(kind="analysis", company="600519",
                          trade_date="2026-11-12",
                          payload={"ticker": "600519", "trade_date": "2026-11-12"})
    job = queue.wait_for(job_id, timeout=600)
    queue.shutdown(wait=True)

handler 的返回值会被写入 ``jobs.result_json``；抛异常则标记为 failed。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from tradingagents.storage.job_store import (
    TERMINAL_JOB_STATUSES,
    claim_job,
    claim_next_job,
    create_job,
    ensure_queued_job,
    finish_job,
    get_job,
    is_cancel_requested,
    list_queued_jobs,
    mark_job_cancelled,
    mark_job_failed,
    reap_stale_jobs,
    request_cancel,
    touch_job,
)

logger = logging.getLogger(__name__)

# handler 签名：(job_id, payload, cancel_event) -> dict | None
JobHandler = Callable[[str, dict[str, Any], threading.Event], Any]


class JobCancelled(Exception):
    """任务被取消。"""


def _json_safe(value: Any) -> Any:
    """把任意对象转换成可 JSON 序列化的结构（避免写库时抛异常）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class JobQueue:
    """固定 worker 池 + SQLite 持久化的任务队列。"""

    def __init__(
        self,
        worker_count: int = 2,
        *,
        poll_interval: float = 0.25,
        heartbeat_interval: float = 5.0,
        stale_after_seconds: int = 1800,
        name: str = "alphaagents-jobs",
        scope: str = "default",
        recover_pending: bool = False,
    ) -> None:
        self.name = name
        self.scope = scope or "default"
        self.worker_count = max(1, int(worker_count))
        self.poll_interval = max(0.01, float(poll_interval))
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        self.stale_after_seconds = max(60, int(stale_after_seconds))
        #: 启动时是否把数据库里遗留的 queued 任务重新排队执行。
        #:
        #: 默认 **False**：进程重启不应该"自己开始干活"。此前默认恢复，
        #: 导致 WebUI 启动时会把上次没跑完的排队任务直接执行 ——
        #: 用户只是打开页面，却看到系统在自动分析某只股票。
        #: 需要断点续跑时显式传入 True。
        self.recover_pending = bool(recover_pending)

        self._handlers: dict[str, JobHandler] = {}
        self._pending: deque[str] = deque()
        self._pending_ids: set[str] = set()
        self._cond = threading.Condition()
        self._cancel_events: dict[str, threading.Event] = {}
        self._done_events: dict[str, threading.Event] = {}
        self._active: set[str] = set()
        self._threads: list[threading.Thread] = []
        self._heartbeat_thread: threading.Thread | None = None
        self._stopping = False
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------- 注册
    def register(self, kind: str, handler: JobHandler) -> None:
        """注册某类任务的处理函数。"""
        if not kind or handler is None:
            raise ValueError("kind 与 handler 不能为空")
        self._handlers[kind] = handler

    def handler_for(self, kind: str) -> JobHandler | None:
        return self._handlers.get(kind)

    # ------------------------------------------------------------- 生命周期
    def start(self) -> JobQueue:
        """启动 worker 线程，并回收僵尸任务、恢复排队任务。"""
        with self._lock:
            if self._started:
                return self
            self._started = True
            self._stopping = False

        reaped = self.reap_stale()
        if reaped:
            logger.warning("回收僵尸任务 %s 个: %s", len(reaped), ", ".join(reaped))

        if self.recover_pending:
            pending = self._recover_pending()
            if pending:
                logger.info("从数据库恢复排队任务 %s 个", len(pending))

        for index in range(self.worker_count):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"{self.name}-worker-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"{self.name}-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info("任务队列已启动: %s (worker=%s)", self.name, self.worker_count)
        return self

    def shutdown(self, wait: bool = True, timeout: float | None = None) -> None:
        """停止队列。

        ``wait=True``（默认）会等当前正在执行的任务自然结束，而不是强杀线程；
        排队中但尚未开始的任务会被标记为失败（``scheduler shutdown``）。
        """
        with self._lock:
            if not self._started:
                return
            self._stopping = True
        with self._cond:
            self._cond.notify_all()

        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            remaining = None
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0 if deadline is None else 1.0)

        with self._lock:
            self._started = False
            self._threads = []
            self._heartbeat_thread = None
        logger.info("任务队列已停止: %s", self.name)

    # ------------------------------------------------------------- 提交/取消
    def submit(
        self,
        job_id: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        kind: str = "analysis",
        company: str = "",
        trade_date: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        """提交任务，返回 job_id。

        任务会先以 ``status='queued'`` 落库，再进入内存等待队列；worker 认领时
        原子地把状态改成 ``running``。
        """
        payload = dict(payload or {})
        job_meta = dict(meta or {})
        job_meta.setdefault("kind", kind)
        job_meta.setdefault("payload", payload)
        job_meta.setdefault("company", company)
        job_meta.setdefault("trade_date", trade_date)

        if not job_id:
            job_id = create_job(company, trade_date, job_meta, scope=self.scope)
        else:
            ensure_queued_job(
                job_id,
                company=company,
                trade_date=trade_date,
                meta=job_meta,
                kind=kind,
                scope=self.scope,
            )

        with self._cond:
            if job_id not in self._pending_ids:
                self._pending.append(job_id)
                self._pending_ids.add(job_id)
            self._done_events.setdefault(job_id, threading.Event())
            self._cond.notify_all()
        return job_id

    def cancel(self, job_id: str) -> bool:
        """取消任务。

        * 还没开始的任务：直接从等待队列摘除并标记为 cancelled；
        * 正在执行的任务：设置取消事件，handler 在下一个检查点退出。
        """
        if not job_id:
            return False
        removed = False
        with self._cond:
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
            if job_id in self._pending_ids:
                self._pending_ids.discard(job_id)
                try:
                    self._pending.remove(job_id)
                except ValueError:
                    pass
                removed = True
            self._cond.notify_all()
        if removed:
            mark_job_cancelled(job_id, "任务在开始前被取消")
        else:
            request_cancel(job_id)
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._cond:
            event = self._cancel_events.get(job_id)
        if event is not None and event.is_set():
            return True
        return is_cancel_requested(job_id)

    # ------------------------------------------------------------- 状态查询
    @property
    def running(self) -> bool:
        """队列是否处于工作状态（已启动且未停止）。"""
        return self._started and not self._stopping

    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending)

    def active_count(self) -> int:
        with self._cond:
            return len(self._active)

    def active_job_ids(self) -> list[str]:
        with self._cond:
            return sorted(self._active)

    def is_running(self, job_id: str) -> bool:
        with self._cond:
            return job_id in self._active

    def wait_for(self, job_id: str, timeout: float | None = None) -> dict[str, Any] | None:
        """等待任务进入终态，返回最新的任务详情。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = get_job(job_id, include_result=True)
            if job is None:
                return None
            if job.get("status") in TERMINAL_JOB_STATUSES:
                return job
            if deadline is not None and time.monotonic() >= deadline:
                return job
            with self._cond:
                event = self._done_events.get(job_id)
            if event is not None:
                event.wait(0.05)
            else:
                time.sleep(0.02)

    # ------------------------------------------------------------- 内部实现
    def _recover_pending(self) -> list[str]:
        """把数据库里排队中的任务重新放回等待队列（只恢复本队列 scope 的任务）。"""
        recovered: list[str] = []
        with self._cond:
            for job in list_queued_jobs(scope=self.scope):
                job_id = job["job_id"]
                if job_id in self._pending_ids:
                    continue
                self._pending.append(job_id)
                self._pending_ids.add(job_id)
                recovered.append(job_id)
        return recovered

    def reap_stale(self) -> list[str]:
        """回收心跳超时的 running 任务。"""
        try:
            return reap_stale_jobs(self.stale_after_seconds)
        except Exception:  # pragma: no cover - 数据库异常不应阻断启动
            logger.exception("回收僵尸任务失败")
            return []

    def _next_job(self) -> tuple[str, dict[str, Any], str] | None:
        """取下一个任务：先内存等待队列，再回落到数据库（支持重启恢复）。"""
        while True:
            job_id: str | None = None
            with self._cond:
                while not self._pending and not self._stopping:
                    self._cond.wait(self.poll_interval)
                if self._pending:
                    job_id = self._pending.popleft()
                    self._pending_ids.discard(job_id)
                elif self._stopping:
                    return None

            if job_id is not None:
                job = self._load_job(job_id)
                if job is None:
                    continue
                return (
                    job["job_id"],
                    job.get("meta") or {},
                    job.get("kind") or "analysis",
                )

            # 数据库兜底：其它进程提交的任务 / 启动前的遗留任务
            try:
                claimed = claim_next_job(scope=self.scope)
            except Exception:  # pragma: no cover
                logger.exception("认领任务失败")
                claimed = None
            if claimed:
                return (
                    claimed["job_id"],
                    claimed.get("meta") or {},
                    claimed.get("kind") or "analysis",
                )
            if self._stopping:
                return None
            time.sleep(self.poll_interval)

    def _load_job(self, job_id: str) -> dict[str, Any] | None:
        try:
            return get_job(job_id, include_result=False)
        except Exception:  # pragma: no cover
            logger.exception("读取任务失败: %s", job_id)
            return None

    def _worker_loop(self) -> None:
        while True:
            item = self._next_job()
            if item is None:
                return
            job_id, meta, kind = item
            try:
                if not claim_job(job_id):
                    # 已被其它 worker/进程认领
                    continue
                self._run_job(job_id, meta, kind)
            except Exception:  # pragma: no cover - 兜底，避免 worker 静默退出
                logger.exception("worker 处理任务异常: %s", job_id)

    def _run_job(self, job_id: str, meta: dict[str, Any], kind: str) -> None:
        payload = meta.get("payload") if isinstance(meta, dict) else None
        payload = dict(payload or {})
        handler = self._handlers.get(kind)
        event = threading.Event()
        with self._cond:
            self._cancel_events[job_id] = event
            self._active.add(job_id)
            self._done_events.setdefault(job_id, threading.Event())
        try:
            if handler is None:
                raise RuntimeError(f"没有注册任务类型 '{kind}' 的处理器")
            if event.is_set():
                raise JobCancelled("任务在开始前被取消")
            result = handler(job_id, payload, event)
            if event.is_set():
                mark_job_cancelled(job_id, "任务被取消")
            else:
                finish_job(job_id, _json_safe(result or {}), status="completed")
        except JobCancelled:
            mark_job_cancelled(job_id, "任务被取消")
        except Exception as exc:
            logger.warning("任务失败: %s (%s)", job_id, exc)
            mark_job_failed(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            with self._cond:
                self._cancel_events.pop(job_id, None)
                self._active.discard(job_id)
                done = self._done_events.get(job_id)
                if done is not None:
                    done.set()
                self._cond.notify_all()

    def _heartbeat_loop(self) -> None:
        """刷新运行中任务的心跳，并把跨进程的取消请求转成取消事件。"""
        while True:
            with self._cond:
                if self._stopping:
                    return
                active = list(self._active)
            for job_id in active:
                try:
                    touch_job(job_id)
                except Exception:  # pragma: no cover
                    logger.exception("刷新心跳失败: %s", job_id)
                try:
                    if is_cancel_requested(job_id):
                        with self._cond:
                            event = self._cancel_events.get(job_id)
                        if event is not None:
                            event.set()
                except Exception:  # pragma: no cover
                    logger.exception("检查取消标记失败: %s", job_id)
            try:
                self.reap_stale()
            except Exception:  # pragma: no cover
                logger.exception("回收僵尸任务失败")
            time.sleep(self.heartbeat_interval)


_QUEUE: JobQueue | None = None
_QUEUE_LOCK = threading.Lock()


def get_job_queue() -> JobQueue | None:
    """返回进程级默认队列（未启动时返回 ``None``）。"""
    return _QUEUE


def set_job_queue(queue: JobQueue | None) -> None:
    """替换进程级默认队列（测试或多队列场景）。"""
    global _QUEUE
    with _QUEUE_LOCK:
        _QUEUE = queue


def ensure_job_queue(
    worker_count: int = 2, handlers: dict[str, JobHandler] | None = None
) -> JobQueue:
    """获取（或创建）进程级默认队列。"""
    global _QUEUE
    with _QUEUE_LOCK:
        if _QUEUE is None:
            _QUEUE = JobQueue(worker_count=worker_count)
        for kind, handler in (handlers or {}).items():
            _QUEUE.register(kind, handler)
        if not _QUEUE.running:
            _QUEUE.start()
        return _QUEUE


def stop_job_queue(wait: bool = True) -> None:
    """停止进程级默认队列。"""
    global _QUEUE
    with _QUEUE_LOCK:
        queue = _QUEUE
        _QUEUE = None
    if queue is not None:
        queue.shutdown(wait=wait)


__all__ = [
    "JobCancelled",
    "JobHandler",
    "JobQueue",
    "ensure_job_queue",
    "get_job_queue",
    "set_job_queue",
    "stop_job_queue",
]
