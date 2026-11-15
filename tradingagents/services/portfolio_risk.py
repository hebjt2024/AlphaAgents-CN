"""组合风险度量、压力测试与再平衡建议。

回答三个此前没有系统化回答的问题：

1. **我现在承担多大风险？** 年化波动、最大回撤、Beta、VaR/CVaR、
   集中度、风险贡献；
2. **如果市场/行业/个股发生冲击，会亏多少？** 大盘 -5/-10/-20%、
   最大持仓黑天鹅、行业普跌等情景；
3. **应该怎么调仓？** 等权、风险平价、仅修复超限三种目标，输出
   目标权重与具体股数（整手）。

所有历史收益来自本地行情优先的 ``_load_prices``；基准默认沪深 300，
指数接口失败时退化为 510300 ETF。样本不足或基准缺失时显式降级，
不给出看似精确的伪结论。
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from tradingagents.dataflows.ashare_rules import normalize_code
from tradingagents.dataflows.index_data import get_index_history
from tradingagents.services.backtest import _load_prices
from tradingagents.services.backtest_engine import CostModel
from tradingagents.services.portfolio import portfolio_snapshot
from tradingagents.services.risk_engine import RiskLimits

logger = logging.getLogger(__name__)
TRADING_DAYS = 252


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if not math.isfinite(number) else number


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _weights(positions: list[dict[str, Any]], equity: float) -> dict[str, float]:
    values = {
        normalize_code(str(item.get("ticker") or "")): _num(item.get("market_value"))
        for item in positions
        if normalize_code(str(item.get("ticker") or ""))
    }
    total = sum(values.values())
    if total <= 0 and equity > 0:
        values = {
            code: _num(item.get("last_price") or item.get("avg_cost"))
            * _num(item.get("quantity"))
            for item in positions
            for code in [normalize_code(str(item.get("ticker") or ""))]
            if code
        }
        total = sum(values.values())
    if total <= 0:
        return {}
    # 统一按账户权益归一（现金的权重为 0）；否则“90% 现金 + 10% 仓位”
    # 会被当成满仓，压力测试和再平衡的分母全部失真。
    denominator = equity if equity > 0 else total
    return {
        code: value / denominator
        for code, value in values.items()
        if value > 0
    }


def _close_matrix(
    tickers: list[str], lookback: int
) -> tuple[pd.DataFrame, dict[str, str]]:
    end = date.today()
    start = end - timedelta(days=int(lookback * 1.8))
    closes: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}
    for ticker in tickers:
        try:
            frame = _load_prices(ticker, start.isoformat(), end.isoformat())
            if frame is None or frame.empty or len(frame) < 60:
                errors[ticker] = "历史数据不足 60 个交易日"
                continue
            series = frame.set_index("Date")["Close"].astype(float)
            closes[ticker] = series
        except Exception as exc:
            errors[ticker] = f"{type(exc).__name__}: {exc}"
            logger.warning("组合风险取数失败 %s: %s", ticker, exc)
    if not closes:
        return pd.DataFrame(), errors
    frame = pd.concat(closes, axis=1).sort_index()
    frame = frame.ffill(limit=3)
    frame = frame.dropna(how="all")
    return frame.tail(lookback), errors


def _benchmark_returns(
    benchmark: str, index: pd.DatetimeIndex
) -> tuple[pd.Series | None, list[str]]:
    warnings: list[str] = []
    result = get_index_history(benchmark, days=max(400, len(index) * 2))
    if not result.get("success"):
        warnings.extend(result.get("warnings") or [f"基准 {benchmark} 不可用"])
        return None, warnings
    frame = result.get("items")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        warnings.append(f"基准 {benchmark} 行情为空")
        return None, warnings
    series = frame.set_index("Date")["Close"].astype(float)
    series = series.reindex(index, method="ffill")
    returns = series.pct_change()
    return returns.dropna(), warnings


def _risk_contributions(
    returns: pd.DataFrame, weights: dict[str, float]
) -> list[dict[str, Any]]:
    codes = [code for code in weights if code in returns.columns]
    if len(codes) < 2:
        return []
    weight_vector = np.array([weights[code] for code in codes], dtype=float)
    covariance = returns[codes].cov().values
    variance = float(weight_vector @ covariance @ weight_vector)
    if variance <= 0:
        return []
    marginal = covariance @ weight_vector / variance
    contribution = weight_vector * marginal
    total = float(contribution.sum()) or 1.0
    return [
        {
            "ticker": code,
            "weight": round(float(weight_vector[index]), 4),
            "risk_contribution": round(float(contribution[index] / total), 4),
        }
        for index, code in enumerate(codes)
    ]


def _stress_tests(
    equity: float,
    weights: dict[str, float],
    beta: float | None,
    sector_weights: dict[str, float],
    contributions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    beta_value = beta if beta is not None else 1.0
    largest = max(weights.values()) if weights else 0.0
    largest_sector = max(sector_weights.values()) if sector_weights else 0.0
    top_risk = (
        max(contributions, key=lambda item: item["risk_contribution"])
        if contributions
        else None
    )
    scenarios = [
        ("大盘 -5%", -beta_value * 0.05, f"按 Beta {beta_value:.2f} 估算"),
        ("大盘 -10%", -beta_value * 0.10, f"按 Beta {beta_value:.2f} 估算"),
        ("大盘 -20%", -beta_value * 0.20, f"按 Beta {beta_value:.2f} 估算"),
        ("最大持仓 -20%", -largest * 0.20, "单票黑天鹅"),
        ("最大行业 -10%", -largest_sector * 0.10, "行业系统性冲击"),
    ]
    if top_risk:
        scenarios.append(
            (
                "最大风险贡献持仓 -15%",
                -top_risk["weight"] * 0.15,
                f"{top_risk['ticker']} 风险贡献 {top_risk['risk_contribution']:.0%}",
            )
        )
    items = []
    for name, loss_pct, note in scenarios:
        items.append(
            {
                "scenario": name,
                "loss_pct": round(abs(loss_pct) * 100, 2),
                "loss_amount": round(abs(loss_pct) * equity, 2),
                "note": note,
            }
        )
    items.sort(key=lambda item: item["loss_amount"], reverse=True)
    return items


def _cap_weights(
    raw: dict[str, float], max_single: float, max_total: float
) -> dict[str, float]:
    """把权重压到单票/总仓位上限内，多出的部分保留为现金。"""
    if not raw:
        return {}
    target = {code: min(weight, max_single) for code, weight in raw.items()}
    # 多轮把超限部分按比例分给未触顶的标的，直到没有可分配或达到总仓位上限。
    for _ in range(8):
        total = sum(target.values())
        if total <= max_total + 1e-9:
            break
        scale = max_total / total
        target = {code: weight * scale for code, weight in target.items()}
    return target


def suggest_rebalance(
    account: str | None,
    *,
    method: str = "equal",
    limits: RiskLimits | None = None,
    volatility: dict[str, float] | None = None,
) -> dict[str, Any]:
    """生成再平衡建议（目标权重 + 具体股数）。"""
    limits = limits or RiskLimits()
    snapshot = portfolio_snapshot(account)
    positions = snapshot.get("positions") or []
    equity = _num(snapshot.get("equity"))
    if not positions or equity <= 0:
        return {
            "success": False,
            "message": "暂无持仓或组合权益为 0",
            "items": [],
            "warnings": [],
        }

    current_weights = _weights(positions, equity)
    codes = list(current_weights)
    if not codes:
        return {"success": False, "message": "无法计算持仓权重", "items": [], "warnings": []}

    if method == "risk_parity" and volatility:
        raw = {
            code: 1.0 / max(0.01, float(volatility.get(code, 0.3)))
            for code in codes
        }
        total = sum(raw.values())
        raw = {code: value / total for code, value in raw.items()}
    elif method == "cap_breaches":
        raw = dict(current_weights)
    else:
        method = "equal"
        raw = {code: 1.0 / len(codes) for code in codes}

    target_weights = _cap_weights(
        raw, limits.max_single_weight, limits.max_total_weight
    )
    price_map = {
        normalize_code(str(item.get("ticker") or "")): _num(
            item.get("last_price") or item.get("avg_cost")
        )
        for item in positions
    }
    quantity_map = {
        normalize_code(str(item.get("ticker") or "")): int(
            _num(item.get("quantity"))
        )
        for item in positions
    }
    name_map = {
        normalize_code(str(item.get("ticker") or "")): item.get("name") or ""
        for item in positions
    }
    cost_model = CostModel()
    items: list[dict[str, Any]] = []
    target_value_total = 0.0
    for code in codes:
        price = price_map.get(code, 0.0)
        current_qty = quantity_map.get(code, 0)
        target_weight = target_weights.get(code, 0.0)
        target_value = equity * target_weight
        target_qty = (
            int(target_value / price / 100) * 100 if price > 0 else 0
        )
        lot_note = ""
        if target_weight > 0 and target_qty == 0 and price > 0:
            lot_note = "目标仓位不足一手（100 股），按 0 股处理"
        delta = target_qty - current_qty
        amount = abs(delta) * price
        fee = (
            cost_model.buy_cost(amount)
            if delta > 0
            else cost_model.sell_cost(amount)
            if delta < 0
            else 0.0
        )
        target_value_total += target_qty * price
        if delta > 0:
            action = "买入"
        elif delta < 0:
            action = "卖出"
        else:
            action = "不动"
        items.append(
            {
                "ticker": code,
                "name": name_map.get(code, ""),
                "price": round(price, 3),
                "quantity": current_qty,
                "current_weight": round(current_weights.get(code, 0.0), 4),
                "target_weight": round(target_weight, 4),
                "target_quantity": target_qty,
                "delta_quantity": delta,
                "action": action,
                "amount": round(amount, 2),
                "estimated_fee": round(fee, 2),
                "reason": (
                    f"{current_weights.get(code, 0.0):.1%} → {target_weight:.1%}"
                    + ("（超配，减仓）" if delta < 0 else "（低配，加仓）" if delta > 0 else "（已匹配）")
                    + (f"；{lot_note}" if lot_note else "")
                ),
            }
        )
    items.sort(key=lambda item: (item["action"] == "不动", -abs(item["amount"])))
    warnings = ["再平衡按最新价估算；A 股 T+1 与涨跌停会限制实际成交"]
    return {
        "success": True,
        "account": snapshot.get("account"),
        "method": method,
        "equity": round(equity, 2),
        "cash_before": round(_num(snapshot.get("cash")), 2),
        "cash_after_estimate": round(equity - target_value_total, 2),
        "target_total_weight": round(sum(target_weights.values()), 4),
        "items": items,
        "warnings": warnings,
    }


def portfolio_risk_report(
    account: str | None = None,
    *,
    lookback: int = 252,
    benchmark: str = "000300",
    rebalance_method: str = "equal",
) -> dict[str, Any]:
    """组合风险、压力测试与再平衡建议。"""
    lookback = max(60, min(int(lookback), 1000))
    snapshot = portfolio_snapshot(account)
    positions = snapshot.get("positions") or []
    if not positions:
        return {
            "success": False,
            "message": "暂无持仓，无法计算组合风险",
            "metrics": {},
            "stress": [],
            "rebalance": {},
            "warnings": [],
        }
    equity = _num(snapshot.get("equity"))
    weights = _weights(positions, equity)
    warnings: list[str] = []
    closes, errors = _close_matrix(list(weights), lookback)
    if errors:
        warnings.extend(f"{code}: {message}" for code, message in errors.items())
    if closes.empty:
        return {
            "success": False,
            "message": "持仓历史行情不足，无法计算风险指标",
            "metrics": {},
            "stress": [],
            "rebalance": suggest_rebalance(account, method=rebalance_method),
            "warnings": warnings,
        }

    # 只用有历史行情的标的，并按可用市值重新归一。
    available = [code for code in weights if code in closes.columns]
    available_weights = {code: weights[code] for code in available}
    total_weight = sum(available_weights.values())
    if total_weight <= 0:
        available_weights = {code: 1.0 / len(available) for code in available}
    else:
        available_weights = {
            code: weight / total_weight for code, weight in available_weights.items()
        }
    if len(available) < len(weights):
        warnings.append("部分持仓缺少历史数据，风险指标按剩余标的重新归一")

    returns = closes[available].pct_change().dropna(how="all").fillna(0)
    if len(returns) < 30:
        warnings.append("有效收益样本少于 30 天，风险指标仅供参考")
    weight_vector = np.array([available_weights[code] for code in available])
    portfolio_returns = returns[available].values @ weight_vector
    portfolio_returns = pd.Series(portfolio_returns, index=returns.index)
    n = len(portfolio_returns)
    annual_return = (
        float((1 + portfolio_returns).prod() ** (TRADING_DAYS / n) - 1)
        if n
        else 0.0
    )
    volatility = float(portfolio_returns.std() * math.sqrt(TRADING_DAYS)) if n > 1 else 0.0
    risk_free = float(os.getenv("RISK_FREE_RATE", "0.02"))
    sharpe = (annual_return - risk_free) / volatility if volatility > 0 else 0.0
    cumulative = (1 + portfolio_returns).cumprod()
    max_drawdown = float((cumulative / cumulative.cummax() - 1).min()) if n else 0.0
    var_95 = float(-np.percentile(portfolio_returns, 5)) if n else 0.0
    var_99 = float(-np.percentile(portfolio_returns, 1)) if n else 0.0
    tail = portfolio_returns[portfolio_returns <= np.percentile(portfolio_returns, 5)] if n else pd.Series(dtype=float)
    cvar_95 = float(-tail.mean()) if len(tail) else var_95
    parametric_var_95 = float(1.645 * portfolio_returns.std()) if n > 1 else 0.0

    benchmark_returns, benchmark_warnings = _benchmark_returns(
        benchmark, portfolio_returns.index
    )
    warnings.extend(benchmark_warnings)
    beta = None
    correlation = None
    tracking_error = None
    alpha = None
    if benchmark_returns is not None and len(benchmark_returns) >= 30:
        aligned = pd.concat(
            [portfolio_returns.rename("portfolio"), benchmark_returns.rename("benchmark")],
            axis=1,
            join="inner",
        ).dropna()
        if len(aligned) >= 30:
            cov = float(aligned["portfolio"].cov(aligned["benchmark"]))
            bench_var = float(aligned["benchmark"].var())
            beta = cov / bench_var if bench_var > 0 else None
            correlation = float(aligned["portfolio"].corr(aligned["benchmark"]))
            diff = aligned["portfolio"] - aligned["benchmark"]
            tracking_error = float(diff.std() * math.sqrt(TRADING_DAYS))
            bench_annual = float(
                (1 + aligned["benchmark"]).prod() ** (TRADING_DAYS / len(aligned)) - 1
            )
            if beta is not None:
                alpha = annual_return - (risk_free + beta * (bench_annual - risk_free))

    hhi = sum(weight * weight for weight in weights.values())
    contributions = _risk_contributions(returns, available_weights)
    sector_weights: dict[str, float] = {}
    for item in positions:
        code = normalize_code(str(item.get("ticker") or ""))
        if code not in weights:
            continue
        sector = str(item.get("sector") or "未分类")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weights[code]

    metrics = {
        "period_days": n,
        "start_date": returns.index[0].strftime("%Y-%m-%d") if n else "",
        "end_date": returns.index[-1].strftime("%Y-%m-%d") if n else "",
        "equity": round(equity, 2),
        "annual_return": round(annual_return, 6),
        "annual_volatility": round(volatility, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 6),
        "var_95_1d": round(var_95, 6),
        "cvar_95_1d": round(cvar_95, 6),
        "var_99_1d": round(var_99, 6),
        "parametric_var_95_1d": round(parametric_var_95, 6),
        "var_95_amount": round(var_95 * equity, 2),
        "cvar_95_amount": round(cvar_95 * equity, 2),
        "beta": _finite(beta, 4),
        "correlation": _finite(correlation, 4),
        "tracking_error": _finite(tracking_error, 6),
        "alpha": _finite(alpha, 6),
        "concentration_hhi": round(hhi, 4),
        "effective_positions": round(1 / hhi, 2) if hhi else 0.0,
        "top_weights": [
            {"ticker": code, "weight": round(weight, 4)}
            for code, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "sector_weights": {key: round(value, 4) for key, value in sorted(sector_weights.items(), key=lambda item: item[1], reverse=True)},
        "risk_contributions": contributions,
        "benchmark": benchmark,
    }
    stress = _stress_tests(
        equity, weights, beta, sector_weights, contributions
    )
    rebalance = suggest_rebalance(
        account,
        method=rebalance_method,
        volatility={
            code: float(closes[code].pct_change().std() * math.sqrt(TRADING_DAYS))
            for code in available
        },
    )
    summary = (
        f"年化波动 {volatility:.1%}，最大回撤 {max_drawdown:.1%}，"
        f"1日 VaR95 {var_95:.2%}（约 {var_95 * equity:,.0f} 元），"
        f"Beta {beta:.2f}。" if beta is not None else
        f"年化波动 {volatility:.1%}，最大回撤 {max_drawdown:.1%}，"
        f"1日 VaR95 {var_95:.2%}（约 {var_95 * equity:,.0f} 元）。"
    )
    return {
        "success": True,
        "account": snapshot.get("account"),
        "as_of": date.today().isoformat(),
        "metrics": metrics,
        "stress": stress,
        "rebalance": rebalance,
        "summary": summary,
        "warnings": warnings,
    }


__all__ = [
    "portfolio_risk_report",
    "suggest_rebalance",
]
