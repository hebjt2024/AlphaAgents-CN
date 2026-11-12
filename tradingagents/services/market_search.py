"""股票搜索与热门股/涨停池。

搜索使用 AkShare 全量名称表 + 内置常用名称映射；
热门榜使用涨停池，避免依赖容易限流的东财人气榜接口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_NAME_CACHE_TTL = 3600
_name_cache: tuple[float, dict[str, str]] | None = None


def _ak():
    from tradingagents.dataflows import akshare_adapter

    if akshare_adapter.ak is None:
        raise ImportError("未安装 akshare，无法获取行情数据。")
    return akshare_adapter.ak


def _call(func, *args, **kwargs):
    from tradingagents.dataflows.akshare_adapter import retry_with_backoff

    return retry_with_backoff(
        lambda: func(*args, **kwargs),
        max_retries=1,
        base_delay=1.0,
    )


def _names() -> dict[str, str]:
    global _name_cache
    import time

    now = time.time()
    if _name_cache and now - _name_cache[0] < _NAME_CACHE_TTL:
        return dict(_name_cache[1])

    from tradingagents.dataflows.akshare_adapter import (
        _CHINESE_NAME_MAP,
        _load_akshare_names,
    )

    merged = dict(_CHINESE_NAME_MAP)
    try:
        merged.update(_load_akshare_names())
    except Exception:
        pass
    _name_cache = (now, merged)
    return dict(merged)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_label(exchange: str) -> str:
    return {"sh": "沪市", "sz": "深市", "bj": "北交所"}.get(exchange, "")


def search_stocks(query: str, limit: int = 20) -> dict[str, Any]:
    """按代码或中文名称搜索 A 股。"""
    from tradingagents.dataflows.ashare_rules import exchange_of

    q = (query or "").strip()
    limit = max(1, min(int(limit or 20), 50))
    if not q:
        return {"success": True, "query": "", "items": []}

    names = _names()
    code_to_name: dict[str, str] = {}
    for name, code in names.items():
        if code not in code_to_name or len(name) < len(code_to_name[code]):
            code_to_name[code] = name

    lowered = q.lower()
    matches = []
    for code, name in code_to_name.items():
        if q.isdigit():
            if not code.startswith(q) and q not in code:
                continue
            score = 0 if code == q else 1 if code.startswith(q) else 2
        elif lowered in name.lower():
            score = 0 if name == q else 1 if name.lower().startswith(lowered) else 2
        else:
            continue
        exchange = exchange_of(code)
        matches.append(
            {
                "ticker": code,
                "name": name,
                "exchange": exchange,
                "market": _market_label(exchange),
                "_score": score,
            }
        )

    matches.sort(key=lambda item: (item["_score"], item["ticker"]))
    for item in matches:
        item.pop("_score", None)
    return {"success": True, "query": q, "items": matches[:limit]}


def _trade_date(date: str | None) -> str:
    from tradingagents.dataflows.trade_calendar import (
        cn_today_str,
        is_cn_trading_day,
        previous_cn_trading_day,
    )

    value = (date or cn_today_str())[:10]
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        value = cn_today_str()
    if not is_cn_trading_day(value):
        value = previous_cn_trading_day(value)
    return value


def get_hot_stocks(
    limit: int = 30, date: str | None = None
) -> dict[str, Any]:
    """返回涨停池个股，按连板数和封板资金排序。"""
    limit = max(1, min(int(limit or 30), 100))
    trade_date = _trade_date(date)
    try:
        frame = _call(
            _ak().stock_zt_pool_em,
            date=trade_date.replace("-", ""),
        )
    except Exception as exc:
        return {
            "success": False,
            "date": trade_date,
            "message": f"{type(exc).__name__}: {str(exc)[:160]}",
            "items": [],
        }

    if frame is None or frame.empty:
        return {
            "success": True,
            "date": trade_date,
            "items": [],
            "message": "该交易日暂无涨停池数据。",
        }

    items = []
    for _, row in frame.iterrows():
        code = str(row.get("代码") or "").strip()
        if not code:
            continue
        items.append(
            {
                "ticker": code,
                "name": str(row.get("名称") or "").strip(),
                "change_pct": _num(row.get("涨跌幅")),
                "price": _num(row.get("最新价")),
                "turnover_yi": round(_num(row.get("成交额")) / 1e8, 2),
                "seal_fund_yi": round(_num(row.get("封板资金")) / 1e8, 2),
                "streak": int(_num(row.get("连板数"))),
                "break_count": int(_num(row.get("炸板次数"))),
                "industry": str(row.get("所属行业") or "").strip(),
                "first_seal": str(row.get("首次封板时间") or "").strip(),
                "last_seal": str(row.get("最后封板时间") or "").strip(),
            }
        )

    items.sort(
        key=lambda item: (
            item["streak"],
            item["seal_fund_yi"],
            item["change_pct"],
        ),
        reverse=True,
    )
    return {
        "success": True,
        "date": trade_date,
        "source": "limit_up",
        "count": len(items),
        "items": items[:limit],
        "disclaimer": "涨停池为实时快照，仅供研究参考，不构成投资建议。",
    }
