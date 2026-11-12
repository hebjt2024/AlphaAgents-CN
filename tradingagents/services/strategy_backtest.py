"""策略信号回测（信号有效性 + 简单跟随净值）。

方法：
  - 对每个交易日，用截至当日的历史窗口重新计算策略信号；
  - 统计信号触发后的 5/10/20 日前瞻收益与胜率；
  - 多头策略：信号触发后次日开盘买入，信号消失/止损/最长持有后卖出；
  - 看空策略：做“避险”回测（看空信号触发时空仓，否则持有）。

参数与规则均为简化模型，不构成投资建议。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from tradingagents.strategies.classic import (
    ALL_STRATEGIES,
    DEFAULT_STRATEGY_KEYS,
    STRATEGY_META,
)

STRATEGY_MAP = {func.__name__: func for func in ALL_STRATEGIES}
META_MAP = {item["key"]: item for item in STRATEGY_META}

HORIZONS = (5, 10, 20)
WINDOW = 180
STOP_LOSS = 0.08
MAX_HOLD_DAYS = 20
COMMISSION = 0.0003
STAMP_DUTY = 0.0005


def _parse_date(value: str | None) -> datetime:
    if value:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


def _load_frame(ticker: str, end_date: str | None, days: int = 730) -> pd.DataFrame:
    from tradingagents.services.backtest import _load_prices

    end = _parse_date(end_date)
    start = end - timedelta(days=days)
    frame = _load_prices(
        ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if frame is None or len(frame) < 80:
        raise ValueError(f"行情数据不足，无法回测策略：{ticker}")
    return frame


def _build_signal_series(
    frame: pd.DataFrame, key: str, start: int, window: int
) -> list[dict]:
    func = STRATEGY_MAP.get(key)
    if func is None:
        return []
    series = []
    n = len(frame)
    for i in range(start, n):
        chunk = frame.iloc[max(0, i - window + 1) : i + 1]
        try:
            signal = func(chunk)
            series.append(
                {
                    "index": i,
                    "date": frame["Date"].iloc[i].strftime("%Y-%m-%d"),
                    "triggered": bool(signal.triggered),
                    "direction": signal.direction,
                    "score": signal.score,
                }
            )
        except Exception:
            series.append(
                {
                    "index": i,
                    "date": frame["Date"].iloc[i].strftime("%Y-%m-%d"),
                    "triggered": False,
                    "direction": "neutral",
                    "score": 0,
                }
            )
    return series


def _forward_stats(frame: pd.DataFrame, series: list[dict]) -> dict:
    n = len(frame)
    result = {}
    for horizon in HORIZONS:
        returns = []
        for point in series:
            if not point["triggered"]:
                continue
            i = point["index"]
            entry_idx = i + 1
            exit_idx = i + horizon
            if entry_idx >= n or exit_idx >= n:
                continue
            entry = float(frame["Open"].iloc[entry_idx])
            exit_price = float(frame["Close"].iloc[exit_idx])
            if entry <= 0 or exit_price <= 0:
                continue
            returns.append(exit_price / entry - 1)
        if returns:
            avg = sum(returns) / len(returns)
            wins = sum(1 for value in returns if value > 0) / len(returns)
            result[str(horizon)] = {
                "count": len(returns),
                "avg": round(avg, 4),
                "win_rate": round(wins, 4),
                "best": round(max(returns), 4),
                "worst": round(min(returns), 4),
            }
        else:
            result[str(horizon)] = {
                "count": 0,
                "avg": 0.0,
                "win_rate": 0.0,
                "best": 0.0,
                "worst": 0.0,
            }
    return result


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def _simulate(
    frame: pd.DataFrame,
    series: list[dict],
    start: int,
    mode: str,
) -> dict:
    signal_map = {point["index"]: point for point in series}
    n = len(frame)
    cash = 100000.0
    shares = 0.0
    entry_price = 0.0
    entry_idx = 0
    equity: list[float] = []
    trade_returns: list[float] = []

    if mode == "avoid":
        first_open = float(frame["Open"].iloc[start])
        if first_open > 0:
            shares = cash / first_open
            cash = 0.0
            entry_price = first_open
            entry_idx = start

    for i in range(start + 1, n):
        signal = signal_map.get(i - 1, {})
        open_price = float(frame["Open"].iloc[i])
        close_price = float(frame["Close"].iloc[i])
        triggered = bool(signal.get("triggered"))
        direction = signal.get("direction", "neutral")

        if mode in ("long", "both"):
            if shares == 0 and triggered and direction == "bullish":
                if open_price > 0:
                    shares = cash / (open_price * (1 + COMMISSION))
                    cash = 0.0
                    entry_price = open_price
                    entry_idx = i
            elif shares > 0:
                hold_days = i - entry_idx
                exit_price = close_price
                should_exit = False
                if close_price <= entry_price * (1 - STOP_LOSS):
                    should_exit = True
                elif hold_days >= MAX_HOLD_DAYS:
                    should_exit = True
                elif triggered and direction == "bearish":
                    should_exit = True
                    exit_price = open_price
                if should_exit and exit_price > 0:
                    cash = shares * exit_price * (1 - COMMISSION - STAMP_DUTY)
                    if entry_price > 0:
                        trade_returns.append(exit_price / entry_price - 1)
                    shares = 0.0
        else:  # avoid
            if shares > 0 and triggered and direction == "bearish":
                if open_price > 0:
                    cash = shares * open_price * (1 - COMMISSION - STAMP_DUTY)
                    trade_returns.append(open_price / entry_price - 1)
                    shares = 0.0
            elif shares == 0 and not (triggered and direction == "bearish"):
                if open_price > 0:
                    shares = cash / (open_price * (1 + COMMISSION))
                    cash = 0.0
                    entry_price = open_price
                    entry_idx = i

        equity.append(cash + shares * close_price)

    if shares > 0:
        last_close = float(frame["Close"].iloc[-1])
        cash = shares * last_close * (1 - COMMISSION - STAMP_DUTY)
        if entry_price > 0:
            trade_returns.append(last_close / entry_price - 1)
        shares = 0.0
        if equity:
            equity[-1] = cash

    if not equity:
        return {
            "type": mode,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "trade_count": 0,
            "win_rate": 0.0,
            "final_equity": 100000.0,
            "equity_curve": [],
        }

    total_return = equity[-1] / 100000.0 - 1
    daily_returns = [
        equity[i] / equity[i - 1] - 1
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]
    mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    variance = (
        sum((value - mean) ** 2 for value in daily_returns) / len(daily_returns)
        if len(daily_returns) > 1
        else 0.0
    )
    std = variance**0.5
    sharpe = (mean / std * (252**0.5)) if std > 0 else 0.0
    win_rate = (
        sum(1 for value in trade_returns if value > 0) / len(trade_returns)
        if trade_returns
        else 0.0
    )
    dates = frame["Date"].iloc[start + 1 :].dt.strftime("%Y-%m-%d").tolist()
    curve = [
        {"date": d, "equity": round(e, 2)}
        for d, e in zip(dates, equity, strict=False)
    ]
    return {
        "type": mode,
        "total_return": round(total_return, 4),
        "max_drawdown": round(_max_drawdown(equity), 4),
        "sharpe": round(sharpe, 3),
        "trade_count": len(trade_returns),
        "win_rate": round(win_rate, 4),
        "final_equity": round(equity[-1], 2),
        "equity_curve": curve,
    }


def run_strategy_backtest(
    ticker: str,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
    days: int = 250,
) -> dict:
    """对选中的策略做信号有效性回测。"""
    frame = _load_frame(ticker, end_date)
    n = len(frame)
    window = min(WINDOW, n)
    days = max(60, min(int(days), 300))
    start = max(65, n - days)
    if start >= n:
        start = max(65, n // 2)

    if strategy_keys:
        selected = [key for key in strategy_keys if key in STRATEGY_MAP]
    else:
        selected = [key for key in DEFAULT_STRATEGY_KEYS if key in STRATEGY_MAP]
    if not selected:
        selected = list(STRATEGY_MAP.keys())

    benchmark = (
        float(frame["Close"].iloc[-1]) / float(frame["Open"].iloc[start]) - 1
        if start < n
        else 0.0
    )

    results = []
    for key in selected:
        meta = META_MAP.get(key, {"key": key, "name": key, "category": "", "description": ""})
        series = _build_signal_series(frame, key, start, window)
        forward = _forward_stats(frame, series)
        direction = series[-1]["direction"] if series else "neutral"
        triggered_points = [point for point in series if point["triggered"]]
        triggered_count = len(triggered_points)
        triggered_directions = {point["direction"] for point in triggered_points}
        recent_direction = (
            triggered_points[-1]["direction"] if triggered_points else "neutral"
        )

        mode = None
        if "bullish" in triggered_directions:
            # 多头信号：触发买入，止损/最长持有/看空信号退出
            mode = "long"
        elif "bearish" in triggered_directions:
            mode = "avoid"
        equity = _simulate(frame, series, start, mode) if mode else None

        results.append(
            {
                "key": key,
                "name": meta["name"],
                "category": meta.get("category", ""),
                "description": meta.get("description", ""),
                "direction": direction,
                "recent_direction": recent_direction,
                "signal_count": triggered_count,
                "forward": forward,
                "equity": equity,
            }
        )

    results.sort(
        key=lambda item: item["forward"].get("10", {}).get("avg", 0),
        reverse=True,
    )

    return {
        "success": True,
        "ticker": ticker,
        "start_date": frame["Date"].iloc[start].strftime("%Y-%m-%d"),
        "end_date": frame["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "bars": n - start,
        "benchmark_return": round(benchmark, 4),
        "strategies": results,
    }
