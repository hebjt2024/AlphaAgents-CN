"""轻量回测服务（不依赖 backtrader）。

内置策略：
  - ma_cross：快慢均线交叉
  - buy_hold：买入持有

数据通过现有 dataflows 路由获取，默认使用 A 股复权日线。

WHY：撮合逻辑全部下沉到 ``services/backtest_engine.py``，本模块只负责
"取数 + 生成信号 + 组织输出"。此前这里用向量化仓位序列（永远 0/1），
无法表达 T+1、涨跌停不可成交、整手、最低佣金等 A 股约束；而且遇到无法
识别的策略字符串会**静默回退成 ma_cross**，WebUI 传入任意字符串都会得到
一份看起来正常的 ma_cross 报告 —— 现在改为显式报错。
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any

import pandas as pd

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.services.backtest_engine import (
    MIN_EFFECTIVE_SAMPLE,
    CostModel,
    ExecutionRules,
    buy_and_hold,
    one_sample_t_test,
    simulate_trades,
)

logger = logging.getLogger(__name__)

#: 本入口支持的策略白名单。WebUI 的 ``BacktestRequest.strategy`` 是自由
#: 文本，必须在这里做校验，不能静默兜底。
SUPPORTED_STRATEGIES: tuple[str, ...] = ("ma_cross", "buy_hold")


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
    """保留旧接口：对外的 numpy/pandas 风格最大回撤。"""
    if equity is None or len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return float(drawdown.min())


def _ma_cross_signals(frame: pd.DataFrame, fast: int, slow: int) -> list[int]:
    """快慢均线交叉信号（1 = 持有，0 = 空仓）。

    WHY：信号在第 i 根 bar 收盘产生，成交发生在第 i+1 根 bar 开盘，
    由撮合引擎统一处理，这里不引入未来函数。
    """
    close = frame["Close"].astype(float)
    fast_ma = close.rolling(max(2, int(fast))).mean()
    slow_ma = close.rolling(max(3, int(slow))).mean()
    signal = (fast_ma > slow_ma).astype(int)
    return [int(value) for value in signal.tolist()]


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
    slippage_rate: float = 0.001,
    min_commission: float = 5.0,
    transfer_fee_rate: float = 0.00001,
    max_position_pct: float = 1.0,
    risk_per_trade: float = 0.01,
    max_hold_days: int = 20,
    stock_name: str = "",
) -> dict[str, Any]:
    """运行轻量回测（事件驱动撮合，A 股交易约束生效）。

    A 股默认交易成本：
      - 佣金 commission_rate=0.0003（万三，双边，单笔不低于 min_commission）
      - 印花税 stamp_duty_rate=0.0005（万五，仅卖出）
      - 过户费 transfer_fee_rate=0.00001（双边）
      - 滑点 slippage_rate 默认 0.001（作用在成交价上；此前默认为 0，
        等于假设所有委托都能按理想价成交，系统性乐观）

    WHY 改动滑点默认值：默认 0 会让"突破买入"这类策略系统性高估收益，
    因为它们最常在跳空高开的日子成交。
    """
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(
            f"不支持的回测策略：{strategy!r}；可用策略：{'、'.join(SUPPORTED_STRATEGIES)}"
        )

    frame = _load_prices(ticker, start_date, end_date)
    close = frame["Close"].astype(float)

    if strategy == "buy_hold":
        signals = [1] * len(frame)
        rules = ExecutionRules(
            max_position_pct=1.0,
            risk_per_trade=0.0,
            max_hold_days=0,
            stop_enabled=False,
            target_enabled=False,
        )
    else:
        signals = _ma_cross_signals(frame, fast, slow)
        rules = ExecutionRules(
            max_position_pct=max_position_pct,
            risk_per_trade=risk_per_trade,
            max_hold_days=max_hold_days,
        )

    cost_model = CostModel(
        commission_rate=commission_rate,
        stamp_duty_rate=stamp_duty_rate,
        slippage_rate=slippage_rate,
        min_commission=min_commission,
        transfer_fee_rate=transfer_fee_rate,
    )

    result = simulate_trades(
        frame,
        signals,
        cost_model=cost_model,
        rules=rules,
        initial_cash=initial_cash,
        ticker=ticker,
        stock_name=stock_name,
    )

    # 毛收益：把费率与滑点全部置零后重算，用于分离"策略本身"与"交易成本"。
    gross_result = simulate_trades(
        frame,
        signals,
        cost_model=cost_model.without_costs(),
        rules=rules,
        initial_cash=initial_cash,
        ticker=ticker,
        stock_name=stock_name,
    )

    # 买入持有基准：此前算了却从不输出，导致无法判断策略是否跑赢指数。
    benchmark = buy_and_hold(
        frame,
        cost_model=cost_model,
        rules=rules,
        initial_cash=initial_cash,
        ticker=ticker,
        stock_name=stock_name,
    )

    trade_returns = [float(trade.get("return_pct", 0.0)) for trade in result.trades]
    t_stat, p_value = one_sample_t_test(trade_returns)
    effective_sample_size = len(trade_returns)
    sample_insufficient = effective_sample_size < MIN_EFFECTIVE_SAMPLE

    days = max(1, len(frame))
    total_return = result.total_return
    annualized = _annualized(total_return, days)
    benchmark_return = (
        float(close.iloc[-1]) / float(frame["Open"].iloc[0]) - 1
        if len(frame)
        else 0.0
    )

    return {
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date,
        "strategy": strategy,
        "fast": fast,
        "slow": slow,
        "initial_cash": initial_cash,
        "final_equity": round(result.final_equity, 2),
        "total_return": round(total_return, 4),
        "gross_return": round(gross_result.total_return, 4),
        "annualized_return": round(annualized, 4),
        "max_drawdown": round(result.max_drawdown, 4),
        "sharpe": round(result.sharpe, 4),
        "trade_count": result.trade_count,
        "win_rate": round(result.win_rate, 4),
        "bars": len(frame),
        "cost_rate": round(
            result.total_cost / initial_cash if initial_cash else 0.0, 4
        ),
        "commission_rate": commission_rate,
        "stamp_duty_rate": stamp_duty_rate,
        "slippage_rate": slippage_rate,
        "equity_curve": result.equity_curve,
        # —— 新增：统计严谨性与可对比基准 ——
        "total_cost": round(result.total_cost, 2),
        "effective_sample_size": effective_sample_size,
        "sample_insufficient": sample_insufficient,
        "p_value": round(p_value, 6),
        "p_value_adjusted": round(p_value, 6),
        "ranking_basis": "single_strategy_trade_returns",
        "t_stat": round(t_stat, 4),
        "trades": result.trades,
        "rejected_fills": result.rejected_fills,
        "notes": result.notes,
        "benchmark_return": round(benchmark_return, 4),
        "benchmark": {
            "total_return": round(benchmark.total_return, 4),
            "max_drawdown": round(benchmark.max_drawdown, 4),
            "sharpe": round(benchmark.sharpe, 4),
            "final_equity": round(benchmark.final_equity, 2),
            "trade_count": benchmark.trade_count,
        },
        "cost_model": cost_model.to_dict(),
        "execution_rules": rules.to_dict(),
    }


def _annualized(total_return: float, days: int) -> float:
    """年化收益（供外部复用的小工具）。"""
    if days <= 0:
        return 0.0
    return (1 + total_return) ** (252.0 / days) - 1 if total_return > -1 else -1.0


__all__ = ["run_backtest", "SUPPORTED_STRATEGIES"]
