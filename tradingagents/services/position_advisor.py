"""持仓顾问：回答“我手里这只现在该怎么办”。

决策卡（``services/decision_plan.py``）只回答“要不要买、买多少”，
对已经持仓的用户没有落点。本模块把决策卡与真实持仓、组合风控对齐，
给出建仓 / 加仓 / 减仓 / 清仓 / 持有 / 观望六种动作，并把 A 股的
整手、T+1、涨跌停约束显式写进建议里。

“目标持仓”的推导方式
--------------------
决策卡里的 ``position_pct`` 是**目标仓位**；而 ``PortfolioRiskEngine``
的 ``adjusted_position_pct`` 语义是“本次允许增加的仓位”。因此：

- 无持仓：目标权重 = 风控调整后的可建仓权重；
- 已有持仓且当前权重 <= 决策目标：目标权重 = min(决策目标,
  当前权重 + 风控允许加仓)；
- 已有持仓且当前权重 > 决策目标：目标权重 = 决策目标（减仓方向
  不受“加仓上限”约束，但绝不放大到超过决策目标）。

所有结果均为规则化估算，不构成投资建议。
"""

from __future__ import annotations

import math
from typing import Any

from tradingagents.dataflows.ashare_rules import (
    is_a_share_code,
    normalize_code,
    price_limit_pct,
)

from .risk_engine import PortfolioRiskEngine, PositionView, RiskLimits

_SEVERITY_ORDER = {"danger": 0, "warn": 1, "info": 2}

_CODE_LABELS = {
    "stop_loss_hit": "止损触发",
    "stop_loss_near": "接近止损",
    "deep_loss": "浮亏过大",
    "no_stop_loss": "未设止损",
    "take_profit_hit": "止盈触发",
    "take_profit_near": "接近止盈",
    "limit_down": "跌停",
    "limit_up": "涨停",
}

#: 判断“封板”时允许的误差（价格按 0.01 元取整会产生小幅偏差）。
_LIMIT_TOLERANCE = 0.002

#: 止损 / 止盈的“接近”阈值。
_NEAR_STOP_RATIO = 1.02
_NEAR_TP_RATIO = 0.98

#: 未设止损时判定“浮亏过大”的阈值。
_DEEP_LOSS_PCT = -0.15


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _field(position: Any, name: str, default: Any = None) -> Any:
    """兼容 ``PositionView`` 与普通 dict 行，缺失字段一律返回默认值。"""
    if isinstance(position, dict):
        return position.get(name, default)
    return getattr(position, name, default)


def _normalized(ticker: str) -> str:
    return normalize_code(str(ticker or "")) or str(ticker or "").strip()


def _same_ticker(left: Any, right: Any) -> bool:
    a = _normalized(left)
    b = _normalized(right)
    if a and b:
        return a.upper() == b.upper()
    return not a and not b


def _limit_state(
    code: str,
    name: str,
    last_price: float,
    prev_close: Any,
) -> tuple[str, float, float]:
    """返回 (状态, 涨跌幅限制, 当日涨跌幅)；缺少昨收时状态为空。"""
    price = _number(last_price)
    previous = _number(prev_close)
    if price <= 0 or previous <= 0:
        return "", 0.0, 0.0
    limit = price_limit_pct(code, name)
    change = price / previous - 1
    if change >= limit - _LIMIT_TOLERANCE:
        return "up", limit, change
    if change <= -limit + _LIMIT_TOLERANCE:
        return "down", limit, change
    return "", limit, change


