"""组合管理服务：持仓账本 + 组合级风控。

此前系统只能对单只股票给建议 —— 建议里的"仓位 20%"没有任何落点。
本模块把三件事接起来：

1. **持仓账本**（``storage/portfolio_store.py``）：账户、持仓、成交流水；
2. **组合视图**：把账本还原成带行业/权重/浮盈的组合快照；
3. **组合级校验**：把 ``services/decision_plan.py`` 的决策卡送进
   ``services/risk_engine.py``，得出"在当前组合下真正可执行的仓位"。

上层（WebUI / CLI / 定时任务）只需调用 :func:`evaluate_plan_for_account`，
就能拿到一份**受组合约束**的决策，而不是一份孤立的建议。
"""

from __future__ import annotations

from typing import Any

from tradingagents.storage import (
    add_transaction,
    create_account,
    list_positions,
    list_transactions,
    upsert_position,
)

from .risk_engine import PortfolioRiskEngine, PositionView, RiskLimits


def _account_name(account: str | None) -> str:
    return account or "默认账户"


def ensure_account(account: str | None = None, initial_cash: float = 100000.0) -> dict:
    """确保账户存在并返回账户信息。"""
    return create_account(_account_name(account), initial_cash)


def load_positions(account: str | None = None) -> list[PositionView]:
    """读取账户持仓并转换为风控引擎可用的视图对象。"""
    rows = list_positions(_account_name(account))
    return [PositionView.from_row(row) for row in rows]


def portfolio_snapshot(
    account: str | None = None,
    limits: RiskLimits | None = None,
) -> dict[str, Any]:
    """生成组合快照：账户 + 持仓明细 + 风险体检。"""
    name = _account_name(account)
    account_row = ensure_account(name)
    positions = load_positions(name)

    equity = float(account_row.get("cash") or 0) + sum(
        position.market_value for position in positions
    )
    health = PortfolioRiskEngine(limits).portfolio_health(positions, equity)

    return {
        "account": name,
        "cash": round(float(account_row.get("cash") or 0), 2),
        "initial_cash": round(float(account_row.get("initial_cash") or 0), 2),
        "equity": round(equity, 2),
        "positions": [
            {
                "ticker": position.ticker,
                "name": position.name,
                "sector": position.sector,
                "quantity": position.quantity,
                "avg_cost": round(position.avg_cost, 4),
                "last_price": round(position.last_price, 4),
                "market_value": round(position.market_value, 2),
                "unrealised_pnl": round(position.unrealised_pnl, 2),
                "unrealised_pnl_pct": round(position.unrealised_pnl_pct, 4),
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
            }
            for position in positions
        ],
        "health": health,
        "recent_transactions": list_transactions(name, limit=20),
    }


def evaluate_plan_for_account(
    plan: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    account: str | None = None,
    ticker: str = "",
    sector: str = "",
    limits: RiskLimits | None = None,
    equity: float | None = None,
    cash: float | None = None,
    daily_pnl_pct: float = 0.0,
    drawdown_pct: float = 0.0,
) -> dict[str, Any]:
    """把一份决策卡放到组合语境下重新评估。

    返回的 ``adjusted_position_pct`` 才是**真正可执行**的仓位；
    ``requested_position_pct`` 是决策卡原本建议的仓位。
    """
    name = _account_name(account)
    account_row = ensure_account(name)
    positions = load_positions(name)

    if equity is None:
        equity = float(account_row.get("cash") or 0) + sum(
            position.market_value for position in positions
        )
    if cash is None:
        cash = float(account_row.get("cash") or 0)

    engine = PortfolioRiskEngine(limits)
    result = engine.evaluate(
        plan=plan,
        summary=summary,
        positions=positions,
        equity=equity,
        cash=cash,
        sector=sector,
        ticker=ticker,
        daily_pnl_pct=daily_pnl_pct,
        drawdown_pct=drawdown_pct,
    )
    result["account"] = name
    result["equity"] = round(equity, 2)
    result["cash"] = round(cash, 2)
    return result


def record_fill(
    account: str | None,
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    fee: float = 0.0,
    note: str = "",
    trade_date: str = "",
    sector: str = "",
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict[str, Any]:
    """记录一笔成交并同步更新持仓与现金。

    卖出时按"移动加权平均成本"结转已实现盈亏；买入时累加成本。
    """
    name = _account_name(account)
    account_row = ensure_account(name)
    cash = float(account_row.get("cash") or 0)

    positions = {p.ticker: p for p in load_positions(name)}
    position = positions.get(ticker)
    side = side.upper()

    realized_pnl = 0.0
    if side == "BUY":
        cash -= abs(quantity) * price + fee
        if position and position.quantity > 0:
            total_cost = position.avg_cost * position.quantity + abs(quantity) * price
            new_quantity = position.quantity + abs(quantity)
            position.avg_cost = total_cost / new_quantity if new_quantity else price
            position.quantity = new_quantity
        else:
            position = None
        if position is None:
            upsert_position(
                name,
                ticker,
                quantity=abs(quantity),
                avg_cost=price,
                last_price=price,
                name=position.name if position else "",
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        else:
            upsert_position(
                name,
                ticker,
                quantity=position.quantity,
                avg_cost=position.avg_cost,
                last_price=price,
                name=position.name,
                stop_loss=stop_loss if stop_loss is not None else position.stop_loss,
                take_profit=(
                    take_profit if take_profit is not None else position.take_profit
                ),
            )
    else:
        sold = abs(quantity)
        if position and position.quantity > 0:
            realized_pnl = (price - position.avg_cost) * sold
            cash += sold * price - fee
            position.quantity = max(0.0, position.quantity - sold)
            if position.quantity <= 0:
                from tradingagents.storage import delete_position

                delete_position(name, ticker)
            else:
                upsert_position(
                    name,
                    ticker,
                    quantity=position.quantity,
                    avg_cost=position.avg_cost,
                    last_price=price,
                    name=position.name,
                    stop_loss=stop_loss if stop_loss is not None else position.stop_loss,
                    take_profit=(
                        take_profit
                        if take_profit is not None
                        else position.take_profit
                    ),
                )
        else:
            cash += sold * price - fee

    from tradingagents.storage import update_account_cash

    update_account_cash(name, cash)

    transaction = add_transaction(
        name,
        ticker,
        side,
        abs(quantity),
        price,
        fee=fee,
        realized_pnl=realized_pnl,
        note=note,
        trade_date=trade_date,
    )
    return {
        "transaction": transaction,
        "cash": round(cash, 2),
        "realized_pnl": round(realized_pnl, 2),
        "sector": sector,
    }
