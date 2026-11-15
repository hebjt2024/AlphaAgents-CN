"""全市场选股数据：快照、最新业绩、资金流排行。

选股需要"横截面"数据，而不是逐只股票拉历史：

- **全市场快照**（``stock_zh_a_spot_em``）：价格、涨跌幅、成交额、量比、
  换手率、PE/PB、市值、60 日/年初至今涨跌幅；
- **最新业绩报表**（``stock_yjbb_em``）：ROE、毛利率、营收/净利同比、
  每股经营现金流、所处行业；
- **资金流排行**（``stock_individual_fund_flow_rank``，可选）：今日主力
  净流入占比。该接口需要翻几十页，默认关闭，避免限流。

所有函数都带进程内缓存；失败时返回空表 + ``attrs["warnings"]``，
让选股服务可以降级而不是整体失败。
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import pandas as pd

from .ashare_rules import is_a_share_code, normalize_code

logger = logging.getLogger(__name__)

#: 快照缓存 60 秒（底层 AkShare 快照已有 5 分钟缓存，这里只缓存列归一结果）。
_SNAPSHOT_TTL = 60.0
#: 业绩报表缓存 6 小时：一天内多次选股不应重复翻页。
_FINANCIAL_TTL = 6 * 3600.0
#: 资金流排行缓存 10 分钟。
_FUND_FLOW_TTL = 600.0

_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}

_SNAPSHOT_COLUMNS = {
    "代码": "code",
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
    "60日涨跌幅": "change_60d",
    "年初至今涨跌幅": "change_ytd",
}

_FINANCIAL_COLUMNS = {
    "股票代码": "code",
    "股票简称": "name_fin",
    "每股收益": "eps",
    "营业总收入-营业总收入": "revenue",
    "营业总收入-同比增长": "revenue_growth",
    "营业总收入-季度环比增长": "revenue_qoq",
    "净利润-净利润": "net_profit",
    "净利润-同比增长": "profit_growth",
    "净利润-季度环比增长": "profit_qoq",
    "每股净资产": "bps",
    "净资产收益率": "roe",
    "每股经营现金流量": "ocf_ps",
    "销售毛利率": "gross_margin",
    "所处行业": "industry",
    "最新公告日期": "announce_date",
}

_EMPTY_SNAPSHOT = pd.DataFrame(columns=list(_SNAPSHOT_COLUMNS.values()))
_EMPTY_FINANCIAL = pd.DataFrame(columns=list(_FINANCIAL_COLUMNS.values()))


def clear_screener_cache() -> None:
    _CACHE.clear()


def _cached(key: str, ttl: float) -> pd.DataFrame | None:
    item = _CACHE.get(key)
    if item is None:
        return None
    timestamp, frame = item
    if time.time() - timestamp > ttl:
        _CACHE.pop(key, None)
        return None
    return frame.copy()


def _store(key: str, frame: pd.DataFrame) -> pd.DataFrame:
    _CACHE[key] = (time.time(), frame.copy())
    return frame


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
        errors="coerce",
    )


def _codes(series: pd.Series) -> pd.Series:
    def _one(value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return normalize_code(str(value or ""))

    return series.map(_one)


def get_universe_snapshot() -> pd.DataFrame:
    """全 A 股实时快照（列已归一为英文）。"""
    cached = _cached("universe", _SNAPSHOT_TTL)
    if cached is not None:
        return cached

    from . import akshare_adapter as adapter

    try:
        adapter._require_akshare()
        frame = adapter.get_akshare_spot_snapshot()
        if frame is None or frame.empty:
            raise ValueError("实时快照为空")
        frame = frame.rename(columns=_SNAPSHOT_COLUMNS)
        keep = [column for column in _SNAPSHOT_COLUMNS.values() if column in frame.columns]
        frame = frame[keep].copy()
        frame["code"] = _codes(frame["code"])
        frame["name"] = frame["name"].astype(str)
        for column in frame.columns:
            if column not in ("code", "name"):
                frame[column] = _num(frame[column])
        frame = frame[frame["code"].map(is_a_share_code)]
        frame = frame.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)
        return _store("universe", frame)
    except Exception as exc:
        logger.warning("全市场快照获取失败: %s", exc)
        result = _EMPTY_SNAPSHOT.copy()
        result.attrs["warnings"] = [f"全市场快照获取失败：{exc}"]
        return result


def _recent_report_periods(today: date | None = None, count: int = 6) -> list[str]:
    current = today or date.today()
    periods: list[str] = []
    year, month = current.year, current.month
    # 从最近一个可能已披露的季末开始，向前取 count 个季度。
    if month >= 10:
        start = (year, 9, 30)
    elif month >= 7:
        start = (year, 6, 30)
    elif month >= 4:
        start = (year, 3, 31)
    else:
        start = (year - 1, 12, 31)
    y, m, d = start
    for _ in range(count):
        periods.append(f"{y:04d}{m:02d}{d:02d}")
        if m == 3:
            y, m, d = y - 1, 12, 31
        elif m == 6:
            m, d = 3, 31
        elif m == 9:
            m, d = 6, 30
        else:
            m, d = 9, 30
    return periods


def get_financial_snapshot(today: date | None = None) -> pd.DataFrame:
    """最近一期全市场业绩报表（ROE / 毛利率 / 营收与净利同比等）。"""
    cache_key = f"financials:{(today or date.today()).isoformat()}"
    cached = _cached(cache_key, _FINANCIAL_TTL)
    if cached is not None:
        return cached

    from . import akshare_adapter as adapter

    warnings: list[str] = []
    try:
        adapter._require_akshare()
        for period in _recent_report_periods(today):
            try:
                frame = adapter.retry_with_backoff(
                    lambda p=period: adapter.ak.stock_yjbb_em(date=p),
                    max_retries=2,
                    base_delay=2.0,
                )
            except Exception as exc:
                warnings.append(f"{period} 业绩报表获取失败：{type(exc).__name__}")
                continue
            if frame is None or frame.empty:
                continue
            frame = frame.rename(columns=_FINANCIAL_COLUMNS)
            keep = [
                column
                for column in _FINANCIAL_COLUMNS.values()
                if column in frame.columns
            ]
            frame = frame[keep].copy()
            frame["code"] = _codes(frame["code"])
            for column in frame.columns:
                if column not in ("code", "name_fin", "industry", "announce_date"):
                    frame[column] = _num(frame[column])
            frame = frame[frame["code"].map(is_a_share_code)]
            frame = frame.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)
            frame.attrs["warnings"] = warnings
            return _store(cache_key, frame)
        raise ValueError("最近 6 个报告期均无业绩数据")
    except Exception as exc:
        logger.warning("业绩报表获取失败: %s", exc)
        warnings.append(str(exc))
        result = _EMPTY_FINANCIAL.copy()
        result.attrs["warnings"] = warnings
        # 负缓存 60 秒，避免用户连续点击“运行选股”反复翻 6 个季度。
        _CACHE[cache_key] = (time.time() - _FINANCIAL_TTL + 60, result)
        return result


def get_fund_flow_snapshot(indicator: str = "今日") -> pd.DataFrame:
    """全市场主力资金流排行（可选；默认关闭以降低限流风险）。"""
    key = f"fund_flow:{indicator}"
    cached = _cached(key, _FUND_FLOW_TTL)
    if cached is not None:
        return cached

    from . import akshare_adapter as adapter

    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_individual_fund_flow_rank(
                indicator=indicator
            ),
            max_retries=2,
            base_delay=2.0,
        )
        if frame is None or frame.empty:
            raise ValueError("资金流排行为空")
        code_column = next(
            (column for column in ("代码", "股票代码") if column in frame.columns),
            None,
        )
        net_column = next(
            (
                column
                for column in frame.columns
                if "主力净流入-净占比" in str(column)
            ),
            None,
        )
        amount_column = next(
            (
                column
                for column in frame.columns
                if "主力净流入-净额" in str(column)
            ),
            None,
        )
        if not code_column:
            raise ValueError("资金流排行缺少代码列")
        result = pd.DataFrame(
            {
                "code": _codes(frame[code_column]),
                "main_net_ratio": _num(frame[net_column]) if net_column else 0.0,
                "main_net_amount": _num(frame[amount_column])
                if amount_column
                else 0.0,
            }
        )
        result = result[result["code"].map(is_a_share_code)]
        result = result.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)
        result.attrs["indicator"] = indicator
        return _store(key, result)
    except Exception as exc:
        logger.warning("资金流排行获取失败: %s", exc)
        result = pd.DataFrame(
            columns=["code", "main_net_ratio", "main_net_amount"]
        )
        result.attrs["warnings"] = [f"资金流排行获取失败：{exc}"]
        return result


__all__ = [
    "clear_screener_cache",
    "get_universe_snapshot",
    "get_financial_snapshot",
    "get_fund_flow_snapshot",
]
