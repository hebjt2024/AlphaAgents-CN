"""公告 / 研报一致预期 / 股东变化（供 LLM 分析师调用）。

这些工具把 ``dataflows/disclosure.py`` 的结构化结果渲染成中文文本。
关键约束：``curr_date`` 必须由调用方传入分析日，历史分析才不会看到
分析日之后才披露的公告/研报/股东户数。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.agents.utils.prompt_guard import wrap_untrusted
from tradingagents.dataflows.disclosure import (
    get_announcements as _get_announcements,
)
from tradingagents.dataflows.disclosure import (
    get_research_consensus as _get_research_consensus,
)
from tradingagents.dataflows.disclosure import (
    get_shareholder_changes as _get_shareholder_changes,
)


def _effective_date(curr_date: str = "") -> str | None:
    if curr_date:
        return curr_date
    try:
        from tradingagents.runtime import get_current_trade_date

        return get_current_trade_date()
    except Exception:
        return None


def _warnings(result: dict) -> str:
    warnings = result.get("warnings") or []
    if not warnings:
        return ""
    return "\n### 数据提示\n" + "\n".join(f"- {item}" for item in warnings)


@tool
def get_announcements(
    ticker: Annotated[str, "A 股代码或名称，如 600519 / 贵州茅台"],
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
    days: Annotated[int, "回看天数，默认 30"] = 30,
) -> str:
    """获取个股公告，并按利好/利空、影响级别与主题分类。

    重点覆盖：减持/增持/回购、业绩预告、重大合同/中标、诉讼/处罚/立案、
    股权质押、解禁、分红、股权激励等 A 股高影响事件。
    """
    result = _get_announcements(ticker, days=days, curr_date=_effective_date(curr_date))
    if not result.get("success"):
        return f"公告获取失败：{result.get('message') or '未知原因'}"
    lines = [f"## 公告（{result.get('ticker')}，{result.get('as_of')}）", result.get("summary", "")]
    high = result.get("high_impact") or []
    if high:
        lines.append("\n### 高影响公告")
        for item in high:
            lines.append(
                f"- {item.get('date')} [{item.get('impact_label')}/{item.get('level')}] "
                f"{item.get('title')}（{item.get('type')}）"
            )
    items = result.get("items") or []
    if items:
        lines.append("\n### 最近公告")
        for item in items[:15]:
            lines.append(
                f"- {item.get('date')} [{item.get('impact_label')}] "
                f"{item.get('title')}（{item.get('type')}）"
            )
    return wrap_untrusted("\n".join(lines) + _warnings(result), "disclosure")


@tool
def get_research_consensus(
    ticker: Annotated[str, "A 股代码或名称，如 600519 / 贵州茅台"],
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
    days: Annotated[int, "回看天数，默认 180"] = 180,
) -> str:
    """获取机构研报评级分布、盈利预测与最近研报列表。

    这是 A 股"一致预期"的代理：买入/增持占比、EPS 预测、机构关注度。
    """
    result = _get_research_consensus(
        ticker, days=days, curr_date=_effective_date(curr_date)
    )
    if not result.get("success"):
        return f"研报获取失败：{result.get('message') or '未知原因'}"
    lines = [
        f"## 研报一致预期（{result.get('ticker')}，{result.get('as_of')}）",
        result.get("summary", ""),
    ]
    ratings = result.get("ratings") or {}
    if ratings:
        lines.append(
            "\n### 评级分布\n"
            + "，".join(f"{key} {value} 篇" for key, value in ratings.items())
        )
    forecasts = result.get("forecasts") or {}
    if forecasts:
        lines.append("\n### 盈利预测（机构均值）")
        for year, item in sorted(forecasts.items()):
            if item.get("eps") is None:
                continue
            lines.append(
                f"- {year}：EPS {item.get('eps')}，PE {item.get('pe')}，"
                f"样本 {item.get('count')} 家"
            )
    reports = result.get("reports") or []
    if reports:
        lines.append("\n### 最近研报")
        for item in reports[:10]:
            lines.append(
                f"- {item.get('date')} {item.get('institution')} "
                f"[{item.get('rating')}] {item.get('title')}"
            )
    return wrap_untrusted("\n".join(lines) + _warnings(result), "disclosure")


@tool
def get_shareholder_changes(
    ticker: Annotated[str, "A 股代码或名称，如 600519 / 贵州茅台"],
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取股东户数变化与最新前十大股东。

    股东户数下降通常意味着筹码集中；前十大股东占比反映股权集中度。
    """
    result = _get_shareholder_changes(ticker, curr_date=_effective_date(curr_date))
    if not result.get("success"):
        return f"股东数据获取失败：{result.get('summary') or '暂无数据'}"
    lines = [
        f"## 股东变化（{result.get('ticker')}，{result.get('as_of')}）",
        result.get("summary", ""),
    ]
    items = result.get("items") or []
    if items:
        lines.append("\n### 股东户数")
        for item in items[:8]:
            lines.append(
                f"- {item.get('date')}：{item.get('holder_count')} 户，"
                f"较上期 {item.get('change_pct')}%"
            )
    top10 = result.get("top10") or []
    if top10:
        lines.append("\n### 前十大股东")
        for item in top10:
            lines.append(
                f"- {item.get('rank')}. {item.get('name')} "
                f"持股 {item.get('ratio')}%（{item.get('change')}）"
            )
    return wrap_untrusted("\n".join(lines) + _warnings(result), "disclosure")


__all__ = [
    "get_announcements",
    "get_research_consensus",
    "get_shareholder_changes",
]
