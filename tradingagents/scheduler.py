"""进程内每日定时分析任务（MVP）。

环境变量：
  SCHEDULE_ENABLED=1        是否启用
  SCHEDULE_TIME=18:00       每日执行时间（本地时区）
  SCHEDULE_STOCKS=600519,000001
  SCHEDULE_ALERTS=1         是否同时推送自选股策略预警
  SCHEDULE_REVIEW=1         是否同时推送市场复盘
  SCHEDULE_BRIEFING=1       是否推送合并后的每日盘后简报（优先于上面两项）
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

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


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.split(":", 1)
        return int(hour), int(minute)
    except Exception:
        return 18, 0


def _enabled(name: str) -> bool:
    return os.getenv(name, "0").lower() in ("1", "true", "yes", "on")


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


def _run_once() -> None:
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
        return

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
            result = run_analysis(
                ticker, trade_date, source="scheduler", notify=True
            )
            logger.info(
                "定时分析完成: %s -> %s (report_id=%s)",
                ticker,
                result.get("decision"),
                result.get("report_id"),
            )
        except Exception:
            logger.exception("定时分析失败: %s", ticker)


def _run_scheduled_jobs() -> None:
    """执行用户在 WebUI/API 中配置的定时分析任务。"""
    if not _enabled("SCHEDULE_ENABLED"):
        return
    if not is_cn_trading_day(cn_today_str()):
        return
    try:
        from tradingagents.runtime import ANALYSIS_LOCK
        from tradingagents.storage import due_scheduled_jobs, mark_scheduled_run

        if ANALYSIS_LOCK.locked():
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        jobs = due_scheduled_jobs(now.strftime("%H:%M"), today)
        for job in jobs:
            if ANALYSIS_LOCK.locked():
                break
            try:
                logger.info("开始定时任务: %s @ %s", job["ticker"], today)
                result = run_analysis(
                    job["ticker"],
                    today,
                    source="scheduled_job",
                    notify=True,
                )
                logger.info(
                    "定时任务完成: %s -> %s (report_id=%s)",
                    job["ticker"],
                    result.get("decision"),
                    result.get("report_id"),
                )
            except Exception:
                logger.exception("定时任务失败: %s", job["ticker"])
            finally:
                mark_scheduled_run(job["id"], today)
    except Exception:
        logger.exception("定时任务检查失败")


def _loop() -> None:
    last_run_date = ""
    while not _stop_event.is_set():
        try:
            if not _enabled("SCHEDULE_ENABLED"):
                time.sleep(30)
                continue

            _run_scheduled_jobs()

            hour, minute = _parse_time(os.getenv("SCHEDULE_TIME", "18:00"))
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if (
                now.hour > hour or (now.hour == hour and now.minute >= minute)
            ) and last_run_date != today:
                last_run_date = today
                _run_once()
        except Exception:
            logger.exception("定时任务循环异常")
        time.sleep(30)


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
    _stop_event.set()
