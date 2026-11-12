"""自然语言问股意图解析（规则版，无额外 LLM 成本）。

输出结构：
{
  "query": 原始输入,
  "ticker": 识别到的股票代码或名称,
  "horizons": ["short"|"medium"|"long", ...],
  "focus_areas": ["technical"|"fundamental"|"news"|"sentiment"|"capital", ...],
  "raw_query": 原始输入
}
"""

from __future__ import annotations

import re

from tradingagents.dataflows.akshare_adapter import (
    _CHINESE_NAME_MAP,
    _load_akshare_names,
    resolve_stock_name,
)

_HORIZON_KEYWORDS = {
    "short": ("短线", "短期", "几天", "一周", "1-2周", "日内", "超短", "short"),
    "medium": ("中线", "中期", "一个月", "1个月", "季度", "几周", "medium"),
    "long": ("长线", "长期", "半年", "一年", "几年", "价值投资", "long"),
}

_FOCUS_KEYWORDS = {
    "technical": (
        "技术", "均线", "macd", "rsi", "kdj", "形态", "趋势", "k线",
        "缠论", "波浪", "technical",
    ),
    "fundamental": (
        "基本面", "财报", "估值", "pe", "pb", "roe", "业绩", "营收",
        "利润", "fundamental",
    ),
    "news": ("新闻", "公告", "消息", "事件", "政策", "研报", "news"),
    "sentiment": ("情绪", "舆情", "股吧", "雪球", "讨论", "热度", "sentiment"),
    "capital": ("资金", "北向", "主力", "龙虎榜", "筹码", "换手", "capital"),
}


def _detect_ticker(query: str) -> str:
    # 1. 6 位 A 股代码
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", query)
    if match:
        return match.group(1)

    # 2. 带交易所前缀/后缀的代码
    match = re.search(r"\b(?:sh|sz|bj)\d{6}\b", query, re.IGNORECASE)
    if match:
        return resolve_stock_name(match.group(0))
    match = re.search(r"\b\d{6}\.(?:SH|SZ|SS|BJ)\b", query, re.IGNORECASE)
    if match:
        return match.group(0).split(".")[0]

    # 3. 中文名称：先查内置映射（零网络），再查 AkShare 全量名称表
    for name in sorted(_CHINESE_NAME_MAP, key=len, reverse=True):
        if name in query:
            return _CHINESE_NAME_MAP[name]
    try:
        names = _load_akshare_names()
        if names:
            matches = [
                (len(name), code)
                for name, code in names.items()
                if name in query
            ]
            if matches:
                return max(matches)[1]
    except Exception:
        # 名称表不可用时保持原有行为，不影响纯代码/内置名称解析
        pass

    # 4. 大写美股代码（排除常见英文词）
    stop_words = {"BUY", "SELL", "HOLD", "A", "I", "THE", "AND", "OR"}
    candidates = [
        token
        for token in re.findall(r"\b[A-Z]{1,5}\b", query)
        if token not in stop_words
    ]
    if candidates:
        return candidates[0].upper()

    return ""


def parse_intent(query: str) -> dict[str, object]:
    text = (query or "").strip()
    lower = text.lower()

    horizons: list[str] = [
        key for key, words in _HORIZON_KEYWORDS.items() if any(w in lower for w in words)
    ]
    if not horizons:
        horizons = ["short", "medium"]

    focus_areas: list[str] = [
        key
        for key, words in _FOCUS_KEYWORDS.items()
        if any(w in lower for w in words)
    ]
    if not focus_areas:
        focus_areas = ["technical", "fundamental", "news"]

    return {
        "query": text,
        "ticker": _detect_ticker(text),
        "horizons": horizons,
        "focus_areas": focus_areas,
        "raw_query": text,
    }
