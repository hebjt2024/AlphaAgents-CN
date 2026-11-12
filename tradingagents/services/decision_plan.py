"""结构化决策卡：把策略共识 + 最终决策转成可执行的参考位。

注意：目标价/止损/仓位是基于 ATR 与策略共识的规则化估算，
不是投资建议，也不代表组合经理原文中的精确点位。
"""

from __future__ import annotations


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(max(low, min(high, round(value))))


def build_decision_plan(
    strategy_result: dict | None,
    decision: str,
    risk_per_trade: float = 0.01,
    max_position: float = 0.20,
) -> dict:
    """根据策略扫描结果和最终决策生成结构化参考计划。"""
    result = strategy_result or {}
    price = result.get("price", {}) or {}
    summary = result.get("summary", {}) or {}

    close = float(price.get("close") or 0)
    atr_value = float(price.get("atr") or 0)
    high20 = float(price.get("recent_high20") or close)
    low20 = float(price.get("recent_low20") or close)
    net_score = float(summary.get("net_score") or 0)
    strength = float(summary.get("strength") or 0)
    triggered = int(summary.get("triggered") or 0)
    consensus = summary.get("consensus") or "中性"

    decision = (decision or "HOLD").upper()
    if decision in ("BUY", "OVERWEIGHT"):
        bias = 1
    elif decision in ("SELL", "UNDERWEIGHT"):
        bias = -1
    else:
        bias = 0

    # 综合参考分：策略净分 + 决策方向 + 共识强度
    score = (
        50
        + max(-20, min(20, net_score * 0.4))
        + bias * 15
        + min(10, strength * 0.1)
    )
    score = _clamp(score)

    # 置信度：共识强度、触发数量、决策方向
    confidence = _clamp(
        strength * 0.6 + min(30, triggered * 6) + (10 if bias else 0)
    )

    stop_loss = take_profit = 0.0
    risk_reward = 0.0
    position_pct = 0.0
    risk_pct = 0.0

    if close > 0:
        atr_distance = atr_value if atr_value > 0 else close * 0.03
        if bias < 0:
            stop_loss = min(high20, close + max(atr_distance * 2, close * 0.03))
            take_profit = max(
                low20, close - max(atr_distance * 3, close * 0.06)
            )
        else:
            stop_loss = max(low20, close - max(atr_distance * 2, close * 0.03))
            take_profit = max(
                high20, close + max(atr_distance * 3, close * 0.06)
            )

        stop_loss = round(stop_loss, 2)
        take_profit = round(take_profit, 2)
        risk = abs(close - stop_loss)
        reward = abs(take_profit - close)
        risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
        risk_pct = risk / close if close > 0 else 0.0
        if bias != 0 and risk_pct > 0:
            position_pct = min(max_position, risk_per_trade / risk_pct)
            position_pct = round(position_pct, 3)

    reasons = [
        f"策略共识：{consensus}（净分 {net_score}，强度 {strength}%）",
        f"触发策略：{triggered} 个",
    ]
    if close > 0:
        reasons.append(
            f"参考价 {close:.2f}，止损 {stop_loss:.2f}，目标 {take_profit:.2f}，风险收益比 {risk_reward:.2f}"
        )
        if bias == 0:
            reasons.append("最终决策为持有/观望，建议仓位按 0 处理")
        else:
            reasons.append(
                f"按单笔风险 {risk_per_trade * 100:.1f}% 估算，建议仓位约 {position_pct * 100:.0f}%（上限 {max_position * 100:.0f}%）"
            )
    else:
        reasons.append("未获取到有效价格，无法计算参考位")

    return {
        "decision": decision,
        "score": score,
        "confidence": confidence,
        "consensus": consensus,
        "entry": round(close, 2) if close else 0.0,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_reward": risk_reward,
        "position_pct": position_pct,
        "risk_pct": round(risk_pct, 4),
        "reasons": reasons,
        "disclaimer": "规则化估算，仅供参考，不构成投资建议。",
    }


def format_decision_plan(plan: dict) -> str:
    """把结构化决策卡转成 Markdown（用于报告中心/导出）。"""
    return "\n".join(
        [
            "### 结构化决策卡",
            f"- **决策**：{plan.get('decision', 'HOLD')}",
            f"- **综合参考分**：{plan.get('score', 0)} / 100",
            f"- **置信度**：{plan.get('confidence', 0)}%",
            f"- **策略共识**：{plan.get('consensus', '中性')}",
            f"- **参考价**：{plan.get('entry', 0)}",
            f"- **止损参考**：{plan.get('stop_loss', 0)}",
            f"- **目标参考**：{plan.get('take_profit', 0)}",
            f"- **风险收益比**：{plan.get('risk_reward', 0)}",
            f"- **建议仓位**：{float(plan.get('position_pct', 0)) * 100:.0f}%",
            "",
            "> " + plan.get("disclaimer", "仅供参考。"),
        ]
    )