def check_position_alerts(
    positions: list[PositionView] | list[dict[str, Any]],
    today: str | None = None,
) -> list[dict[str, Any]]:
    """检查持仓的止损、止盈、浮亏与涨跌停状态。

    ``today`` 仅用于提示文案，不参与任何日期推算。缺失字段不会抛错。
    """
    alerts: list[dict[str, Any]] = []

    for position in positions or []:
        ticker = _normalized(_field(position, "ticker", ""))
        name = str(_field(position, "name", "") or "")
        quantity = abs(_number(_field(position, "quantity")))
        price = _number(_field(position, "last_price"))
        avg_cost = _number(_field(position, "avg_cost"))
        stop_loss = _number(_field(position, "stop_loss"))
        take_profit = _number(_field(position, "take_profit"))
        pnl_pct = price / avg_cost - 1 if avg_cost > 0 and price > 0 else 0.0

        def add(
            alert_code: str,
            level: str,
            message: str,
            detail: dict[str, Any] | None = None,
            ticker: str = ticker,
            name: str = name,
        ) -> None:
            alerts.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "code": alert_code,
                    "level": level,
                    "message": message,
                    "detail": detail or {},
                }
            )

        if quantity <= 0:
            continue

        if stop_loss > 0 and price > 0:
            if price <= stop_loss:
                add(
                    "stop_loss_hit",
                    "danger",
                    f"现价 {price:.2f} 已跌破止损价 {stop_loss:.2f}"
                    f"（{pnl_pct * 100:+.1f}%），建议执行止损纪律。",
                    {
                        "last_price": price,
                        "stop_loss": stop_loss,
                        "unrealised_pnl_pct": round(pnl_pct, 4),
                    },
                )
            elif price <= stop_loss * _NEAR_STOP_RATIO:
                distance = price / stop_loss - 1
                add(
                    "stop_loss_near",
                    "warn",
                    f"现价 {price:.2f} 距止损价 {stop_loss:.2f} 仅 "
                    f"{distance * 100:.1f}%，请留意。",
                    {
                        "last_price": price,
                        "stop_loss": stop_loss,
                        "distance_pct": round(distance, 4),
                    },
                )

        if take_profit > 0 and price > 0:
            if price >= take_profit:
                add(
                    "take_profit_hit",
                    "info",
                    f"现价 {price:.2f} 已达到止盈价 {take_profit:.2f}，"
                    "可考虑分批止盈。",
                    {
                        "last_price": price,
                        "take_profit": take_profit,
                        "unrealised_pnl_pct": round(pnl_pct, 4),
                    },
                )
            elif price >= take_profit * _NEAR_TP_RATIO:
                distance = take_profit / price - 1
                add(
                    "take_profit_near",
                    "info",
                    f"现价 {price:.2f} 距止盈价 {take_profit:.2f} 仅 "
                    f"{distance * 100:.1f}%，关注冲高回落。",
                    {
                        "last_price": price,
                        "take_profit": take_profit,
                        "distance_pct": round(distance, 4),
                    },
                )

        if stop_loss <= 0:
            if pnl_pct <= _DEEP_LOSS_PCT:
                add(
                    "deep_loss",
                    "warn",
                    f"浮亏 {pnl_pct * 100:.1f}% 且未设止损，建议补充止损纪律。",
                    {
                        "avg_cost": avg_cost,
                        "last_price": price,
                        "unrealised_pnl_pct": round(pnl_pct, 4),
                    },
                )
            add(
                "no_stop_loss",
                "warn",
                "未设置止损价，回撤风险不受控。",
                {"avg_cost": avg_cost, "last_price": price},
            )

        state, limit, change = _limit_state(
            ticker,
            name,
            price,
            _field(position, "prev_close"),
        )
        if state == "up":
            add(
                "limit_up",
                "info",
                f"当前处于涨停（{change * 100:+.1f}%，限制 {limit * 100:.0f}%），"
                "买入可能无法成交。",
                {"limit_pct": limit, "change_pct": round(change, 4)},
            )
        elif state == "down":
            add(
                "limit_down",
                "warn",
                f"当前处于跌停（{change * 100:+.1f}%，限制 {limit * 100:.0f}%），"
                "卖出可能无法成交，需准备次日处理预案。",
                {"limit_pct": limit, "change_pct": round(change, 4)},
            )

    alerts.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("level") or "info"), 3),
            str(item.get("ticker") or ""),
            str(item.get("code") or ""),
        )
    )
    return alerts


