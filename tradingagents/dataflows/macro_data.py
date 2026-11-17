"""宏观 / 两融 / 行业轮动 / ETF 资金流。

这四类数据回答的是"市场处在什么环境、钱在往哪里走"：

- **宏观**：PMI、CPI、PPI、M2、LPR、Shibor、中美国债收益率；
- **两融**：沪深融资融券余额与变化，反映杠杆资金风险偏好；
- **行业轮动**：今日 / 5 日 / 10 日行业涨跌幅与主力净流入，合成轮动分；
- **ETF 资金流**：场内 ETF 主力净流入/流出，观察宽基与主题资金方向。

所有函数都带缓存；单个数据源失败返回空表 + warning，不阻断其他部分。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any

import pandas as pd

from . import akshare_adapter as adapter

logger = logging.getLogger(__name__)

MACRO_TTL = 6 * 3600.0
MARGIN_TTL = 1800.0
ROTATION_TTL = 600.0
ETF_TTL = 300.0
_CACHE: dict[str, tuple[float, Any]] = {}


def clear_macro_cache() -> None:
    _CACHE.clear()


def _cache_get(key: str, ttl: float) -> Any:
    item = _CACHE.get(key)
    if item is None:
        return None
    timestamp, value = item
    if time.time() - timestamp > ttl:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    _CACHE[key] = (time.time(), value)
    return value


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return None if not pd.notna(number) else number


def _text(value: Any) -> str:
    if value is None or not pd.notna(value):
        return ""
    return str(value).strip()


def _call(func, *, name: str, warnings: list[str], retries: int = 2):
    try:
        adapter._require_akshare()
        return adapter.retry_with_backoff(func, max_retries=retries, base_delay=1.5)
    except Exception as exc:
        logger.warning("%s 获取失败: %s", name, exc)
        warnings.append(f"{name}获取失败：{type(exc).__name__}: {exc}")
        return pd.DataFrame()


def _yearly_item(
    frame: pd.DataFrame, label: str, unit: str
) -> dict[str, Any] | None:
    if frame is None or frame.empty or "今值" not in frame.columns:
        return None
    data = frame.copy()
    data["日期"] = pd.to_datetime(data.get("日期"), errors="coerce")
    data = data.dropna(subset=["日期", "今值"])
    if data.empty:
        return None
    row = data.iloc[-1]
    value = _num(row.get("今值"))
    previous = _num(row.get("前值"))
    change = round(value - previous, 4) if value is not None and previous is not None else None
    return {
        "key": label,
        "label": label,
        "value": value,
        "previous": previous,
        "change": change,
        "date": row["日期"].strftime("%Y-%m-%d"),
        "unit": unit,
        "status": "上升" if change and change > 0 else "下降" if change and change < 0 else "持平",
    }


def _first_attr(*names: str):
    """返回第一个存在的 AkShare 接口；兼容接口改名/移除。"""
    for name in names:
        func = getattr(adapter.ak, name, None)
        if func is not None:
            return func
    return None


def _to_month(value: Any) -> pd.Timestamp:
    """解析 AkShare 的“2026年10月份”格式。"""
    text = str(value or "")
    match = re.search(r"(\d{4})\D+(\d{1,2})", text)
    if not match:
        return pd.NaT
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return pd.NaT
    return pd.Timestamp(year=year, month=month, day=1)


def _month_item(
    frame: pd.DataFrame, label: str, column: str, unit: str
) -> dict[str, Any] | None:
    """新版 AkShare 月度接口：月份列 + 指定数值列，按月份升序取最新。"""
    if frame is None or frame.empty or column not in frame.columns:
        return None
    data = frame.copy()
    data["_date"] = data.get("月份").map(_to_month) if "月份" in data else pd.NaT
    if data["_date"].isna().all():
        data["_date"] = pd.to_datetime(data.get("月份"), errors="coerce")
    data = data.dropna(subset=["_date"]).sort_values("_date")
    if data.empty:
        return None
    row = data.iloc[-1]
    value = _num(row.get(column))
    if value is None:
        return None
    previous = _num(data.iloc[-2].get(column)) if len(data) > 1 else None
    change = (
        round(value - previous, 4)
        if value is not None and previous is not None
        else None
    )
    return {
        "key": label,
        "label": label,
        "value": value,
        "previous": previous,
        "change": change,
        "date": row["_date"].strftime("%Y-%m-%d"),
        "unit": unit,
        "status": "上升"
        if change and change > 0
        else "下降"
        if change and change < 0
        else "持平",
    }


def _historical_guard(curr_date: str | None) -> dict[str, Any] | None:
    """宏观/两融/轮动/ETF 只有实时快照，历史分析日必须显式拒绝。"""
    if curr_date and str(curr_date)[:10] != date.today().isoformat():
        message = "宏观/两融/行业轮动/ETF 仅提供实时数据，无法回溯历史日期"
        return {
            "success": False,
            "message": message,
            "warnings": [message],
        }
    return None


def get_macro_indicators(curr_date: str | None = None) -> dict[str, Any]:
    """PMI / CPI / PPI / M2 / LPR / Shibor / 中美国债收益率。"""
    guarded = _historical_guard(curr_date)
    if guarded:
        return guarded
    cached = _cache_get("macro", MACRO_TTL)
    if cached is not None:
        return cached
    warnings: list[str] = []
    items: list[dict[str, Any]] = []

    # AkShare 1.20.42+ 把 macro_china_*_yearly 系列陆续移除/改名，
    # 这里同时兼容新接口（月度表）与旧接口（今值/前值表）。
    monthly_specs = (
        (
            "制造业 PMI",
            "制造业-指数",
            "点",
            ("macro_china_pmi", "macro_china_pmi_yearly"),
        ),
        (
            "CPI 同比",
            "全国-同比增长",
            "%",
            ("macro_china_cpi", "macro_china_cpi_yearly"),
        ),
        (
            "PPI 同比",
            "当月同比增长",
            "%",
            ("macro_china_ppi", "macro_china_ppi_yearly"),
        ),
        (
            "M2 同比",
            "货币和准货币(M2)-同比增长",
            "%",
            ("macro_china_money_supply_em", "macro_china_m2_yearly"),
        ),
    )
    for label, column, unit, names in monthly_specs:
        func = _first_attr(*names)
        if func is None:
            warnings.append(f"{label}：当前 AkShare 版本没有可用接口")
            continue
        frame = _call(func, name=label, warnings=warnings)
        if frame is not None and not frame.empty and "月份" in frame.columns:
            item = _month_item(frame, label, column, unit)
        else:
            item = _yearly_item(frame, label, unit)
        if item:
            items.append(item)

    lpr_func = _first_attr("interest_rate_lpr_em", "macro_china_lpr")
    lpr = (
        _call(lpr_func, name="LPR", warnings=warnings)
        if lpr_func is not None
        else pd.DataFrame()
    )
    if lpr is not None and not lpr.empty:
        data = lpr.copy()
        data["TRADE_DATE"] = pd.to_datetime(
            data.get("TRADE_DATE", data.get("日期")), errors="coerce"
        )
        data = data.dropna(subset=["TRADE_DATE"]).sort_values("TRADE_DATE")
        row = data.iloc[-1]
        for columns, label in (
            (("1年期LPR", "LPR1Y"), "LPR 1 年"),
            (("5年期以上LPR", "LPR5Y"), "LPR 5 年"),
        ):
            value = None
            for column in columns:
                value = _num(row.get(column))
                if value is not None:
                    break
            if value is not None:
                items.append(
                    {
                        "key": label,
                        "label": label,
                        "value": value,
                        "previous": None,
                        "change": None,
                        "date": row["TRADE_DATE"].strftime("%Y-%m-%d"),
                        "unit": "%",
                        "status": "持平",
                    }
                )

    shibor_func = _first_attr("interest_rate_shibor_js", "macro_china_shibor_all")
    shibor = (
        _call(shibor_func, name="Shibor", warnings=warnings)
        if shibor_func is not None
        else pd.DataFrame()
    )
    if shibor is not None and not shibor.empty:
        data = shibor.copy()
        data["日期"] = pd.to_datetime(data.get("日期"), errors="coerce")
        data = data.dropna(subset=["日期"]).sort_values("日期")
        row = data.iloc[-1]
        for column, label in (("O/N-定价", "Shibor 隔夜"), ("1W-定价", "Shibor 1 周")):
            value = _num(row.get(column))
            if value is not None:
                items.append(
                    {
                        "key": label,
                        "label": label,
                        "value": value,
                        "previous": None,
                        "change": None,
                        "date": row["日期"].strftime("%Y-%m-%d"),
                        "unit": "%",
                        "status": "持平",
                    }
                )

    bond_func = _first_attr("bond_zh_us_yield_em", "bond_zh_us_rate")
    bonds = (
        _call(
            lambda: bond_func(start_date="20200101"),
            name="中美国债收益率",
            warnings=warnings,
        )
        if bond_func is not None
        else pd.DataFrame()
    )
    if bonds is not None and not bonds.empty:
        data = bonds.copy()
        data["日期"] = pd.to_datetime(data.get("日期"), errors="coerce")
        data = data.dropna(subset=["日期"]).sort_values("日期")
        row = data.iloc[-1]
        for column, label in (
            ("中国国债收益率10年", "中国 10Y 国债"),
            ("美国国债收益率10年", "美国 10Y 国债"),
        ):
            value = _num(row.get(column))
            if value is not None:
                items.append(
                    {
                        "key": label,
                        "label": label,
                        "value": value,
                        "previous": None,
                        "change": None,
                        "date": row["日期"].strftime("%Y-%m-%d"),
                        "unit": "%",
                        "status": "持平",
                    }
                )
        china = _num(row.get("中国国债收益率10年"))
        us = _num(row.get("美国国债收益率10年"))
        if china is not None and us is not None:
            items.append(
                {
                    "key": "中美 10Y 利差",
                    "label": "中美 10Y 利差",
                    "value": round(china - us, 4),
                    "previous": None,
                    "change": None,
                    "date": row["日期"].strftime("%Y-%m-%d"),
                    "unit": "%",
                    "status": "倒挂" if china < us else "正利差",
                }
            )

    summary_bits = []
    stale_items = []
    today = date.today()
    for item in items:
        try:
            item_date = date.fromisoformat(str(item.get("date") or "")[:10])
            item["stale"] = (today - item_date).days > 120
            if item["stale"]:
                stale_items.append(item["label"])
        except ValueError:
            item["stale"] = False
        if item["key"] == "制造业 PMI" and item["value"] is not None:
            summary_bits.append("PMI 扩张" if item["value"] >= 50 else "PMI 收缩")
        if item["key"] == "M2 同比" and item["value"] is not None:
            summary_bits.append(f"M2 {item['value']:.1f}%")
    if stale_items:
        warnings.append(
            "部分宏观指标数据较旧（超过 120 天）：" + "、".join(stale_items[:5])
        )
    summary = "；".join(summary_bits) or "宏观数据暂不完整"
    result = {
        "success": bool(items),
        "as_of": date.today().isoformat(),
        "items": items,
        "summary": summary,
        "warnings": warnings,
    }
    return _cache_set("macro", result)


def get_margin_snapshot(
    days: int = 60, curr_date: str | None = None
) -> dict[str, Any]:
    """沪深两融余额与近期变化。"""
    guarded = _historical_guard(curr_date)
    if guarded:
        return guarded
    key = f"margin:{days}"
    cached = _cache_get(key, MARGIN_TTL)
    if cached is not None:
        return cached
    warnings: list[str] = []
    frames = []
    margin_specs = (
        ("沪市", ("stock_margin_sse_js", "macro_china_market_margin_sh")),
        ("深市", ("stock_margin_szse_js", "macro_china_market_margin_sz")),
    )
    for label, names in margin_specs:
        func = _first_attr(*names)
        if func is None:
            warnings.append(f"{label}两融：当前 AkShare 版本没有可用接口")
            continue
        frame = _call(func, name=f"{label}两融", warnings=warnings)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["日期"] = pd.to_datetime(frame.get("日期"), errors="coerce")
        frame = frame.dropna(subset=["日期"])
        for column in ("融资余额", "融券余额", "融资融券余额"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frames.append(frame)
    if not frames:
        result = {
            "success": False,
            "message": "两融数据不可用",
            "items": [],
            "series": [],
            "warnings": warnings,
        }
        return _cache_set(key, result)

    combined = pd.concat(frames, ignore_index=True)
    grouped = (
        combined.groupby("日期", as_index=False)[["融资余额", "融券余额", "融资融券余额"]]
        .sum(min_count=1)
        .sort_values("日期")
    )
    latest = grouped.iloc[-1]
    def _change(periods: int) -> float | None:
        if len(grouped) <= periods:
            return None
        previous = grouped.iloc[-1 - periods]["融资融券余额"]
        current = latest["融资融券余额"]
        if pd.notna(previous) and pd.notna(current) and float(previous) > 0:
            return round((float(current) / float(previous) - 1) * 100, 2)
        return None

    series = [
        {
            "date": row["日期"].strftime("%Y-%m-%d"),
            "balance_yi": round(float(row["融资融券余额"]) / 1e8, 2)
            if pd.notna(row["融资融券余额"])
            else None,
            "financing_yi": round(float(row["融资余额"]) / 1e8, 2)
            if pd.notna(row["融资余额"])
            else None,
        }
        for _, row in grouped.tail(max(5, min(int(days), 240))).iterrows()
    ]
    balance = float(latest["融资融券余额"]) if pd.notna(latest["融资融券余额"]) else 0.0
    change_5d = _change(5)
    change_20d = _change(20)
    summary = (
        f"全市场两融余额 {balance / 1e12:.2f} 万亿元，"
        f"较 5 日前 {change_5d:+.2f}%，较 20 日前 {change_20d:+.2f}%。"
        if change_5d is not None and change_20d is not None
        else f"全市场两融余额 {balance / 1e12:.2f} 万亿元。"
    )
    result = {
        "success": True,
        "as_of": latest["日期"].strftime("%Y-%m-%d"),
        "balance_yi": round(balance / 1e8, 2),
        "balance_wanyi": round(balance / 1e12, 2),
        "change_5d": change_5d,
        "change_20d": change_20d,
        "series": series,
        "summary": summary,
        "warnings": warnings,
    }
    return _cache_set(key, result)


def _rotation_frame(indicator: str, warnings: list[str]) -> pd.DataFrame:
    func = _first_attr(
        "stock_fund_flow_sector_rank_em", "stock_sector_fund_flow_rank"
    )
    if func is None:
        warnings.append("行业资金流：当前 AkShare 版本没有可用接口")
        return pd.DataFrame()
    frame = _call(
        lambda: func(indicator=indicator, sector_type="行业资金流"),
        name=f"行业资金流（{indicator}）",
        warnings=warnings,
    )
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    mapping = {
        "名称": "name",
        f"{indicator}涨跌幅": "change_pct",
        f"{indicator}主力净流入-净额": "main_net",
        f"{indicator}主力净流入-净占比": "main_net_ratio",
        f"{indicator}主力净流入最大股": "top_stock",
    }
    frame = frame.rename(columns={k: v for k, v in mapping.items() if k in frame.columns})
    keep = [column for column in mapping.values() if column in frame.columns]
    frame = frame[keep].copy()
    for column in ("change_pct", "main_net", "main_net_ratio"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def get_industry_rotation(
    limit: int = 20, curr_date: str | None = None
) -> dict[str, Any]:
    """行业今日 / 5 日 / 10 日涨跌幅、主力净流入与轮动分。"""
    guarded = _historical_guard(curr_date)
    if guarded:
        return guarded
    limit = max(1, min(int(limit), 50))
    key = f"rotation:{limit}"
    cached = _cache_get(key, ROTATION_TTL)
    if cached is not None:
        return cached
    warnings: list[str] = []
    frames = {
        "today": _rotation_frame("今日", warnings),
        "5d": _rotation_frame("5日", warnings),
        "10d": _rotation_frame("10日", warnings),
    }
    base = frames["5d"] if not frames["5d"].empty else frames["today"]
    if base.empty:
        result = {
            "success": False,
            "message": "行业轮动数据不可用",
            "items": [],
            "warnings": warnings,
        }
        return _cache_set(key, result)

    merged = base.rename(
        columns={
            "change_pct": "change_5d",
            "main_net": "main_net_5d",
            "main_net_ratio": "main_net_ratio_5d",
            "top_stock": "top_stock_5d",
        }
    )
    for suffix, frame in (("today", frames["today"]), ("10d", frames["10d"])):
        if frame.empty:
            continue
        renamed = frame.rename(
            columns={
                "change_pct": f"change_{suffix}",
                "main_net": f"main_net_{suffix}",
                "main_net_ratio": f"main_net_ratio_{suffix}",
                "top_stock": f"top_stock_{suffix}",
            }
        )
        merged = merged.merge(
            renamed, on="name", how="outer", suffixes=("", f"_{suffix}")
        )
    merged = merged.drop_duplicates(subset=["name"]).reset_index(drop=True)
    if frames["5d"].empty:
        # 5 日接口失败时不要把“今日”伪装成“5日”。
        merged["change_5d"] = None
        merged["main_net_5d"] = None
        merged["main_net_ratio_5d"] = None
        warnings.append("5日行业资金流不可用，轮动分已降级")
    if frames["10d"].empty:
        merged["change_10d"] = None
        warnings.append("10日行业资金流不可用")

    def _rank(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series, errors="coerce").rank(pct=True) * 100

    score = pd.Series(0.0, index=merged.index)
    weight = pd.Series(0.0, index=merged.index)
    for column, w in (
        ("change_5d", 0.45),
        ("main_net_ratio_5d", 0.35),
        ("change_today", 0.20),
    ):
        if column in merged.columns:
            values = _rank(merged[column])
            score += values.fillna(0) * w
            weight += values.notna().astype(float) * w
    merged["rotation_score"] = (score / weight.replace(0, pd.NA)).fillna(0).round(2)
    merged = merged.sort_values("rotation_score", ascending=False)
    limit = max(1, min(int(limit), 50))

    def _items(frame: pd.DataFrame) -> list[dict[str, Any]]:
        items = []
        for _, row in frame.iterrows():
            items.append(
                {
                    "name": row.get("name"),
                    "change_today": _num(row.get("change_today")),
                    "change_5d": _num(row.get("change_5d")),
                    "change_10d": _num(row.get("change_10d")),
                    "main_net_5d_yi": round(float(row.get("main_net_5d") or 0) / 1e8, 2)
                    if pd.notna(row.get("main_net_5d"))
                    else None,
                    "main_net_ratio_5d": _num(row.get("main_net_ratio_5d")),
                    "top_stock": _text(row.get("top_stock_5d"))
                    or _text(row.get("top_stock")),
                    "rotation_score": _num(row.get("rotation_score")),
                }
            )
        return items

    result = {
        "success": True,
        "as_of": date.today().isoformat(),
        "items": _items(merged.head(limit)),
        "bottom": _items(merged.tail(min(10, len(merged))).iloc[::-1]),
        "summary": (
            f"行业轮动：领涨 {merged.iloc[0]['name']}"
            f"（5日 {_num(merged.iloc[0].get('change_5d')) or 0:+.2f}%），"
            f"领跌 {merged.iloc[-1]['name']}"
            f"（5日 {_num(merged.iloc[-1].get('change_5d')) or 0:+.2f}%）。"
        ),
        "warnings": warnings,
    }
    return _cache_set(key, result)


def get_etf_flows(
    limit: int = 20, curr_date: str | None = None
) -> dict[str, Any]:
    """场内 ETF 主力净流入/流出。"""
    guarded = _historical_guard(curr_date)
    if guarded:
        return guarded
    limit = max(1, min(int(limit), 100))
    key = f"etf:{limit}"
    cached = _cache_get(key, ETF_TTL)
    if cached is not None:
        return cached
    warnings: list[str] = []
    frame = _call(
        adapter.ak.fund_etf_spot_em, name="ETF 实时行情", warnings=warnings
    )
    if frame is None or frame.empty:
        result = {
            "success": False,
            "message": "ETF 行情不可用",
            "items": [],
            "warnings": warnings,
        }
        return _cache_set(key, result)
    frame = frame.copy()
    mapping = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "成交额": "amount",
        "换手率": "turnover",
        "主力净流入-净额": "main_net",
        "主力净流入-净占比": "main_net_ratio",
        "最新份额": "shares",
        "总市值": "market_cap",
    }
    frame = frame.rename(columns={k: v for k, v in mapping.items() if k in frame.columns})
    for column in ("price", "change_pct", "amount", "turnover", "main_net", "main_net_ratio", "market_cap"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "main_net" not in frame.columns:
        result = {
            "success": False,
            "message": "ETF 行情缺少主力净流入字段",
            "items": [],
            "warnings": warnings,
        }
        return _cache_set(key, result)
    frame = frame.sort_values("main_net", ascending=False)
    limit = max(1, min(int(limit), 100))

    def _items(sub: pd.DataFrame) -> list[dict[str, Any]]:
        items = []
        for _, row in sub.iterrows():
            items.append(
                {
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "price": _num(row.get("price")),
                    "change_pct": _num(row.get("change_pct")),
                    "amount_yi": round(float(row.get("amount")) / 1e8, 2)
                    if _num(row.get("amount")) is not None
                    else None,
                    "main_net_yi": round(float(row.get("main_net")) / 1e8, 2)
                    if _num(row.get("main_net")) is not None
                    else None,
                    "main_net_ratio": _num(row.get("main_net_ratio")),
                    "market_cap_yi": round(float(row.get("market_cap")) / 1e8, 2)
                    if _num(row.get("market_cap")) is not None
                    else None,
                }
            )
        return items

    result = {
        "success": True,
        "as_of": date.today().isoformat(),
        "items": _items(frame.head(limit)),
        "outflow": _items(frame.tail(min(10, len(frame))).iloc[::-1]),
        "summary": (
            f"ETF 主力净流入前列：{_text(frame.iloc[0].get('name'))}"
            f"（{(_num(frame.iloc[0].get('main_net')) or 0) / 1e8:+.2f} 亿）。"
        ),
        "warnings": warnings,
    }
    return _cache_set(key, result)


def get_macro_dashboard(curr_date: str | None = None) -> dict[str, Any]:
    """宏观 + 两融 + 行业轮动 + ETF 合并面板。"""
    macro = get_macro_indicators(curr_date)
    margin = get_margin_snapshot(curr_date=curr_date)
    rotation = get_industry_rotation(curr_date=curr_date)
    etf = get_etf_flows(curr_date=curr_date)
    warnings = (
        macro.get("warnings", [])
        + margin.get("warnings", [])
        + rotation.get("warnings", [])
        + etf.get("warnings", [])
    )
    summary = "；".join(
        item
        for item in (
            macro.get("summary", ""),
            margin.get("summary", ""),
            rotation.get("summary", ""),
            etf.get("summary", ""),
        )
        if item
    )
    return {
        "success": bool(
            macro.get("success") or margin.get("success") or rotation.get("success")
        ),
        "as_of": date.today().isoformat(),
        "macro": macro,
        "margin": margin,
        "rotation": rotation,
        "etf": etf,
        "summary": summary,
        "warnings": warnings,
    }


__all__ = [
    "clear_macro_cache",
    "get_macro_indicators",
    "get_margin_snapshot",
    "get_industry_rotation",
    "get_etf_flows",
    "get_macro_dashboard",
]
