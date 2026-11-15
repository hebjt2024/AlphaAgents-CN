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

import math
from datetime import date, datetime
from typing import Any

from tradingagents.storage import (
    add_transaction,
    create_account,
    get_account,
    list_portfolio_snapshots,
    list_positions,
    list_transactions,
    save_portfolio_snapshot,
    update_position_prices,
    upsert_position,
)

from .risk_engine import PortfolioRiskEngine, PositionView, RiskLimits


def _account_name(account: str | None) -> str:
    return account or "默认账户"


def ensure_account(
    account: str | None = None,
    initial_cash: float = 100000.0,
    owner: str = "",
) -> dict:
    """确保账户存在并返回账户信息。"""
    return create_account(_account_name(account), initial_cash, owner=owner)


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
    # 读路径绝不创建账户：否则 viewer 只打开页面就会凭空得到 10 万现金。
    account_row = get_account(name) or {
        "name": name,
        "cash": 0.0,
        "initial_cash": 0.0,
    }
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
    owner: str = "",
) -> dict[str, Any]:
    """把一份决策卡放到组合语境下重新评估。

    返回的 ``adjusted_position_pct`` 才是**真正可执行**的仓位；
    ``requested_position_pct`` 是决策卡原本建议的仓位。
    """
    name = _account_name(account)
    account_row = ensure_account(name, owner=owner)
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
    owner: str = "",
) -> dict[str, Any]:
    """记录一笔成交并同步更新持仓与现金。

    卖出时按"移动加权平均成本"结转已实现盈亏；买入时累加成本。
    """
    name = _account_name(account)
    account_row = ensure_account(name, owner=owner)
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
                sector=sector,
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
                sector=sector,
                stop_loss=stop_loss if stop_loss is not None else position.stop_loss,
                take_profit=(
                    take_profit if take_profit is not None else position.take_profit
                ),
            )
    else:
        sold = abs(quantity)
        if position is None or position.quantity <= 0:
            raise ValueError(f"账户未持有 {ticker}，不能卖出")
        if sold > position.quantity + 1e-9:
            raise ValueError(
                f"卖出数量 {sold:g} 超过持仓 {position.quantity:g}"
            )
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
                    sector=sector or position.sector,
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


# ---------------------------------------------------------------------------
# 盯市 / 快照 / 绩效
# ---------------------------------------------------------------------------


def refresh_prices(account: str | None = None) -> dict[str, Any]:
    """用全市场快照刷新账户持仓最新价，并返回刷新后的组合。

    只刷新能取到价格的标的；取数失败不会把已有价格清空。
    """
    name = _account_name(account)
    positions = list_positions(name)
    tickers = [str(item.get("ticker") or "") for item in positions]
    prices: dict[str, float] = {}
    error = ""
    if tickers:
        try:
            from tradingagents.dataflows.akshare_adapter import (
                get_akshare_spot_prices,
            )

            prices = get_akshare_spot_prices(tickers)
        except Exception as exc:  # 数据源失败不应阻断组合读取
            error = str(exc)
    updated = update_position_prices(name, prices)
    snapshot = portfolio_snapshot(name)
    return {
        "account": name,
        "prices": prices,
        "updated": updated,
        "portfolio": snapshot,
        "error": error,
    }


def record_snapshot(
    account: str | None = None,
    snapshot_date: str | None = None,
    owner: str = "",
) -> dict[str, Any]:
    """把当前组合权益写入每日快照（同日重复调用会覆盖）。"""
    name = _account_name(account)
    ensure_account(name, owner=owner)
    snapshot = portfolio_snapshot(name)
    pos_value = float(snapshot["equity"]) - float(snapshot["cash"])
    row = save_portfolio_snapshot(
        name,
        snapshot_date or date.today().isoformat(),
        cash=float(snapshot["cash"]),
        position_value=pos_value,
        equity=float(snapshot["equity"]),
    )
    return {"account": name, "snapshot": row, "portfolio": snapshot}


