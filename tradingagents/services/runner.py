"""无界面分析服务：供定时任务 / API / 命令行复用。"""

from __future__ import annotations

from typing import Any

from tradingagents import bootstrap as _bootstrap  # noqa: F401
from tradingagents.notify import build_report_notification, send_notification
from tradingagents.runtime import ANALYSIS_LOCK
from tradingagents.storage import save_report


def run_analysis(
    ticker: str,
    trade_date: str,
    config: dict[str, Any] | None = None,
    source: str = "scheduler",
    notify: bool = True,
    horizon: str = "medium",
    owner: str = "",
) -> dict[str, Any]:
    """执行一次完整分析并持久化、可选推送通知。"""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.storage.audit_store import append_audit_event

    cfg = (config or DEFAULT_CONFIG).copy()
    cfg["run_source"] = source

    try:
        with ANALYSIS_LOCK:
            graph = TradingAgentsGraph(config=cfg)
            final_state, decision = graph.propagate(ticker, trade_date, horizon)
    except Exception as exc:
        append_audit_event(
            action=f"分析失败：{ticker}",
            detail={
                "source": source,
                "trade_date": trade_date,
                "error": f"{type(exc).__name__}: {exc}",
            },
            status=500,
        )
        raise

    from tradingagents.services.decision_plan import (
        build_decision_plan,
        compare_decisions,
        format_decision_plan,
    )
    from tradingagents.services.decision_schema import (
        format_structured_decision,
        parse_structured_decision,
    )
    from tradingagents.services.decision_validation import (
        BLOCKED_DECISION_TEXT,
        assess_analysis_evidence,
        build_neutral_decision_plan,
        get_stored_decision,
        validate_investment_conclusion,
    )
    from tradingagents.services.fast_preview import preliminary_decision
    from tradingagents.services.plain_language import (
        build_plain_summary,
        format_plain_summary,
    )

    structured_decision = final_state.get("decision_structured") or (
        parse_structured_decision(
            final_state.get("final_trade_decision", ""), decision
        )
    )
    final_state["decision_structured"] = structured_decision
    model_decision = final_state.get("model_decision") or decision
    evidence_assessment = final_state.get("evidence_assessment")
    if not evidence_assessment:
        try:
            evidence_assessment = assess_analysis_evidence(
                final_state,
                model_decision,
                decision,
            )
        except Exception as exc:
            evidence_assessment = {
                "level": "blocked",
                "allow_directional": False,
                "summary": f"证据评估失败，暂不形成方向判断：{type(exc).__name__}: {exc}",
                "items": [],
                "warnings": ["证据评估失败"],
            }
    metrics_value = getattr(graph, "last_run_metrics", None)
    if metrics_value is None:
        metrics_value = final_state.get("run_metrics")
    if hasattr(metrics_value, "to_dict"):
        metrics_value = metrics_value.to_dict()
    run_metrics = metrics_value if isinstance(metrics_value, dict) else {}
    totals = run_metrics.get("totals")
    totals = dict(totals) if isinstance(totals, dict) else {}
    totals.setdefault("call_count", 0)
    totals.setdefault("total_tokens", 0)
    totals.setdefault("estimated_cost", 0.0)
    run_metrics["totals"] = totals
    run_metrics.setdefault("duration_ms", 0.0)
    run_metrics.setdefault("currency", "AUTO")

    blocked = (
        evidence_assessment.get("level") == "blocked"
        or evidence_assessment.get("allow_directional") is False
    )
    model_decision_text = str(
        final_state.get("model_decision_text")
        or final_state.get("final_trade_decision")
        or ""
    )
    if blocked:
        # 证据门禁未通过：最终状态必须净化，但模型原文单独保留供审计。
        decision = "HOLD"
        structured_decision = dict(structured_decision or {})
        structured_decision["decision"] = "HOLD"
        structured_decision["decision_source"] = "evidence_gate"
        structured_decision["source"] = "evidence_gate"
        final_state["decision"] = "HOLD"
        final_state["model_decision_text"] = model_decision_text
        final_state["final_trade_decision"] = BLOCKED_DECISION_TEXT
        final_state["decision_structured"] = structured_decision

    strategy_result = final_state.get("strategy_result")
    decision_plan = (
        build_neutral_decision_plan()
        if blocked
        else build_decision_plan(strategy_result, decision)
    )
    decision_text = final_state.get("final_trade_decision", "")
    decision_text_plain = str(final_state.get("decision_text_plain") or "")

    # 规则初判与 LLM 最终决策的差异（两者是独立来源，分歧值得显式呈现）
    if blocked:
        preliminary = "HOLD"
        preliminary_plan = build_neutral_decision_plan()
    else:
        preliminary = preliminary_decision((strategy_result or {}).get("summary") or {})
        preliminary_plan = build_decision_plan(strategy_result, preliminary)
    decision_diff = compare_decisions(
        preliminary, decision, preliminary_plan, decision_plan
    )

    # 投资结论校验（决策对齐）：汇总最终决策与各来源的矛盾、与上一次结论的方向翻转。
    # 结果随 report meta 持久化，供 WebUI / CLI 展示。
    decision_validation = validate_investment_conclusion(
        decision=decision,
        decision_plan=decision_plan,
        structured_decision=structured_decision,
        decision_diff=decision_diff,
        stored_decision=get_stored_decision(ticker),
    )

    # 持仓建议：只有当账户里确实持有该标的时才生成。
    # WHY：分析回答的是"买不买"，持仓者关心的是"卖不卖"。把这一步接进
    # 主流程，用户就不必再手动去快捷工具里点一次（实际上很少有人会点）。
    from tradingagents.services.position_advisor import advice_for_analysis

    account = f"默认账户@{owner}" if owner else "默认账户"
    position_advice = advice_for_analysis(
        ticker, decision_plan, account=account, owner=owner
    )
    if blocked:
        position_advice = None

    # 人话层：定时任务的接收者多半不是专业读者，
    # 通知里必须先给"结论 + 该做什么"，而不是一整篇行话报告。
    plain_summary = build_plain_summary(
        ticker=ticker,
        decision=decision,
        plan=decision_plan,
        structured=structured_decision,
        decision_text=decision_text,
        position_advice=position_advice,
        evidence=evidence_assessment,
    )
    notification_text = decision_text if blocked else (decision_text_plain or decision_text)
    notification_plain = plain_summary
    if blocked:
        # 通知正文里连风险说明也不能出现原始方向词，避免读者误读成操作建议。
        notification_plain = dict(plain_summary)
        notification_plain["risks"] = [
            item
            for item in plain_summary.get("risks") or []
            if not any(
                token in str(item)
                for token in ("BUY", "SELL", "买入", "卖出", "增持", "减持")
            )
        ] or ["核心数据不足，暂缓所有方向性交易操作"]

    reports = {
        "plain_summary": format_plain_summary(plain_summary),
        "market_report": final_state.get("market_report", ""),
        "strategy_report": final_state.get("strategy_report", ""),
        "sentiment_report": final_state.get("sentiment_report", ""),
        "news_report": final_state.get("news_report", ""),
        "fundamentals_report": final_state.get("fundamentals_report", ""),
        "investment_plan": final_state.get("investment_plan", ""),
        "trader_investment_plan": final_state.get("trader_investment_plan", ""),
        "final_trade_decision": final_state.get("final_trade_decision", ""),
        "decision_plan": format_decision_plan(decision_plan),
        "decision_structured": format_structured_decision(structured_decision),
    }
    if position_advice:
        reports["position_advice"] = position_advice["markdown"]

    report_id = save_report(
        ticker=ticker,
        trade_date=trade_date,
        decision=decision,
        decision_text=decision_text,
        reports=reports,
        source=source,
        owner=owner,
        meta={
            "llm_provider": cfg.get("llm_provider"),
            "deep_think_llm": cfg.get("deep_think_llm"),
            "quick_think_llm": cfg.get("quick_think_llm"),
            "horizon": horizon,
            "decision_plan": decision_plan,
            "decision_diff": decision_diff,
            "decision_structured": structured_decision,
            "model_decision": model_decision,
            "model_decision_text": model_decision_text,
            "decision_text_plain": decision_text_plain,
            "evidence_assessment": evidence_assessment,
            "run_metrics": run_metrics,
            "position_advice": position_advice,
            "plain_summary": plain_summary,
            "decision_validation": decision_validation,
        },
    )

    notify_result = {}
    if notify:
        title, text = build_report_notification(
            {
                "ticker": ticker,
                "trade_date": trade_date,
                "decision": decision,
                "decision_text": notification_text,
                "plain_summary": notification_plain,
            }
        )
        notify_result = send_notification(title, text)

    append_audit_event(
        action=f"分析：{ticker}",
        detail={
            "source": source,
            "trade_date": trade_date,
            "horizon": horizon,
            "owner": owner,
            "decision": decision,
            "report_id": report_id,
            "validation_level": decision_validation.get("level"),
        },
        status=200,
    )
    return {
        "report_id": report_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "decision": decision,
        "decision_plan": decision_plan,
        "decision_diff": decision_diff,
        "decision_structured": structured_decision,
        "model_decision": model_decision,
        "model_decision_text": model_decision_text,
        "decision_text": decision_text,
        "decision_text_plain": decision_text_plain,
        "evidence_assessment": evidence_assessment,
        "run_metrics": run_metrics,
        "position_advice": position_advice,
        "plain_summary": plain_summary,
        "decision_validation": decision_validation,
        "final_state": final_state,
        "notification": notify_result,
    }
