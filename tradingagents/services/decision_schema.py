"""最终决策的结构化解析与校验。

LLM 的最终输出通常是一段长文，自动化系统只需要一个决策词，但审阅者还需要：

- 置信度（模型自己怎么判断）；
- 关键论点（3-5 条）；
- 风险标记（数据缺口、事件风险）；
- 引用来源（公告、研报、行情、资金流）。

本模块要求组合经理在报告末尾输出一个 JSON 代码块（见 prompt），并在这里做：

1. 尽力解析 JSON；解析失败时退回文本决策词；
2. 把中文/英文决策统一到 BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL；
3. 校验置信度、列表字段、引用数量；
4. 输出可直接落库/返回前端的字典，而不是把 LLM 原文硬塞给下游。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_DECISIONS = ("BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL")

_ALIASES = {
    "买入": "BUY",
    "增持": "OVERWEIGHT",
    "超配": "OVERWEIGHT",
    "持有": "HOLD",
    "中性": "HOLD",
    "观望": "HOLD",
    "低配": "UNDERWEIGHT",
    "减持": "UNDERWEIGHT",
    "卖出": "SELL",
    "清仓": "SELL",
    "buy": "BUY",
    "overweight": "OVERWEIGHT",
    "hold": "HOLD",
    "underweight": "UNDERWEIGHT",
    "sell": "SELL",
}


def normalize_decision(value: Any) -> str:
    """从文本中取**最后一次**出现的决策词。

    LLM 常在正文里写“虽然不建议买入，但综合判断应减持”：按枚举顺序找第一个
    出现词会把结论判成 BUY。这里按字符位置取最后一个匹配，更接近结论所在。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    candidates: list[tuple[int, str]] = []
    upper = text.upper()
    for decision in ALLOWED_DECISIONS:
        start = 0
        while True:
            index = upper.find(decision, start)
            if index < 0:
                break
            candidates.append((index, decision))
            start = index + len(decision)
    lowered = text.lower()
    for key, decision in _ALIASES.items():
        start = 0
        needle = key.lower()
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            candidates.append((index, decision))
            start = index + len(needle)
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[0])[1]


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE
    )
    candidates = [fenced.group(1)] if fenced else []
    # 没有围栏时，尝试从第一个 { 到最后一个 }，再逐步截断（避免正文里的花括号）。
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _as_list(value: Any, limit: int = 20) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item not in (None, "")][:limit]
    return [value]


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        number *= 100
    return round(min(100.0, max(0.0, number)), 2)


def parse_structured_decision(
    text: str, fallback_decision: str = "HOLD"
) -> dict[str, Any]:
    """从最终决策文本中解析结构化字段，失败时退回决策词。"""
    raw = str(text or "")
    payload = _extract_json(raw) or {}
    warnings: list[str] = []
    raw_decision = (
        payload.get("decision")
        or payload.get("rating")
        or payload.get("signal")
        or raw
    )
    decision = normalize_decision(raw_decision)
    if not decision:
        decision = normalize_decision(fallback_decision) or "HOLD"
        warnings.append("决策词无法识别或不在允许枚举内，已使用回退值")
    confidence = _confidence(
        payload.get("confidence")
        or payload.get("confidence_pct")
        or payload.get("score")
    )
    key_points = _as_list(
        payload.get("key_points") or payload.get("keyPoints") or payload.get("points")
    )
    citations = _as_list(
        payload.get("citations") or payload.get("sources") or payload.get("references")
    )
    risk_flags = _as_list(
        payload.get("risk_flags") or payload.get("risks") or payload.get("flags")
    )
    summary = str(
        payload.get("summary")
        or payload.get("executive_summary")
        or ""
    ).strip()

    if not payload:
        warnings.append("未找到结构化决策 JSON，已退回文本决策词")
    if confidence is None:
        warnings.append("缺少置信度")
    if not key_points:
        warnings.append("缺少关键论点")
    if not citations:
        warnings.append("缺少引用来源")
    if decision not in ALLOWED_DECISIONS:
        decision = "HOLD"
        warnings.append("决策词不在允许枚举内，已回退 HOLD")

    return {
        "decision": decision,
        "confidence": confidence,
        "summary": summary,
        "key_points": key_points,
        "citations": citations,
        "risk_flags": risk_flags,
        "structured": bool(payload),
        "valid": decision in ALLOWED_DECISIONS,
        "warnings": warnings,
        "raw_decision_text": raw,
    }


def format_structured_decision(value: dict[str, Any]) -> str:
    """把结构化决策渲染成简短 Markdown，供报告/通知使用。"""
    if not value:
        return ""
    lines = [
        f"**决策**：{value.get('decision') or 'HOLD'}"
        + (
            f"（置信度 {value.get('confidence')}%）"
            if value.get("confidence") is not None
            else ""
        )
    ]
    if value.get("summary"):
        lines.append(f"**摘要**：{value['summary']}")
    points = value.get("key_points") or []
    if points:
        lines.append("**关键论点**：")
        lines.extend(f"- {item}" for item in points)
    flags = value.get("risk_flags") or []
    if flags:
        lines.append("**风险提示**：" + "；".join(str(item) for item in flags))
    citations = value.get("citations") or []
    if citations:
        lines.append("**引用**：" + "；".join(str(item) for item in citations[:8]))
    return "\n".join(lines)


__all__ = [
    "ALLOWED_DECISIONS",
    "parse_structured_decision",
    "format_structured_decision",
    "normalize_decision",
]
