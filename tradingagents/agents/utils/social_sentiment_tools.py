"""真实社媒舆情工具（供舆情分析师调用）。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.agents.utils.prompt_guard import wrap_untrusted
from tradingagents.dataflows.social_sentiment import (
    get_social_sentiment as _get_social_sentiment,
)


def _effective_date(curr_date: str = "") -> str | None:
    if curr_date:
        return curr_date
    try:
        from tradingagents.runtime import get_current_trade_date

        return get_current_trade_date()
    except Exception:
        return None


@tool
def get_social_sentiment(
    ticker: Annotated[str, "A 股代码或名称，如 600519 / 贵州茅台"],
    name: Annotated[str, "股票简称，用于匹配微博舆情；可留空"] = "",
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取东方财富人气榜、概念热度、微博情绪与社媒异常热度。

    注意：社媒数据只提供实时快照；历史分析日会返回不可用，避免未来函数。
    """
    result = _get_social_sentiment(
        ticker, name=name, curr_date=_effective_date(curr_date)
    )
    if not result.get("success"):
        return f"社媒舆情不可用：{result.get('message') or '未知原因'}"
    lines = [
        f"## 社媒舆情（{result.get('name') or result.get('ticker')}，{result.get('as_of')}）",
        result.get("summary", ""),
    ]
    keywords = result.get("keywords") or []
    if keywords:
        lines.append("\n### 概念热度")
        for item in keywords[:8]:
            lines.append(f"- {item.get('name')}：热度 {item.get('heat')}")
    xueqiu = result.get("xueqiu") or {}
    if xueqiu:
        lines.append(
            f"\n### 雪球\n- 关注 {xueqiu.get('follow')}，讨论 {xueqiu.get('tweet')}"
        )
    baseline = result.get("baseline") or {}
    if baseline.get("count"):
        lines.append(
            f"\n### 历史基线\n- 近 {baseline.get('count')} 次快照："
            f"热度均值 {baseline.get('heat_avg')}，情绪均值 {baseline.get('sentiment_avg')}"
        )
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("\n### 数据提示\n" + "\n".join(f"- {item}" for item in warnings))
    return wrap_untrusted("\n".join(lines), "social_sentiment")


__all__ = ["get_social_sentiment"]
