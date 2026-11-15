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

    cfg = (config or DEFAULT_CONFIG).copy()

    with ANALYSIS_LOCK:
        graph = TradingAgentsGraph(config=cfg)
        final_state, decision = graph.propagate(ticker, trade_date, horizon)

    from tradingagents.services.decision_plan import (
        build_decision_plan,
        compare_decisions,
        format_decision_plan,
    )
    from tradingagents.services.decision_schema import (
        format_structured_decision,
        parse_structured_decision,
    )
    from tradingagents.services.fast_preview import preliminary_decision

    structured_decision = final_state.get("decision_structured") or (
        parse_structured_decision(
            final_state.get("final_trade_decision", ""), decision
        )
    )

    strategy_result = final_state.get("strategy_result")
    decision_plan = build_decision_plan(strategy_result, decision)

    # 规则初判与 LLM 最终决策的差异（两者是独立来源，分歧值得显式呈现）
    preliminary = preliminary_decision((strategy_result or {}).get("summary") or {})
    preliminary_plan = build_decision_plan(strategy_result, preliminary)
    decision_diff = compare_decisions(
        preliminary, decision, preliminary_plan, decision_plan
    )

    # 持仓建议：只有当账户里确实持有该标的时才生成。
    # WHY：分析回答的是"买不买"，持仓者关心的是"卖不卖"。把这一步接进
    # 主流程，用户就不必再手动去快捷工具里点一次（实际上很少有人会点）。
    from tradingagents.services.position_advisor import advice_for_analysis

    account = f"默认账户@{owner}" if owner else "默认账户"
    position_advice = advice_for_analysis(
        ticker, decision_plan, account=account, owner=owner
    )

    reports = {
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
        decision_text=final_state.get("final_trade_decision", ""),
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
            "position_advice": position_advice,
        },
    )

    notify_result = {}
    if notify:
        title, text = build_report_notification(
            {
                "ticker": ticker,
                "trade_date": trade_date,
                "decision": decision,
                "decision_text": final_state.get("final_trade_decision", ""),
            }
        )
        notify_result = send_notification(title, text)

    return {
        "report_id": report_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "decision": decision,
        "decision_plan": decision_plan,
        "decision_diff": decision_diff,
        "decision_structured": structured_decision,
        "position_advice": position_advice,
        "final_state": final_state,
        "notification": notify_result,
    }
