"""公告 / 研报一致预期 / 股东变化。

这是 A 股研究里"最接近事件本身"的一层数据：

- **公告**：减持、回购、中标、诉讼、监管、业绩预告……往往在财报前就决定股价；
- **研报**：机构评级、盈利预测、目标逻辑，构成"一致预期"的代理；
- **股东变化**：股东户数增减（筹码集中/分散）、前十大股东、董监高增减持。

数据源全部来自 AkShare（东方财富 / 巨潮），本模块只做：
1. 抓取与缓存；
2. 列名归一；
3. 标题/公告类型的关键词分类（利好/利空/中性 + 影响级别）；
4. 面向 LLM 与 WebUI 的中文摘要。

历史分析必须传 ``curr_date``：公告、研报、股东户数都按**披露日**过滤，
避免把分析日之后才发生的信息喂给模型。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from . import akshare_adapter as adapter
from .ashare_rules import (
    exchange_of,
    is_a_share_code,
    normalize_code,
    report_available_date,
)

logger = logging.getLogger(__name__)

ANNOUNCEMENT_TTL = 3600.0
RESEARCH_TTL = 6 * 3600.0
SHAREHOLDER_TTL = 6 * 3600.0
_CACHE: dict[str, tuple[float, Any]] = {}

#: 关键词 → (分类, 影响方向, 权重)
_KEYWORDS: list[tuple[str, str, str, int]] = [
    ("增持", "股东增减持", "positive", 3),
    ("回购", "回购", "positive", 3),
    ("中标", "重大合同", "positive", 2),
    ("预增", "业绩", "positive", 3),
    ("扭亏", "业绩", "positive", 3),
    ("分红", "分红", "positive", 2),
    ("股权激励", "股权激励", "positive", 2),
    ("重大合同", "重大合同", "positive", 2),
    ("战略合作", "战略合作", "positive", 1),
    ("获批", "审批", "positive", 2),
    ("提价", "经营", "positive", 2),
    ("业绩增长", "业绩", "positive", 2),
    ("减持", "股东增减持", "negative", 3),
    ("质押", "股权质押", "negative", 2),
    ("冻结", "风险", "negative", 3),
    ("诉讼", "诉讼", "negative", 2),
    ("处罚", "监管", "negative", 3),
    ("立案", "监管", "negative", 3),
    ("警示", "监管", "negative", 2),
    ("问询", "监管", "negative", 1),
    ("亏损", "业绩", "negative", 2),
    ("预减", "业绩", "negative", 3),
    ("首亏", "业绩", "negative", 3),
    ("退市", "风险", "negative", 4),
    ("风险提示", "风险", "negative", 2),
    ("终止", "经营", "negative", 2),
    ("违约", "风险", "negative", 3),
    ("商誉减值", "业绩", "negative", 3),
    ("解禁", "解禁", "negative", 2),
    ("监管", "监管", "negative", 2),
]

_IMPACT_LABELS = {"positive": "利好", "negative": "利空", "neutral": "中性"}


def clear_disclosure_cache() -> None:
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
    return None if pd.isna(number) else number


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _cutoff(curr_date: str | None) -> date:
    if curr_date:
        try:
            return datetime.strptime(str(curr_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def _invalid(ticker: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "ticker": ticker,
        "message": message,
        "items": [],
        "summary": message,
        "warnings": [message],
    }


def classify_announcement(title: str, announcement_type: str = "") -> dict[str, Any]:
    """按标题/公告类型判断利好利空、影响级别与主题分类。"""
    text = f"{announcement_type} {title}"
    hits: list[tuple[str, str, int]] = []
    for keyword, category, impact, weight in _KEYWORDS:
        if keyword in text:
            hits.append((category, impact, weight))
    positive = sum(weight for _, impact, weight in hits if impact == "positive")
    negative = sum(weight for _, impact, weight in hits if impact == "negative")
    if positive > negative:
        impact = "positive"
    elif negative > positive:
        impact = "negative"
    else:
        impact = "neutral"
    score = max(positive, negative)
    level = "high" if score >= 3 else "medium" if score >= 2 else "low"
    categories = sorted({category for category, _, _ in hits}) or ["其他"]
    return {
        "impact": impact,
        "impact_label": _IMPACT_LABELS[impact],
        "level": level,
        "categories": categories,
        "score": score,
    }


def get_announcements(
    ticker: str,
    *,
    days: int = 30,
    limit: int = 50,
    curr_date: str | None = None,
) -> dict[str, Any]:
    """获取个股公告并按披露日与关键词分类。"""
    raw = str(ticker or "").strip()
    code = normalize_code(raw)
    if not is_a_share_code(code):
        return _invalid(raw, f"{raw} 不是有效 A 股代码")
    cutoff = _cutoff(curr_date)
    days = max(1, min(int(days), 180))
    begin = cutoff - timedelta(days=days)
    key = f"announcements:{code}:{begin}:{cutoff}:{limit}"
    cached = _cache_get(key, ANNOUNCEMENT_TTL)
    if cached is not None:
        return cached

    try:
        adapter._require_akshare()
        from akshare.stock.research.stock_notice import _stock_notice_report

        frame = adapter.retry_with_backoff(
            lambda: _stock_notice_report(
                security=code,
                symbol="全部",
                begin_date=begin.isoformat(),
                end_date=cutoff.isoformat(),
            ),
            max_retries=2,
            base_delay=1.5,
        )
    except Exception as exc:
        logger.warning("公告获取失败 %s: %s", code, exc)
        result = _invalid(code, f"公告获取失败：{type(exc).__name__}: {exc}")
        result["as_of"] = cutoff.isoformat()
        return _cache_set(key, result)

    items: list[dict[str, Any]] = []
    if frame is not None and not frame.empty:
        for _, row in frame.iterrows():
            announcement_date = _date_text(row.get("公告日期"))
            if announcement_date and announcement_date > cutoff.isoformat():
                continue
            title = _text(row.get("公告标题"))
            announcement_type = _text(row.get("公告类型"))
            classified = classify_announcement(title, announcement_type)
            items.append(
                {
                    "ticker": code,
                    "name": _text(row.get("名称")),
                    "date": announcement_date,
                    "title": title,
                    "type": announcement_type,
                    "url": _text(row.get("网址")),
                    **classified,
                }
            )
    items.sort(key=lambda item: item["date"], reverse=True)
    items = items[: max(1, min(int(limit), 200))]
    high = [item for item in items if item["level"] == "high"]
    positive = [item for item in items if item["impact"] == "positive"]
    negative = [item for item in items if item["impact"] == "negative"]
    summary = (
        f"近 {days} 天共 {len(items)} 条公告："
        f"利好 {len(positive)} 条、利空 {len(negative)} 条，"
        f"高影响 {len(high)} 条。"
    )
    result = {
        "success": True,
        "ticker": code,
        "as_of": cutoff.isoformat(),
        "days": days,
        "items": items,
        "summary": summary,
        "counts": {
            "total": len(items),
            "positive": len(positive),
            "negative": len(negative),
            "neutral": len(items) - len(positive) - len(negative),
            "high": len(high),
        },
        "high_impact": high[:5],
        "warnings": [],
    }
    return _cache_set(key, result)


def _rating_label(value: str) -> str:
    text = str(value or "")
    if "买入" in text:
        return "买入"
    if "增持" in text or "推荐" in text:
        return "增持"
    if "中性" in text or "持有" in text:
        return "中性"
    if "减持" in text:
        return "减持"
    if "卖出" in text:
        return "卖出"
    return text or "未评级"


def _forecast_columns(frame: pd.DataFrame) -> dict[str, dict[str, str]]:
    """把 ``2026-盈利预测-收益`` 这类动态列按年份归类。"""
    result: dict[str, dict[str, str]] = {}
    for column in frame.columns:
        text = str(column)
        if "盈利预测-收益" in text or "盈利预测-市盈率" in text:
            year = text.split("-")[0]
            kind = "eps" if "收益" in text else "pe"
            result.setdefault(year, {})[kind] = column
    return result


def get_research_consensus(
    ticker: str,
    *,
    days: int = 180,
    limit: int = 20,
    curr_date: str | None = None,
) -> dict[str, Any]:
    """研报评级分布、盈利预测与最近研报列表。"""
    raw = str(ticker or "").strip()
    code = normalize_code(raw)
    if not is_a_share_code(code):
        return _invalid(raw, f"{raw} 不是有效 A 股代码")
    cutoff = _cutoff(curr_date)
    days = max(1, min(int(days), 730))
    key = f"research:{code}:{cutoff}:{days}:{limit}"
    cached = _cache_get(key, RESEARCH_TTL)
    if cached is not None:
        return cached

    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_research_report_em(symbol=code),
            max_retries=2,
            base_delay=2.0,
        )
    except Exception as exc:
        logger.warning("研报获取失败 %s: %s", code, exc)
        result = _invalid(code, f"研报获取失败：{type(exc).__name__}: {exc}")
        result["as_of"] = cutoff.isoformat()
        return _cache_set(key, result)

    if frame is None or frame.empty:
        result = _invalid(code, "暂无研报数据")
        result["as_of"] = cutoff.isoformat()
        return _cache_set(key, result)

    if "日期" not in frame.columns:
        result = _invalid(code, "研报数据缺少日期列")
        result["as_of"] = cutoff.isoformat()
        return _cache_set(key, result)
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    frame = frame[frame["date"].dt.date <= cutoff]
    recent = frame[frame["date"].dt.date >= cutoff - timedelta(days=days)]
    if recent.empty:
        recent = frame.head(limit)
    recent = recent.sort_values("date", ascending=False).head(limit)

    ratings: dict[str, int] = {}
    for value in recent.get("东财评级", pd.Series(dtype=str)):
        label = _rating_label(_text(value))
        if not label or label == "未评级":
            continue
        ratings[label] = ratings.get(label, 0) + 1
    total = sum(ratings.values())
    buy = ratings.get("买入", 0) + ratings.get("增持", 0)
    buy_ratio = buy / total if total else None
    if buy_ratio is None:
        consensus = "无评级数据"
    elif buy_ratio >= 0.7:
        consensus = "偏多"
    elif buy_ratio >= 0.5:
        consensus = "中性偏多"
    elif buy_ratio >= 0.3:
        consensus = "中性"
    else:
        consensus = "偏空"

    forecasts: dict[str, dict[str, Any]] = {}
    for year, columns in _forecast_columns(frame).items():
        eps_series = pd.to_numeric(frame[columns["eps"]], errors="coerce") if "eps" in columns else pd.Series(dtype=float)
        pe_series = pd.to_numeric(frame[columns["pe"]], errors="coerce") if "pe" in columns else pd.Series(dtype=float)
        forecasts[year] = {
            "eps": round(float(eps_series.mean()), 2) if eps_series.notna().any() else None,
            "pe": round(float(pe_series.mean()), 2) if pe_series.notna().any() else None,
            "count": int(eps_series.notna().sum()),
        }

    reports: list[dict[str, Any]] = []
    for _, row in recent.iterrows():
        reports.append(
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "title": _text(row.get("报告名称")),
                "institution": _text(row.get("机构")),
                "rating": _rating_label(_text(row.get("东财评级"))),
                "industry": _text(row.get("行业")),
                "pdf": _text(row.get("报告PDF链接")),
            }
        )

    industry = next(
        (_text(value) for value in recent.get("行业", []) if _text(value)), ""
    )
    year_keys = sorted(forecasts)
    forecast_text = "，".join(
        f"{year} EPS {item['eps']}" for year, item in forecasts.items() if item["eps"] is not None
    )
    ratio_text = f"{buy_ratio:.0%}" if buy_ratio is not None else "--"
    summary = (
        f"近 {days} 天 {len(reports)} 篇研报，评级分布 "
        f"{'、'.join(f'{key}{value}篇' for key, value in ratings.items()) or '暂无'}，"
        f"综合判断 {consensus}（买入+增持占比 {ratio_text}）。"
    )
    if forecast_text:
        summary += f" 一致预期：{forecast_text}。"

    result = {
        "success": True,
        "ticker": code,
        "as_of": cutoff.isoformat(),
        "days": days,
        "industry": industry,
        "ratings": ratings,
        "buy_ratio": round(buy_ratio, 4) if buy_ratio is not None else None,
        "consensus": consensus,
        "forecasts": forecasts,
        "years": year_keys,
        "reports": reports,
        "summary": summary,
        "warnings": [],
    }
    return _cache_set(key, result)


def _report_periods(today: date, count: int = 4) -> list[str]:
    periods: list[str] = []
    year, month = today.year, today.month
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
        period = f"{y:04d}{m:02d}{d:02d}"
        # 只有法定披露截止日 <= 分析日，才允许使用该报告期数据。
        available = report_available_date(period)
        if available and available <= today.isoformat():
            periods.append(period)
        if m == 3:
            y, m, d = y - 1, 12, 31
        elif m == 6:
            m, d = 3, 31
        elif m == 9:
            m, d = 6, 30
        else:
            m, d = 9, 30
    return periods


def get_shareholder_changes(
    ticker: str,
    *,
    limit: int = 8,
    curr_date: str | None = None,
) -> dict[str, Any]:
    """股东户数变化 + 最新前十大股东。"""
    raw = str(ticker or "").strip()
    code = normalize_code(raw)
    if not is_a_share_code(code):
        return _invalid(raw, f"{raw} 不是有效 A 股代码")
    cutoff = _cutoff(curr_date)
    key = f"shareholders:{code}:{cutoff}:{limit}"
    cached = _cache_get(key, SHAREHOLDER_TTL)
    if cached is not None:
        return cached

    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_zh_a_gdhs_detail_em(symbol=code),
            max_retries=2,
            base_delay=2.0,
        )
        if frame is not None and not frame.empty:
            frame = frame.copy()
            frame["截止日"] = pd.to_datetime(
                frame.get("股东户数统计截止日"), errors="coerce"
            )
            frame["公告日"] = pd.to_datetime(
                frame.get("股东户数公告日期"), errors="coerce"
            )
            frame = frame.dropna(subset=["截止日"])
            if frame["公告日"].notna().any():
                frame = frame[
                    frame["公告日"].dt.date.fillna(cutoff) <= cutoff
                ]
            frame = frame[frame["截止日"].dt.date <= cutoff]
            frame = frame.sort_values("截止日", ascending=False).head(limit)
            for _, row in frame.iterrows():
                items.append(
                    {
                        "date": row["截止日"].strftime("%Y-%m-%d"),
                        "holder_count": _num(row.get("股东户数-本次")),
                        "previous_count": _num(row.get("股东户数-上次")),
                        "change": _num(row.get("股东户数-增减")),
                        "change_pct": _num(row.get("股东户数-增减比例")),
                        "avg_value": _num(row.get("户均持股市值")),
                        "market_cap": _num(row.get("总市值")),
                    }
                )
    except Exception as exc:
        warnings.append(f"股东户数获取失败：{type(exc).__name__}: {exc}")

    latest = items[0] if items else None
    trend = "未知"
    if len(items) >= 2 and latest:
        change = latest.get("change_pct")
        if change is not None:
            if change <= -5:
                trend = "筹码集中"
            elif change >= 5:
                trend = "筹码分散"
            else:
                trend = "基本稳定"

    top10: list[dict[str, Any]] = []
    try:
        exchange = exchange_of(code)
        if exchange:
            for period in _report_periods(cutoff):
                try:
                    top_frame = adapter.retry_with_backoff(
                        lambda p=period: adapter.ak.stock_gdfx_top_10_em(
                            symbol=f"{exchange}{code}", date=p
                        ),
                        max_retries=1,
                        base_delay=1.0,
                    )
                except Exception:
                    continue
                if top_frame is None or top_frame.empty:
                    continue
                for _, row in top_frame.iterrows():
                    top10.append(
                        {
                            "rank": _text(row.get("名次")),
                            "name": _text(row.get("股东名称")),
                            "share_type": _text(row.get("股份类型")),
                            "shares": _num(row.get("持股数")),
                            "ratio": _num(row.get("占总股本持股比例")),
                            "change": _text(row.get("增减")),
                        }
                    )
                break
    except Exception as exc:
        warnings.append(f"前十大股东获取失败：{type(exc).__name__}: {exc}")

    summary = "暂无股东户数数据。"
    if latest:
        summary = (
            f"最新股东户数 {latest.get('holder_count')} 户"
            f"（较上期 {latest.get('change_pct')}%，{trend}）。"
        )
        if top10:
            concentration = sum(
                float(item["ratio"] or 0) for item in top10 if item.get("ratio")
            )
            summary += f" 前十大股东合计持股 {concentration:.2f}%。"
    result = {
        "success": bool(items or top10),
        "ticker": code,
        "as_of": cutoff.isoformat(),
        "items": items,
        "top10": top10,
        "trend": trend,
        "summary": summary,
        "warnings": warnings,
    }
    return _cache_set(key, result)


def get_disclosure_summary(
    ticker: str,
    *,
    days: int = 30,
    curr_date: str | None = None,
) -> dict[str, Any]:
    """公告 + 研报 + 股东变化的合并摘要，供 WebUI 一次加载。"""
    announcements = get_announcements(
        ticker, days=days, curr_date=curr_date
    )
    research = get_research_consensus(ticker, curr_date=curr_date)
    shareholders = get_shareholder_changes(ticker, curr_date=curr_date)
    warnings = (
        announcements.get("warnings", [])
        + research.get("warnings", [])
        + shareholders.get("warnings", [])
    )
    summary = "；".join(
        item
        for item in (
            announcements.get("summary", ""),
            research.get("summary", ""),
            shareholders.get("summary", ""),
        )
        if item
    )
    return {
        "success": bool(
            announcements.get("success")
            or research.get("success")
            or shareholders.get("success")
        ),
        "ticker": normalize_code(str(ticker or "")) or str(ticker or ""),
        "as_of": _cutoff(curr_date).isoformat(),
        "days": days,
        "announcements": announcements,
        "research": research,
        "shareholders": shareholders,
        "summary": summary,
        "warnings": warnings,
    }


__all__ = [
    "clear_disclosure_cache",
    "classify_announcement",
    "get_announcements",
    "get_research_consensus",
    "get_shareholder_changes",
    "get_disclosure_summary",
]