def _performance_stats(
    snapshots: list[dict[str, Any]], initial_cash: float
) -> dict[str, Any]:
    """由快照序列计算收益、回撤、波动与夏普。"""
    if not snapshots:
        return {
            "points": 0,
            "start_date": "",
            "end_date": "",
            "start_equity": round(initial_cash, 2),
            "end_equity": round(initial_cash, 2),
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "equity_curve": [],
        }

    equities = [float(item.get("equity") or 0) for item in snapshots]
    # 第一张快照不一定等于初始资金（用户可能过几天才第一次记录）。
    # 累计收益必须以"初始资金"为基准，否则第一次打开绩效页会显示 0%。
    series = list(equities)
    if initial_cash > 0 and (
        not series or abs(series[0] - initial_cash) > 1e-9
    ):
        series = [initial_cash, *series]
    start_equity = initial_cash or (series[0] if series else 1.0)
    end_equity = equities[-1]
    total_return = end_equity / start_equity - 1 if start_equity else 0.0

    peak = series[0] if series else 0.0
    max_drawdown = 0.0
    for value in series:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1)

    daily_returns: list[float] = []
    for previous, current in zip(series, series[1:], strict=False):
        if previous > 0:
            daily_returns.append(current / previous - 1)
    mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    variance = (
        sum((item - mean) ** 2 for item in daily_returns) / len(daily_returns)
        if daily_returns
        else 0.0
    )
    std = math.sqrt(variance)
    volatility = std * math.sqrt(252)
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0

    start = _parse_date(snapshots[0].get("snapshot_date"))
    end = _parse_date(snapshots[-1].get("snapshot_date"))
    days = (end - start).days if start and end else 0
    annualized = (
        (1 + total_return) ** (365 / days) - 1
        if days > 0 and total_return > -1
        else 0.0
    )

    return {
        "points": len(snapshots),
        "start_date": snapshots[0].get("snapshot_date") or "",
        "end_date": snapshots[-1].get("snapshot_date") or "",
        "start_equity": round(start_equity, 2),
        "end_equity": round(end_equity, 2),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown": round(max_drawdown, 6),
        "volatility": round(volatility, 6),
        "sharpe": round(sharpe, 4),
        "win_rate": round(
            sum(1 for item in daily_returns if item > 0) / len(daily_returns), 4
        )
        if daily_returns
        else 0.0,
        "equity_curve": [
            {
                "date": item.get("snapshot_date") or "",
                "equity": round(float(item.get("equity") or 0), 2),
                "cash": round(float(item.get("cash") or 0), 2),
                "position_value": round(float(item.get("position_value") or 0), 2),
            }
            for item in snapshots
        ],
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def portfolio_performance(
    account: str | None = None, limit: int = 500
) -> dict[str, Any]:
    """返回组合绩效曲线与统计。

    没有每日快照时退化为"初始资金 → 当前权益"的单点收益，并在响应里
    显式标注 ``snapshot_missing``，避免把单点收益当成完整曲线。
    """
    name = _account_name(account)
    account_row = get_account(name) or {"cash": 0.0, "initial_cash": 0.0}
    snapshots = list_portfolio_snapshots(name, limit=limit)
    initial_cash = float(account_row.get("initial_cash") or 0)
    stats = _performance_stats(snapshots, initial_cash)

    # 没有快照时只用当前权益生成一个“临时点”，不写库；
    # 否则 viewer 打开页面就会产生一条快照（GET 不应有写副作用）。
    if not snapshots:
        current = portfolio_snapshot(name)
        point = {
            "snapshot_date": date.today().isoformat(),
            "cash": current.get("cash", 0),
            "position_value": float(current.get("equity", 0))
            - float(current.get("cash", 0)),
            "equity": current.get("equity", 0),
        }
        stats = _performance_stats([point], initial_cash)
        stats["snapshot_missing"] = True
    else:
        stats["snapshot_missing"] = False

    return {
        "account": name,
        "initial_cash": round(initial_cash, 2),
        "current": portfolio_snapshot(name),
        "performance": stats,
    }
