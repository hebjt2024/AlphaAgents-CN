"""盘中实时行情与分时数据。

与 ``akshare_adapter`` 的分工：

- ``akshare_adapter`` 负责日线、财务、资金流等**历史/低频**数据；
- 本模块只负责**盘中**：全市场快照、分时 K 线、买卖盘口、交易时段判断。

所有函数都返回纯 Python 结构，WebUI / 告警引擎 / CLI 共用；AkShare
的列名与异常口径只在这里出现一次。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .ashare_rules import normalize_code
from .trade_calendar import is_cn_trading_day

logger = logging.getLogger(__name__)

#: 支持的分钟周期。
INTRADAY_PERIODS = ("1", "5", "15", "30", "60")

#: 快照列 → 输出字段。
_QUOTE_COLUMNS = {
    "名称": "name",
    "最新价": "price",
    "涨跌幅": "change_pct",
    "涨跌额": "change",
    "今开": "open",
    "昨收": "prev_close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "量比": "volume_ratio",
    "换手率": "turnover",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "market_cap",
    "流通市值": "float_market_cap",
    "涨速": "speed",
    "5分钟涨跌": "change_5m",
}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def market_session(now: datetime | None = None) -> dict[str, Any]:
    """判断当前是否在 A 股连续竞价时段。

    返回 ``status``：``pre_open`` / ``open`` / ``lunch_break`` /
    ``closed`` / ``holiday``；``is_open`` 表示是否可产生盘中信号。
    """
    current = now or datetime.now()
    today = current.strftime("%Y-%m-%d")
    try:
        trading_day = is_cn_trading_day(today)
    except Exception:
        trading_day = current.weekday() < 5
    if not trading_day:
        return {
            "date": today,
            "time": current.strftime("%H:%M:%S"),
            "status": "holiday",
            "is_open": False,
            "label": "非交易日",
        }

    clock = current.hour * 60 + current.minute
    if clock < 9 * 60 + 30:
        status, label = "pre_open", "开盘前"
    elif clock <= 11 * 60 + 30:
        status, label = "open", "上午交易"
    elif clock < 13 * 60:
        status, label = "lunch_break", "午间休市"
    elif clock <= 15 * 60:
        status, label = "open", "下午交易"
    else:
        status, label = "closed", "已收盘"
    return {
        "date": today,
        "time": current.strftime("%H:%M:%S"),
        "status": status,
        "is_open": status == "open",
        "label": label,
    }


def get_realtime_quotes(tickers: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """批量读取实时快照，返回 ``{六位代码: quote}``。

    WHY 批量：``stock_zh_a_spot_em`` 一次返回全市场，且有 5 分钟缓存；
    逐只调用 ``stock_bid_ask_em`` 会触发限流且慢一个数量级。

    WHY 有腾讯兜底：``stock_zh_a_spot_em`` 走东方财富 ``push2`` 域名，
    该域名是最容易被 IP 限流的接口。腾讯 ``qt.gtimg.cn`` 是完全独立的
    数据源，东方财富限流期间通常仍然可用，因此作为第二通道。
    """
    wanted = {normalize_code(str(item)) for item in (tickers or [])}
    wanted.discard("")
    if not wanted:
        return {}

    from .akshare_adapter import get_akshare_spot_snapshot

    df = None
    try:
        df = get_akshare_spot_snapshot()
    except Exception as exc:  # noqa: BLE001 - 快照失败不应中断调用方
        logger.info("东方财富快照不可用，尝试腾讯行情兜底: %s", exc)
        df = None

    if df is None or getattr(df, "empty", True):
        return _tencent_fallback(sorted(wanted))

    code_col = next(
        (column for column in ("代码", "code", "symbol") if column in df.columns),
        None,
    )
    if not code_col:
        return _tencent_fallback(sorted(wanted))

    quotes: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = normalize_code(_text(row.get(code_col)))
        if code not in wanted:
            continue
        quote: dict[str, Any] = {"ticker": code, "as_of": datetime.now().isoformat(timespec="seconds")}
        for column, key in _QUOTE_COLUMNS.items():
            if column in df.columns:
                quote[key] = _text(row.get(column)) if key == "name" else _num(row.get(column))
        quotes[code] = quote

    # 东方财富快照返回了，但没覆盖到请求的标的（例如全市场快照缺行）：
    # 对缺失的标的再走腾讯补一次，避免"部分空值"。
    missing = [code for code in wanted if code not in quotes]
    if missing:
        quotes.update(_tencent_fallback(sorted(missing)))
    return quotes


def _tencent_fallback(codes: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯行情兜底（独立数据源）。失败时返回空字典。"""
    if not codes:
        return {}
    try:
        from .direct_sources import get_tencent_quotes

        return get_tencent_quotes(codes)
    except Exception as exc:  # noqa: BLE001
        logger.info("腾讯行情兜底也失败: %s", exc)
        return {}


