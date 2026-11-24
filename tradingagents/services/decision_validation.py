"""投资结论校验：决策对齐 + 与上一次结论对比。

``services/runner.py`` 把一次分析的结论落到 reports 表（``decision`` 列）后，
此前的缺口是：把多个独立决策来源（最终决策 / 决策卡 / 结构化决策 / 规则初判）
与上一次持久化结论之间的矛盾、方向翻转一律没有统一的校验记录。

本模块做「投资结论校验（决策对齐）」：

1. 汇总最终决策与各来源对齐（双方向方向相反即矛盾）；
2. 与上一次已持久化结论对比（方向翻转值得警惕）；
3. 把置信度偏低、结论词非法等问题归纳为 warning。

``validate_investment_conclusion`` 输出统一的结果字典，
``runner.py`` 把它写入 report meta（``decision_validation``），供 WebUI / CLI 消费。
"""

from __future__ import annotations

from typing import Any

from tradingagents.services.decision_schema import ALLOWED_DECISIONS

#: 偏多决策集合（含 BUY / OVERWEIGHT）。
_MULTI_DECISIONS = {"BUY", "OVERWEIGHT"}

#: 偏空决策集合（含 UNDERWEIGHT / SELL）。
_SHORT_DECISIONS = {"UNDERWEIGHT", "SELL"}


def direction_of(decision: Any) -> str:
    """决策 → 方向标签：``多`` / ``空`` / ``中``（HOLD 为中性，双方向无界限）。"""
    d = str(decision or "").upper()
    if d in _MULTI_DECISIONS:
        return "多"
    if d in _SHORT_DECISIONS:
        return "空"
    return "中"


def _direction_contradicts(a: str, b: str) -> bool:
    """两个方向是否真正矛盾：双方向差异且任一方向非中性。

    中性（HOLD / 未识别）不算矛盾——被动中性是正常决策，不是反面证据。
    """
    da, db = direction_of(a), direction_of(b)
    if not da or not db or da == "中" or db == "中":
        return False
    return da != db


def _summary_line(final: str, level: str, mismatches: list[str], warnings: list[str]) -> str:
    """给人看的一句话摘要。"""
    if level == "contradicted":
        head = "矛盾"
    elif level == "warning":
        head = "有异议"
    else:
        head = "对齐"
    detail = "；".join(mismatches + warnings)
    if detail:
        return f"结论校验{head}（最终：{final or '-'}）：{detail}"
    return f"结论校验{head}（最终：{final or '-'}）"


def validate_investment_conclusion(
    *,
    decision: str,
    decision_plan: dict | None = None,
    structured_decision: dict | None = None,
    decision_diff: dict | None = None,
    stored_decision: str | None = None,
    min_confidence: float = 50.0,
) -> dict[str, Any]:
    """校验最终决策与各来源（决策卡 / 结构化决策 / 规则初判 / 存储结论）的对齐。

    参数
    ----
    decision:
        最终决策（``parse_structured_decision`` 的输出，如 ``BUY``）。
    decision_plan:
        :func:`build_decision_plan` 的输出（决策卡）。
    structured_decision:
        :func:`parse_structured_decision` 的输出。
    decision_diff:
        :func:`compare_decisions` 的输出（规则初判 vs 最终决策）。
    stored_decision:
        上一次已持久化的决策（:func:`get_stored_decision`）。
    min_confidence:
        结构化决策置信度的警戒线（< 视为 warning）。

    返回
    ----
    dict
        ``ok``: 是否无矛盾；
        ``level``: ``ok`` / ``warning`` / ``contradicted``；
        ``mismatches``: 矛盾说明列表；
        ``warnings``: 提示列表；
        ``summary``: 人话摘要。
    """
    mismatches: list[str] = []
    warnings: list[str] = []
    contradiction = False

    final = str(decision or "").upper()
    if final not in ALLOWED_DECISIONS:
        mismatches.append(f"最终决策词不在允许枚举内（{final or '空'}）")
        contradiction = True
        final = ""

    plan = decision_plan if isinstance(decision_plan, dict) else {}
    structured = structured_decision if isinstance(structured_decision, dict) else {}
    diff = decision_diff if isinstance(decision_diff, dict) else {}

    def _contradict(a: str, b: str, label: str) -> None:
        nonlocal contradiction
        if _direction_contradicts(a, b):
            mismatches.append(f"{label}：方向矛盾（{a} vs {b}）")
            contradiction = True

    # 1) 最终决策 vs 结构化决策卡（决策卡是规则化生成的参考卡，方向必须可信）
    _contradict(final, str(plan.get("decision") or ""), "最终决策 vs 结构化决策卡")

    # 2) 最终决策 vs 结构化决策
    _contradict(final, str(structured.get("decision") or ""), "最终决策 vs 结构化决策")

    # 3) 最终决策 vs 规则初判
    prelim = str(diff.get("preliminary_decision") or "")
    if prelim:
        _contradict(final, prelim, "最终决策 vs 规则初判")

    # 4) 规则初判 vs 最终决策差异（两者独立来源，分歧值得显式呈现）
    if diff.get("has_diff") and not contradiction:
        warnings.append(
            str(diff.get("note") or "规则初判与最终决策方向不一致，请重点核对分歧原因。")
        )

    # 5) 置信度
    conf = structured.get("confidence")
    if final and isinstance(conf, (int, float)) and not isinstance(conf, bool):
        if float(conf) < float(min_confidence):
            warnings.append(f"结构化决策置信度偏低（{conf}%），建议复核")

    # 6) 与上一次持久化结论对比（方向翻转警告；当前决策是最新权威）
    stored = str(stored_decision or "").upper()
    if stored and final and _direction_contradicts(stored, final):
        warnings.append(
            f"与上一次结论方向相反（{stored} -> {final}），确认是否由新信息驱动"
        )

    level = "contradicted" if contradiction else ("warning" if warnings else "ok")
    return {
        "ok": level == "ok",
        "level": level,
        "mismatches": mismatches,
        "warnings": warnings,
        "summary": _summary_line(final, level, mismatches, warnings),
    }


def get_stored_decision(ticker: str) -> str | None:
    """查询该标的最近一次已持久化的决策（供结论对比）。

    任何异常（DB 不可用、表缺失）都视为「无存储结论」，返回 ``None``。
    """
    if not ticker:
        return None
    try:
        from tradingagents.storage import list_reports

        reports = list_reports(ticker=str(ticker), limit=1)
        for report in reports or []:
            decision = str(report.get("decision") or "").upper()
            if decision:
                return decision
    except Exception:
        return None
    return None


__all__ = [
    "direction_of",
    "get_stored_decision",
    "validate_investment_conclusion",
]
