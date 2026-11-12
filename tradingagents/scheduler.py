"""每日定时分析任务。

环境变量：
  SCHEDULE_ENABLED=1        是否启用
  SCHEDULE_TIME=18:00       每日执行时间（本地时区）
  SCHEDULE_STOCKS=600519,000001
  SCHEDULE_ALERTS=1         是否同时推送自选股策略预警
  SCHEDULE_REVIEW=1         是否同时推送市场复盘
  SCHEDULE_BRIEFING=1       是否推送合并后的每日盘后简报（优先于上面两项）
  SCHEDULE_MAX_ATTEMPTS=3   每日批次失败后的最大重试次数
  SCHEDULE_RETRY_BASE_SECONDS=60   重试退避基数（指数退避）
  SCHEDULE_RETRY_MAX_SECONDS=3600  重试退避上限
  SCHEDULE_LEASE_TTL_SECONDS=1800  每日批次租约有效期

可靠性设计
----------
* **状态持久化**：每日批次的 ``last_run_date``、重试次数、下次重试时间都写在
  ``scheduler_state`` 表里（见 :mod:`tradingagents.storage.scheduled_store`），
  进程重启后不会重复跑当天的批次，也不会因为"跑了一半"而丢状态。
* **失败可重试**：只有分析全部成功才会写 ``last_run_date``；失败会记录错误、
  累加重试次数并按指数退避安排下一次重试，超过上限才放弃。
* **多进程安全**：每日批次用 SQLite 租约（``scheduler_leases`` 表）抢占，
  只有拿到租约的进程才真正执行；``uvicorn --workers 4`` 不会再跑 4 份。
* **补跑（catch-up）**：如果进程在计划时间之后才启动，且当天还没跑过，
  会立即补跑一次，不会像旧实现那样整段错过。
* **分析执行走任务队列**：见 :mod:`tradingagents.services.job_queue`，
  分析有界并发、可取消、可回收僵尸任务。

独立运行（不依赖 WebUI）::

    python -m tradingagents.scheduler --once    # 跑一轮就退出（适合 cron）
    python -m tradingagents.scheduler --loop     # 常驻循环（默认）
    python -m tradingagents.scheduler --status   # 打印状态后退出

cron / systemd timer 示例::

    # 每天 18:00 的定时分析（工作日）
    0 18 * * 1-5 cd /srv/AlphaAgents-CN && uv run python -m tradingagents.scheduler --once

systemd timer 示例::

    [Timer]
    OnCalendar=Mon..Fri 18:00
    Persistent=true        # 机器宕机错过的任务会在开机后补跑
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from tradingagents.dataflows.trade_calendar import (
    cn_today_str,
    is_cn_symbol,
    is_cn_trading_day,
    previous_cn_trading_day,
)
from tradingagents.services.runner import run_analysis

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 调度器状态键（持久化在 scheduler_state 表）
STATE_KEY_DAILY_BATCH = "daily_batch"
# 每日批次的租约键
LEASE_KEY_DAILY_BATCH = "daily_batch"


def _state_key() -> str:
    """每日批次状态键（可用环境变量隔离多实例/测试）。"""
    return os.getenv("SCHEDULE_STATE_KEY", STATE_KEY_DAILY_BATCH)


def _lease_key() -> str:
    """每日批次租约键（可用环境变量隔离多实例/测试）。"""
    return os.getenv("SCHEDULE_LEASE_KEY", LEASE_KEY_DAILY_BATCH)


def _owner_id() -> str:
    """进程级唯一的租约持有者标识。"""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


_OWNER = _owner_id()


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.split(":", 1)
        return int(hour), int(minute)
    except Exception:
        return 18, 0


def _enabled(name: str) -> bool:
    return os.getenv(name, "0").lower() in ("1", "true", "yes", "on")


def _enabled_by_default(name: str) -> bool:
    """默认开启的开关：只有显式设为 0/false 才关闭。

    用于"只要调度器在跑就应该做"的事情，例如每日情绪快照 ——
    关掉它只会让历史序列断档，几乎没有人会想这么做。
    """
    return os.getenv(name, "1").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "").strip() or default))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _max_attempts() -> int:
    return max(1, _env_int("SCHEDULE_MAX_ATTEMPTS", 3))


def _lease_ttl() -> int:
    return max(30, _env_int("SCHEDULE_LEASE_TTL_SECONDS", 1800))


def _retry_delay(attempts: int) -> float:
    base = _env_float("SCHEDULE_RETRY_BASE_SECONDS", 60.0)
    maximum = _env_float("SCHEDULE_RETRY_MAX_SECONDS", 3600.0)
    delay = base * (2 ** max(0, attempts - 1))
    return float(min(delay, maximum))


# ---------------------------------------------------------------- 状态存储


def _load_batch_state() -> dict[str, Any]:
    from tradingagents.storage.scheduled_store import get_state_json

    try:
        return get_state_json(_state_key(), {}) or {}
    except Exception:  # pragma: no cover - 数据库异常不应阻断调度
        logger.exception("读取调度器状态失败")
        return {}


def _save_batch_state(state: dict[str, Any]) -> None:
    from tradingagents.storage.scheduled_store import set_state_json

    try:
        set_state_json(_state_key(), state)
    except Exception:  # pragma: no cover
        logger.exception("写入调度器状态失败")


# ---------------------------------------------------------------- 任务队列

_queue: Any = None
_queue_lock = threading.Lock()


def _analysis_handler(job_id: str, payload: dict[str, Any], cancel_event) -> dict:
    """队列处理器：执行一次分析。

    取消检查点：与 WebUI 侧 ``analysis_cancel_event`` 相同的模式——在开始前和
    结束后检查取消事件，命中即抛出 :class:`JobCancelled`，队列会把任务标记为
    cancelled 而不会写入结果。
    """
    if cancel_event.is_set():
        from tradingagents.services.job_queue import JobCancelled

        raise JobCancelled("任务在开始前被取消")
    ticker = payload.get("ticker", "")
    trade_date = payload.get("trade_date", "")
    source = payload.get("source", "scheduler")
    notify = bool(payload.get("notify", True))
    result = run_analysis(ticker, trade_date, source=source, notify=notify)
    if cancel_event.is_set():
        from tradingagents.services.job_queue import JobCancelled

        raise JobCancelled("任务在分析结束后被取消")
    return result


def _get_queue():
    """获取（必要时创建）调度器使用的任务队列。"""
    global _queue
    with _queue_lock:
        if _queue is not None and not _queue.running:
            # 队列被停止过（例如 WebUI 重启），重新创建一个
            _queue = None
        if _queue is None:
            try:
                from tradingagents.services.job_queue import JobQueue

                _queue = JobQueue(
                    worker_count=max(1, _env_int("SCHEDULE_WORKERS", 2)),
                    name="alphaagents-scheduler",
                    scope="scheduler",
                )
                _queue.register("analysis", _analysis_handler)
                _queue.start()
            except Exception:
                logger.exception("任务队列启动失败，回退为同步执行")
                _queue = None
        return _queue


def _stop_queue() -> None:
    global _queue
    with _queue_lock:
        queue = _queue
        _queue = None
    if queue is not None:
        try:
            queue.shutdown(wait=True)
        except Exception:  # pragma: no cover
            logger.exception("停止任务队列失败")


def _execute_analysis(
    ticker: str,
    trade_date: str,
    source: str = "scheduler",
    notify: bool = True,
    timeout: float = 14400.0,
) -> dict[str, Any]:
    """执行一次分析（优先走任务队列，队列不可用时回退为同步执行）。"""
    payload = {
        "ticker": ticker,
        "trade_date": trade_date,
        "source": source,
        "notify": notify,
    }
    queue = _get_queue()
    if queue is None:
        return run_analysis(ticker, trade_date, source=source, notify=notify)

    job_id = queue.submit(
        kind="analysis",
        company=ticker,
        trade_date=trade_date,
        payload=payload,
    )
    job = queue.wait_for(job_id, timeout=timeout)
    if job is None:
        raise RuntimeError(f"分析任务 {job_id} 没有返回结果")
    status = job.get("status")
    if status == "completed":
        return job.get("result") or {}
    if status == "cancelled":
        raise RuntimeError(f"分析任务 {job_id} 已取消")
    raise RuntimeError(job.get("error") or f"分析任务 {job_id} 状态异常: {status}")


# ---------------------------------------------------------------- 推送任务


def _run_alerts_once() -> None:
    if not _enabled("SCHEDULE_ALERTS"):
        return
    if not is_cn_trading_day(cn_today_str()):
        logger.info("非交易日，跳过自选股策略预警")
        return
    try:
        from tradingagents.services.strategy_alerts import send_strategy_alerts

        result = send_strategy_alerts(only_triggered=True)
        notify = result.get("notify") or {}
        channels = [
            name
            for name, value in notify.items()
            if isinstance(value, dict) and value.get("ok")
        ]
        logger.info(
            "自选股策略预警: %s，已推送=%s",
            result.get("message"),
            channels or "无",
        )
    except Exception:
        logger.exception("自选股策略预警失败")


def _run_review_once() -> None:
    if not _enabled("SCHEDULE_REVIEW"):
        return
    if not is_cn_trading_day(cn_today_str()):
        logger.info("非交易日，跳过市场复盘")
        return
    try:
        from tradingagents.services.market_review import send_market_review

        result = send_market_review()
        notify = result.get("notify") or {}
        channels = [
            name
            for name, value in notify.items()
            if isinstance(value, dict) and value.get("ok")
        ]
        logger.info(
            "市场复盘: %s，已推送=%s",
            result.get("as_of"),
            channels or "无",
        )
    except Exception:
        logger.exception("市场复盘推送失败")


def _run_briefing_once() -> None:
    if not _enabled("SCHEDULE_BRIEFING"):
        return
    if not is_cn_trading_day(cn_today_str()):
        logger.info("非交易日，跳过每日盘后简报")
        return
    try:
        from tradingagents.services.daily_briefing import send_daily_briefing

        result = send_daily_briefing()
        notify = result.get("notify") or {}
        channels = [
            name
            for name, value in notify.items()
            if isinstance(value, dict) and value.get("ok")
        ]
        logger.info(
            "每日盘后简报: %s，已推送=%s",
            result.get("as_of"),
            channels or "无",
        )
    except Exception:
        logger.exception("每日盘后简报推送失败")


# ---------------------------------------------------------------- 每日批次


def _record_batch_result(today: str, errors: list[str]) -> None:
    """记录每日批次结果：成功才写 ``last_run_date``，失败则安排重试。"""
    state = _load_batch_state()
    if not errors:
        state["last_run_date"] = today
        state["attempts"] = 0
        state["attempts_date"] = today
        state["last_error"] = ""
        state["next_retry_at"] = ""
        logger.info("每日批次完成: %s", today)
    else:
        attempts = (
            int(state.get("attempts", 0)) + 1
            if state.get("attempts_date") == today
            else 1
        )
        delay = _retry_delay(attempts)
        next_retry = datetime.now() + timedelta(seconds=delay)
        state["attempts"] = attempts
        state["attempts_date"] = today
        state["last_error"] = "; ".join(errors)[:500]
        state["next_retry_at"] = next_retry.isoformat(timespec="seconds")
        logger.warning(
            "每日批次失败(第 %s 次)，%s 秒后重试: %s",
            attempts,
            int(delay),
            state["last_error"],
        )
    _save_batch_state(state)


def _run_sentiment_snapshot_once() -> None:
    """每日记录一次市场情绪快照。

    WHY：``market_review`` 的数据源是**实时快照**，接口不支持回溯历史。
    如果不在每个交易日主动写一条记录，系统就永远无法回答
    "现在的情绪处于冰点还是高潮"——而这恰恰是 A 股最重要的择时依据。

    该步骤与是否推送复盘解耦：即使 `SCHEDULE_REVIEW=0`（不推送），
    只要当日批次跑过，也会积累一条情绪记录。
    """
    if not _enabled_by_default("SCHEDULE_SENTIMENT"):
        return
    today = cn_today_str()
    if not is_cn_trading_day(today):
        logger.info("非交易日，跳过情绪快照")
        return
    try:
        from tradingagents.services.market_sentiment import (
            snapshot_market_sentiment,
        )

        result = snapshot_market_sentiment()
        if result.get("saved"):
            logger.info("情绪快照已记录: %s", result.get("trade_date"))
        else:
            logger.warning(
                "情绪快照未记录: %s", result.get("message") or "未知原因"
            )
    except Exception:
        logger.exception("情绪快照记录失败")


def _run_once() -> None:
    """执行一次每日批量任务（简报/预警/复盘 + 情绪快照 + 定时分析）。"""
    errors: list[str] = []

    # 情绪快照优先：它不依赖推送渠道，纯粹是本地历史积累
    _run_sentiment_snapshot_once()

    if _enabled("SCHEDULE_BRIEFING"):
        _run_briefing_once()
    else:
        _run_alerts_once()
        _run_review_once()

    stocks = [
        item.strip()
        for item in os.getenv("SCHEDULE_STOCKS", "").split(",")
        if item.strip()
    ]
    if not stocks:
        logger.warning("SCHEDULE_STOCKS 为空，跳过定时分析")
    else:
        today = cn_today_str()
        trade_date = today
        if not is_cn_trading_day(today):
            trade_date = previous_cn_trading_day(today)
            logger.info("今天是非交易日，使用最近交易日 %s", trade_date)

        for ticker in stocks:
            try:
                if is_cn_symbol(ticker) and not is_cn_trading_day(trade_date):
                    logger.warning("跳过 %s：%s 非交易日", ticker, trade_date)
                    continue
                logger.info("开始定时分析: %s @ %s", ticker, trade_date)
                result = _execute_analysis(
                    ticker, trade_date, source="scheduler", notify=True
                )
                logger.info(
                    "定时分析完成: %s -> %s (report_id=%s)",
                    ticker,
                    result.get("decision"),
                    result.get("report_id"),
                )
            except Exception as exc:
                logger.exception("定时分析失败: %s", ticker)
                errors.append(f"{ticker}: {type(exc).__name__}: {exc}")

    _record_batch_result(cn_today_str(), errors)


def _batch_due(now: datetime, state: dict[str, Any]) -> bool:
    """判断每日批次当前是否应该执行（含补跑与重试窗口判断）。"""
    today = now.strftime("%Y-%m-%d")
    if state.get("last_run_date") == today:
        return False
    if state.get("attempts_date") == today:
        try:
            attempts = int(state.get("attempts", 0))
        except Exception:
            attempts = 0
        if attempts >= _max_attempts():
            logger.warning("每日批次重试次数已达上限 %s，今天不再重试", attempts)
            return False
    next_retry = state.get("next_retry_at")
    if next_retry:
        try:
            if now < datetime.fromisoformat(str(next_retry)):
                return False
        except Exception:
            pass
    hour, minute = _parse_time(os.getenv("SCHEDULE_TIME", "18:00"))
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # now >= 计划时间 即视为"到点"；进程在计划时间之后启动也会补跑
    return now >= scheduled


def _acquire_lease(key: str, ttl: int | None = None) -> bool:
    from tradingagents.storage.scheduled_store import acquire_lease

    try:
        return acquire_lease(
            key, _OWNER, ttl_seconds=ttl or _lease_ttl()
        )
    except Exception:  # pragma: no cover
        logger.exception("获取调度器租约失败: %s", key)
        return False


def _release_lease(key: str) -> None:
    from tradingagents.storage.scheduled_store import release_lease

    try:
        release_lease(key, _OWNER)
    except Exception:  # pragma: no cover
        logger.exception("释放调度器租约失败: %s", key)


def _run_daily_batch_if_due(now: datetime | None = None) -> bool:
    """检查每日批次是否到点（到点则执行，含补跑）；返回是否执行。"""
    if not _enabled("SCHEDULE_ENABLED"):
        return False
    reference = now or datetime.now()
    state = _load_batch_state()
    if not _batch_due(reference, state):
        return False
    if not _acquire_lease(_lease_key()):
        logger.info("每日批次已被其它进程占用，跳过")
        return False
    try:
        _run_once()
    except Exception:
        logger.exception("每日批次执行异常")
        _record_batch_result(reference.strftime("%Y-%m-%d"), ["每日批次执行异常"])
        return True
    finally:
        _release_lease(_lease_key())
    return True


# ---------------------------------------------------------------- 定时任务


def _run_scheduled_jobs() -> None:
    """执行用户在 WebUI/API 中配置的定时分析任务。"""
    if not _enabled("SCHEDULE_ENABLED"):
        return
    if not is_cn_trading_day(cn_today_str()):
        return
    try:
        from tradingagents.storage import due_scheduled_jobs, mark_scheduled_run
        from tradingagents.storage.scheduled_store import (
            claim_scheduled_job,
            mark_scheduled_failure,
            release_scheduled_claim,
        )

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        jobs = due_scheduled_jobs(now.strftime("%H:%M"), today)
        if not jobs:
            return
        for job in jobs:
            job_id = job["id"]
            ticker = job["ticker"]
            # 抢占式执行权：多进程/多 worker 下同一个任务只会被执行一次
            if not claim_scheduled_job(job_id, _OWNER):
                logger.info("定时任务已被其它进程占用，跳过: %s", ticker)
                continue
            try:
                logger.info("开始定时任务: %s @ %s", ticker, today)
                result = _execute_analysis(
                    ticker, today, source="scheduled_job", notify=True
                )
                logger.info(
                    "定时任务完成: %s -> %s (report_id=%s)",
                    ticker,
                    result.get("decision"),
                    result.get("report_id"),
                )
                # 只有成功才标记"今天已跑"，失败保持可重试
                mark_scheduled_run(job_id, today)
            except Exception as exc:
                logger.exception("定时任务失败: %s", ticker)
                mark_scheduled_failure(job_id, f"{type(exc).__name__}: {exc}")
            finally:
                release_scheduled_claim(job_id, _OWNER)
    except Exception:
        logger.exception("定时任务检查失败")


# ---------------------------------------------------------------- 主循环


def _loop() -> None:
    while not _stop_event.is_set():
        try:
            if not _enabled("SCHEDULE_ENABLED"):
                time.sleep(30)
                continue

            _run_scheduled_jobs()
            _run_daily_batch_if_due()
        except Exception:
            logger.exception("定时任务循环异常")
        _stop_event.wait(30)


def start_scheduler() -> threading.Thread | None:
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="alphaagents-scheduler", daemon=True)
    _thread.start()
    logger.info("定时分析线程已启动")
    return _thread


def stop_scheduler() -> None:
    """停止调度线程（并等待队列里的任务收尾）。"""
    _stop_event.set()
    thread = _thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)
    _stop_queue()


def run_scheduler_forever(poll_seconds: float = 30.0) -> None:
    """独立运行调度器（不依赖 WebUI），阻塞直到收到停止信号。"""
    logger.info("独立调度器已启动")
    try:
        while not _stop_event.is_set():
            _stop_event.wait(poll_seconds)
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        logger.info("收到中断信号，调度器退出")
    finally:
        stop_scheduler()


def scheduler_status() -> dict[str, Any]:
    """返回调度器状态快照（供 CLI / WebUI 展示）。"""
    state = _load_batch_state()
    status: dict[str, Any] = {
        "enabled": _enabled("SCHEDULE_ENABLED"),
        "schedule_time": os.getenv("SCHEDULE_TIME", "18:00"),
        "stocks": [
            item.strip()
            for item in os.getenv("SCHEDULE_STOCKS", "").split(",")
            if item.strip()
        ],
        "owner": _OWNER,
        "daily_batch": state,
        "last_run_date": state.get("last_run_date", ""),
        "attempts": state.get("attempts", 0),
    }
    try:
        from tradingagents.storage.scheduled_store import get_lease

        status["lease"] = get_lease(_lease_key())
    except Exception:  # pragma: no cover
        status["lease"] = None
    return status


def main(argv: list[str] | None = None) -> int:
    """命令行入口：``python -m tradingagents.scheduler``。"""
    parser = argparse.ArgumentParser(description="AlphaAgents-CN 定时分析调度器")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一轮（到期才跑），然后退出；适合 cron / systemd timer",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="常驻循环（默认行为）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="打印当前调度状态后退出",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=30.0,
        help="常驻模式的轮询间隔（默认 30 秒）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("SCHEDULE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.status:
        import json

        print(json.dumps(scheduler_status(), ensure_ascii=False, indent=2))
        return 0

    if args.once:
        _run_scheduled_jobs()
        _run_daily_batch_if_due()
        _stop_queue()
        return 0

    _stop_event.clear()
    run_scheduler_forever(args.poll_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
