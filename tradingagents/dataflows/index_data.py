"""指数 / ETF 基准行情（组合风险度量用）。

组合的 Beta、超额收益、跟踪误差必须有一个基准。默认使用沪深 300
（``000300``）；指数接口不可用时退化为沪深 300 ETF（``510300``）。

行情写入本地行情库的独立复权口径 ``index``，与个股 qfq 数据隔离：
指数没有复权概念，混在一起会让覆盖范围和同步状态变得难以解释。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from tradingagents.storage.market_store import (
    covered_range,
    load_daily_bars,
    save_daily_bars,
)

from . import akshare_adapter as adapter

logger = logging.getLogger(__name__)

INDEX_ADJUST = "index"


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame = frame.rename(
        columns={
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
            "成交额": "Amount",
        }
    )
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close"])
    frame = frame[frame["Close"] > 0]
    return (
        frame.sort_values("Date")
        .drop_duplicates(subset=["Date"], keep="last")
        .reset_index(drop=True)
    )


def _fetch_index(symbol: str, start: str, end: str) -> tuple[pd.DataFrame, str]:
    """先试指数，再试沪深 300 ETF。返回 (frame, source)。"""
    adapter._require_akshare()
    try:
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.index_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            ),
            max_retries=2,
            base_delay=1.5,
        )
        normalized = _normalize(frame)
        if not normalized.empty:
            return normalized, "index"
    except Exception as exc:
        logger.info("指数行情不可用 %s: %s", symbol, exc)

    # 指数接口失败时，用沪深 300 ETF 作为基准代理。
    etf_symbol = "510300" if symbol in ("000300", "sh000300") else symbol
    try:
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.fund_etf_hist_em(
                symbol=etf_symbol,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust="",
            ),
            max_retries=1,
            base_delay=1.5,
        )
        normalized = _normalize(frame)
        if not normalized.empty:
            return normalized, f"etf:{etf_symbol}"
    except Exception as exc:
        logger.warning("基准 ETF 行情也不可用 %s: %s", etf_symbol, exc)
    return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"]), ""


def get_index_history(
    symbol: str = "000300", days: int = 400
) -> dict[str, Any]:
    """读取指数/ETF 基准日线，优先本地缓存。"""
    code = str(symbol or "000300").strip() or "000300"
    end = date.today()
    start = end - timedelta(days=max(30, min(int(days), 3650)))
    start_text, end_text = start.isoformat(), end.isoformat()
    warnings: list[str] = []

    adjust = INDEX_ADJUST
    source = "index"
    local = load_daily_bars(code, start_text, end_text, adjust=adjust)
    covered = covered_range(code, adjust=adjust)
    need_fetch = local.empty or (
        covered
        and (
            covered[1] < (end - timedelta(days=3)).isoformat()
            or covered[0] > start_text
        )
    )
    if need_fetch:
        frame, fetched_source = _fetch_index(code, start_text, end_text)
        if not frame.empty:
            # 指数与 ETF 备胎必须使用不同缓存键，否则 4 元的 ETF 价格
            # 会和 3800 点的指数价格拼成一条序列。
            adjust = "index_etf" if fetched_source.startswith("etf") else INDEX_ADJUST
            source = fetched_source or adjust
            save_daily_bars(code, frame, adjust=adjust, source=source)
            local = load_daily_bars(code, start_text, end_text, adjust=adjust)
            if adjust == "index_etf":
                warnings.append(f"指数 {code} 不可用，使用 ETF 代理作为基准")
        else:
            warnings.append(f"基准 {code} 行情获取失败")
    if local.empty:
        # 指数主键为空时，可能上次只写入了 ETF 代理。
        local = load_daily_bars(code, start_text, end_text, adjust="index_etf")
        if not local.empty:
            adjust = "index_etf"
            source = "etf"
            warnings.append(f"使用 ETF 代理基准 {code}")
    if local.empty:
        return {
            "success": False,
            "symbol": code,
            "items": [],
            "warnings": warnings,
        }
    return {
        "success": True,
        "symbol": code,
        "source": source,
        "adjust": adjust,
        "as_of": local["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "items": local,
        "warnings": warnings,
    }


__all__ = ["get_index_history", "INDEX_ADJUST"]