def summarize_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """统计提醒数量并生成一行中文摘要。"""
    items = list(alerts or [])
    counts = {"danger": 0, "warn": 0, "info": 0}
    code_counts: dict[str, int] = {}
    for alert in items:
        level = str(alert.get("level") or "info")
        counts[level] = counts.get(level, 0) + 1
        code = str(alert.get("code") or "")
        code_counts[code] = code_counts.get(code, 0) + 1

    parts = [
        f"{code_counts[code]} 条{label}"
        for code, label in _CODE_LABELS.items()
        if code_counts.get(code)
    ]
    total = len(items)
    if parts:
        headline = f"{total} 条提醒：" + "、".join(parts)
    elif total:
        headline = f"{total} 条提醒"
    else:
        headline = "暂无持仓提醒"

    return {
        "total": total,
        "danger": counts.get("danger", 0),
        "warn": counts.get("warn", 0),
        "info": counts.get("info", 0),
        "headline": headline,
    }


def advise_position(
    ticker: str,
    plan: dict | None,
    account: str | None = None,
    *,
    position: PositionView | None = None,
    equity: float | None = None,
    cash: float | None = None,
    limits: RiskLimits | None = None,
    lot_size: int = 100,
    today: str | None = None,
) -> dict:
    """把决策卡换算成针对当前持仓的可执行动作。

    返回的 ``action`` 只可能是：建仓 / 加仓 / 减仓 / 清仓 / 持有 / 观望。
    A 股不支持做空，因此 ``target_qty`` 永远不小于 0，减仓方向也不会
    出现负数量。
    """
    plan = plan or {}
    code = _normalized(ticker)
    lot = max(1, int(lot_size or 100))

    current: PositionView | None = position
    position_list: list[PositionView] = []
    account_row: dict[str, Any] = {}

    if account is not None:
        from .portfolio import ensure_account, load_positions

        account_row = ensure_account(account)
        loaded = load_positions(account)
        if current is None:
            current = next(
                (item for item in loaded if _same_ticker(item.ticker, code)),
                None,
            )
        position_list = list(loaded)
        if current is not None and not any(
            _same_ticker(item.ticker, code) for item in position_list
        ):
            position_list.append(current)
    elif current is not None:
        position_list = [current]

    if equity is None:
        equity = _number(account_row.get("cash")) + sum(
            item.market_value for item in position_list
        )
    if cash is None:
        cash = _number(account_row.get("cash"))
    equity = _number(equity)
    cash = _number(cash)

    current_qty = max(0.0, _number(_field(current, "quantity")))
    current_value = abs(current_qty) * _number(_field(current, "last_price"))
    current_weight = current_value / equity if equity > 0 else 0.0

    requested = _number(plan.get("position_pct"))
    conflict = bool(plan.get("conflict"))
    decision = str(plan.get("decision") or "HOLD").upper()

    engine = PortfolioRiskEngine(limits)
    engine_result = engine.evaluate(
        plan=plan,
        positions=position_list,
        equity=equity,
        cash=cash,
        sector=str(_field(current, "sector", "") or ""),
        ticker=code,
    )
    adjusted_add = _number(engine_result.get("adjusted_position_pct"))

    target_weight = 0.0
    target_note = ""
    if conflict:
        target_weight = 0.0
        target_note = (
            "决策卡检测到规则共识与最终决策方向冲突，目标仓位按 0 处理"
            "（对已持仓者为减仓/清仓）"
        )
    elif requested <= 0:
        target_weight = 0.0
        if decision in ("SELL", "UNDERWEIGHT"):
            target_note = (
                f"决策为 {decision}，A 股不支持做空，目标仓位按 0 处理"
                "（减仓/清仓）"
            )
        else:
            target_note = (
                "决策卡未给出目标仓位（持有/门槛未过/风险预算不足），"
                "对已持仓者按减仓处理"
            )
    elif current_qty <= 0:
        target_weight = adjusted_add
        if target_weight <= 0:
            target_note = "组合约束未放行建仓，目标仓位为 0"
        else:
            target_note = (
                f"决策卡目标仓位 {requested * 100:.1f}%，"
                f"经组合约束后为 {target_weight * 100:.1f}%"
            )
    elif current_weight > requested + 1e-9:
        target_weight = requested
        target_note = (
            f"当前权重 {current_weight * 100:.1f}% 高于决策卡目标 "
            f"{requested * 100:.1f}%，按目标权重减仓"
        )
    else:
        target_weight = min(requested, current_weight + adjusted_add)
        if target_weight < requested - 1e-9:
            target_note = (
                f"组合约束（单票/行业/总仓位/现金/风险预算）将目标仓位由 "
                f"{requested * 100:.1f}% 下调至 {target_weight * 100:.1f}%"
            )
        else:
            target_note = (
                f"决策卡目标仓位 {requested * 100:.1f}% 在组合约束内"
            )

    latest_price = _number(_field(current, "last_price"))
    if latest_price <= 0:
        latest_price = _number(plan.get("entry"))
    if latest_price <= 0 and current is not None:
        latest_price = _number(_field(current, "avg_cost"))

    if target_weight > 0 and equity > 0 and latest_price > 0:
        raw_shares = equity * target_weight / latest_price
        target_qty = math.floor(raw_shares / lot + 1e-9) * lot
        if target_qty <= 0:
            target_note = (
                f"{target_note}；目标权重 {target_weight * 100:.1f}% 按 "
                f"{equity:,.0f} 元权益与 {latest_price:.2f} 元价格换算不足 "
                f"1 手（{lot} 股），有效目标仓位为 0"
            )
            target_weight = 0.0
    else:
        target_qty = 0.0
    if target_weight > 0 and latest_price <= 0:
        target_qty = current_qty
        target_note = "缺少有效价格，无法换算目标股数，暂按持有处理"

    delta_qty = target_qty - current_qty
    if delta_qty > 0 and lot > 1:
        delta_qty = math.floor(delta_qty / lot) * lot
        target_qty = current_qty + delta_qty
    elif delta_qty < 0 and target_qty > 0 and lot > 1:
        sellable = math.floor(abs(delta_qty) / lot) * lot
        delta_qty = -sellable
        target_qty = current_qty + delta_qty

    delta_lots = int(delta_qty / lot) if lot else 0

    if current_qty <= 0 and target_qty > 0:
        action = "建仓"
    elif current_qty > 0 and target_qty <= 0:
        action = "清仓"
    elif delta_qty > 0:
        action = "加仓"
    elif delta_qty < 0:
        action = "减仓"
    elif current_qty > 0:
        action = "持有"
    else:
        action = "观望"

    alerts = (
        check_position_alerts([current], today=today)
        if current is not None
        else []
    )

    reasons: list[str] = []
    if current_qty > 0:
        reasons.append(
            f"当前持仓 {current_qty:.0f} 股，市值 {current_value:,.0f} 元，"
            f"占组合 {current_weight * 100:.1f}%"
        )
    else:
        reasons.append("当前无持仓")
    if equity > 0:
        reasons.append(
            f"组合权益 {equity:,.0f} 元，可用现金 {cash:,.0f} 元"
        )
    else:
        reasons.append("组合权益未知（<=0），无法换算目标仓位")
    reasons.append(
        f"决策卡：{decision}，置信度 {int(_number(plan.get('confidence')))}%"
    )
    if target_note:
        reasons.append(target_note)
    if latest_price > 0:
        reasons.append(
            f"最新价 {latest_price:.2f} 元，目标持仓 {target_qty:.0f} 股"
            f"（目标权重 {target_weight * 100:.1f}%）"
        )
    else:
        reasons.append("缺少有效最新价，无法换算目标股数")

    if action == "加仓":
        reasons.append(
            f"需买入 {delta_qty:.0f} 股（{delta_lots} 手），"
            f"须为 {lot} 股整数倍，已按整手向下取整"
        )
    elif action == "减仓":
        reasons.append(
            f"需卖出 {abs(delta_qty):.0f} 股（{abs(delta_lots)} 手），"
            "卖出需确认可卖数量（T+1：当日买入不可卖）"
        )
        if current_qty % lot != 0:
            reasons.append(
                "持仓含零股：零股仅可一次性全部卖出，"
                "部分减仓须先按整手处理"
            )
    elif action == "清仓":
        reasons.append(
            "清仓可一次性卖出全部持股（A 股允许卖出零股），"
            "卖出需确认可卖数量（T+1：当日买入不可卖）"
        )
    elif action == "持有":
        if target_weight < current_weight - 1e-9:
            reasons.append(
                "目标权重低于当前权重，但受整手/零股限制无法在不产生零股的"
                "前提下继续减仓，暂按持有处理"
            )
        else:
            reasons.append("目标持仓与当前持仓一致，建议持有并跟踪止损位")
    elif action == "观望":
        reasons.append("无持仓且决策卡未给出目标仓位，建议观望")

    if not is_a_share_code(code):
        reasons.append("非 A 股代码，整手/T+1/涨跌停规则可能不适用")

    prev_close = plan.get("prev_close")
    if prev_close is None and current is not None:
        prev_close = _field(current, "prev_close")
    state, limit, change = _limit_state(
        code,
        str(_field(current, "name", "") or ""),
        latest_price,
        prev_close,
    )
    if state == "up":
        reasons.append(
            f"当前价格已触及涨停（{change * 100:+.1f}%，限制 "
            f"{limit * 100:.0f}%），加仓可能无法成交"
        )
    elif state == "down":
        reasons.append(
            f"当前价格已触及跌停（{change * 100:+.1f}%，限制 "
            f"{limit * 100:.0f}%），减仓可能无法成交"
        )

    for finding in engine_result.get("findings") or []:
        if finding.get("level") in ("block", "warn"):
            reasons.append(f"组合风控：{finding.get('message')}")

    avg_cost = _number(_field(current, "avg_cost"))
    unrealised_pnl = _number(_field(current, "unrealised_pnl"))
    unrealised_pnl_pct = _number(_field(current, "unrealised_pnl_pct"))
    if unrealised_pnl == 0 and current_qty > 0 and avg_cost > 0 and latest_price > 0:
        unrealised_pnl = (latest_price - avg_cost) * current_qty
        unrealised_pnl_pct = latest_price / avg_cost - 1

    stop_loss = _field(current, "stop_loss")
    if stop_loss is None:
        stop_loss = plan.get("stop_loss")
    take_profit = _field(current, "take_profit")
    if take_profit is None:
        take_profit = plan.get("take_profit")

    return {
        "success": True,
        "ticker": code,
        "action": action,
        "current_qty": round(current_qty, 2),
        "current_weight": round(current_weight, 4),
        "target_weight": round(target_weight, 4),
        "target_qty": round(max(0.0, target_qty), 2),
        "delta_qty": round(delta_qty, 2),
        "delta_lots": delta_lots,
        "latest_price": round(latest_price, 4),
        "avg_cost": round(avg_cost, 4),
        "unrealised_pnl": round(unrealised_pnl, 2),
        "unrealised_pnl_pct": round(unrealised_pnl_pct, 4),
        "stop_loss": round(_number(stop_loss), 4),
        "take_profit": round(_number(take_profit), 4),
        "risk_reward": _number(plan.get("risk_reward")),
        "decision": decision,
        "confidence": int(_number(plan.get("confidence"))),
        "alerts": alerts,
        "reasons": reasons,
        "disclaimer": "规则化估算，仅供参考，不构成投资建议。",
    }
