"""轻量回测服务（不依赖 backtrader）。

内置策略：
  - ma_cross：快慢均线交叉
  - buy_hold：买入持有

数据通过现有 dataflows 路由获取，默认使用 A 股复权日线。
"""

from __future__ import annotations

import math
from io import StringIO
from typing import Any

import pandas as pd

from tradingagents.dataflows.interface import route_to_vendor


def _load_prices(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    text = route_to_vendor("get_stock_data", ticker, start_date, end_date)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("未获取到行情数据")

    frame = pd.read_csv(StringIO(text), comment="#")
    # 兼容中文列名（A 股数据源）与 Date 被当作索引的情况
    frame = frame.rename(
        columns={
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
        }
    )
    if "Date" not in frame.columns and frame.index.name in ("Date", "日期"):
        frame = frame.reset_index()
        if "日期" in frame.columns:
            frame = frame.rename(columns={"日期": "Date"})

    if "Date" not in frame.columns or "Close" not in frame.columns:
        raise ValueError("行情数据格式不符合预期")

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # 丢弃无效行、按日期升序、去重，避免停牌/重复行污染回测
    frame = frame.dropna(subset=["Date", "Close"])
    frame = frame[frame["Close"] > 0]
    frame = (
        frame.sort_values("Date")
        .drop_duplicates(subset=["Date"], keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError("行情数据为空")
    return frame


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return float(drawdown.min())


def run_backtest(
    ticker: str,
    start_date: str,
    end_date: str,
    strategy: str = "ma_cross",
    fast: int = 5,
    slow: int = 20,
    initial_cash: float = 100000.0,
    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.0005,
    slippage_rate: float = 0.0,
) -> dict[str, Any]:
    """运行轻量回测。

    A 股默认交易成本：
      - 佣金 commission_rate=0.0003（万三，双边）
      - 印花税 stamp_duty_rate=0.0005（万五，仅卖出）
      - 滑点 slippage_rate 默认 0，可按需设置
    """
    frame = _load_prices(ticker, start_date, end_date)
    close = frame["Close"].astype(float)
    daily_return = close.pct_change().fillna(0)

    if strategy == "buy_hold":
        position = pd.Series(1.0, index=frame.index)
    else:
        fast_ma = close.rolling(max(2, int(fast))).mean()
        slow_ma = close.rolling(max(3, int(slow))).mean()
        signal = (fast_ma > slow_ma).astype(int)
        # 用前一交易日信号交易，避免未来函数（A 股 T+1 的最简体现）
        position = signal.shift(1).fillna(0).astype(float)

    # 换手成本：买入收佣金+滑点，卖出额外收印花税
    turnover = position.diff().fillna(position)
    buy_turnover = turnover.clip(lower=0)
    sell_turnover = (-turnover).clip(lower=0)
    cost = (
        buy_turnover * (commission_rate + slippage_rate)
        + sell_turnover * (commission_rate + stamp_duty_rate + slippage_rate)
    )
    gross_return = position * daily_return
    strategy_return = gross_return - cost

    equity = (1 + strategy_return).cumprod() * float(initial_cash)
    total_return = float(equity.iloc[-1] / initial_cash - 1)
    days = max(1, len(frame))
    annualized = (
        (1 + total_return) ** (252.0 / days) - 1 if total_return > -1 else -1.0
    )
    volatility = float(strategy_return.std())
    sharpe = (
        float(strategy_return.mean() / volatility * math.sqrt(252))
        if volatility > 0
        else 0.0
    )

    # 逐笔交易收益
    trades = []
    in_position = False
    entry_index = 0
    for idx, value in enumerate(position.values):
        if value == 1 and not in_position:
            in_position = True
            entry_index = idx
        elif value == 0 and in_position:
            in_position = False
            trades.append(float(close.iloc[idx] / close.iloc[entry_index] - 1))
    if in_position:
        trades.append(float(close.iloc[-1] / close.iloc[entry_index] - 1))

    win_rate = (
        sum(1 for value in trades if value > 0) / len(trades) if trades else 0.0
    )

    dates = frame["Date"].dt.strftime("%Y-%m-%d").tolist()
    curve = [
        {"date": d, "equity": round(float(v), 2)}
        for d, v in zip(dates, equity.tolist(), strict=False)
    ]

    return {
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date,
        "strategy": strategy,
        "fast": fast,
        "slow": slow,
        "initial_cash": initial_cash,
        "final_equity": round(float(equity.iloc[-1]), 2),
        "total_return": round(total_return, 4),
        "gross_return": round(
            float((1 + gross_return).cumprod().iloc[-1] - 1), 4
        ),
        "annualized_return": round(annualized, 4),
        "max_drawdown": round(_max_drawdown(equity), 4),
        "sharpe": round(sharpe, 4),
        "trade_count": len(trades),
        "win_rate": round(win_rate, 4),
        "bars": len(frame),
        "cost_rate": round(float(cost.sum()), 4),
        "commission_rate": commission_rate,
        "stamp_duty_rate": stamp_duty_rate,
        "slippage_rate": slippage_rate,
        "equity_curve": curve,
    }
