"""A 股交易日历工具。

优先使用 AkShare 的 `tool_trade_date_hist_sina()` 获取真实交易日；
网络不可用时退化为“周一至周五”规则（并标记为降级状态）。

对外提供：
  - is_cn_symbol(symbol)
  - is_cn_trading_day(date_str)
  - previous_cn_trading_day(date_str)
  - next_cn_trading_day(date_str)
  - validate_analysis_date(symbol, date_str)
  - calendar_degraded()
  - cn_today_str()
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta

_lock = threading.Lock()
_cached_dates: set | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 24 * 3600

# 日历降级标记：为 True 表示当前拿不到真实交易日历，
# 只能退化为“周一至周五”规则判断。
#
# WHY（FIX 3）：历史实现把“抓取失败后得到的空集合”当成有效日历缓存 24 小时，
# 结果是这 24 小时内每个法定节假日都会被当成交易日 —— “缺失交易日”提示
# 与 validate_analysis_date 会静默出错。必须区分：
#   - 日历不可用（网络/接口故障）→ 降级 + 对外暴露降级状态；
#   - 日历可用（成功抓到）→ 正常判断。
_calendar_unavailable = False

# 降级状态下的重试间隔（秒）。
# WHY：日历不可用时不希望每次调用都去打网络（会拖慢整条分析链路），
# 但也不能像以前那样缓存 24 小时，因此用一个较短的退避间隔重试。
_CALENDAR_RETRY_INTERVAL = float(os.getenv("TRADE_CALENDAR_RETRY_INTERVAL", "300"))
_calendar_retry_ts: float = 0.0


def cn_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _load_trade_dates() -> set:
    """加载 A 股交易日集合。

    WHY（FIX 3）：抓取失败时**不能**把空集合当作有效日历缓存 24 小时。
    空集合会让 is_cn_trading_day 退化为“周一至周五”规则，
    从而把每一个法定节假日都误判为交易日。
    因此：
      - 抓取成功 → 写入缓存并清除降级标记；
      - 抓取失败 → 不写入空缓存，标记 _calendar_unavailable = True，
        并保留上一次成功抓到的日历（若仍比工作日规则准确）；
        若从未成功抓取过，则返回空集合并由调用方走降级分支。
    """
    global _cached_dates, _cache_ts, _calendar_unavailable
    global _calendar_retry_ts
    now = time.time()
    with _lock:
        if _cached_dates is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cached_dates
        # 降级状态下做短间隔重试，避免每次调用都去打网络
        if (
            _calendar_unavailable
            and (now - _calendar_retry_ts) < _CALENDAR_RETRY_INTERVAL
        ):
            return _cached_dates if _cached_dates is not None else set()

    dates: set = set()
    fetched = False
    try:
        import akshare as ak

        func = getattr(ak, "calendar_trade_date_hist_sina", None) or getattr(
            ak, "tool_trade_date_hist_sina", None
        )
        if func is None:
            raise RuntimeError("当前 AkShare 版本没有交易日历接口")
        df = func()
        if df is not None and not df.empty:
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            for value in df[col].tolist():
                try:
                    dates.add(_parse_date(value).isoformat())
                except Exception:
                    continue
            fetched = bool(dates)
    except Exception:
        fetched = False

    with _lock:
        if fetched:
            # 成功：写入缓存，并清除降级状态
            _cached_dates = dates
            _cache_ts = now
            _calendar_unavailable = False
            _calendar_retry_ts = 0.0
            return dates

        # 失败：绝不把空集合缓存成“有效交易日历”
        _calendar_unavailable = True
        _calendar_retry_ts = now
        if _cached_dates is not None:
            # 保留上一次成功抓到的日历（过期也比工作日规则准确）
            return _cached_dates
        return dates


def calendar_degraded() -> bool:
    """返回 True 表示当前交易日历不可用（只能按“周一至周五”规则判断）。

    WHY：调用方（例如生成“缺失交易日”提示、校验分析日期）需要知道
    当前判断是基于降级规则，才能提醒用户“节假日可能被误判”。
    """
    return bool(_calendar_unavailable)


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
    """判断某日是否为 A 股交易日。

    WHY：日历不可用时只能退化为“周一至周五”规则，此时法定节假日会被
    误判为交易日。降级状态通过 calendar_degraded() 对外暴露，
    调用方据此提示用户（而不是静默给出错误结论）。
    """
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
        - 交易日历不可用（降级）时，message 中会带上降级提示
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
        if calendar_degraded():
            # WHY：日历不可用时按工作日规则判断，节假日可能被误判为交易日，
            # 必须把降级状态如实告诉调用方，而不是静默给出"是交易日"的结论。
            return (
                True,
                "注意：交易日历不可用，本次按“周一至周五”规则判断，"
                "法定节假日可能被误判为交易日。",
                date_str,
            )
        return True, "", date_str

    adjusted = previous_cn_trading_day(date_str)
    message = f"{date_str} 不是 A 股交易日（周末或节假日），建议使用 {adjusted}"
    if calendar_degraded():
        message += "（注意：交易日历不可用，本次按“周一至周五”规则判断）"
    return (
        False,
        message,
        adjusted,
    )
