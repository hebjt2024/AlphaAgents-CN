"""策略信号的基础数据结构和通用指标。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd


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


def rsi_series(close: pd.Series, window: int = 14) -> pd.Series:
    if len(close) < window + 1:
        return pd.Series([50.0] * len(close), index=close.index)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, pd.NA)
    values = 100 - (100 / (1 + rs))
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
    value = tr.rolling(window, min_periods=1).mean().iloc[-1]
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
