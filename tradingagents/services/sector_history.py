"""板块热度历史：同花顺行业指数区间表现。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

_NAME_CACHE_TTL = 3600
_name_cache: tuple[float, list[dict[str, Any]]] | None = None


def _ak():
    from tradingagents.dataflows import akshare_adapter

    if akshare_adapter.ak is None:
        raise ImportError("未安装 akshare，无法获取板块历史。")
    return akshare_adapter.ak


def _call(func, *args, **kwargs):
    from tradingagents.dataflows.akshare_adapter import retry_with_backoff

    return retry_with_backoff(
        lambda: func(*args, **kwargs),
        max_retries=1,
        base_delay=1.0,
    )


def list_sector_names(force: bool = False) -> list[dict[str, Any]]:
    """返回同花顺行业列表，带 1 小时进程缓存。"""
    global _name_cache
    now = time.time()
    if not force and _name_cache and now - _name_cache[0] < _NAME_CACHE_TTL:
        return [dict(item) for item in _name_cache[1]]

    frame = _call(_ak().stock_board_industry_name_ths)
    if frame is None or frame.empty:
        return []
    items = []
    for _, row in frame.iterrows():
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        items.append({"name": name, "code": str(row.get("code") or "").strip()})
    _name_cache = (now, items)
    return [dict(item) for item in items]


def _pct_change(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods:
        return None
    base = closes[-1 - periods]
    if base <= 0:
        return None
    return round((closes[-1] / base - 1) * 100, 2)


def get_sector_history(name: str, days: int = 60) -> dict[str, Any]:
    """返回单个同花顺行业指数的近期走势与 1/5/20 日涨跌幅。"""
    name = (name or "").strip()
    if not name:
        return {"success": False, "message": "请提供板块名称。"}

    days = max(5, min(int(days or 60), 250))
    end = datetime.now()
    start = end - timedelta(days=max(120, days * 2))
    try:
        frame = _call(
            _ak().stock_board_industry_index_ths,
            symbol=name,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as exc:
        return {
            "success": False,
            "name": name,
            "message": f"{type(exc).__name__}: {str(exc)[:160]}",
        }

    if frame is None or frame.empty:
        return {"success": False, "name": name, "message": "未获取到该板块历史数据。"}

    frame = frame.sort_values("日期")
    closes: list[float] = []
    amounts: list[float] = []
    dates: list[str] = []
    for _, row in frame.iterrows():
        try:
            close = float(row.get("收盘价"))
            amount = float(row.get("成交额"))
        except (TypeError, ValueError):
            continue
        closes.append(close)
        amounts.append(amount)
        dates.append(str(row.get("日期") or "")[:10])
    if len(closes) < 2:
        return {"success": False, "name": name, "message": "该板块历史数据不足。"}

    series = [
        {"date": date, "close": round(close, 2)}
        for date, close in zip(dates[-days:], closes[-days:], strict=False)
    ]
    return {
        "success": True,
        "name": name,
        "as_of": dates[-1],
        "days": len(series),
        "close": round(closes[-1], 2),
        "change_1d": _pct_change(closes, 1),
        "change_5d": _pct_change(closes, 5),
        "change_20d": _pct_change(closes, 20),
        "turnover_yi": round(amounts[-1] / 1e8, 1) if amounts else 0.0,
        "series": series,
        "disclaimer": "板块历史为同花顺行业指数，仅供研究参考，不构成投资建议。",
    }
