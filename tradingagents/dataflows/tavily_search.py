"""Tavily 联网搜索适配器。

用于补充新闻、公告、政策与事件信息。API Key 从环境变量读取：
  - TAVILY_API_KEY
  - TAVILY_API_KEYS（兼容参考项目的复数写法）
"""

from __future__ import annotations

import os
from datetime import datetime

import requests

TAVILY_API_URL = "https://api.tavily.com/search"


def _api_key() -> str:
    return (
        os.getenv("TAVILY_API_KEY", "").strip()
        or os.getenv("TAVILY_API_KEYS", "").strip()
    )


def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    topic: str = "general",
    days: int | None = None,
) -> str:
    """调用 Tavily 搜索并返回 Markdown 结果。"""
    key = _api_key()
    if not key:
        raise RuntimeError(
            "未配置 TAVILY_API_KEY，无法使用联网搜索。请在 .env 中设置 "
            "TAVILY_API_KEY（或 TAVILY_API_KEYS）。"
        )

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max(1, min(int(max_results), 20)),
        "include_answer": True,
        "topic": topic,
    }
    if days and topic == "news":
        payload["days"] = max(1, min(int(days), 30))

    response = requests.post(TAVILY_API_URL, json=payload, timeout=25)
    response.raise_for_status()
    data = response.json()

    lines = [f"## 联网搜索：{query}", ""]
    answer = (data.get("answer") or "").strip()
    if answer:
        lines.extend([f"**摘要**：{answer}", ""])

    results = data.get("results") or []
    if not results:
        lines.append("未检索到相关结果。")
        return "\n".join(lines)

    for index, item in enumerate(results, 1):
        title = str(item.get("title") or "无标题")
        url = str(item.get("url") or "")
        content = str(item.get("content") or "").strip()
        if len(content) > 600:
            content = content[:600] + "..."
        published = item.get("published_date") or item.get("published") or ""
        lines.append(f"### {index}. {title}")
        if published:
            lines.append(f"- 发布时间：{published}")
        if url:
            lines.append(f"- 链接：{url}")
        if content:
            lines.append(content)
        lines.append("")

    lines.append(
        f"> 数据来源：Tavily 搜索 · {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    return "\n".join(lines)


def search_stock_news(
    ticker: str,
    company: str = "",
    curr_date: str | None = None,
    look_back_days: int = 7,
    max_results: int = 8,
) -> str:
    """搜索个股新闻/公告（Tavily news 模式）。"""
    name_part = f"{company} " if company else ""
    query = f"{name_part}{ticker} 股票 最新消息 公告"
    if curr_date:
        query += f" {str(curr_date)[:10]}"
    return tavily_search(
        query,
        max_results=max_results,
        search_depth="advanced",
        topic="news",
        days=look_back_days,
    )
