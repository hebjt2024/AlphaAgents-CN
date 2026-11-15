"""本地优先的行情取数。

调用方（回测 / 评估 / K 线 / 模拟盘）只需要给日期区间：

1. 先读 ``storage/market_store.py`` 的本地 SQLite；
2. 只对缺失的首尾区间调用一次在线数据源；
3. 在线数据标准化后写回本地，再返回合并结果；
4. 在线失败但本地有历史数据时，**降级返回本地数据**并附带 warning，
   而不是让整个回测/评估失败。

这样同一份历史行情只会下载一次，回测可复现，离线也能工作。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd

from tradingagents.services.price_loader import normalize_price_frame
from tradingagents.storage.market_store import (
    covered_range,
    get_sync_state,
    load_daily_bars,
    market_cache_enabled,
    market_stats,
    missing_ranges,
    save_daily_bars,
)

logger = logging.getLogger(__name__)

FetchText = Callable[[str, str, str], str]


def _has_weekday(start: str, end: str) -> bool:
    """区间内是否至少有一个工作日（周末不必打数据源）。"""
    try:
        current = date.fromisoformat(str(start)[:10])
        last = date.fromisoformat(str(end)[:10])
    except ValueError:
        return True
    while current <= last:
        if current.weekday() < 5:
            return True
        current += timedelta(days=1)
    return False


def _trim_to_trading_days(start: str, end: str) -> tuple[str, str] | None:
    """把请求区间收窄到真实交易日，避免为周末/节假日发请求。

    ``missing_ranges`` 只做日期比较，不知道 2026-01-01 是元旦、周六不是
    交易日；如果直接拿它去补洞，每次同步都会对假期发一次必然为空的请求。
    交易日历不可用时退化为工作日判断，仍比原始区间好。
    """
    try:
        from tradingagents.dataflows.trade_calendar import (
            is_cn_trading_day,
            next_cn_trading_day,
            previous_cn_trading_day,
        )

        if not is_cn_trading_day(start):
            start = next_cn_trading_day(start)
        if not is_cn_trading_day(end):
            end = previous_cn_trading_day(end)
    except Exception:
        # 日历加载失败：至少跳过纯周末区间
        if not _has_weekday(start, end):
            return None
    if start > end:
        return None
    return start, end


def _is_unadjusted(text: str) -> bool:
    """数据源是否明确声明“不复权”。

    东财限流时路由层会降级到腾讯（不复权）。若把它写进 qfq 缓存，
    跨除权日会出现数倍甚至数十倍的虚假涨跌，因此在入口直接拒绝。
    """
    return "不复权" in text or "未复权" in text


def _fetch_and_store(
    ticker: str,
    start: str,
    end: str,
    fetch: FetchText,
    adjust: str,
    source: str,
) -> pd.DataFrame:
    text = fetch(ticker, start, end)
    if adjust == "qfq" and _is_unadjusted(text):
        raise ValueError("数据源返回不复权行情，拒绝写入 qfq 缓存")
    frame = normalize_price_frame(text)
    save_daily_bars(ticker, frame, adjust=adjust, source=source)
    return frame


def load_prices(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    fetch: FetchText,
    adjust: str = "qfq",
    source: str = "akshare",
    use_cache: bool | None = None,
) -> pd.DataFrame:
    """本地优先读取行情；缺失区间用 ``fetch`` 补齐。

    参数
    ----
    fetch:
        在线取数函数，签名 ``(ticker, start, end) -> CSV 文本``。
        测试可以注入合成数据；生产由 ``backtest._load_prices`` 传
        ``route_to_vendor``。
    use_cache:
        ``None`` 时读取 ``TA_MARKET_CACHE``（默认开启）。
    """
    code = str(ticker or "").strip()
    start = str(start_date)[:10]
    end = str(end_date)[:10]
    if not code or not start or not end:
        raise ValueError("ticker / start_date / end_date 不能为空")

    enabled = market_cache_enabled() if use_cache is None else bool(use_cache)
    if not enabled:
        return normalize_price_frame(fetch(code, start, end))

    warnings: list[str] = []
    ranges = missing_ranges(code, start, end, adjust=adjust)
    for range_start, range_end in ranges:
        trimmed = _trim_to_trading_days(range_start, range_end)
        if trimmed is None:
            continue
        range_start, range_end = trimmed
        # 收窄后可能已经落在本地覆盖范围内（例如区间首尾是假期）
        if not missing_ranges(code, range_start, range_end, adjust=adjust):
            continue
        try:
            _fetch_and_store(code, range_start, range_end, fetch, adjust, source)
        except Exception as exc:
            warnings.append(
                f"在线补洞失败（{range_start}~{range_end}）：{type(exc).__name__}: {exc}"
            )
            logger.warning("行情补洞失败 %s %s~%s: %s", code, range_start, range_end, exc)

    frame = load_daily_bars(code, start, end, adjust=adjust)
    if frame.empty:
        # 本地完全没有这段数据（例如首次请求且补洞失败）：再尝试一次整段，
        # 这样错误信息仍然来自数据源，而不是伪装成"本地为空"。
        text = fetch(code, start, end)
        if adjust == "qfq" and _is_unadjusted(text):
            raise ValueError("数据源返回不复权行情，拒绝写入 qfq 缓存")
        frame = normalize_price_frame(text)
        save_daily_bars(code, frame, adjust=adjust, source=source)
        frame = load_daily_bars(code, start, end, adjust=adjust)
    if warnings:
        frame.attrs["warnings"] = warnings
    return frame


def sync_symbol(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    fetch: FetchText,
    adjust: str = "qfq",
    source: str = "akshare",
    force: bool = False,
) -> dict[str, Any]:
    """显式同步某个标的，返回同步前后的覆盖范围与写入行数。"""
    code = str(ticker or "").strip()
    before = covered_range(code, adjust=adjust)
    warnings: list[str] = []
    written = 0
    ranges = [(str(start_date)[:10], str(end_date)[:10])]
    if not force:
        ranges = missing_ranges(code, start_date, end_date, adjust=adjust)
    for range_start, range_end in ranges:
        trimmed = _trim_to_trading_days(range_start, range_end)
        if trimmed is None:
            continue
        range_start, range_end = trimmed
        if not force and not missing_ranges(
            code, range_start, range_end, adjust=adjust
        ):
            continue
        try:
            frame = _fetch_and_store(
                code, range_start, range_end, fetch, adjust, source
            )
            written += len(frame)
        except Exception as exc:
            warnings.append(
                f"{range_start}~{range_end}: {type(exc).__name__}: {exc}"
            )
            logger.warning("显式同步失败 %s: %s", code, exc)
    after = covered_range(code, adjust=adjust)
    return {
        "ticker": code,
        "adjust": adjust,
        "before": before,
        "after": after,
        "written": written,
        "warnings": warnings,
        "sync_state": get_sync_state(code, adjust=adjust),
    }


def coverage(ticker: str, *, adjust: str = "qfq") -> dict[str, Any]:
    """返回本地覆盖范围与同步状态。"""
    code = str(ticker or "").strip()
    covered = covered_range(code, adjust=adjust)
    return {
        "ticker": code,
        "adjust": adjust,
        "first_date": covered[0] if covered else "",
        "last_date": covered[1] if covered else "",
        "sync_state": get_sync_state(code, adjust=adjust),
    }


__all__ = [
    "load_prices",
    "sync_symbol",
    "coverage",
    "market_stats",
]
