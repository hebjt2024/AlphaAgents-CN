"""宏观 / 两融 / 行业轮动 / ETF 资金流工具（供市场与新闻分析师调用）。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.macro_data import (
    get_etf_flows as _get_etf_flows,
)
from tradingagents.dataflows.macro_data import (
    get_industry_rotation as _get_industry_rotation,
)
from tradingagents.dataflows.macro_data import (
    get_macro_indicators as _get_macro_indicators,
)
from tradingagents.dataflows.macro_data import (
    get_margin_snapshot as _get_margin_snapshot,
)


def _warnings(result: dict) -> str:
    warnings = result.get("warnings") or []
    if not warnings:
        return ""
    return "\n### 数据提示\n" + "\n".join(f"- {item}" for item in warnings)


def _effective_date(curr_date: str = "") -> str | None:
    if curr_date:
        return curr_date
    try:
        from tradingagents.runtime import get_current_trade_date

        return get_current_trade_date()
    except Exception:
        return None


@tool
def get_macro_overview(
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取 PMI、CPI、PPI、M2、LPR、Shibor 与中美国债收益率。

    用于判断当前宏观环境：经济扩张/收缩、通胀方向、流动性松紧与中美利差。
    """
    result = _get_macro_indicators(_effective_date(curr_date))
    if not result.get("success"):
        return f"宏观数据不可用：{result.get('message') or '未知原因'}"
    lines = ["## 宏观指标", result.get("summary", "")]
    for item in result.get("items") or []:
        lines.append(
            f"- {item.get('label')}：{item.get('value')}{item.get('unit') or ''}"
            f"（{item.get('date')}，{item.get('status')}）"
        )
    return "\n".join(lines) + _warnings(result)


@tool
def get_margin_snapshot(
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取沪深两融余额及 5/20 日变化，判断杠杆资金风险偏好。"""
    result = _get_margin_snapshot(curr_date=_effective_date(curr_date))
    if not result.get("success"):
        return f"两融数据不可用：{result.get('message') or '未知原因'}"
    return (
        f"## 两融余额\n- {result.get('summary')}\n"
        f"- 5 日变化：{result.get('change_5d')}%\n"
        f"- 20 日变化：{result.get('change_20d')}%"
        + _warnings(result)
    )


@tool
def get_industry_rotation(
    limit: Annotated[int, "返回前 N 个行业，默认 15"] = 15,
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取行业今日/5日/10日涨跌幅、主力净流入与轮动分。"""
    result = _get_industry_rotation(limit, _effective_date(curr_date))
    if not result.get("success"):
        return f"行业轮动数据不可用：{result.get('message') or '未知原因'}"
    lines = ["## 行业轮动", result.get("summary", "")]
    for item in result.get("items") or []:
        lines.append(
            f"- {item.get('name')}：今日 {item.get('change_today')}%，"
            f"5日 {item.get('change_5d')}%，10日 {item.get('change_10d')}%，"
            f"5日主力净流入 {item.get('main_net_5d_yi')} 亿，"
            f"轮动分 {item.get('rotation_score')}"
        )
    bottom = result.get("bottom") or []
    if bottom:
        lines.append("\n### 弱势行业")
        for item in bottom[:5]:
            lines.append(
                f"- {item.get('name')}：5日 {item.get('change_5d')}%"
            )
    return "\n".join(lines) + _warnings(result)


@tool
def get_etf_flows(
    limit: Annotated[int, "返回前 N 只 ETF，默认 10"] = 10,
    curr_date: Annotated[str, "分析日期 YYYY-MM-DD；留空表示今天"] = "",
) -> str:
    """获取场内 ETF 主力净流入/流出，观察宽基与主题资金方向。"""
    result = _get_etf_flows(limit, _effective_date(curr_date))
    if not result.get("success"):
        return f"ETF 资金流不可用：{result.get('message') or '未知原因'}"
    lines = ["## ETF 主力资金流", result.get("summary", "")]
    for item in result.get("items") or []:
        lines.append(
            f"- {item.get('code')} {item.get('name')}："
            f"{item.get('change_pct')}%，主力净流入 {item.get('main_net_yi')} 亿"
        )
    outflow = result.get("outflow") or []
    if outflow:
        lines.append("\n### 净流出前列")
        for item in outflow[:5]:
            lines.append(
                f"- {item.get('name')}：{item.get('main_net_yi')} 亿"
            )
    return "\n".join(lines) + _warnings(result)


__all__ = [
    "get_macro_overview",
    "get_margin_snapshot",
    "get_industry_rotation",
    "get_etf_flows",
]
