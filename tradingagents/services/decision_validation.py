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

import math
from datetime import date, datetime
from typing import Any

from tradingagents.services.decision_schema import ALLOWED_DECISIONS

#: 偏多决策集合（含 BUY / OVERWEIGHT）。
_MULTI_DECISIONS = {"BUY", "OVERWEIGHT"}

#: 偏空决策集合（含 UNDERWEIGHT / SELL）。
_SHORT_DECISIONS = {"UNDERWEIGHT", "SELL"}

BLOCKED_DECISION_TEXT = "HOLD：核心数据不足，暂不形成方向判断，暂缓交易。"
CONFLICTED_DECISION_TEXT = "HOLD：显式决策来源冲突，暂不形成方向判断，需人工复核。"


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


_REPORT_FIELDS = {
    "market": ("market_report", "市场/技术面报告"),
    "social": ("sentiment_report", "社媒舆情报告"),
    "news": ("news_report", "新闻与公告报告"),
    "fundamentals": ("fundamentals_report", "基本面报告"),
    "market_report": ("market_report", "市场/技术面报告"),
    "sentiment_report": ("sentiment_report", "社媒舆情报告"),
    "news_report": ("news_report", "新闻与公告报告"),
    "fundamentals_report": ("fundamentals_report", "基本面报告"),
}


