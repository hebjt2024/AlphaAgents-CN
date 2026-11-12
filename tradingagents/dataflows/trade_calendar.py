"""A 股交易日历工具。

优先使用 AkShare 的 `tool_trade_date_hist_sina()` 获取真实交易日；
网络不可用时退化为“周一至周五”规则。

对外提供：
  - is_cn_symbol(symbol)
  - is_cn_trading_day(date_str)
  - previous_cn_trading_day(date_str)
  - next_cn_trading_day(date_str)
  - validate_analysis_date(symbol, date_str)
  - cn_today_str()
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

_lock = threading.Lock()
_cached_dates: set | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 24 * 3600


def cn_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _load_trade_dates() -> set:
    """加载 A 股交易日集合，失败时回退为工作日规则（返回空集合）。"""
    global _cached_dates, _cache_ts
    now = time.time()
    with _lock:
        if _cached_dates is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cached_dates

    dates = set()
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            for value in df[col].tolist():
                try:
                    dates.add(_parse_date(value).isoformat())
                except Exception:
                    continue
    except Exception:
        dates = set()

    with _lock:
        _cached_dates = dates
        _cache_ts = now
    return dates


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_cn_symbol(symbol: str) -> bool:
    """判断是否为 A 股标的（6 位数字、中文名或带 .SH/.SZ/.SS 后缀）。"""
    if not symbol:
        return False
    s = symbol.strip()
    if s.isdigit() and len(s) == 6:
        return True
    if s.lower().startswith(("sh", "sz", "bj")) and (
        len(s) == 8 or len(s) == 9
    ):
        return True
    if s.upper().endswith((".SH", ".SZ", ".SS", ".BJ")):
        return True
    # 中文名按 A 股处理
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def is_cn_trading_day(date_str) -> bool:
    d = _parse_date(date_str)
    dates = _load_trade_dates()
    if dates:
        return d.isoformat() in dates
    return _is_weekday(d)


def previous_cn_trading_day(date_str) -> str:
    d = _parse_date(date_str)
    for _ in range(30):
        d = d - timedelta(days=1)
        if is_cn_trading_day(d):
            return d.isoformat()
    return date_str


def next_cn_trading_day(date_str) -> str:
    d = _parse_date(date_str)
    for _ in range(30):
        d = d + timedelta(days=1)
        if is_cn_trading_day(d):
            return d.isoformat()
    return date_str


def validate_analysis_date(symbol: str, date_str: str) -> tuple[bool, str, str | None]:
    """
    校验分析日期。

    返回:
        (ok, message, adjusted_date)
        - ok=True 表示日期可用
        - 非 A 股标的不做交易日校验
        - 日期格式错误返回 ok=False
        - 非交易日返回 ok=False，并给出建议的上一交易日
    """
    if not date_str:
        return False, "分析日期不能为空", None
    try:
        parsed = _parse_date(date_str)
    except (ValueError, TypeError):
        return False, "日期格式必须为 YYYY-MM-DD", None

    # 所有市场都不允许分析未来日期（会造成无数据或未来函数）
    today = date.today()
    if parsed > today:
        return (
            False,
            f"{date_str} 晚于今天（{today.isoformat()}），不能分析未来日期",
            today.isoformat(),
        )

    if not is_cn_symbol(symbol):
        return True, "", date_str

    if is_cn_trading_day(date_str):
        return True, "", date_str

    adjusted = previous_cn_trading_day(date_str)
    return (
        False,
        f"{date_str} 不是 A 股交易日（周末或节假日），建议使用 {adjusted}",
        adjusted,
    )
