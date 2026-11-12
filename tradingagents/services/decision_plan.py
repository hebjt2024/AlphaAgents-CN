"""结构化决策卡：把策略共识 + 最终决策转成可执行的参考位。

注意：目标价/止损/仓位是基于 ATR 与策略共识的规则化估算，
不是投资建议，也不代表组合经理原文中的精确点位。

P0 修正（此前的实现存在四处会误导使用者的问题）：

1. **止损方向反了。** 旧实现做多时取 ``max(low20, close - 2*ATR)``，
   永远落在"支撑位"与"波动止损"中*更近*的那个上，等于把止损恰好贴在
   20 日最低点——那正是止损盘密集区；更糟的是止损越近 ``risk_pct``
   越小，而 ``position_pct = risk_per_trade / risk_pct`` 会让**仓位
   反而变大**。现在做多时止损取 ``min(low20, close - 2*ATR)``，放在
   支撑与波动止损的*外侧*（更保守 → 风险更大 → 仓位更小）。

2. **目标价过于乐观。** 旧实现做多时取 ``max(high20, close + 3*ATR)``，
   相当于默认"一定能摸到 20 日最高点"，系统性虚高风险收益比。现在
   统一取保守的一侧：做多 ``min(high20, close + 3*ATR)``、做空
   ``max(low20, close - 3*ATR)``。

3. **BUY 与 OVERWEIGHT 完全等价。** 旧实现两者映射到同一个 bias，
   最终给出逐字节相同的评分、置信度、止损、目标与仓位，"分级"只是
   标签。现在 OVERWEIGHT/UNDERWEIGHT 使用折减后的方向权重。

4. **单条规则即可给出高置信度与满仓。** 现在引入最小触发数量门槛、
   规则/LLM 方向冲突检测，冲突时不给仓位并显式标注。
"""

from __future__ import annotations

from tradingagents.strategies.base import (
    CONSENSUS_NET_THRESHOLD,
    MIN_SIGNALS_FOR_FULL_STRENGTH,
    MIN_TRIGGERED_FOR_POSITION,
)

#: 决策 → 方向权重（幅度）。买入/卖出为满强度，超配/低配为折减强度。
DECISION_BIAS: dict[str, float] = {
    "BUY": 1.0,
    "OVERWEIGHT": 0.6,
    "HOLD": 0.0,
    "UNDERWEIGHT": -0.6,
    "SELL": -1.0,
}

#: ATR 兜底比例（ATR 缺失时按收盘价的该比例估算波动）。
ATR_FALLBACK_PCT = 0.03

#: 止损最小距离（占收盘价比例），避免 ATR 极小时止损贴到 0。
MIN_STOP_PCT = 0.03

#: 目标最小距离（占收盘价比例）。
MIN_TARGET_PCT = 0.06

#: ATR 止损倍数。
ATR_STOP_MULT = 2.0

#: ATR 目标倍数。
ATR_TARGET_MULT = 3.0

#: 止损让开支撑位的缓冲（占收盘价比例）。
#: 止损恰好落在 20 日最低点上是最常见的被扫损位置，需要留出一点空间。
STOP_BUFFER_PCT = 0.005

#: 方向标签。
DIRECTION_LABELS = {1: "偏多", -1: "偏空", 0: "中性"}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(max(low, min(high, round(value))))