def _evidence_date(value: Any) -> date | None:
    """把常见日期/时间值归一为日期，非法值返回 None。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _report_failed(report: Any) -> bool:
    """识别分析师节点生成的显式失败占位报告。"""
    text = str(report or "").strip()
    if not text:
        return False
    head = text[:120]
    return (
        "报告生成失败" in head
        or "分析失败" in head
        or "未返回报告" in head
        or "预取失败" in head
        or (text.startswith("（") and "失败" in head)
    )


def build_neutral_decision_plan() -> dict[str, Any]:
    """构建 blocked 状态使用的中性空计划，不暴露方向性价位或评分。"""
    return {
        "decision": "HOLD",
        "direction": "中性",
        "score": 0,
        "confidence": 0,
        "consensus": "中性",
        "conflict": False,
        "entry": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "risk_reward": 0.0,
        "position_pct": 0.0,
        "risk_pct": 0.0,
        "triggered": 0,
        "family_count": 0,
        "reasons": ["核心数据不足或分析未完成，暂不形成方向判断"],
        "disclaimer": "证据门禁未通过，本计划不包含方向性交易建议。",
    }


def apply_evidence_gate(
    final_state: dict[str, Any] | None,
    decision: str,
    structured_decision: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """将门禁结果同步到最终状态，blocked/冲突时净化 final_trade_decision。"""
    state = final_state if isinstance(final_state, dict) else {}
    structured = dict(structured_decision) if isinstance(structured_decision, dict) else {}
    assessment = evidence if isinstance(evidence, dict) else {}
    model_decision = str(state.get("model_decision") or decision or "HOLD").upper()
    raw_text = state.get("model_decision_text")
    if raw_text is None:
        raw_text = state.get("final_trade_decision") or ""
    state["model_decision_text"] = str(raw_text or "")
    state["model_decision"] = model_decision
    blocked = (
        assessment.get("level") == "blocked"
        or assessment.get("allow_directional") is False
    )
    conflicted = structured.get("decision_source") == "conflicted_explicit"
    if blocked or conflicted:
        decision = "HOLD"
        structured["decision"] = "HOLD"
        structured["decision_source"] = "evidence_gate" if blocked else "conflicted_explicit"
        structured["source"] = structured["decision_source"]
        structured["model_decision"] = model_decision
        structured.setdefault("raw_decision_text", state["model_decision_text"])
        warnings = list(structured.get("warnings") or [])
        warning = (
            "核心数据不足或分析未完成，方向性决策已降级为 HOLD："
            + str(assessment.get("summary") or "")
            if blocked
            else "显式决策来源冲突，最终决策已降级为 HOLD"
        )
        if warning not in warnings:
            warnings.append(warning)
        structured["warnings"] = warnings
        state["final_trade_decision"] = (
            BLOCKED_DECISION_TEXT if blocked else CONFLICTED_DECISION_TEXT
        )
    state["decision"] = decision
    state["decision_structured"] = structured
    state["evidence_assessment"] = assessment
    return state, decision, structured


def assess_analysis_evidence(
    final_state: dict[str, Any] | None,
    model_decision: str,
    decision: str,
    required_reports: tuple | list = (),
) -> dict[str, Any]:
    """评估方向性结论是否具备核心行情、策略与引用证据。

    核心行情或策略不可用、行情日期晚于分析日、分析未完成时返回 blocked。
    分析师报告缺失、单策略失败或结构化引用缺失只降级为 warning，避免把
    普通观点分歧误当成核心数据故障。
    """
    state = final_state if isinstance(final_state, dict) else {}
    result = state.get("strategy_result")
    result = result if isinstance(result, dict) else {}
    structured = state.get("decision_structured")
    structured = structured if isinstance(structured, dict) else {}
    items: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    warning_reasons: list[str] = []

    def add_item(
        label: str,
        status: str,
        detail: str,
        as_of: Any = None,
        *,
        blocks: bool = False,
    ) -> None:
        if isinstance(as_of, (date, datetime)):
            as_of = as_of.isoformat()
        elif as_of is not None:
            as_of = str(as_of)
        items.append(
            {
                "label": label,
                "status": status,
                "detail": detail,
                "as_of": as_of,
            }
        )
        if blocks:
            blocked_reasons.append(f"{label}：{detail}")
        elif status != "ok":
            warning_reasons.append(f"{label}：{detail}")

    trade_date = _evidence_date(state.get("trade_date"))
    as_of = _evidence_date(result.get("as_of"))
    success = result.get("success") is True
    add_item(
        "策略扫描",
        "ok" if success else "missing",
        "策略扫描成功" if success else "策略扫描结果缺失或未成功",
        result.get("as_of"),
        blocks=not success,
    )

    price = result.get("price")
    price = price if isinstance(price, dict) else {}
    close_value = price.get("close")
    try:
        close = float(close_value)
        close_valid = (
            not isinstance(close_value, bool)
            and math.isfinite(close)
            and close > 0
        )
    except (TypeError, ValueError):
        close = 0.0
        close_valid = False
    add_item(
        "核心收盘价",
        "ok" if close_valid else "missing",
        f"有效收盘价 {close:.2f}" if close_valid else "缺少有效收盘价",
        result.get("as_of"),
        blocks=not close_valid,
    )

    if trade_date is None:
        date_status = "missing"
        date_detail = "分析日缺失或格式无效，无法核验未来数据"
        future = True
    elif as_of is None:
        date_status = "missing"
        date_detail = "行情日期缺失，无法核验未来数据"
        future = True
    elif as_of > trade_date:
        date_status = "missing"
        date_detail = f"行情日期 {as_of.isoformat()} 晚于分析日 {trade_date.isoformat()}"
        future = True
    else:
        date_status = "ok"
        date_detail = f"行情日期 {as_of.isoformat()} 不晚于分析日 {trade_date.isoformat()}"
        future = False
    add_item(
        "数据时点",
        date_status,
        date_detail,
        result.get("as_of"),
        blocks=future,
    )

    report_text = str(state.get("strategy_report") or "")
    result_error = str(result.get("error") or result.get("errors") or "")
    scan_failed = bool(
        result_error
        or "策略扫描失败" in report_text
        or result.get("success") is False
    )
    signals = [
        signal
        for signal in result.get("signals") or []
        if isinstance(signal, dict)
    ]
    failed_signals = [
        signal
        for signal in signals
        if "策略计算失败" in str(signal.get("summary") or "")
    ]
    summary = result.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    # 所有已列策略都失败时无论 summary.total 是否存在都应 blocked：
    # 缺失 total 只说明汇总字段不完整，不能据此把"全部失败"降级成 warning。
    all_strategies_failed = bool(signals) and len(failed_signals) == len(signals)
    add_item(
        "策略计算完整性",
        "missing" if scan_failed or all_strategies_failed else ("warning" if failed_signals else "ok"),
        (
            f"策略扫描失败：{result_error or report_text or '未返回成功结果'}"
            if scan_failed
            else (
                f"全部 {len(signals)} 个策略计算失败，无法形成策略证据"
                if all_strategies_failed
                else (
                    f"{len(failed_signals)} 个策略计算失败，其余结果仍可参考"
                    if failed_signals
                    else "已选策略均完成计算"
                )
            )
        ),
        result.get("as_of"),
        blocks=scan_failed or all_strategies_failed,
    )

    for required in required_reports or ():
        key = str(required or "").strip()
        report_field, label = _REPORT_FIELDS.get(key, (key, key or "分析师报告"))
        report = state.get(report_field)
        failed = _report_failed(report)
        missing = not str(report or "").strip() or failed
        detail = (
            "报告缺失"
            if not str(report or "").strip()
            else ("报告生成失败：" + str(report).strip()[:160] if failed else "报告已生成")
        )
        add_item(label, "missing" if missing else "ok", detail, state.get("trade_date"))

    citations = [
        citation
        for citation in structured.get("citations") or []
        if str(citation or "").strip()
    ]
    add_item(
        "结构化引用",
        "ok" if citations else "missing",
        f"已提供 {len(citations)} 条引用" if citations else "结构化决策缺少引用来源",
        result.get("as_of"),
    )

    if state.get("analysis_completed") is False or state.get("analysis_cancelled") is True:
        add_item(
            "分析完成状态",
            "missing",
            "分析未完成或已取消，不能把部分结果视为成功结论",
            state.get("trade_date"),
            blocks=True,
        )
    if state.get("analysis_error"):
        add_item(
            "分析执行状态",
            "missing",
            f"分析执行异常：{state['analysis_error']}",
            state.get("trade_date"),
            blocks=True,
        )

    final = str(decision or "").upper()
    model = str(model_decision or "").upper()
    if final != model:
        add_item(
            "门禁后决策",
            "warning",
            f"模型原始决策为 {model or '-'}，门禁后决策为 {final or '-'}",
            result.get("as_of"),
        )

    if blocked_reasons:
        level = "blocked"
        summary = "核心数据不足，暂不形成方向判断/暂缓交易：" + "；".join(blocked_reasons)
    elif warning_reasons:
        level = "warning"
        summary = (
            f"核心证据可用，但有 {len(warning_reasons)} 项需复核："
            + "；".join(warning_reasons)
        )
    else:
        level = "ok"
        summary = "核心行情、策略与结构化引用检查通过"
    warnings = blocked_reasons + warning_reasons
    return {
        "level": level,
        "allow_directional": not blocked_reasons,
        "summary": summary,
        "items": items,
        "warnings": warnings,
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
    "apply_evidence_gate",
    "assess_analysis_evidence",
    "build_neutral_decision_plan",
    "direction_of",
    "get_stored_decision",
    "validate_investment_conclusion",
]
