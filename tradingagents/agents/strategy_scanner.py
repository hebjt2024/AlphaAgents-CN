"""策略扫描节点：在分析师之前运行本次选中的策略组合。

这样策略信号会成为分析流程中的独立阶段，而不是完全依赖 LLM 是否调用工具。
"""

from __future__ import annotations

from tradingagents.dataflows.config import get_config


def create_strategy_scanner_node():
    def strategy_scanner_node(state):
        ticker = state.get("company_of_interest", "")
        trade_date = state.get("trade_date")
        keys = get_config().get("strategy_keys") or None
        result = {}
        try:
            from tradingagents.strategies import (
                format_strategy_report,
                scan_all_strategies,
            )

            result = scan_all_strategies(ticker, trade_date, keys)
            report = format_strategy_report(result)
        except Exception as exc:
            report = (
                f"策略扫描失败（{type(exc).__name__}: {exc}）。"
                "后续分析将仅基于行情、资金与新闻数据。"
            )
        return {"strategy_report": report, "strategy_result": result}

    return strategy_scanner_node
