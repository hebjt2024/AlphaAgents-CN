"""联网搜索工具（Tavily）。

用于补充最新新闻、公告、政策与事件信息。需要在 .env 中配置
TAVILY_API_KEY；未配置时返回明确提示，不影响其他分析流程。
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.tavily_search import (
    search_stock_news as _search_stock_news,
)
from tradingagents.dataflows.tavily_search import tavily_search as _tavily_search


@tool
def search_web(
    query: Annotated[str, "搜索关键词，例如：贵州茅台 最新公告 业绩"],
    max_results: Annotated[int, "返回结果数量，默认 5"] = 5,
    days: Annotated[int, "回溯天数（新闻模式），默认 7"] = 7,
) -> str:
    """联网搜索最新信息（新闻、公告、政策、行业事件等）。

    当本地新闻接口覆盖不足、或需要核实最新事件时使用。
    """
    try:
        return _tavily_search(
            query,
            max_results=max_results,
            search_depth="advanced",
            topic="news",
            days=days,
        )
    except Exception as exc:
        return f"联网搜索暂时不可用（{type(exc).__name__}: {exc}）"


@tool
def search_stock_news(
    ticker: Annotated[str, "股票代码，如 600519"],
    curr_date: Annotated[str, "分析日期，格式 yyyy-mm-dd"] = None,
    look_back_days: Annotated[int, "回溯天数，默认 7"] = 7,
    max_results: Annotated[int, "返回结果数量，默认 8"] = 8,
) -> str:
    """联网搜索个股最新新闻与公告（Tavily news 模式）。"""
    try:
        return _search_stock_news(
            ticker,
            curr_date=curr_date,
            look_back_days=look_back_days,
            max_results=max_results,
        )
    except Exception as exc:
        return f"个股新闻搜索暂时不可用（{type(exc).__name__}: {exc}）"
