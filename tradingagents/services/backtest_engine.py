"""共享回测引擎：事件驱动的 A 股撮合模拟。

WHY：项目里此前存在两套互不相通的回测实现——``services/backtest.py`` 用向量化
的仓位序列（永远 0/1，即永远满仓/空仓），``services/strategy_backtest.py`` 自建
一套成本与止损口径。两者成本模型不同、止损口径不同（一个固定 8%、一个 ATR）、
评估窗口不同（180 根 bar vs 线上扫描的 500 自然日），因此"回测结论"与"线上
行为"从来不是同一个东西。

本模块把撮合收敛到**唯一实现**：显式的逐 bar 事件驱动模拟，把 A 股下列
**无法向量化表达**的约束放在同一处：

  - T+1：当日买入不可当日卖出；
  - 涨跌停不可成交：涨停板开盘无法买入、跌停板开盘无法卖出；
  - 停牌/节假日缺口：缺口内不可成交，止损不能按止损价成交，只能按复牌开盘价；
  - 100 股整手：不足一手不开仓；
  - 最低佣金 5 元：单边收取，与费率无关；
  - 滑点：作用在**成交价**上，而不是事后按比例扣减；
  - ATR 止损/止盈：与 ``services/decision_plan.py`` 的决策卡使用同一组常数，
    保证"回测的风险模型 == 部署的风险模型"。

只依赖 pandas + 标准库，不引入任何第三方依赖。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pandas as pd

from tradingagents.dataflows.ashare_rules import price_limit_pct
from tradingagents.strategies.base import LIVE_SCAN_WINDOW_DAYS as _LIVE_SCAN_WINDOW_DAYS
from tradingagents.strategies.base import smma

logger = logging.getLogger(__name__)

#: 实时扫描（``strategies/scanner.py``）使用的回看窗口，自然日。
#: 回测必须与线上一致 —— 否则"被验证的对象"和"被部署的对象"不是同一个。
#: 常量定义在 strategies/base.py，此处转出以保持回测模块自洽。
LIVE_SCAN_WINDOW_DAYS = _LIVE_SCAN_WINDOW_DAYS

#: A 股一手 = 100 股。
LOT_SIZE = 100

#: 最小有效样本量门槛：低于该值的统计量不允许被当作"性能指标"渲染。
#: 15 个策略 × 3 个前瞻周期 = 45 个统计量，没有样本量门槛就一定会挑出噪声。
MIN_EFFECTIVE_SAMPLE = 8

#: 价格比较容差（A 股报价按分四舍五入，比较时留一点容差）。
_PRICE_EPS = 1e-6

#: 年化交易日数。
TRADING_DAYS_PER_YEAR = 252


@dataclass
class CostModel:
    """A 股交易成本模型（唯一版本）。

    WHY：此前两套回测的成本口径不一致（一边把滑点当成本比例扣减，一边完全
    没有滑点），导致同一策略在两条路径上的收益不可比。这里统一为"成交价 +
    逐边费用"模型：滑点作用在成交价上，最低佣金作用在每笔成交上。
    """

    #: 佣金费率（万三，双边）。
    commission_rate: float = 0.0003
    #: 印花税（万五，仅卖出）。
    stamp_duty_rate: float = 0.0005
    #: 滑点（作用在成交价上）。默认非零：此前默认为 0，系统性乐观。
    slippage_rate: float = 0.001
    #: 单笔最低佣金（元），与费率无关。
    min_commission: float = 5.0
    #: 过户费（双边，量级极小但必须有）。
    transfer_fee_rate: float = 0.00001

    def buy_cost(self, amount: float) -> float:
        """买入总费用 = 佣金（含最低佣金）+ 过户费。"""
        if amount <= 0:
            return 0.0
        commission = max(self.min_commission, amount * self.commission_rate)
        return commission + amount * self.transfer_fee_rate

    def sell_cost(self, amount: float) -> float:
        """卖出总费用 = 佣金（含最低佣金）+ 印花税 + 过户费。"""
        if amount <= 0:
            return 0.0
        commission = max(self.min_commission, amount * self.commission_rate)
        return commission + amount * (self.stamp_duty_rate + self.transfer_fee_rate)

    def to_dict(self) -> dict[str, float]:
        return {
            "commission_rate": self.commission_rate,
            "stamp_duty_rate": self.stamp_duty_rate,
            "slippage_rate": self.slippage_rate,
            "min_commission": self.min_commission,
            "transfer_fee_rate": self.transfer_fee_rate,
        }

    def without_costs(self) -> CostModel:
        """返回一个零成本副本，用于计算"毛收益"对照。"""
        return CostModel(
            commission_rate=0.0,
            stamp_duty_rate=0.0,
            slippage_rate=0.0,
            min_commission=0.0,
            transfer_fee_rate=0.0,
        )


@dataclass
class ExecutionRules:
    """A 股撮合规则（唯一版本）。

    止损/止盈的 ATR 常数与 ``services/decision_plan.py`` 完全一致
    （ATR_STOP_MULT=2.0 / MIN_STOP_PCT=0.03 / ATR_TARGET_MULT=3.0 /
    MIN_TARGET_PCT=0.06），仓位公式同为 ``risk_per_trade / risk_pct``。
    """

    #: 最小交易单位（一手 = 100 股）。
    lot_size: int = LOT_SIZE
    #: T+1：当日买入不可当日卖出。
    t_plus_1: bool = True
    #: 涨跌停板不可成交。
    limit_price_blocked: bool = True
    #: 单标的仓位上限（占总资金比例）。
    max_position_pct: float = 1.0
    #: ATR 止损倍数。
    stop_atr_multiple: float = 2.0
    #: 止损最小距离（占参考价比例）。
    stop_min_pct: float = 0.03
    #: ATR 止盈倍数。
    target_atr_multiple: float = 3.0
    #: 止盈最小距离（占参考价比例）。
    target_min_pct: float = 0.06
    #: 最长持有交易日（<= 0 表示不限制）。
    max_hold_days: int = 20
    #: ATR 周期（Wilder），与 ``strategies/base.py::atr`` 一致。
    atr_window: int = 14
    #: ATR 缺失时的兜底比例（与决策卡 ATR_FALLBACK_PCT 一致）。
    atr_fallback_pct: float = 0.03
    #: 单笔风险预算（占总资金比例）；<= 0 表示不按风险缩放仓位。
    risk_per_trade: float = 0.01
    #: 是否启用 ATR 止损（买入持有等场景关闭）。
    stop_enabled: bool = True
    #: 是否启用 ATR 止盈。
    target_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot_size": self.lot_size,
            "t_plus_1": self.t_plus_1,
            "limit_price_blocked": self.limit_price_blocked,
            "max_position_pct": self.max_position_pct,
            "stop_atr_multiple": self.stop_atr_multiple,
            "stop_min_pct": self.stop_min_pct,
            "target_atr_multiple": self.target_atr_multiple,
            "target_min_pct": self.target_min_pct,
            "max_hold_days": self.max_hold_days,
            "atr_window": self.atr_window,
            "atr_fallback_pct": self.atr_fallback_pct,
            "risk_per_trade": self.risk_per_trade,
            "stop_enabled": self.stop_enabled,
            "target_enabled": self.target_enabled,
        }


@dataclass
class SimulationResult:
    """一次模拟的完整结果。"""

    trades: list[dict[str, Any]] = field(default_factory=list)
    rejected_fills: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    equity_values: list[float] = field(default_factory=list)
    final_equity: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    total_cost: float = 0.0
    notes: list[str] = field(default_factory=list)

    def summary(self, initial_cash: float) -> dict[str, Any]:
        """输出统一的指标字典（两个入口共用同一套 key）。"""
        return {
            "total_return": round(self.total_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4),
            "win_rate": round(self.win_rate, 4),
            "trade_count": self.trade_count,
            "final_equity": round(self.final_equity, 2),
            "total_cost": round(self.total_cost, 2),
            "cost_rate": round(self.total_cost / initial_cash, 4) if initial_cash else 0.0,
            "effective_sample_size": self.trade_count,
            "sample_insufficient": self.trade_count < MIN_EFFECTIVE_SAMPLE,
            "equity_curve": self.equity_curve,
        }


# ---------------------------------------------------------------------------
# 指标工具
# ---------------------------------------------------------------------------
def atr_series(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ATR 序列。

    WHY：逐 bar 回测需要**历史某一天的 ATR**，而 ``strategies/base.py::atr``
    只返回最后一个值。这里复用同一套 ``smma`` 平滑口径，保证回测里的 ATR
    与线上决策卡里的 ATR 是同一个数。
    """
    if frame.empty:
        return pd.Series(dtype=float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    true_range = true_range.fillna(high - low)
    return smma(true_range, window)


def daily_returns(equity: Sequence[float]) -> list[float]:
    """由净值序列计算日收益率。"""
    values = list(equity)
    returns: list[float] = []
    for index in range(1, len(values)):
        previous = values[index - 1]
        if previous > 0:
            returns.append(values[index] / previous - 1.0)
    return returns


def max_drawdown(equity: Sequence[float] | pd.Series) -> float:
    """最大回撤（负数，例如 -0.25 表示最大回撤 25%）。"""
    values = list(equity)
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def sharpe(returns: Sequence[float], periods: int = TRADING_DAYS_PER_YEAR) -> float:
    """年化夏普比率（无风险利率按 0 处理）。"""
    values = [float(item) for item in returns]
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = math.sqrt(variance)
    if std <= 0:
        return 0.0
    return mean / std * math.sqrt(periods)


def build_equity_curve(
    frame: pd.DataFrame,
    equity_values: Sequence[float],
    start_index: int = 0,
) -> list[dict[str, Any]]:
    """把净值序列转成前端可消费的 ``[{"date", "equity"}]`` 结构。"""
    curve: list[dict[str, Any]] = []
    for offset, value in enumerate(equity_values):
        index = start_index + offset
        if index >= len(frame):
            break
        date_value = frame["Date"].iloc[index]
        curve.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "equity": round(float(value), 2),
            }
        )
    return curve