def get_realtime_quote(ticker: str) -> dict[str, Any] | None:
    code = normalize_code(str(ticker or ""))
    if not code:
        return None
    return get_realtime_quotes([code]).get(code)


def get_intraday_bars(
    ticker: str,
    period: str = "5",
    days: int = 3,
) -> list[dict[str, Any]]:
    """读取最近 N 天的分钟线，返回按时间升序的 OHLCV 列表。"""
    code = normalize_code(str(ticker or ""))
    if not code:
        return []
    period = str(period or "5")
    if period not in INTRADAY_PERIODS:
        period = "5"
    days = max(1, min(int(days), 10))
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    from . import akshare_adapter as adapter

    adapter._require_akshare()
    try:
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_zh_a_hist_min_em(
                symbol=code,
                start_date=start,
                end_date=end,
                period=period,
                adjust="qfq",
            ),
            max_retries=2,
            base_delay=1.0,
        )
    except Exception as exc:
        logger.warning("分时行情获取失败 %s: %s", code, exc)
        return []
    if frame is None or frame.empty:
        return []

    columns = {
        "时间": "datetime",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "均价": "avg_price",
    }
    frame = frame.rename(columns=columns)
    if "datetime" not in frame.columns or "close" not in frame.columns:
        return []
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    items: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        items.append(
            {
                "datetime": row["datetime"].strftime("%Y-%m-%d %H:%M"),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "close": _num(row.get("close")),
                "volume": _num(row.get("volume")),
                "amount": _num(row.get("amount")),
                "avg_price": _num(row.get("avg_price")),
            }
        )
    return items


def get_order_book(ticker: str) -> dict[str, Any]:
    """读取五档买卖盘；部分标的/时段可能没有数据。"""
    code = normalize_code(str(ticker or ""))
    if not code:
        return {"ticker": "", "items": {}, "success": False}
    from . import akshare_adapter as adapter

    adapter._require_akshare()
    try:
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_bid_ask_em(symbol=code),
            max_retries=2,
            base_delay=1.0,
        )
    except Exception as exc:
        logger.warning("盘口获取失败 %s: %s", code, exc)
        return {"ticker": code, "items": {}, "success": False, "message": str(exc)}
    items: dict[str, Any] = {}
    if frame is not None and not frame.empty:
        item_col = next(
            (column for column in ("item", "项目", "指标") if column in frame.columns),
            None,
        )
        value_col = next(
            (column for column in ("value", "值", "数值") if column in frame.columns),
            None,
        )
        if item_col and value_col:
            for _, row in frame.iterrows():
                key = _text(row.get(item_col))
                if key:
                    items[key] = _num(row.get(value_col))
    return {"ticker": code, "success": bool(items), "items": items}


__all__ = [
    "INTRADAY_PERIODS",
    "market_session",
    "get_realtime_quotes",
    "get_realtime_quote",
    "get_intraday_bars",
    "get_order_book",
]
