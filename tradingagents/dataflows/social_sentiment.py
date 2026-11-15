"""真实社媒舆情：东方财富人气榜 / 概念热度 / 微博情绪 / 雪球热度（可选）。

数据边界
--------
社媒数据是**实时**的，公开接口不提供历史回溯。因此：

- 分析日不是今天时，本模块直接返回"不可用"，避免历史回测使用未来舆情；
- 需要历史趋势时，用每日快照（``storage/social_store.py``）自行积累；
- 雪球接口需要 cookie，失败时只给 warning，不影响东方财富/微博部分。
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime
from typing import Any

import pandas as pd

from tradingagents.storage import (
    save_social_snapshot,
    social_baseline,
)

from . import akshare_adapter as adapter
from .ashare_rules import exchange_of, is_a_share_code, normalize_code

logger = logging.getLogger(__name__)

GUBA_TTL = 300.0
KEYWORD_TTL = 3600.0
WEIBO_TTL = 600.0
XUEQIU_TTL = 600.0
_CACHE: dict[str, tuple[float, Any]] = {}

_POSITIVE_WORDS = (
    "利好",
    "涨停",
    "突破",
    "龙头",
    "加仓",
    "看多",
    "牛市",
    "回购",
    "超预期",
    "增长",
    "强势",
    "新高",
    "爆发",
    "中标",
)
_NEGATIVE_WORDS = (
    "利空",
    "跌停",
    "减持",
    "爆雷",
    "退市",
    "亏损",
    "看空",
    "熊市",
    "风险",
    "下跌",
    "新低",
    "处罚",
    "质押",
    "立案",
)


def clear_social_cache() -> None:
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


def _guba_rank() -> pd.DataFrame:
    cached = _cache_get("guba_rank", GUBA_TTL)
    if cached is not None:
        return cached
    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            adapter.ak.stock_hot_rank_em, max_retries=2, base_delay=1.0
        )
        if frame is None:
            frame = pd.DataFrame()
    except Exception as exc:
        logger.warning("东财人气榜获取失败: %s", exc)
        frame = pd.DataFrame()
        frame.attrs["warning"] = str(exc)
    if not frame.empty and "代码" in frame.columns:
        frame = frame.copy()
        frame["code"] = frame["代码"].map(
            lambda value: normalize_code(str(value or ""))
        )
    return _cache_set("guba_rank", frame)


def _guba_keywords(symbol: str) -> pd.DataFrame:
    key = f"keywords:{symbol}"
    cached = _cache_get(key, KEYWORD_TTL)
    if cached is not None:
        return cached
    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_hot_keyword_em(symbol=symbol),
            max_retries=2,
            base_delay=1.0,
        )
        if frame is None:
            frame = pd.DataFrame()
    except Exception as exc:
        logger.warning("概念热度获取失败 %s: %s", symbol, exc)
        frame = pd.DataFrame()
        frame.attrs["warning"] = str(exc)
    return _cache_set(key, frame)


def _weibo_report() -> pd.DataFrame:
    cached = _cache_get("weibo", WEIBO_TTL)
    if cached is not None:
        return cached
    try:
        adapter._require_akshare()
        frame = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_js_weibo_report(time_period="CNHOUR12"),
            max_retries=2,
            base_delay=1.0,
        )
        if frame is None:
            frame = pd.DataFrame()
    except Exception as exc:
        logger.warning("微博舆情获取失败: %s", exc)
        frame = pd.DataFrame()
        frame.attrs["warning"] = str(exc)
    return _cache_set("weibo", frame)


def _xueqiu_hot() -> dict[str, dict[str, Any]]:
    """雪球关注/讨论榜；接口需要 cookie，失败时返回空字典。"""
    cached = _cache_get("xueqiu", XUEQIU_TTL)
    if cached is not None:
        return cached
    result: dict[str, dict[str, Any]] = {}
    try:
        adapter._require_akshare()
        follow = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_hot_follow_xq(symbol="最热门"),
            max_retries=1,
            base_delay=1.0,
        )
        tweet = adapter.retry_with_backoff(
            lambda: adapter.ak.stock_hot_tweet_xq(symbol="最热门"),
            max_retries=1,
            base_delay=1.0,
        )
        if follow is not None and not follow.empty:
            for _, row in follow.iterrows():
                code = normalize_code(str(row.get("股票代码") or ""))
                if not code:
                    continue
                result.setdefault(code, {})["follow"] = _num(row.get("关注"))
        if tweet is not None and not tweet.empty:
            for _, row in tweet.iterrows():
                code = normalize_code(str(row.get("股票代码") or ""))
                if not code:
                    continue
                result.setdefault(code, {})["tweet"] = _num(row.get("关注"))
    except Exception as exc:
        logger.info("雪球热度不可用（通常需要 cookie）: %s", exc)
        result = {}
    return _cache_set("xueqiu", result)


def _keyword_sentiment(keywords: list[dict[str, Any]]) -> float:
    positive = sum(
        1
        for item in keywords
        if any(word in str(item.get("name") or "") for word in _POSITIVE_WORDS)
    )
    negative = sum(
        1
        for item in keywords
        if any(word in str(item.get("name") or "") for word in _NEGATIVE_WORDS)
    )
    total = positive + negative
    if total == 0:
        return 50.0
    return round(50 + (positive - negative) / total * 40, 2)


def _attention_label(score: float) -> str:
    if score >= 80:
        return "过热"
    if score >= 65:
        return "偏热"
    if score >= 40:
        return "正常"
    if score >= 20:
        return "偏冷"
    return "冰点"


def _weibo_score(rate: float | None) -> float | None:
    """金十微博 rate 实际范围约 -5~+5，统一映射到 0-100。"""
    if rate is None:
        return None
    return max(0.0, min(100.0, (float(rate) + 5) / 10 * 100))


def _sentiment_label(score: float) -> str:
    if score >= 70:
        return "乐观"
    if score >= 58:
        return "偏多"
    if score >= 42:
        return "中性"
    if score >= 30:
        return "偏空"
    return "悲观"


def get_social_sentiment(
    ticker: str,
    *,
    name: str = "",
    curr_date: str | None = None,
    include_xueqiu: bool = False,
    save: bool = False,
) -> dict[str, Any]:
    """获取个股社媒热度与情绪。

    注意：社媒接口只提供实时数据，``curr_date`` 不是今天时返回不可用，
    避免历史分析使用未来信息。
    """
    raw = str(ticker or "").strip()
    code = normalize_code(raw)
    if not is_a_share_code(code):
        return {
            "success": False,
            "ticker": raw,
            "message": f"{raw} 不是有效 A 股代码",
            "warnings": [f"{raw} 不是有效 A 股代码"],
        }
    today = date.today().isoformat()
    if curr_date and str(curr_date)[:10] != today:
        return {
            "success": False,
            "ticker": code,
            "as_of": str(curr_date)[:10],
            "message": "社媒热度数据仅支持实时快照，无法回溯历史日期",
            "warnings": ["历史分析不使用当前社媒数据，避免未来函数"],
        }

    warnings: list[str] = []
    guba = _guba_rank()
    if guba.attrs.get("warning"):
        warnings.append(f"人气榜获取失败：{guba.attrs['warning']}")
    guba_row = None
    if not guba.empty and "code" in guba.columns:
        matched = guba[guba["code"] == code]
        if not matched.empty:
            guba_row = matched.iloc[0]
    guba_rank_value = (
        _num(guba_row.get("当前排名")) if guba_row is not None else None
    )
    guba_rank = int(guba_rank_value) if guba_rank_value is not None else None
    total_ranked = len(guba) if not guba.empty else 0
    rank_score = (
        max(
            0.0,
            min(100.0, 100.0 - (guba_rank - 1) / max(1, total_ranked) * 100),
        )
        if guba_rank and total_ranked
        else 0.0
    )

    stock_name = name or (
        _text(guba_row.get("股票名称")) if guba_row is not None else ""
    )
    if not stock_name:
        try:
            from .realtime import get_realtime_quote

            quote = get_realtime_quote(code) or {}
            stock_name = _text(quote.get("name"))
        except Exception:
            stock_name = ""

    exchange = exchange_of(code)
    keyword_frame = (
        _guba_keywords(f"{exchange.upper()}{code}") if exchange else pd.DataFrame()
    )
    keywords: list[dict[str, Any]] = []
    if not keyword_frame.empty:
        keyword_frame = keyword_frame.copy()
        keyword_frame["热度"] = pd.to_numeric(
            keyword_frame.get("热度"), errors="coerce"
        )
        keyword_frame = keyword_frame.sort_values("热度", ascending=False).head(8)
        for _, row in keyword_frame.iterrows():
            keywords.append(
                {
                    "name": _text(row.get("概念名称")),
                    "heat": _num(row.get("热度")),
                }
            )
    heat_sum = sum(float(item["heat"] or 0) for item in keywords)
    keyword_score = (
        min(100.0, math.log1p(heat_sum) / math.log1p(200000) * 100)
        if heat_sum > 0
        else 0.0
    )

    weibo_frame = _weibo_report()
    if weibo_frame.attrs.get("warning"):
        warnings.append(f"微博舆情获取失败：{weibo_frame.attrs['warning']}")
    weibo_rate = None
    market_weibo_avg = None
    if not weibo_frame.empty and "name" in weibo_frame.columns:
        rates = pd.to_numeric(weibo_frame.get("rate"), errors="coerce")
        if rates.notna().any():
            market_weibo_avg = round(float(rates.mean()), 4)
        if stock_name:
            matched = weibo_frame[weibo_frame["name"].astype(str) == stock_name]
            if not matched.empty:
                weibo_rate = _num(matched.iloc[0].get("rate"))

    xueqiu: dict[str, Any] = {}
    if include_xueqiu:
        xueqiu = _xueqiu_hot().get(code, {})
        if not xueqiu:
            warnings.append("雪球热度不可用（通常需要 cookie）")

    heat_score = round(rank_score * 0.65 + keyword_score * 0.35, 2)
    keyword_sentiment = _keyword_sentiment(keywords)
    if weibo_rate is not None:
        weibo_score = _weibo_score(weibo_rate) or 50.0
        sentiment_score = round(weibo_score * 0.7 + keyword_sentiment * 0.3, 2)
    else:
        sentiment_score = keyword_sentiment
    sentiment_score = max(0.0, min(100.0, sentiment_score))
    social_score = round(
        max(0.0, min(100.0, heat_score * 0.6 + sentiment_score * 0.4)), 2
    )

    baseline = social_baseline(code, 20)
    abnormal = "正常"
    if baseline.get("count", 0) >= 5 and baseline.get("heat_avg") is not None:
        delta = heat_score - float(baseline["heat_avg"])
        if delta >= 15:
            abnormal = "异常升温"
        elif delta <= -15:
            abnormal = "异常降温"
        elif abs(delta) >= 8:
            abnormal = "明显升温" if delta > 0 else "明显降温"

    attention = _attention_label(heat_score)
    sentiment_label = _sentiment_label(sentiment_score)
    guba_available = bool(guba_rank) or (
        not guba.empty and not guba.attrs.get("warning")
    )
    if guba_rank:
        rank_text = f"人气榜第 {guba_rank}"
    elif guba_available:
        rank_text = f"人气榜未进入前 {total_ranked or 100}"
    else:
        rank_text = "人气榜不可用"
    if weibo_rate is not None:
        sentiment_text = (
            f"微博情绪 {'+' if weibo_rate >= 0 else ''}{weibo_rate:.2f}"
            f"（{sentiment_label}）"
        )
    else:
        sentiment_text = f"情绪 {sentiment_score:.0f}（{sentiment_label}）"
    summary = (
        f"{stock_name or code} {rank_text}，"
        f"综合热度 {heat_score:.0f}（{attention}），{sentiment_text}；"
        f"社媒趋势 {abnormal}。"
    )

    payload = {
        "guba": {
            "rank": guba_rank,
            "total": total_ranked,
            "price": _num(guba_row.get("最新价")) if guba_row is not None else None,
            "change_pct": _num(guba_row.get("涨跌幅"))
            if guba_row is not None
            else None,
        },
        "keywords": keywords,
        "weibo": {
            "rate": weibo_rate,
            "market_avg": market_weibo_avg,
            "stock_name": stock_name,
        },
        "xueqiu": xueqiu,
        "baseline": baseline,
    }

    result = {
        "success": True,
        "ticker": code,
        "name": stock_name,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "guba_rank": guba_rank,
        "guba_total": total_ranked,
        "rank_score": round(rank_score, 2),
        "keyword_score": round(keyword_score, 2),
        "heat_score": heat_score,
        "attention": attention,
        "sentiment_score": sentiment_score,
        "sentiment": sentiment_label,
        "weibo_rate": weibo_rate,
        "market_weibo_avg": market_weibo_avg,
        "keywords": keywords,
        "xueqiu": xueqiu,
        "baseline": baseline,
        "abnormal": abnormal,
        "social_score": social_score,
        "summary": summary,
        "warnings": warnings,
        "payload": payload,
    }
    if save:
        try:
            snapshot = save_social_snapshot(
                code,
                heat_score=heat_score,
                sentiment_score=sentiment_score,
                guba_rank=guba_rank,
                weibo_rate=weibo_rate,
                payload=payload,
            )
            result["snapshot"] = snapshot
        except Exception as exc:
            warnings.append(f"快照保存失败：{exc}")
    return result


def get_social_history(ticker: str, days: int = 60) -> dict[str, Any]:
    from tradingagents.storage import list_social_snapshots

    code = normalize_code(str(ticker or ""))
    items = list_social_snapshots(code, days)
    return {
        "success": bool(code),
        "ticker": code,
        "days": days,
        "items": items,
        "baseline": social_baseline(code, 20),
    }


def get_social_hot(limit: int = 30) -> dict[str, Any]:
    """东财人气榜 + 微博情绪的情绪/热度榜。"""
    guba = _guba_rank()
    if guba.empty or "code" not in guba.columns:
        return {
            "success": False,
            "message": "人气榜暂无数据",
            "items": [],
            "warnings": [f"人气榜获取失败：{guba.attrs.get('warning')}"],
        }
    weibo = _weibo_report()
    weibo_map: dict[str, float] = {}
    if not weibo.empty and "name" in weibo.columns:
        for _, row in weibo.iterrows():
            name = _text(row.get("name"))
            rate = _num(row.get("rate"))
            if name and rate is not None:
                weibo_map[name] = rate

    items: list[dict[str, Any]] = []
    for _, row in guba.head(max(1, min(int(limit), 100))).iterrows():
        name = _text(row.get("股票名称"))
        rate = weibo_map.get(name)
        rank_value = _num(row.get("当前排名")) or 0
        items.append(
            {
                "rank": int(rank_value),
                "ticker": _text(row.get("code")),
                "name": name,
                "price": _num(row.get("最新价")),
                "change_pct": _num(row.get("涨跌幅")),
                "weibo_rate": rate,
                "sentiment": _sentiment_label(_weibo_score(rate) or 50.0)
                if rate is not None
                else "未知",
            }
        )
    return {
        "success": True,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "warnings": [],
    }


__all__ = [
    "clear_social_cache",
    "get_social_sentiment",
    "get_social_history",
    "get_social_hot",
]
