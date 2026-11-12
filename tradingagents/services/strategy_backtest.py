"""策略信号回测（信号有效性 + 事件驱动净值模拟）。

方法：
  - 对每个交易日，用截至当日的历史窗口重新计算策略信号；
  - 统计信号触发后的 5/10/20 日前瞻收益与胜率；
  - 样本窗口与实时扫描（``strategies/scanner.py``）一致：500 自然日；
  - 评估窗口切分为样本内（前 60%）与样本外（后 40%），两者分别统计；
  - 净值模拟统一委托给 ``services/backtest_engine.py``，与单策略回测共用
    同一套成本模型、ATR 止损、整手、T+1 与涨跌停约束。

WHY 需要这么严格：此前这里对 15 个策略 × 3 个前瞻周期 = 45 个统计量，
不做任何多重检验校正，也不做样本外确认，还按**样本内**均值排序，
等于教科书式的数据挖掘（data snooping）。现在：
  - 排名优先用样本外前瞻均值，样本外样本不足的条目降级并显式标注；
  - 每个策略给出单样本 t 检验的 p 值（纯 Python 实现，无 scipy）；
  - 跨策略做 Benjamini-Hochberg FDR 校正，给出 ``p_value_adjusted``；
  - 报告**有效样本量**（重叠观测按周期折算），低于门槛的条目标记
    ``sample_insufficient``，不允许被当成性能指标渲染。

参数与规则均为简化模型，不构成投资建议。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from tradingagents.services.backtest_engine import (
    LIVE_SCAN_WINDOW_DAYS,
    MIN_EFFECTIVE_SAMPLE,
    CostModel,
    ExecutionRules,
    benjamini_hochberg,
    buy_and_hold,
    one_sample_t_test,
    simulate_trades,
)
from tradingagents.strategies.classic import (
    ALL_STRATEGIES,
    DEFAULT_STRATEGY_KEYS,
    STRATEGY_META,
)

logger = logging.getLogger(__name__)

STRATEGY_MAP = {func.__name__: func for func in ALL_STRATEGIES}
META_MAP = {item["key"]: item for item in STRATEGY_META}

#: 前瞻周期（交易日）。
HORIZONS = (5, 10, 20)

#: 排名所用的前瞻周期（与旧实现保持一致：10 日）。
RANK_HORIZON = 10

#: 样本内占比，其余为样本外。
IN_SAMPLE_RATIO = 0.6

#: 最小有效样本量门槛（有效 N = 重叠观测数 / 周期）。
MIN_EFFECTIVE_SAMPLE_SIZE = MIN_EFFECTIVE_SAMPLE

#: 评估窗口（自然日），与实时扫描保持一致。
EVALUATION_WINDOW_DAYS = LIVE_SCAN_WINDOW_DAYS

#: 共享成本模型（与单策略回测一致）。
COST_MODEL = CostModel()

#: 共享撮合规则（与单策略回测一致）。
EXECUTION_RULES = ExecutionRules()


def _parse_date(value: str | None) -> datetime:
    if value:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now()


def _load_frame(ticker: str, end_date: str | None, days: int = EVALUATION_WINDOW_DAYS) -> pd.DataFrame:
    """加载评估窗口内的行情。

    WHY 用 500 自然日：实时扫描用的是 ``end - timedelta(days=500)``，
    回测如果只用 180 根 bar，被验证的对象与被部署的对象就不是同一个。
    """
    from tradingagents.services.backtest import _load_prices

    end = _parse_date(end_date)
    start = end - timedelta(days=days)
    frame = _load_prices(
        ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if frame is None or len(frame) < 80:
        raise ValueError(f"行情数据不足，无法回测策略：{ticker}")
    return frame


def _build_signal_series(frame: pd.DataFrame, key: str, start: int) -> list[dict]:
    """逐 bar 重建策略信号。

    WHY 给策略**整段历史**而不是固定 180 根：实时扫描就是把整段窗口喂给
    策略函数，两者必须一致，否则回测验证的是一个线上不存在的对象。
    """
    func = STRATEGY_MAP.get(key)
    if func is None:
        return []
    series: list[dict] = []
    n = len(frame)
    for i in range(start, n):
        chunk = frame.iloc[: i + 1]
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


def _forward_returns(
    frame: pd.DataFrame,
    series: list[dict],
    begin: int,
    end: int,
    horizon: int,
) -> list[float]:
    """区间 [begin, end) 内触发信号的前瞻收益（毛收益，未扣交易成本）。

    WHY 保留毛收益：这里衡量的是**信号本身的预测力**；交易成本由净值
    模拟（``simulate_trades``）单独刻画，两者职责分开。
    """
    n = len(frame)
    returns: list[float] = []
    for point in series:
        if not point["triggered"]:
            continue
        index = point["index"]
        if index < begin or index >= end:
            continue
        entry_idx = index + 1
        exit_idx = index + horizon
        # 进出场都必须落在同一区间内，否则样本内外的边界会互相污染
        if entry_idx >= end or exit_idx >= end or exit_idx >= n:
            continue
        entry = float(frame["Open"].iloc[entry_idx])
        exit_price = float(frame["Close"].iloc[exit_idx])
        if entry <= 0 or exit_price <= 0:
            continue
        returns.append(exit_price / entry - 1)
    return returns


def _forward_stats(
    frame: pd.DataFrame,
    series: list[dict],
    begin: int,
    end: int,
) -> dict[str, dict[str, Any]]:
    """按周期统计前瞻收益，并给出有效样本量。

    WHY 有效样本量：同一只股票上相隔 5/10/20 日的触发样本彼此重叠，
    原始观测数会严重高估统计强度。周期为 h 时，有效 N ≈ 原始数 / h。
    """
    result: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        returns = _forward_returns(frame, series, begin, end, horizon)
        count = len(returns)
        effective = count / horizon if horizon > 0 else 0.0
        if returns:
            avg = sum(returns) / count
            wins = sum(1 for value in returns if value > 0) / count
            best = max(returns)
            worst = min(returns)
        else:
            avg = 0.0
            wins = 0.0
            best = 0.0
            worst = 0.0
        result[str(horizon)] = {
            # —— 保留旧 key ——
            "count": count,
            "avg": round(avg, 4),
            "win_rate": round(wins, 4),
            "best": round(best, 4),
            "worst": round(worst, 4),
            # —— 新增：有效样本量与样本量门槛 ——
            "effective_count": round(effective, 2),
            "sample_insufficient": effective < MIN_EFFECTIVE_SAMPLE_SIZE,
        }
    return result


def _max_drawdown(equity: list[float]) -> float:
    """保留旧接口：列表净值的最大回撤。"""
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
    ticker: str = "",
    cost_model: CostModel | None = None,
    rules: ExecutionRules | None = None,
) -> dict[str, Any]:
    """用共享引擎模拟策略净值。

    WHY 不再自建撮合：此前这条路径的成本模型、止损口径与单策略回测不同
    （固定 8% 止损、无滑点、无整手），两条路径的结论不可比。
    """
    cost_model = cost_model or COST_MODEL
    rules = rules or EXECUTION_RULES
    n = len(frame)
    signal_map = {point["index"]: point for point in series}

    signals = [0] * n
    for index in range(n):
        point = signal_map.get(index)
        triggered = bool(point and point.get("triggered"))
        direction = point.get("direction", "neutral") if point else "neutral"
        if mode == "avoid":
            # 看空/避险：只有出现看空信号才空仓，否则一直持有
            signals[index] = 0 if (triggered and direction == "bearish") else 1
        else:
            # 多头：触发看多才买入
            signals[index] = 1 if (triggered and direction == "bullish") else 0

    result = simulate_trades(
        frame,
        signals,
        cost_model=cost_model,
        rules=rules,
        initial_cash=100000.0,
        ticker=ticker,
        start_index=min(start, max(0, n - 1)),
    )
    summary = result.summary(100000.0)
    summary["type"] = mode
    return summary


def run_strategy_backtest(
    ticker: str,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """对选中的策略做信号有效性回测（含样本内/样本外切分与统计检验）。

    参数
    ----
    days:
        评估窗口长度（**bar** 数）。``None`` 表示使用与实时扫描一致的
        完整窗口（500 自然日 ≈ 340 根 bar）。显式传入的数值是对完整窗口
        的**收窄**，会同时压缩样本内外的样本量。
    """
    frame = _load_frame(ticker, end_date)
    n = len(frame)

    if days is None:
        start = 0
    else:
        days = max(60, min(int(days), 400))
        start = max(0, n - days)
    if start >= n:
        start = max(0, n // 2)

    if strategy_keys:
        selected = [key for key in strategy_keys if key in STRATEGY_MAP]
    else:
        selected = [key for key in DEFAULT_STRATEGY_KEYS if key in STRATEGY_MAP]
    if not selected:
        selected = list(STRATEGY_MAP.keys())

    # 样本内 / 样本外切分（前 60% / 后 40%）
    evaluation_bars = n - start
    in_sample_end = start + max(1, int(evaluation_bars * IN_SAMPLE_RATIO))

    benchmark = (
        float(frame["Close"].iloc[-1]) / float(frame["Open"].iloc[start]) - 1
        if start < n
        else 0.0
    )
    # 买入持有基准：用同一套成本模型跑一遍，便于判断策略是否真的跑赢指数。
    benchmark_result = buy_and_hold(
        frame,
        cost_model=COST_MODEL,
        rules=EXECUTION_RULES,
        initial_cash=100000.0,
        ticker=ticker,
        start_index=min(start, max(0, n - 1)),
    )

    results: list[dict[str, Any]] = []
    for key in selected:
        meta = META_MAP.get(key, {"key": key, "name": key, "category": "", "description": ""})
        series = _build_signal_series(frame, key, start)
        forward = _forward_stats(frame, series, start, n)
        forward_in_sample = _forward_stats(frame, series, start, in_sample_end)
        forward_out_of_sample = _forward_stats(frame, series, in_sample_end, n)
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
        equity = (
            _simulate(frame, series, start, mode, ticker=ticker) if mode else None
        )
        # 单策略条目与 ``run_backtest`` 的顶层保持同一套 key，
        # 便于前端用同一段代码渲染两种回测结果。
        item_metrics = {
            "total_return": equity["total_return"] if equity else 0.0,
            "max_drawdown": equity["max_drawdown"] if equity else 0.0,
            "sharpe": equity["sharpe"] if equity else 0.0,
            "win_rate": equity["win_rate"] if equity else 0.0,
            "trade_count": equity["trade_count"] if equity else 0,
            "equity_curve": equity["equity_curve"] if equity else [],
        }

        # —— 统计显著性：排名周期上的单样本 t 检验 ——
        oos_returns = _forward_returns(
            frame, series, in_sample_end, n, RANK_HORIZON
        )
        is_returns = _forward_returns(frame, series, start, in_sample_end, RANK_HORIZON)
        oos_effective = len(oos_returns) / RANK_HORIZON
        is_effective = len(is_returns) / RANK_HORIZON
        oos_qualified = oos_effective >= MIN_EFFECTIVE_SAMPLE_SIZE
        if oos_qualified:
            basis_returns = oos_returns
            basis = "out_of_sample"
        else:
            basis_returns = is_returns
            basis = "in_sample_only"
        t_stat, p_value = one_sample_t_test(basis_returns)

        results.append(
            {
                "key": key,
                "name": meta["name"],
                "category": meta.get("category", ""),
                "description": meta.get("description", ""),
                "direction": direction,
                "recent_direction": recent_direction,
                "signal_count": triggered_count,
                # —— 保留旧 key ——
                "forward": forward,
                "equity": equity,
                **item_metrics,
                # —— 新增：样本内/样本外、统计量与排名依据 ——
                "forward_in_sample": forward_in_sample,
                "forward_out_of_sample": forward_out_of_sample,
                "ranking_basis": (
                    f"out_of_sample_forward_{RANK_HORIZON}d"
                    if oos_qualified
                    else "in_sample_only"
                ),
                "effective_sample_size": round(len(basis_returns) / RANK_HORIZON, 2),
                "sample_insufficient": not oos_qualified,
                "p_value": round(p_value, 6),
                "p_value_adjusted": 1.0,
                "t_stat": round(t_stat, 4),
                "in_sample_effective_sample_size": round(is_effective, 2),
                "out_of_sample_effective_sample_size": round(oos_effective, 2),
                "note": (
                    ""
                    if oos_qualified
                    else (
                        f"样本外有效样本量 {oos_effective:.1f} 低于门槛 "
                        f"{MIN_EFFECTIVE_SAMPLE_SIZE}，仅有样本内统计，"
                        "不可作为性能结论"
                    )
                ),
                "_basis_key": basis,
                "_basis_returns": basis_returns,
            }
        )

    # —— 多重检验校正 ——
    # 分组做 BH：样本外合格组与样本内-only 组各自成族，避免把不同来源的
    # p 值混在一起校正（统计上不严谨）。
    for basis_key in ("out_of_sample", "in_sample_only"):
        group = [item for item in results if item["_basis_key"] == basis_key]
        if not group:
            continue
        adjusted = benjamini_hochberg([item["p_value"] for item in group])
        for item, value in zip(group, adjusted, strict=False):
            item["p_value_adjusted"] = round(value, 6)

    # —— 排名 ——
    # 样本外合格条目在前（按样本外前瞻均值排），样本外样本不足的条目降级，
    # 只能按样本内均值排，并显式标注 ranking_basis。
    def _rank_key(item: dict[str, Any]) -> tuple[int, float]:
        horizon = str(RANK_HORIZON)
        if item["ranking_basis"].startswith("out_of_sample"):
            value = item["forward_out_of_sample"].get(horizon, {}).get("avg", 0.0)
            return (1, value)
        value = item["forward_in_sample"].get(horizon, {}).get("avg", 0.0)
        return (0, value)

    results.sort(key=_rank_key, reverse=True)
    for position, item in enumerate(results, start=1):
        item["rank"] = position
        item.pop("_basis_key", None)
        item.pop("_basis_returns", None)

    qualified = [item for item in results if not item["sample_insufficient"]]
    return {
        "success": True,
        "ticker": ticker,
        "start_date": frame["Date"].iloc[start].strftime("%Y-%m-%d"),
        "end_date": frame["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "bars": n - start,
        "benchmark_return": round(benchmark, 4),
        # —— 新增 ——
        "benchmark": {
            "total_return": round(benchmark_result.total_return, 4),
            "max_drawdown": round(benchmark_result.max_drawdown, 4),
            "sharpe": round(benchmark_result.sharpe, 4),
            "final_equity": round(benchmark_result.final_equity, 2),
            "trade_count": benchmark_result.trade_count,
        },
        "ranking_basis": (
            f"out_of_sample_forward_{RANK_HORIZON}d"
            if qualified
            else "in_sample_only"
        ),
        "sample_insufficient": len(qualified) < len(results),
        "qualified_count": len(qualified),
        "min_effective_sample_size": MIN_EFFECTIVE_SAMPLE_SIZE,
        "in_sample_end": frame["Date"].iloc[in_sample_end - 1].strftime("%Y-%m-%d"),
        "strategies": results,
    }
