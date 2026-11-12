"""市场情绪时间序列：把实时复盘快照沉淀成可比较的历史。

``services/market_review.py::build_market_review`` 的数据源（AkShare 的
Sina / 乐咕 / 东财接口）只提供实时快照，没有历史查询接口。因此本模块
**不伪造历史**：情绪序列从第一次快照开始逐日累积，调用
:func:`backfill_sentiment` 会得到明确的拒绝说明。

有了时间序列，才能回答 A 股择时里最重要的问题之一：当前情绪处于
“冰点”还是“高潮”，以及最近是在回暖还是走弱。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tradingagents.storage import (
    list_sentiment_snapshots,
    save_sentiment_snapshot,
)

from .market_review import build_market_review

#: 少于该样本数时，分位与趋势的统计意义有限。
MIN_SAMPLES = 10

#: 给出"可靠"判定的最小样本量。
#:
#: WHY 单独设一个更高的门槛：``MIN_SAMPLES`` 只是"能算出数来"的下限，
#: 用 3~12 天数据算分位数，会被近期一两天的极端值完全支配，得出的
#: "冰点/高潮"结论几乎必然误导。只有样本足够覆盖一段完整情绪周期
#: （约一个月交易日）时，分位数才有参考意义。
RELIABLE_SAMPLES = 20

#: 趋势判定阈值（上涨占比的 5 日均值差）。
TREND_THRESHOLD = 0.01

_EMPTY_MESSAGE = "暂无情绪历史快照，请先运行市场复盘。"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _up_ratio(row: dict[str, Any]) -> float:
    up = _number(row.get("up_count"))
    down = _number(row.get("down_count"))
    flat = _number(row.get("flat_count"))
    total = up + down + flat
    if total > 0:
        return up / total
    breadth = (row.get("payload") or {}).get("breadth") or {}
    return _number(breadth.get("up_ratio"))


def _percentile_rank(values: list[float], latest: float) -> float:
    """经验分位（midrank）：小于与等于 latest 的样本各按一半权重计入。

    全部样本都相等时返回 50（中性），最小值趋近 0，最大值趋近 100。
    """
    if not values:
        return 0.0
    below = sum(1 for value in values if value < latest - 1e-12)
    equal = sum(1 for value in values if abs(value - latest) <= 1e-12)
    return round((below + 0.5 * equal) / len(values) * 100, 1)


def _classify(percentile: float) -> str:
    if percentile < 20:
        return "冰点"
    if percentile < 40:
        return "偏冷"
    if percentile < 60:
        return "中性"
    if percentile < 80:
        return "偏热"
    return "高潮"


def _trend(ratios: list[float]) -> str:
    if len(ratios) < MIN_SAMPLES:
        return "持平"
    recent = _average(ratios[-5:])
    previous = _average(ratios[-10:-5])
    diff = recent - previous
    if diff > TREND_THRESHOLD:
        return "回暖"
    if diff < -TREND_THRESHOLD:
        return "走弱"
    return "持平"


def snapshot_market_sentiment(date: str | None = None) -> dict[str, Any]:
    """抓取一次实时复盘并把情绪快照落库。"""
    try:
        review = build_market_review(date)
    except Exception as exc:
        return {
            "success": False,
            "saved": False,
            "message": (
                f"市场复盘获取失败：{type(exc).__name__}: {str(exc)[:120]}"
            ),
        }
    if not isinstance(review, dict) or not review:
        return {
            "success": False,
            "saved": False,
            "message": "市场复盘数据不可用，未写入情绪历史。",
        }

    trade_date = str(
        review.get("as_of") or datetime.now().strftime("%Y-%m-%d")
    )[:10]
    breadth = review.get("breadth") or {}
    indices = review.get("indices") or []
    if not breadth and not indices:
        return {
            "success": False,
            "trade_date": trade_date,
            "saved": False,
            "message": "复盘未返回广度或指数数据，未写入情绪历史。",
            "warnings": review.get("warnings") or [],
        }

    payload = {
        "as_of": review.get("as_of") or trade_date,
        "breadth": breadth,
        "indices": indices,
        "summary": review.get("summary") or {},
        "warnings": review.get("warnings") or [],
    }
    snapshot = save_sentiment_snapshot(trade_date, payload)
    return {
        "success": True,
        "trade_date": trade_date,
        "saved": True,
        "payload": payload,
        "snapshot": snapshot,
    }


def get_sentiment_history(days: int = 60) -> dict[str, Any]:
    """返回情绪时间序列、统计量、趋势与“冰点/高潮”分级。"""
    days = max(1, int(days))
    rows = list_sentiment_snapshots(days)
    if not rows:
        return {
            "success": False,
            "days": days,
            "series": [],
            "stats": {},
            "trend": "",
            "level": "",
            "summary": _EMPTY_MESSAGE,
            "warnings": [_EMPTY_MESSAGE],
        }

    series: list[dict[str, Any]] = []
    for row in rows:
        series.append(
            {
                "date": row["trade_date"],
                "up_count": row["up_count"],
                "down_count": row["down_count"],
                "limit_up": row["limit_up"],
                "limit_down": row["limit_down"],
                "up_ratio": round(_up_ratio(row), 4),
                "tone": row.get("tone") or "",
            }
        )

    ratios = [item["up_ratio"] for item in series]
    latest = series[-1]
    percentile = _percentile_rank(ratios, ratios[-1])
    level = _classify(percentile)
    trend = _trend(ratios)

    recent5 = series[-5:]
    recent20 = series[-20:]
    stats = {
        "samples": len(series),
        "latest": dict(latest),
        "avg5": {
            "limit_up": round(
                _average([_number(item["limit_up"]) for item in recent5]), 1
            ),
            "up_ratio": round(
                _average([_number(item["up_ratio"]) for item in recent5]), 4
            ),
        },
        "avg20": {
            "limit_up": round(
                _average([_number(item["limit_up"]) for item in recent20]), 1
            ),
            "up_ratio": round(
                _average([_number(item["up_ratio"]) for item in recent20]), 4
            ),
        },
        "percentile": percentile,
    }

    summary = (
        f"近 {len(series)} 个交易日：今日上涨占比 "
        f"{latest['up_ratio'] * 100:.0f}%（近 {len(series)} 日 "
        f"{percentile:.0f}% 分位，{level}），涨停 {latest['limit_up']} 家 / "
        f"跌停 {latest['limit_down']} 家，较前 5 日均值{trend}。"
    )

    warnings: list[str] = []
    reliable = len(series) >= RELIABLE_SAMPLES
    if not reliable:
        warnings.append(
            f"情绪历史样本不足（{len(series)} 天，未达到 {RELIABLE_SAMPLES} 天的"
            "可靠门槛）；分位与\"冰点/高潮\"判定仅供参考，请勿据此重仓决策。"
        )

    if not reliable:
        # 明确标注判定不可靠，避免把"3 天样本的 0% 分位"读成"历史冰点"
        summary = f"⚠ 样本不足（{len(series)}/{RELIABLE_SAMPLES} 天），以下判定不可靠：{summary}"

    return {
        "success": True,
        "days": days,
        "series": series,
        "stats": {**stats, "reliable": reliable, "required_samples": RELIABLE_SAMPLES},
        "trend": trend,
        "level": level,
        "level_reliable": reliable,
        "reliable": reliable,
        "min_samples": RELIABLE_SAMPLES,
        "summary": summary,
        "warnings": warnings,
    }


def backfill_sentiment(days: int = 0) -> dict[str, Any]:
    """明确拒绝历史回溯：数据源只有实时快照，无法编造过去的数据。"""
    return {
        "success": False,
        "requested_days": int(days or 0),
        "message": "复盘数据源为实时快照，无法回溯历史",
        "detail": (
            "情绪历史从第一次快照调用开始逐日累积；"
            "请在每个交易日收盘后运行 snapshot_market_sentiment。"
        ),
    }
