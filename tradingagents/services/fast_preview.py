"""快速预览：不调用 LLM，先用策略扫描给出盘前/盘中初判。

用于“先出快速预览 + 后台继续深度分析”的交互：
  - 秒级返回规则化结论
  - 明确标注为预览，不替代最终 LLM 决策
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _preliminary_decision(summary: dict[str, Any]) -> str:
    net = float(summary.get("net_score") or 0)
    strength = float(summary.get("strength") or 0)
    if net >= 20 and strength >= 25:
        return "BUY"
    if net >= 12:
        return "OVERWEIGHT"
    if net <= -20 and strength >= 25:
        return "SELL"
    if net <= -12:
        return "UNDERWEIGHT"
    return "HOLD"


def build_fast_preview(
    ticker: str,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
) -> dict[str, Any]:
    """基于策略扫描生成规则化快速预览（不调用 LLM）。"""
    from tradingagents.services.decision_plan import build_decision_plan
    from tradingagents.strategies.scanner import scan_all_strategies

    result = scan_all_strategies(ticker, end_date, strategy_keys)
    summary = result.get("summary") or {}
    price = result.get("price") or {}
    decision = _preliminary_decision(summary)
    plan = build_decision_plan(result, decision)
    triggered = [
        signal
        for signal in result.get("signals", [])
        if signal.get("triggered")
    ]
    top_signals = [
        {
            "name": signal.get("name"),
            "direction": signal.get("direction"),
            "score": signal.get("score"),
            "summary": signal.get("summary"),
        }
        for signal in triggered[:4]
    ]
    lines = [
        f"## 快速预览 {result.get('ticker', ticker)} · {result.get('as_of', '')}",
        "",
        f"- 规则初判：**{decision}**",
        f"- 策略共识：{summary.get('consensus', '中性')}"
        f"（净分 {summary.get('net_score', 0)}，强度 {summary.get('strength', 0)}%）",
        f"- 参考价：{price.get('close', '-')}（{price.get('change_pct', 0)}%）",
        f"- 触发信号：{summary.get('triggered', 0)} 个",
    ]
    if top_signals:
        lines.append(
            "- 主要信号："
            + "、".join(
                f"{item['name']}（{item['score']} 分）"
                for item in top_signals
            )
        )
    lines.extend(
        [
            "",
            "> 快速预览为规则化初判，不调用 LLM，不构成投资建议；"
            "请以深度分析结果为准。",
        ]
    )
    return {
        "success": True,
        "mode": "fast_preview",
        "ticker": result.get("ticker", ticker),
        "as_of": result.get("as_of", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "price": price,
        "summary": summary,
        "preliminary_decision": decision,
        "decision_plan": plan,
        "top_signals": top_signals,
        "markdown": "\n".join(lines),
        "disclaimer": "快速预览为规则化初判，仅供研究参考，不构成投资建议。",
    }
