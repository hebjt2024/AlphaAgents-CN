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
    "强烈买入": "BUY",
    "强烈卖出": "SELL",
    "超配买入": "OVERWEIGHT",
    "buy": "BUY",
    "overweight": "OVERWEIGHT",
    "hold": "HOLD",
    "underweight": "UNDERWEIGHT",
    "sell": "SELL",
}

_DECISION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(?:OVERWEIGHT|UNDERWEIGHT|BUY|SELL|HOLD)(?![A-Za-z])"
    r"|强烈买入|强烈卖出|超配买入|买入|增持|超配|持有|中性|观望|低配|减持|卖出|清仓",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"不建议|不要|避免|暂不|不宜|不能|不得|无需|别买|别卖|别追|别碰|"
    r"do\s+not|don['’]t|avoid|not\s+recommend",
    re.IGNORECASE,
)
_NEUTRAL_DECISION_RE = re.compile(r"暂不形成方向判断|不形成方向判断|暂缓交易|核心数据不足")
_EXPLICIT_LINE_RE = re.compile(
    r"最终决策|最终结论|交易决策|投资结论|结论|评级|rating|"
    r"final\s+decision|final\s+rating|recommendation",
    re.IGNORECASE,
)


def _decision_from_explicit_text(text: str) -> str:
    """解析显式结论，否定买入/卖出时返回 HOLD。"""
    value = str(text or "")
    if not value.strip():
        return ""
    if _NEUTRAL_DECISION_RE.search(value):
        return "HOLD"
    negated = False
    for match in _DECISION_TOKEN_RE.finditer(value):
        prefix = value[max(0, match.start() - 16) : match.start()]
        if _NEGATION_RE.search(prefix):
            negated = True
            continue
        token = match.group(0)
        return _ALIASES.get(token.lower()) or token.upper()
    return "HOLD" if negated else ""


def extract_explicit_decision_line(text: str) -> str:
    """从“最终决策/结论/评级”等显式语句提取结论，尊重否定语义。"""
    for line in str(text or "").splitlines():
        match = _EXPLICIT_LINE_RE.search(line)
        if match is None:
            continue
        decision = _decision_from_explicit_text(line[match.end() :])
        if decision:
            return decision
    return ""


def normalize_decision(value: Any) -> str:
    """从文本中取**最后一次**出现的决策词。

    LLM 常在正文里写“虽然不建议买入，但综合判断应减持”：按枚举顺序找第一个
    出现词会把结论判成 BUY。这里按字符位置取最后一个匹配，更接近结论所在。

    注意：这是**兜底**策略，正文里出现"当日不得卖出"这类描述时仍会误判，
    因此调用方应优先用 :func:`extract_rating_decision` 读取评级小节。
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


#: 评级小节的标题关键词（组合经理固定输出 "## 1. 评级（Rating）"）。
_RATING_HEADINGS = ("评级", "rating")


def extract_rating_decision(text: str) -> str:
    """从“评级（Rating）”小节里取决策词。

    WHY 需要单独解析评级小节：
    组合经理的最终报告是一篇长文，里面到处会出现“买入”“卖出”这类词
    （例如“A 股实行 T+1，当日买入的股票不得在当日卖出”）。按全文抓
    最后一个词会得到“卖出”，与正文开头的评级“持有”直接矛盾 —— 用户看到
    的是自相矛盾的结论。评级是作者明确声明的结论，必须优先于正文用词。

    返回空字符串表示没有找到评级小节（调用方应回退到其它策略）。
    """
    raw = str(text or "")
    if not raw:
        return ""

    # 逐行扫描，找到评级小节的标题行，取该标题之后、下一个同级标题之前的正文
    lines = raw.splitlines()
    heading_index = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        lowered = stripped.lower()
        if any(keyword in stripped or keyword in lowered for keyword in _RATING_HEADINGS):
            heading_index = index
            break
    if heading_index < 0:
        return ""

    section: list[str] = []
    for line in lines[heading_index + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped:
            section.append(stripped)
    if not section:
        return ""

    # 评级小节里取**第一个**决策词：作者写下评级时的措辞才是结论，
    # 后面跟着的通常是解释（例如"持有（Hold）表示……"）。
    block = "\n".join(section)
    return _decision_from_explicit_text(block)


def _first_decision_in(text: str) -> str:
    """取文本中**首次**出现的决策词（评级小节专用）。"""
    if not text:
        return ""
    upper = text.upper()
    lowered = text.lower()
    candidates: list[tuple[int, str]] = []
    for decision in ALLOWED_DECISIONS:
        index = upper.find(decision)
        if index >= 0:
            candidates.append((index, decision))
    for key, decision in _ALIASES.items():
        index = lowered.find(key.lower())
        if index >= 0:
            candidates.append((index, decision))
    if not candidates:
        return ""
    return min(candidates, key=lambda item: item[0])[1]


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
    """从最终决策文本中解析结构化字段，失败时退回文本决策词。"""
    raw = str(text or "")
    payload = _extract_json(raw) or {}
    warnings: list[str] = []

    # 决策词的来源，决定可信度：
    #   json           → 模型自己输出的结构化块（最可信）
    #   rating_section → 正文"评级（Rating）"小节里的明确结论
    #   text           → 正文抓词（可能只是描述，例如"当日不得卖出"）
    #   fallback       → 调用方传入的兜底值
    decision = ""
    source = ""
    json_decision = ""
    if payload:
        raw_value = str(
            payload.get("decision")
            or payload.get("rating")
            or payload.get("signal")
            or ""
        ).strip()
        # 模型有时会把提示词里的模板整段抄回来
        # （"BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL"），这不是有效结论。
        if "|" in raw_value:
            raw_value = ""
        json_decision = _decision_from_explicit_text(raw_value)
    rating_decision = extract_rating_decision(raw)
    if json_decision and rating_decision and json_decision != rating_decision:
        decision = "HOLD"
        source = "conflicted_explicit"
        warnings.append(
            f"JSON 决策 {json_decision} 与评级小节 {rating_decision} 冲突，"
            "已回退 HOLD，需人工复核"
        )
    elif json_decision:
        decision = json_decision
        source = "json"
    elif rating_decision:
        decision = rating_decision
        source = "rating_section"
    else:
        decision = extract_explicit_decision_line(raw)
        source = "explicit_text" if decision else ""
    if not decision:
        decision = normalize_decision(raw)
        source = "text"
    if not decision:
        decision = normalize_decision(fallback_decision) or "HOLD"
        source = "fallback"
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
    if source == "text":
        warnings.append("未能定位评级小节，决策词由正文推断，可能与作者结论不一致")
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
        "decision_source": source,
        "source": source,
        "confidence": confidence,
        "summary": summary,
        "key_points": key_points,
        "citations": citations,
        "risk_flags": risk_flags,
        "structured": bool(payload),
        "valid": source != "conflicted_explicit" and decision in ALLOWED_DECISIONS,
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
    "extract_explicit_decision_line",
    "parse_structured_decision",
    "format_structured_decision",
    "normalize_decision",
]
