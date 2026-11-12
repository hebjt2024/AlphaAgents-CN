"""策略信号的基础数据结构和通用指标。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

# ---------------------------------------------------------------------------
# 共识强度与仓位门槛（集中定义，避免各处出现魔数）
# ---------------------------------------------------------------------------

#: 达到"满强度"所需的最小触发信号数量。
#: 少于该数量时共识强度按比例衰减，避免"仅 1 条规则触发 = 强度 100%"。
MIN_SIGNALS_FOR_FULL_STRENGTH = 3

#: 允许给出建议仓位的最小触发信号数量。
#: 单条规则信号不足以支撑仓位建议（15 个策略中多数互为子集）。
MIN_TRIGGERED_FOR_POSITION = 2

#: 净分超过该阈值即视为策略共识成立（与"偏多/偏空"判定一致）。
CONSENSUS_NET_THRESHOLD = 12


@dataclass
class StrategySignal:
    key: str
    name: str
    category: str
    triggered: bool
    direction: str  # bullish / bearish / neutral
    score: int
    summary: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(max(low, min(high, round(value))))


def last(series: pd.Series, default: float = 0.0) -> float:
    try:
        value = float(series.iloc[-1])
        return default if pd.isna(value) else value
    except Exception:
        return default


def prev(series: pd.Series, offset: int = 1, default: float = 0.0) -> float:
    try:
        value = float(series.iloc[-1 - offset])
        return default if pd.isna(value) else value
    except Exception:
        return default


def pct(a: float, b: float) -> float:
    return (a / b - 1) if b else 0.0


def ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=1).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def volume_ratio(df: pd.DataFrame, window: int = 5) -> float:
    if "Volume" not in df.columns or len(df) < 2:
        return 1.0
    base = float(df["Volume"].iloc[-1 - window : -1].mean())
    return (float(df["Volume"].iloc[-1]) / base) if base > 0 else 1.0


def smma(series: pd.Series, window: int) -> pd.Series:
    """平滑移动平均，参数与 stockstats ``smma`` 完全一致。

    ``ewm(ignore_na=False, alpha=1/window, min_periods=0, adjust=True)``

    规则层与 LLM 工具层（stockstats）必须使用同一套平滑口径，否则同一份
    报告里会出现互相矛盾的 RSI / ATR 判定。
    """
    return series.ewm(
        ignore_na=False,
        alpha=1.0 / window,
        min_periods=0,
        adjust=True,
    ).mean()


def rsi_series(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI，公式与 stockstats ``_rsi`` 逐点一致。

    注意 stockstats 的两处细节：``change.iloc[0] = 0``（首根不算涨跌），
    以及 ``smma`` 使用 ``adjust=True``。二者都会影响暖机期数值。
    """
    if len(close) < window + 1:
        return pd.Series([50.0] * len(close), index=close.index)
    change = close.diff()
    change.iloc[0] = 0.0
    close_pm = (change + change.abs()) / 2
    close_nm = (-change + change.abs()) / 2
    p_ema = smma(close_pm, window)
    n_ema = smma(close_nm, window)
    rs = p_ema / n_ema
    values = 100 - (100 / (1.0 + rs))
    return pd.to_numeric(values, errors="coerce").fillna(50.0)


def rsi(close: pd.Series, window: int = 14) -> float:
    return last(rsi_series(close, window))


def macd_values(close: pd.Series) -> tuple[float, float, float]:
    if len(close) < 35:
        return 0.0, 0.0, 0.0
    macd_line = ema(close, 12) - ema(close, 26)
    signal_line = ema(macd_line, 9)
    hist = macd_line - signal_line
    return last(macd_line), last(signal_line), last(hist)


def bollinger(close: pd.Series, window: int = 20) -> tuple[float, float, float]:
    if len(close) < window:
        value = last(close)
        return value, value, value
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    return (
        last(mid),
        last(mid + 2 * std),
        last(mid - 2 * std),
    )


def atr(df: pd.DataFrame, window: int = 14) -> float:
    """Wilder ATR（真实波幅的 RMA 平滑）。

    与 stockstats 的 ``atr_14`` 口径一致；此处此前使用简单平均，
    会让规则层的止损距离与 LLM 看到的 ATR 不一致。
    """
    if len(df) < 2:
        return 0.0
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = smma(tr, window).iloc[-1]
    return float(value) if not pd.isna(value) else 0.0


def bias(close: float, value: float) -> float:
    return pct(close, value)


def find_fractals(df: pd.DataFrame, window: int = 2) -> list[dict]:
    """识别顶/底分型（简化缠论）。"""
    points: list[dict] = []
    highs = df["High"].astype(float).tolist()
    lows = df["Low"].astype(float).tolist()
    dates = df["Date"].dt.strftime("%Y-%m-%d").tolist()
    n = len(df)
    for i in range(window, n - window):
        left = highs[i - window : i]
        right = highs[i + 1 : i + 1 + window]
        if highs[i] >= max(left) and highs[i] >= max(right):
            points.append(
                {"index": i, "date": dates[i], "price": highs[i], "type": "high"}
            )
            continue
        left_low = lows[i - window : i]
        right_low = lows[i + 1 : i + 1 + window]
        if lows[i] <= min(left_low) and lows[i] <= min(right_low):
            points.append(
                {"index": i, "date": dates[i], "price": lows[i], "type": "low"}
            )
    return points


def build_swings(points: list[dict], min_gap: int = 4) -> list[dict]:
    """把分型合并成交替的笔/波段端点。"""
    swings: list[dict] = []
    for point in points:
        if not swings:
            swings.append(dict(point))
            continue
        previous = swings[-1]
        if point["type"] == previous["type"]:
            # 同向分型：保留更极端的那个
            if (point["type"] == "high" and point["price"] > previous["price"]) or (
                point["type"] == "low" and point["price"] < previous["price"]
            ):
                swings[-1] = dict(point)
            continue
        if point["index"] - previous["index"] >= min_gap:
            swings.append(dict(point))
    return swings