# ---------------------------------------------------------------------------
# 统计检验（纯 Python 实现，不引入 scipy）
# ---------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float) -> float:
    """不完全 Beta 函数的连分式展开（Lentz 方法，Numerical Recipes）。"""
    max_iter, eps, tiny = 300, 3.0e-16, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    front = math.exp(log_beta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_two_sided_p(t_stat: float, df: int) -> float:
    """t 分布双侧 p 值。

    公式：``p = I_{df/(df+t^2)}(df/2, 1/2)``。这里用不完全 Beta 函数精确
    计算，**不依赖 scipy**，也不使用正态近似 —— 有效样本量经常只有 8~20，
    正态近似在那里会明显失真。
    """
    if df < 1:
        return 1.0
    if t_stat == 0.0:
        return 1.0
    x = df / (df + t_stat * t_stat)
    return max(0.0, min(1.0, _betai(df / 2.0, 0.5, x)))


def one_sample_t_test(values: Sequence[float], mu: float = 0.0) -> tuple[float, float]:
    """单样本 t 检验，返回 ``(t 统计量, 双侧 p 值)``。

    WHY：只报"平均前瞻收益"而不报 p 值，等于把噪声当结论。这里用纯 Python
    实现（``math`` + 不完全 Beta 函数），不引入第三方依赖。
    """
    sample = [float(item) for item in values]
    n = len(sample)
    if n < 2:
        return 0.0, 1.0
    mean = sum(sample) / n
    variance = sum((value - mean) ** 2 for value in sample) / (n - 1)
    std = math.sqrt(variance)
    if std <= 0:
        # 全部观测值相同：均值等于总体均值，无信息量。
        return 0.0, 1.0
    t_stat = (mean - mu) / (std / math.sqrt(n))
    return t_stat, _t_two_sided_p(t_stat, n - 1)


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg FDR 校正，返回与输入顺序一致的校正后 p 值。

    算法：按 p 值升序，``adjusted_(i) = min_{k>=i} (m / k * p_(k))``，裁剪到 1。

    WHY 选 BH 而不是 Bonferroni：15 个策略 × 3 个前瞻周期 = 45 个检验，
    Bonferroni 的阈值是 0.011，几乎会把所有"温和但真实"的信号判成不显著；
    BH 控制的是**错误发现比例**（FDR），与"要从一堆策略里挑出真正有效的"
    这一使用场景更匹配。
    """
    values = [max(0.0, min(1.0, float(item))) for item in p_values]
    m = len(values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: values[i])
    adjusted = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        candidate = min(1.0, values[index] * m / rank)
        running = min(running, candidate)
        adjusted[index] = running
    return adjusted


def limit_prices(prev_close: float | None, limit_pct: float) -> tuple[float | None, float | None]:
    """按前收盘价计算涨停价 / 跌停价（四舍五入到分）。

    WHY：不能直接用浮点比较，必须按交易所规则四舍五入到分，否则
    ``open == limit_up`` 这类边界会漏判。Python 的 ``round`` 是银行家舍入，
    与交易所规则不同，这里用 half-up。
    """
    if prev_close is None or prev_close <= 0:
        return None, None
    return (
        math.floor(prev_close * (1 + limit_pct) * 100 + 0.5) / 100,
        math.floor(prev_close * (1 - limit_pct) * 100 + 0.5) / 100,
    )


def _missing_weekdays(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """统计 ``(start, end)`` 之间缺失的工作日数量。

    WHY：AkShare 对停牌日**不返回任何行**，日历被压缩；周末同样没有行，
    但周末不缺失工作日。用"缺失工作日数"可以把"跳过了交易日"与"只是周末"
    区分开，从而识别出非交易间隔（停牌或节假日）。
    """
    count = 0
    cursor = start + timedelta(days=1)
    while cursor < end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def detect_gaps(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """检测行情日历中的非交易间隔（停牌 / 节假日）。

    返回每段间隔的下标、跨越的自然日数与缺失的工作日数。
    """
    gaps: list[dict[str, Any]] = []
    if frame is None or len(frame) < 2:
        return gaps
    dates = pd.to_datetime(frame["Date"])
    for index in range(1, len(frame)):
        previous = dates.iloc[index - 1]
        current = dates.iloc[index]
        missing = _missing_weekdays(previous, current)
        if missing <= 0:
            continue
        gaps.append(
            {
                "index": index,
                "prev_date": previous,
                "date": current,
                "gap_days": int((current - previous).days),
                "missing_sessions": int(missing),
            }
        )
    return gaps


# ---------------------------------------------------------------------------
# 撮合核心
# ---------------------------------------------------------------------------
def _normalize_signals(signal_series: Sequence[int] | pd.Series, length: int) -> list[int]:
    """把信号序列规范化为 0/1 列表（1 = 想持有，0 = 想空仓）。"""
    if isinstance(signal_series, pd.Series):
        raw = signal_series.tolist()
    else:
        raw = list(signal_series)
    signals: list[int] = []
    for index in range(length):
        value = raw[index] if index < len(raw) else 0
        try:
            signals.append(1 if int(value) > 0 else 0)
        except (TypeError, ValueError):
            signals.append(0)
    return signals


def _at_limit(price: float, limit_price: float | None, upward: bool) -> bool:
    """判断某个价格是否"贴在涨跌停价上"。

    WHY 需要容差带：长期停牌复牌后交易所会重设参考价，个股可以在一次
    跳空里走出超过 ±10% 的幅度（此时数据里的价格远低于/高于理论涨跌停
    价）。这种情况下不能按"跌停封死"处理，否则会把**完全无法成交**的
    止损误判成可成交。因此只有当价格确实**贴住**涨跌停价时才判定封板。
    """
    if limit_price is None or limit_price <= 0:
        return False
    tolerance = limit_price * 0.01
    if upward:
        return limit_price - _PRICE_EPS <= price <= limit_price + tolerance
    return limit_price - tolerance <= price <= limit_price + _PRICE_EPS


def _resolve_exit_price(
    kind: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    stop_price: float,
    target_price: float,
    limit_down: float | None,
    limit_blocked: bool,
    gap_before: bool,
) -> tuple[float | None, str]:
    """把"想离场"翻译成"实际能成交的价格"。

    返回 ``(成交价, 说明)``；``成交价为 None`` 表示该笔离场当下不可成交
    （跌停封死），需要延后到下一个可成交的时点。

    WHY：这是回测里最容易被高估的一环。旧实现无条件按开盘价/收盘价离场，
    等于假设"跌停板上也能随时卖出"，而 A 股跌停封单堆积时根本卖不掉。
    """
    if kind == "open":
        if limit_blocked and _at_limit(open_price, limit_down, upward=False):
            return None, "跌停封板，无法卖出"
        return open_price, "开盘离场"

    if kind == "stop":
        if limit_blocked and _at_limit(open_price, limit_down, upward=False):
            return None, "跌停封板，无法卖出"
        if gap_before and open_price < stop_price - _PRICE_EPS:
            # 复牌直接跳空穿越止损：止损在缺口内不可能按止损价成交，
            # 只能按（更差的）复牌开盘价成交。
            return open_price, "跳空穿越止损，按复牌开盘价成交"
        if limit_blocked and _at_limit(low_price, limit_down, upward=False):
            # 盘中触及跌停：止损价无法成交，退化为按开盘价成交。
            return open_price, "盘中跌停，止损价不可成交"
        return min(stop_price, open_price), "止损离场"

    if kind == "target":
        if limit_blocked and _at_limit(high_price, limit_down, upward=False):
            return None, "跌停封板，无法卖出"
        return max(target_price, open_price), "止盈离场"

    # kind == "close"：按收盘价强制离场
    if limit_blocked and _at_limit(close_price, limit_down, upward=False):
        return None, "跌停封板，无法卖出"
    return close_price, "收盘离场"


def simulate_trades(
    frame: pd.DataFrame,
    signal_series: Sequence[int] | pd.Series,
    *,
    cost_model: CostModel | None = None,
    rules: ExecutionRules | None = None,
    initial_cash: float = 100000.0,
    ticker: str = "",
    stock_name: str = "",
    start_index: int = 0,
    atr_window: int | None = None,
) -> SimulationResult:
    """事件驱动撮合。

    参数
    ----
    frame:
        日线行情，需含 ``Date/Open/High/Low/Close``。
    signal_series:
        与 ``frame`` 行对齐的 0/1 序列。``signal_series[i] = 1`` 表示
        "第 i 根 bar 收盘时希望持有多头"。成交发生在**下一根 bar 的开盘**
        （信号次日开盘执行），因此天然没有未来函数。
    """
    cost_model = cost_model or CostModel()
    rules = rules or ExecutionRules()

    result = SimulationResult()
    if frame is None or len(frame) == 0:
        result.final_equity = float(initial_cash)
        return result
    if "Date" not in frame.columns:
        raise ValueError("行情数据缺少 Date 列")

    total_bars = len(frame)
    start_index = max(0, min(int(start_index), total_bars - 1))
    signals = _normalize_signals(signal_series, total_bars)

    opens = frame["Open"].astype(float).tolist()
    highs = frame["High"].astype(float).tolist()
    lows = frame["Low"].astype(float).tolist()
    closes = frame["Close"].astype(float).tolist()
    dates = pd.to_datetime(frame["Date"])

    window = atr_window or rules.atr_window
    atr_values = atr_series(frame, window).tolist()
    limit_pct = price_limit_pct(ticker, stock_name) if ticker else 0.10
    gap_map = {item["index"]: item for item in detect_gaps(frame)}

    cash = float(initial_cash)
    shares = 0
    entry_index = -1
    entry_price = 0.0
    entry_amount = 0.0
    entry_cost = 0.0
    stop_price = 0.0
    target_price = 0.0
    pending_exit = False
    pending_note = ""
    gap_moves: list[dict[str, Any]] = []
    equity_values: list[float] = []
    total_cost = 0.0
    trade_returns: list[float] = []

    for index in range(start_index, total_bars):
        # 决策来自上一根 bar 收盘，本根开盘执行
        desired = signals[index - 1] if index > 0 else 0
        open_price = opens[index]
        high_price = highs[index]
        low_price = lows[index]
        close_price = closes[index]
        date_text = dates.iloc[index].strftime("%Y-%m-%d")

        prev_close = closes[index - 1] if index > 0 else None
        limit_up, limit_down = limit_prices(prev_close, limit_pct)
        gap_before = index in gap_map

        # 缺口内的价格路径不可知：持仓跨过非交易间隔时，收益只能记成一笔
        # "跳空变动"，不能被拆成若干个假想的日内波动。
        if gap_before and shares > 0 and prev_close and prev_close > 0:
            gap_moves.append(
                {
                    "date": date_text,
                    "gap_days": gap_map[index]["gap_days"],
                    "missing_sessions": gap_map[index]["missing_sessions"],
                    "move": round(open_price / prev_close - 1.0, 6),
                }
            )

        # ---------------- 买入 ----------------
        if shares == 0 and desired == 1 and open_price > 0:
            if (
                rules.limit_price_blocked
                and _at_limit(open_price, limit_up, upward=True)
            ):
                # 涨停板开盘：想买买不到。这是突破类策略最大的高估来源 ——
                # 它们恰恰在股票跳空涨停的那一天发出买入信号。
                result.rejected_fills.append(
                    {
                        "date": date_text,
                        "action": "buy",
                        "reason": "limit_up",
                        "detail": f"开盘价 {open_price:.2f} 处于涨停价 {limit_up:.2f}，无法成交",
                    }
                )
                logger.debug("买入被拒：%s 涨停无法成交", date_text)
            else:
                ref_index = index - 1 if index > 0 else index
                ref_close = closes[ref_index] if ref_index >= 0 else open_price
                atr_value = atr_values[ref_index] if ref_index >= 0 else 0.0
                atr_distance = (
                    atr_value if atr_value > 0 else ref_close * rules.atr_fallback_pct
                )
                stop_distance = max(
                    atr_distance * rules.stop_atr_multiple,
                    ref_close * rules.stop_min_pct,
                )
                target_distance = max(
                    atr_distance * rules.target_atr_multiple,
                    ref_close * rules.target_min_pct,
                )
                stop_price = ref_close - stop_distance
                target_price = ref_close + target_distance
                risk_pct = stop_distance / ref_close if ref_close > 0 else 0.0

                fill_price = open_price * (1 + cost_model.slippage_rate)
                # 仓位：风险预算驱动（与决策卡一致），再受单标的上限约束
                if rules.risk_per_trade > 0 and risk_pct > 0:
                    position_pct = min(
                        rules.max_position_pct, rules.risk_per_trade / risk_pct
                    )
                else:
                    position_pct = rules.max_position_pct
                budget = cash * position_pct
                raw_shares = (
                    math.floor(
                        budget / (fill_price * (1 + cost_model.commission_rate))
                        / rules.lot_size
                    )
                    * rules.lot_size
                )
                if raw_shares < rules.lot_size:
                    result.notes.append(
                        f"{date_text}：资金不足一手，未开仓"
                    )
                else:
                    entry_amount = raw_shares * fill_price
                    entry_cost = cost_model.buy_cost(entry_amount)
                    cash -= entry_amount + entry_cost
                    total_cost += entry_cost
                    shares = raw_shares
                    entry_index = index
                    entry_price = fill_price

        # ---------------- 离场 ----------------
        if shares > 0:
            can_exit = not (rules.t_plus_1 and index == entry_index)
            if can_exit:
                candidates: list[tuple[str, str]] = []
                if pending_exit:
                    candidates.append(("open", f"延后离场（{pending_note or '不可成交'}）"))
                elif desired == 0:
                    candidates.append(("open", "信号反转离场"))
                if (
                    rules.stop_enabled
                    and stop_price > 0
                    and low_price <= stop_price + _PRICE_EPS
                ):
                    candidates.append(("stop", "止损"))
                if (
                    rules.target_enabled
                    and target_price > 0
                    and high_price >= target_price - _PRICE_EPS
                ):
                    candidates.append(("target", "止盈"))
                if (
                    rules.max_hold_days > 0
                    and (index - entry_index) >= rules.max_hold_days
                ):
                    candidates.append(("close", "达到最长持有期"))

                blocked: tuple[str, str] | None = None
                for kind, reason in candidates:
                    fill, note = _resolve_exit_price(
                        kind,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        stop_price,
                        target_price,
                        limit_down,
                        rules.limit_price_blocked,
                        gap_before and kind == "stop",
                    )
                    if fill is None:
                        # 当下不可成交（跌停封死）：离场指令挂起，等待下一个
                        # 可成交时点。旧实现无条件按收盘价离场，等于假设
                        # "跌停板上也能随时卖出"。
                        if blocked is None:
                            blocked = (kind, note)
                        continue
                    exit_price = fill * (1 - cost_model.slippage_rate)
                    amount = shares * exit_price
                    exit_cost = cost_model.sell_cost(amount)
                    cash += amount - exit_cost
                    total_cost += exit_cost
                    pnl = amount - exit_cost - entry_amount - entry_cost
                    base = entry_amount + entry_cost
                    ret = pnl / base if base > 0 else 0.0
                    trade_returns.append(ret)
                    result.trades.append(
                        {
                            "entry_date": dates.iloc[entry_index].strftime("%Y-%m-%d"),
                            "entry_price": round(entry_price, 4),
                            "shares": int(shares),
                            "exit_date": date_text,
                            "exit_price": round(exit_price, 4),
                            "exit_reason": reason,
                            "hold_days": int(index - entry_index),
                            "pnl": round(pnl, 2),
                            "return_pct": round(ret, 4),
                            "cost": round(entry_cost + exit_cost, 2),
                            "stop_price": round(stop_price, 4),
                            "target_price": round(target_price, 4),
                            "gap_moves": list(gap_moves),
                        }
                    )
                    shares = 0
                    pending_exit = False
                    pending_note = ""
                    gap_moves = []
                    break
                else:
                    if blocked is not None:
                        pending_exit = True
                        pending_note = blocked[1]
                        result.notes.append(f"{date_text}：{blocked[1]}，离场指令延后")

        # ---------------- 净值 ----------------
        equity_values.append(cash + shares * close_price)

    # 期末强制平仓：按最后一根 bar 的收盘价清算，否则净值里永远挂着浮盈。
    if shares > 0:
        final_index = total_bars - 1
        final_price = closes[final_index] * (1 - cost_model.slippage_rate)
        amount = shares * final_price
        exit_cost = cost_model.sell_cost(amount)
        cash += amount - exit_cost
        total_cost += exit_cost
        pnl = amount - exit_cost - entry_amount - entry_cost
        base = entry_amount + entry_cost
        ret = pnl / base if base > 0 else 0.0
        trade_returns.append(ret)
        result.trades.append(
            {
                "entry_date": dates.iloc[entry_index].strftime("%Y-%m-%d"),
                "entry_price": round(entry_price, 4),
                "shares": int(shares),
                "exit_date": dates.iloc[final_index].strftime("%Y-%m-%d"),
                "exit_price": round(final_price, 4),
                "exit_reason": "期末平仓",
                "hold_days": int(final_index - entry_index),
                "pnl": round(pnl, 2),
                "return_pct": round(ret, 4),
                "cost": round(entry_cost + exit_cost, 2),
                "stop_price": round(stop_price, 4),
                "target_price": round(target_price, 4),
                "gap_moves": list(gap_moves),
            }
        )
        shares = 0
        if equity_values:
            equity_values[-1] = cash

    result.equity_values = equity_values
    result.equity_curve = build_equity_curve(frame, equity_values, start_index)
    result.final_equity = cash
    result.total_return = (cash / initial_cash - 1.0) if initial_cash else 0.0
    result.max_drawdown = max_drawdown(equity_values)
    result.sharpe = sharpe(daily_returns(equity_values))
    result.trade_count = len(result.trades)
    result.win_rate = (
        sum(1 for value in trade_returns if value > 0) / len(trade_returns)
        if trade_returns
        else 0.0
    )
    result.total_cost = total_cost
    return result


def buy_and_hold(
    frame: pd.DataFrame,
    *,
    cost_model: CostModel | None = None,
    rules: ExecutionRules | None = None,
    initial_cash: float = 100000.0,
    ticker: str = "",
    stock_name: str = "",
    start_index: int = 0,
) -> SimulationResult:
    """买入持有基准：用同一套成本模型与撮合规则跑一次全仓持有。"""
    base_rules = rules or ExecutionRules()
    hold_rules = ExecutionRules(
        **{
            **base_rules.to_dict(),
            "stop_enabled": False,
            "target_enabled": False,
            "max_hold_days": 0,
            "risk_per_trade": 0.0,
            "max_position_pct": 1.0,
        }
    )
    signals = [1] * len(frame)
    return simulate_trades(
        frame,
        signals,
        cost_model=cost_model,
        rules=hold_rules,
        initial_cash=initial_cash,
        ticker=ticker,
        stock_name=stock_name,
        start_index=start_index,
    )