def build_decision_plan(
    strategy_result: dict | None,
    decision: str,
    risk_per_trade: float = 0.01,
    max_position: float = 0.20,
    min_triggered: int = MIN_TRIGGERED_FOR_POSITION,
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
    # 独立信号族数量：同族的多个策略（例如均线金叉与 MACD 金叉）并不是
    # 多个独立假设，仓位门槛与置信度必须按族计数。
    family_count = int(summary.get("family_count") or 0) or triggered
    consensus = summary.get("consensus") or "中性"

    decision = (decision or "HOLD").upper()
    bias = DECISION_BIAS.get(decision, 0.0)

    # 规则冲突检测：最终决策方向与策略共识方向相反且幅度显著时，
    # 视为冲突：不给仓位，并在理由里显式说明。
    conflict = (
        (bias > 0 and net_score <= -CONSENSUS_NET_THRESHOLD)
        or (bias < 0 and net_score >= CONSENSUS_NET_THRESHOLD)
    )

    # 综合参考分：策略净分 + 决策方向 + 共识强度
    #
    # 广度因子：net_score 是**族内去重**后的净分，一个族最多贡献一个族的
    # 分数。若不按族数折减，"只有趋势族一个信号"也会拿到接近满分的综合分，
    # 而综合分本应表达"多路独立证据的一致程度"。
    breadth = min(1.0, max(0, family_count) / MIN_SIGNALS_FOR_FULL_STRENGTH)
    # 冲突时不再计入方向加分，避免"规则看空、模型看多"却仍得高分。
    bias_bonus = 0.0 if conflict else bias * 15
    score = (
        50
        + max(-20, min(20, net_score * 0.4)) * breadth
        + bias_bonus
        + min(10, strength * 0.1) * breadth
    )
    score = _clamp(score)

    # 置信度：共识强度、独立信号族数量、决策方向
    confidence = _clamp(
        strength * 0.6 + min(30, family_count * 6) + (10 if bias else 0)
    )
    if conflict:
        # 冲突时置信度不因方向而提升
        confidence = _clamp(strength * 0.6 + min(30, family_count * 6))

    stop_loss = take_profit = 0.0
    risk_reward = 0.0
    position_pct = 0.0
    risk_pct = 0.0

    if close > 0:
        atr_distance = atr_value if atr_value > 0 else close * ATR_FALLBACK_PCT
        stop_distance = max(atr_distance * ATR_STOP_MULT, close * MIN_STOP_PCT)
        target_distance = max(
            atr_distance * ATR_TARGET_MULT, close * MIN_TARGET_PCT
        )
        if bias < 0:
            # 卖出 / 低配：止损在阻力上方，目标取保守（更高）的一侧
            stop_loss = max(high20, close + stop_distance)
            take_profit = max(low20, close - target_distance)
        else:
            # 买入 / 超配 / 观望：止损在支撑下方，目标取保守（更低）的一侧
            support = min(low20, close - stop_distance)
            stop_loss = support - close * STOP_BUFFER_PCT
            take_profit = min(high20, close + target_distance)

        stop_loss = round(stop_loss, 2)
        take_profit = round(take_profit, 2)
        risk = abs(close - stop_loss)
        reward = abs(take_profit - close)
        risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
        risk_pct = risk / close if close > 0 else 0.0

    # 仓位门槛：
    #   1) 卖出/低配 → A 股不可做空，仓位一律按 0 处理（含义是减仓/清仓）
    #   2) 触发信号数量不足 → 不给仓位
    #   3) 规则与决策方向冲突 → 不给仓位
    position_blocked = ""
    if bias < 0:
        position_blocked = "A 股不支持做空，卖出/低配对应减仓或清仓，仓位按 0 处理"
    elif bias != 0 and risk_pct > 0:
        if family_count < min_triggered:
            position_blocked = (
                f"独立信号族仅 {family_count} 个，低于仓位门槛 {min_triggered} 个"
            )
        elif conflict:
            position_blocked = "规则共识与最终决策方向冲突"
        else:
            # 方向强度决定目标仓位：满强度（买入/卖出）用满额，
            # 折减强度（超配/低配）按比例缩减。
            base_position = min(max_position, risk_per_trade / risk_pct)
            position_pct = round(base_position * abs(bias), 3)

    direction_sign = 1 if bias > 0 else (-1 if bias < 0 else 0)

    reasons = [
        f"策略共识：{consensus}（净分 {net_score}，强度 {strength}%）",
        f"触发策略：{triggered} 个（去重后独立信号族 {family_count} 个）",
    ]
    if conflict:
        reasons.append(
            f"⚠ 规则共识（{consensus}，净分 {net_score}）与最终决策"
            f"（{decision}）方向冲突，已暂停给出仓位建议"
        )
    if close > 0:
        reasons.append(
            f"参考价 {close:.2f}，止损 {stop_loss:.2f}，目标 {take_profit:.2f}，风险收益比 {risk_reward:.2f}"
        )
        if bias == 0:
            reasons.append("最终决策为持有/观望，建议仓位按 0 处理")
        elif position_blocked:
            reasons.append(f"暂不给出仓位：{position_blocked}")
        else:
            reasons.append(
                f"按单笔风险 {risk_per_trade * 100:.1f}% 估算，建议仓位约 {position_pct * 100:.0f}%（上限 {max_position * 100:.0f}%）"
            )
    else:
        reasons.append("未获取到有效价格，无法计算参考位")

    return {
        "decision": decision,
        "direction": DIRECTION_LABELS[direction_sign],
        "score": score,
        "confidence": confidence,
        "consensus": consensus,
        "conflict": conflict,
        "entry": round(close, 2) if close else 0.0,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_reward": risk_reward,
        "position_pct": position_pct,
        "risk_pct": round(risk_pct, 4),
        "triggered": triggered,
        "family_count": family_count,
        "reasons": reasons,
        "disclaimer": "规则化估算，仅供参考，不构成投资建议。",
    }


def compare_decisions(
    preliminary: str,
    final: str,
    preliminary_plan: dict | None = None,
    final_plan: dict | None = None,
) -> dict:
    """对比规则初判与最终决策，标出两者的分歧。

    规则初判（策略扫描）与组合经理的 LLM 结论是两个独立来源。此前系统
    只展示最终结论，用户看到"规则说偏多、结论说卖出"时无从察觉 ——
    这类分歧恰恰是最值得关注的信号。

    返回
    ----
    dict，包含 ``has_diff`` / ``decision_changed`` / ``changed_fields`` /
    ``score_delta`` / ``confidence_delta`` / ``position_delta`` /
    ``direction_changed`` / ``note``。
    """
    preliminary = (preliminary or "HOLD").upper()
    final = (final or "HOLD").upper()

    changed_fields: list[str] = []
    score_delta = 0.0
    confidence_delta = 0.0
    position_delta = 0.0
    direction_changed = False

    if preliminary_plan and final_plan:
        for field in ("score", "confidence", "position_pct", "risk_reward"):
            before = float(preliminary_plan.get(field) or 0)
            after = float(final_plan.get(field) or 0)
            if abs(after - before) > 1e-9:
                changed_fields.append(field)
        score_delta = float(final_plan.get("score") or 0) - float(
            preliminary_plan.get("score") or 0
        )
        confidence_delta = float(final_plan.get("confidence") or 0) - float(
            preliminary_plan.get("confidence") or 0
        )
        position_delta = float(final_plan.get("position_pct") or 0) - float(
            preliminary_plan.get("position_pct") or 0
        )
        direction_changed = preliminary_plan.get("direction") != final_plan.get(
            "direction"
        )

    decision_changed = preliminary != final
    has_diff = decision_changed or bool(changed_fields) or direction_changed

    note = ""
    if decision_changed:
        note = (
            f"规则初判为 {preliminary}，最终决策为 {final}，两者方向不一致，"
            "请重点核对分歧原因。"
        )
    elif has_diff:
        note = "规则初判与最终决策方向一致，但评分/仓位等参数存在差异。"

    return {
        "has_diff": has_diff,
        "decision_changed": decision_changed,
        "direction_changed": direction_changed,
        "preliminary_decision": preliminary,
        "final_decision": final,
        "changed_fields": changed_fields,
        "score_delta": round(score_delta, 2),
        "confidence_delta": round(confidence_delta, 2),
        "position_delta": round(position_delta, 4),
        "note": note,
    }


def format_decision_plan(plan: dict) -> str:
    """把结构化决策卡转成 Markdown（用于报告中心/导出）。"""
    conflict_line = ""
    if plan.get("conflict"):
        conflict_line = "- **方向冲突**：规则共识与最终决策方向相反，仓位建议已暂停\n"
    return "\n".join(
        [
            "### 结构化决策卡",
            f"- **决策**：{plan.get('decision', 'HOLD')}"
            f"（方向 {plan.get('direction', '中性')}）",
            f"- **综合参考分**：{plan.get('score', 0)} / 100",
            f"- **置信度**：{plan.get('confidence', 0)}%",
            f"- **策略共识**：{plan.get('consensus', '中性')}",
            f"- **参考价**：{plan.get('entry', 0)}",
            f"- **止损参考**：{plan.get('stop_loss', 0)}",
            f"- **目标参考**：{plan.get('take_profit', 0)}",
            f"- **风险收益比**：{plan.get('risk_reward', 0)}",
            f"- **建议仓位**：{float(plan.get('position_pct', 0)) * 100:.0f}%",
            conflict_line.rstrip("\n") if conflict_line else "",
            "",
            "> " + plan.get("disclaimer", "仅供参考。"),
        ]
    )
