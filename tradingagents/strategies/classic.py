"""经典 A 股策略信号（可计算版本）。

这些规则是启发式的量化近似，用于给页面和 LLM 提供结构化的“策略视角”，
不构成投资建议。缠论/波浪为简化实现，仅提取笔、中枢与 5 浪结构的关键特征。
"""

from __future__ import annotations

import pandas as pd

from .base import (
    StrategySignal,
    bias,
    bollinger,
    build_swings,
    clamp,
    ema,
    find_fractals,
    last,
    ma,
    macd_values,
    pct,
    prev,
    rsi,
    rsi_series,
    volume_ratio,
)


def _need(df: pd.DataFrame, bars: int) -> bool:
    return df is not None and len(df) >= bars


def _skip(key: str, name: str, category: str, message: str) -> StrategySignal:
    return StrategySignal(
        key=key,
        name=name,
        category=category,
        triggered=False,
        direction="neutral",
        score=0,
        summary=message,
        reasons=[message],
        metrics={},
    )


def _crossed_recent(fast: pd.Series, slow: pd.Series, days: int = 3) -> bool:
    if last(fast) <= last(slow):
        return False
    for offset in range(1, days + 1):
        if prev(fast, offset) <= prev(slow, offset):
            return True
    return False


# ---------------------------------------------------------------------------
# 趋势类
# ---------------------------------------------------------------------------
def bull_trend(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 30):
        return _skip("bull_trend", "多头趋势", "trend", "数据不足，暂无法判断多头趋势")
    close = df["Close"].astype(float)
    ma5, ma10, ma20 = ma(close, 5), ma(close, 10), ma(close, 20)
    c, m5, m10, m20 = last(close), last(ma5), last(ma10), last(ma20)
    aligned = m5 >= m10 >= m20
    slope_up = m20 > prev(ma20, 5)
    b = bias(c, m20)
    low_bias = abs(b) <= 0.08
    triggered = aligned and slope_up and low_bias

    score = 35
    reasons = []
    if aligned:
        score += 20
        reasons.append("MA5 ≥ MA10 ≥ MA20，多头排列")
    else:
        reasons.append("均线未形成多头排列")
    if slope_up:
        score += 15
        reasons.append("MA20 斜率向上，趋势延续")
    else:
        reasons.append("MA20 走平或向下")
    if abs(b) <= 0.04:
        score += 15
        reasons.append(f"距 MA20 乖离率 {b * 100:.2f}%，位置不高")
    elif low_bias:
        score += 8
        reasons.append(f"距 MA20 乖离率 {b * 100:.2f}%，仍在可接受范围")
    else:
        reasons.append(f"距 MA20 乖离率 {b * 100:.2f}%，偏高，注意追高风险")

    return StrategySignal(
        key="bull_trend",
        name="多头趋势",
        category="trend",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 20),
        summary="多头排列 + 趋势向上 + 低乖离" if triggered else "多头结构不完整或位置偏高",
        reasons=reasons,
        metrics={"ma5": round(m5, 2), "ma10": round(m10, 2), "ma20": round(m20, 2), "bias20": round(b, 4)},
    )


def ma_golden_cross(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 35):
        return _skip("ma_golden_cross", "均线金叉", "trend", "数据不足，暂无法判断均线金叉")
    close = df["Close"].astype(float)
    ma5, ma10, ma20 = ma(close, 5), ma(close, 10), ma(close, 20)
    cross_5_10 = _crossed_recent(ma5, ma10, 3)
    cross_10_20 = _crossed_recent(ma10, ma20, 5)
    vr = volume_ratio(df, 5)
    macd_line, signal_line, hist = macd_values(close)
    macd_bull = macd_line > signal_line or hist > 0
    b5 = bias(last(close), last(ma5))
    triggered = (cross_5_10 or cross_10_20) and abs(b5) <= 0.06

    score = 40
    reasons = []
    if cross_5_10:
        score += 18
        reasons.append("MA5 近 3 日上穿 MA10")
    if cross_10_20:
        score += 12
        reasons.append("MA10 近 5 日上穿 MA20（更可靠）")
    if vr >= 1.2:
        score += 12
        reasons.append(f"金叉伴随放量，量比 {vr:.2f}")
    elif cross_5_10 or cross_10_20:
        reasons.append(f"量能未明显放大（量比 {vr:.2f}）")
    if macd_bull:
        score += 6
        reasons.append("MACD 处于多头状态")
    if abs(b5) <= 0.03:
        score += 6
        reasons.append("价格靠近均线，未明显追高")
    if not (cross_5_10 or cross_10_20):
        reasons.append("近几日未检测到有效金叉")

    return StrategySignal(
        key="ma_golden_cross",
        name="均线金叉",
        category="trend",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 15),
        summary="均线金叉 + 量能确认" if triggered else "未出现有效金叉或价格偏离过大",
        reasons=reasons,
        metrics={"ma5": round(last(ma5), 2), "ma10": round(last(ma10), 2), "ma20": round(last(ma20), 2), "volume_ratio": round(vr, 2)},
    )


# ---------------------------------------------------------------------------
# 量价类
# ---------------------------------------------------------------------------
def volume_breakout(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 25):
        return _skip("volume_breakout", "放量突破", "volume", "数据不足，暂无法判断放量突破")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    c = last(close)
    recent_high = float(high.iloc[-21:-1].max()) if len(df) >= 21 else float(high.max())
    breakout = c > recent_high
    vr = volume_ratio(df, 5)
    triggered = breakout and vr >= 1.5
    score = 40
    reasons = []
    if breakout:
        score += 20
        reasons.append(f"收盘价突破近 20 日高点 {recent_high:.2f}")
    else:
        reasons.append(f"尚未突破近 20 日高点 {recent_high:.2f}")
    if vr >= 1.5:
        score += 20 + min(15, (vr - 1.5) * 15)
        reasons.append(f"成交量放大，量比 {vr:.2f}")
    else:
        reasons.append(f"量能不足，量比 {vr:.2f}")
    if last(close) > float(df["Open"].iloc[-1]):
        score += 5
        reasons.append("突破日收阳线")

    return StrategySignal(
        key="volume_breakout",
        name="放量突破",
        category="volume",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 20),
        summary="价格突破 + 成交量放大" if triggered else "突破或量能条件未同时满足",
        reasons=reasons,
        metrics={"recent_high": round(recent_high, 2), "volume_ratio": round(vr, 2), "breakout": breakout},
    )


def shrink_pullback(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 40):
        return _skip("shrink_pullback", "缩量回踩", "volume", "数据不足，暂无法判断缩量回踩")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    c = last(close)
    ma10, ma20 = ma(close, 10), ma(close, 20)
    uptrend = last(ma20) > prev(ma20, 5) and c >= last(ma20) * 0.98
    near_ma10 = abs(bias(c, last(ma10))) <= 0.02
    near_ma20 = abs(bias(c, last(ma20))) <= 0.02
    high20 = float(high.iloc[-21:].max())
    drawdown = high20 / c - 1 if c else 0.0
    pullback = 0.03 <= drawdown <= 0.12
    vr = volume_ratio(df, 5)
    shrink = vr <= 0.8
    triggered = uptrend and (near_ma10 or near_ma20) and pullback and shrink

    score = 40
    reasons = []
    if uptrend:
        score += 12
        reasons.append("MA20 向上，趋势背景健康")
    else:
        reasons.append("中期趋势未确认向上")
    if near_ma10 or near_ma20:
        score += 15
        reasons.append("价格回踩至 MA10/MA20 附近")
    else:
        reasons.append("价格尚未回到关键均线附近")
    if pullback:
        score += 12
        reasons.append(f"距近 20 日高点回撤 {drawdown * 100:.1f}%")
    if shrink:
        score += 15
        reasons.append(f"缩量回踩，量比 {vr:.2f}")
    else:
        reasons.append(f"量能未明显萎缩（量比 {vr:.2f}）")

    return StrategySignal(
        key="shrink_pullback",
        name="缩量回踩",
        category="volume",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 15),
        summary="回踩均线 + 缩量，潜在低吸点" if triggered else "回踩/缩量条件未同时满足",
        reasons=reasons,
        metrics={"drawdown20": round(drawdown, 4), "volume_ratio": round(vr, 2), "ma10": round(last(ma10), 2), "ma20": round(last(ma20), 2)},
    )


def bottom_volume(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 65):
        return _skip("bottom_volume", "底部放量", "volume", "数据不足，暂无法判断底部放量")
    close = df["Close"].astype(float)
    low = df["Low"].astype(float)
    c = last(close)
    low60 = float(low.iloc[-60:].min())
    near_low = c <= low60 * 1.08
    vr = volume_ratio(df, 20)
    spike = vr >= 2.0
    bull_candle = last(close) > float(df["Open"].iloc[-1])
    quiet_before = prev(df["Volume"].astype(float), 1) < float(df["Volume"].iloc[-21:-1].mean()) * 0.75
    triggered = near_low and spike and bull_candle

    score = 30
    reasons = []
    if near_low:
        score += 18
        reasons.append(f"价格接近 60 日低点 {low60:.2f}")
    else:
        reasons.append("尚未接近 60 日低点")
    if spike:
        score += 20 + min(15, (vr - 2.0) * 8)
        reasons.append(f"底部放量，量比 {vr:.2f}")
    else:
        reasons.append(f"量能不足，量比 {vr:.2f}")
    if bull_candle:
        score += 8
        reasons.append("当日收阳线")
    if quiet_before:
        score += 8
        reasons.append("前一日出现地量，具备反转前提")

    return StrategySignal(
        key="bottom_volume",
        name="底部放量",
        category="reversal",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 15),
        summary="底部区域 + 放量阳线，潜在反转" if triggered else "底部/放量条件未同时满足",
        reasons=reasons,
        metrics={"low60": round(low60, 2), "volume_ratio": round(vr, 2), "quiet_before": quiet_before},
    )


# ---------------------------------------------------------------------------
# 强势/形态类
# ---------------------------------------------------------------------------
def dragon_head(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 65):
        return _skip("dragon_head", "龙头策略", "momentum", "数据不足，暂无法判断龙头强度")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    c = last(close)
    ma5, ma10, ma20 = ma(close, 5), ma(close, 10), ma(close, 20)
    aligned = last(ma5) >= last(ma10) >= last(ma20)
    slope_up = last(ma20) > prev(ma20, 5)
    ret20 = pct(c, prev(close, 20))
    high60 = float(high.iloc[-60:].max())
    near_high = c >= high60 * 0.95
    r = rsi(close)
    vr = volume_ratio(df, 5)
    triggered = aligned and slope_up and ret20 >= 0.15 and near_high and r >= 55

    score = 40
    reasons = []
    if aligned and slope_up:
        score += 15
        reasons.append("均线多头排列且 MA20 向上")
    if ret20 >= 0.15:
        score += 15 + min(15, (ret20 - 0.15) * 60)
        reasons.append(f"近 20 日涨幅 {ret20 * 100:.1f}%")
    else:
        reasons.append(f"近 20 日涨幅 {ret20 * 100:.1f}%，强度不足")
    if near_high:
        score += 10
        reasons.append(f"接近 60 日高点 {high60:.2f}")
    if r >= 55:
        score += 6
        reasons.append(f"RSI {r:.1f}，动能偏强")
    if vr >= 1.2:
        score += 6
        reasons.append(f"量比 {vr:.2f}，资金承接尚可")

    return StrategySignal(
        key="dragon_head",
        name="龙头策略",
        category="momentum",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 15),
        summary="强势龙头，趋势延续（追高需控制仓位）" if triggered else "强势/龙头条件未同时满足",
        reasons=reasons,
        metrics={"ret20": round(ret20, 4), "rsi": round(r, 1), "high60": round(high60, 2), "volume_ratio": round(vr, 2)},
    )


def one_yang_three_yin(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 6):
        return _skip("one_yang_three_yin", "一阳夹三阴", "pattern", "数据不足，暂无法判断一阳夹三阴")
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    last_bull = last(close) > last(open_)
    prev_bears = all(
        float(close.iloc[-1 - i]) < float(open_.iloc[-1 - i])
        for i in range(1, 4)
    )
    engulf_close = last(close) > float(close.iloc[-4:-1].max())
    engulf_open = last(close) > float(open_.iloc[-4:-1].max())
    vr = volume_ratio(df, 5)
    triggered = last_bull and prev_bears and (engulf_close or engulf_open) and vr >= 1.0

    score = 35
    reasons = []
    if last_bull:
        score += 10
        reasons.append("最后一根为阳线")
    else:
        reasons.append("最后一根不是阳线")
    if prev_bears:
        score += 15
        reasons.append("前三根连续收阴")
    else:
        reasons.append("前三根并非连续阴线")
    if engulf_close or engulf_open:
        score += 15
        reasons.append("阳线反包前三根阴线实体")
    if vr >= 1.2:
        score += 10
        reasons.append(f"反包伴随放量，量比 {vr:.2f}")
    if last(high) > float(high.iloc[-4:-1].max()):
        score += 5

    return StrategySignal(
        key="one_yang_three_yin",
        name="一阳夹三阴",
        category="pattern",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else score - 15),
        summary="洗盘后阳线反包，短线转强" if triggered else "未形成有效反包形态",
        reasons=reasons,
        metrics={"volume_ratio": round(vr, 2), "engulf": bool(engulf_close or engulf_open)},
    )


def box_oscillation(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 65):
        return _skip("box_oscillation", "箱体震荡", "pattern", "数据不足，暂无法判断箱体")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    c = last(close)
    box_high = float(high.iloc[-60:].max())
    box_low = float(low.iloc[-60:].min())
    range_pct = (box_high / box_low - 1) if box_low else 0.0
    position = (c - box_low) / (box_high - box_low) if box_high > box_low else 0.5
    bull_candle = last(close) > float(df["Open"].iloc[-1])
    bear_candle = last(close) < float(df["Open"].iloc[-1])
    valid_box = range_pct <= 0.25
    triggered = False
    direction = "neutral"
    score = 30
    reasons = []
    if valid_box:
        reasons.append(f"近 60 日箱体振幅 {range_pct * 100:.1f}%，区间有效")
        if position <= 0.25 and bull_candle:
            triggered = True
            direction = "bullish"
            score = 50 + (1 - position) * 30
            reasons.append(f"价格位于箱体下沿（位置 {position:.0%}），出现阳线")
        elif position >= 0.75 and bear_candle:
            triggered = True
            direction = "bearish"
            score = 50 + position * 30
            reasons.append(f"价格位于箱体上沿（位置 {position:.0%}），出现阴线")
        elif position <= 0.25:
            reasons.append(f"接近箱体下沿（位置 {position:.0%}），等待企稳")
        elif position >= 0.75:
            reasons.append(f"接近箱体上沿（位置 {position:.0%}），注意压力")
        else:
            reasons.append(f"位于箱体中部（位置 {position:.0%}）")
    else:
        reasons.append(f"近 60 日振幅 {range_pct * 100:.1f}%，箱体不明确")

    return StrategySignal(
        key="box_oscillation",
        name="箱体震荡",
        category="pattern",
        triggered=triggered,
        direction=direction,
        score=clamp(score if triggered else 25),
        summary="箱体下沿低吸" if direction == "bullish" and triggered else ("箱体上沿高抛" if direction == "bearish" and triggered else "箱体条件不足"),
        reasons=reasons,
        metrics={"box_high": round(box_high, 2), "box_low": round(box_low, 2), "position": round(position, 3), "range_pct": round(range_pct, 4)},
    )


# ---------------------------------------------------------------------------
# 缠论 / 波浪（简化）
# ---------------------------------------------------------------------------
def chan_theory(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 60):
        return _skip("chan_theory", "缠论（简化）", "theory", "数据不足，暂无法识别笔/中枢")
    points = find_fractals(df, window=2)
    swings = build_swings(points, min_gap=4)
    pivots = []
    for i in range(max(0, len(swings) - 8), max(0, len(swings) - 3)):
        if i + 3 >= len(swings):
            break
        pts = swings[i : i + 4]
        ranges = []
        for j in range(3):
            a, b = pts[j]["price"], pts[j + 1]["price"]
            ranges.append((min(a, b), max(a, b)))
        zg = min(r[1] for r in ranges)
        zd = max(r[0] for r in ranges)
        if zg > zd:
            pivots.append(
                {
                    "start": pts[0]["date"],
                    "end": pts[-1]["date"],
                    "zg": zg,
                    "zd": zd,
                }
            )
    latest = pivots[-1] if pivots else None
    c = last(df["Close"].astype(float))
    triggered = False
    direction = "neutral"
    score = 35
    reasons = []
    if latest:
        if c > latest["zg"]:
            triggered = True
            direction = "bullish"
            score = 62 + min(18, (c / latest["zg"] - 1) * 200)
            reasons.append(f"价格站上中枢上沿 {latest['zg']:.2f}")
        elif c < latest["zd"]:
            triggered = True
            direction = "bearish"
            score = 62 + min(18, (latest["zd"] / c - 1) * 200)
            reasons.append(f"价格跌破中枢下沿 {latest['zd']:.2f}")
        else:
            reasons.append(f"价格位于中枢 [{latest['zd']:.2f}, {latest['zg']:.2f}] 内")
    else:
        reasons.append("未识别到有效中枢（可能趋势尚未形成盘整）")
    reasons.append(f"识别到 {len(swings)} 个笔端点、{len(pivots)} 个中枢")

    return StrategySignal(
        key="chan_theory",
        name="缠论（简化）",
        category="theory",
        triggered=triggered,
        direction=direction,
        score=clamp(score if triggered else 30),
        summary=("向上离开中枢" if direction == "bullish" and triggered else ("向下离开中枢" if direction == "bearish" and triggered else "中枢内震荡/未形成有效中枢")),
        reasons=reasons,
        metrics={"swings": len(swings), "pivots": len(pivots), "latest_pivot": latest},
    )


def wave_theory(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 90):
        return _skip("wave_theory", "波浪理论（简化）", "theory", "数据不足，暂无法识别 5 浪结构")
    points = find_fractals(df, window=3)
    swings = build_swings(points, min_gap=5)
    valid = None
    for start in range(max(0, len(swings) - 8), max(0, len(swings) - 5)):
        pts = swings[start : start + 6]
        if len(pts) < 6:
            continue
        types = [p["type"] for p in pts]
        if types != ["low", "high", "low", "high", "low", "high"]:
            continue
        p0, p1, p2, p3, p4, p5 = [p["price"] for p in pts]
        wave1, wave3, wave5 = p1 - p0, p3 - p2, p5 - p4
        if (
            p2 > p0
            and p3 > p1
            and p4 > p2
            and p5 > p3
            and wave3 >= wave1 * 0.8
            and wave3 >= wave5 * 0.8
        ):
            valid = pts
            break

    c = last(df["Close"].astype(float))
    triggered = False
    direction = "neutral"
    score = 30
    reasons = []
    if valid:
        p4 = valid[4]["price"]
        p5 = valid[5]["price"]
        if c >= p4 and c <= p5 * 1.08:
            triggered = True
            direction = "bullish"
            score = 58 + min(18, (c / p4 - 1) * 150)
            reasons.append("识别到 5 浪上涨结构，价格处于第 5 浪/延伸段")
        elif c < p4:
            triggered = True
            direction = "bearish"
            score = 58 + min(18, (p4 / c - 1) * 100)
            reasons.append(f"5 浪后跌破第 4 浪低点 {p4:.2f}，进入调整")
        else:
            reasons.append(f"价格高于第 5 浪高点 {p5:.2f}，可能处于延伸浪")
        reasons.append(f"浪型：{valid[0]['date']} 起 1→2→3→4→5")
    else:
        reasons.append("未识别到完整 5 浪结构")

    return StrategySignal(
        key="wave_theory",
        name="波浪理论（简化）",
        category="theory",
        triggered=triggered,
        direction=direction,
        score=clamp(score if triggered else 25),
        summary=("5 浪上涨/延伸" if direction == "bullish" and triggered else ("5 浪后调整" if direction == "bearish" and triggered else "未识别到完整 5 浪")),
        reasons=reasons,
        metrics={"swings": len(swings), "wave_points": valid},
    )


# ---------------------------------------------------------------------------
# 情绪周期
# ---------------------------------------------------------------------------
def emotion_cycle(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 65):
        return _skip("emotion_cycle", "情绪周期", "sentiment", "数据不足，暂无法判断情绪周期")
    close = df["Close"].astype(float)
    c = last(close)
    high60 = float(df["High"].astype(float).iloc[-60:].max())
    low60 = float(df["Low"].astype(float).iloc[-60:].min())
    position = (c - low60) / (high60 - low60) if high60 > low60 else 0.5
    r = rsi(close)
    b20 = bias(c, last(ma(close, 20)))
    ret20 = pct(c, prev(close, 20))
    vr = volume_ratio(df, 20)

    phase = "中性"
    direction = "neutral"
    triggered = False
    score = 35
    reasons = []

    if r <= 30 or (position <= 0.15 and vr < 0.8):
        phase = "冰点"
        direction = "bullish"
        triggered = True
        score = 55
        reasons.append("RSI 低位/价格处于区间底部且量能萎缩，情绪冰点")
    elif r < 45 and -0.05 <= ret20 <= 0.05 and 0.8 <= vr <= 1.3:
        phase = "修复"
        direction = "bullish"
        triggered = True
        score = 50
        reasons.append("超跌后情绪修复，量能温和")
    elif r >= 70 or (position >= 0.85 and vr >= 1.5):
        phase = "高潮"
        direction = "bearish"
        triggered = True
        score = 62
        reasons.append("RSI 高位/价格接近区间顶部且放量，情绪高潮")
    elif r >= 55 and ret20 < 0 and vr < 0.9:
        phase = "退潮"
        direction = "bearish"
        triggered = True
        score = 55
        reasons.append("前期强势后缩量回落，情绪退潮")
    elif r >= 55 and ret20 > 0.05:
        phase = "升温"
        direction = "bullish"
        triggered = False
        score = 45
        reasons.append("情绪升温但尚未进入高潮，观察持续性")
    else:
        reasons.append("情绪处于中性区间")

    return StrategySignal(
        key="emotion_cycle",
        name="情绪周期",
        category="sentiment",
        triggered=triggered,
        direction=direction,
        score=clamp(score),
        summary=f"情绪阶段：{phase}",
        reasons=reasons,
        metrics={"phase": phase, "rsi": round(r, 1), "bias20": round(b20, 4), "ret20": round(ret20, 4), "volume_ratio": round(vr, 2), "position": round(position, 3)},
    )


# ---------------------------------------------------------------------------
# 补充策略
# ---------------------------------------------------------------------------
def macd_cross(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 40):
        return _skip("macd_cross", "MACD 金叉", "trend", "数据不足，暂无法判断 MACD 金叉")
    close = df["Close"].astype(float)
    macd_series = ema(close, 12) - ema(close, 26)
    signal_series = ema(macd_series, 9)
    cross_up = last(macd_series) > last(signal_series) and (
        prev(macd_series, 1) <= prev(signal_series, 1)
        or prev(macd_series, 2) <= prev(signal_series, 2)
    )
    above_zero = last(macd_series) > 0
    triggered = cross_up
    score = 45 + (15 if above_zero else 0) + (10 if last(macd_series) > last(signal_series) else 0)
    reasons = [
        "MACD 线上穿信号线" if cross_up else "近几日未出现 MACD 金叉",
        "MACD 位于零轴上方" if above_zero else "MACD 位于零轴下方",
    ]
    return StrategySignal(
        key="macd_cross",
        name="MACD 金叉",
        category="trend",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else 25),
        summary="MACD 金叉" if triggered else "未出现 MACD 金叉",
        reasons=reasons,
        metrics={"macd": round(last(macd_series), 4), "signal": round(last(signal_series), 4)},
    )


def rsi_reversal(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 20):
        return _skip("rsi_reversal", "RSI 超跌反转", "reversal", "数据不足，暂无法判断 RSI 反转")
    values = rsi_series(df["Close"].astype(float))
    r = last(values)
    prev_r = prev(values, 1)
    triggered = r < 35 and r > prev_r
    score = 40 + (20 if r < 30 else 10) + (10 if r > prev_r else 0)
    return StrategySignal(
        key="rsi_reversal",
        name="RSI 超跌反转",
        category="reversal",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else 25),
        summary="RSI 低位拐头" if triggered else "RSI 未出现超跌反转",
        reasons=[f"RSI {r:.1f}", f"前一日 {prev_r:.1f}"],
        metrics={"rsi": round(r, 1), "prev_rsi": round(prev_r, 1)},
    )


def boll_rebound(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 25):
        return _skip("boll_rebound", "布林下轨反弹", "reversal", "数据不足，暂无法判断布林反弹")
    close = df["Close"].astype(float)
    low = df["Low"].astype(float)
    mid, upper, lower = bollinger(close)
    touched = last(low) <= lower and last(close) > lower
    bull = last(close) > float(df["Open"].iloc[-1])
    triggered = touched and bull
    score = 40 + (15 if touched else 0) + (10 if bull else 0)
    return StrategySignal(
        key="boll_rebound",
        name="布林下轨反弹",
        category="reversal",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else 25),
        summary="触及布林下轨后收回" if triggered else "未出现布林下轨反弹",
        reasons=[f"下轨 {lower:.2f}", f"收盘 {last(close):.2f}"],
        metrics={"mid": round(mid, 2), "upper": round(upper, 2), "lower": round(lower, 2)},
    )


def platform_breakout(df: pd.DataFrame) -> StrategySignal:
    if not _need(df, 25):
        return _skip("platform_breakout", "平台突破", "pattern", "数据不足，暂无法判断平台突破")
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    c = last(close)
    platform_high = float(high.iloc[-21:-1].max())
    platform_low = float(low.iloc[-21:-1].min())
    range_pct = platform_high / platform_low - 1 if platform_low else 1.0
    vr = volume_ratio(df, 5)
    triggered = range_pct <= 0.08 and c > platform_high and vr >= 1.5
    score = 40 + (15 if range_pct <= 0.08 else 0) + (20 if c > platform_high else 0) + (15 if vr >= 1.5 else 0)
    return StrategySignal(
        key="platform_breakout",
        name="平台突破",
        category="pattern",
        triggered=triggered,
        direction="bullish" if triggered else "neutral",
        score=clamp(score if triggered else 25),
        summary="窄幅平台放量突破" if triggered else "平台/突破/量能条件未同时满足",
        reasons=[f"近 20 日振幅 {range_pct * 100:.1f}%", f"平台高点 {platform_high:.2f}", f"量比 {vr:.2f}"],
        metrics={"range_pct": round(range_pct, 4), "platform_high": round(platform_high, 2), "volume_ratio": round(vr, 2)},
    )


ALL_STRATEGIES = [
    bull_trend,
    ma_golden_cross,
    volume_breakout,
    shrink_pullback,
    bottom_volume,
    dragon_head,
    one_yang_three_yin,
    box_oscillation,
    chan_theory,
    wave_theory,
    emotion_cycle,
    macd_cross,
    rsi_reversal,
    boll_rebound,
    platform_breakout,
]

# 策略元数据：供 WebUI 多选、API 与校验使用
STRATEGY_META = [
    {"key": "bull_trend", "name": "多头趋势", "category": "趋势", "description": "MA5≥MA10≥MA20 + MA20 向上 + 低乖离"},
    {"key": "ma_golden_cross", "name": "均线金叉", "category": "趋势", "description": "MA5/MA10、MA10/MA20 金叉 + 量能确认"},
    {"key": "macd_cross", "name": "MACD 金叉", "category": "趋势", "description": "MACD 线上穿信号线"},
    {"key": "volume_breakout", "name": "放量突破", "category": "量价", "description": "突破近 20 日高点 + 成交量放大"},
    {"key": "shrink_pullback", "name": "缩量回踩", "category": "量价", "description": "回踩 MA10/MA20 + 量能萎缩"},
    {"key": "bottom_volume", "name": "底部放量", "category": "反转", "description": "接近 60 日低点 + 放量阳线"},
    {"key": "rsi_reversal", "name": "RSI 超跌反转", "category": "反转", "description": "RSI 低位拐头"},
    {"key": "boll_rebound", "name": "布林下轨反弹", "category": "反转", "description": "触及布林下轨后收回"},
    {"key": "dragon_head", "name": "龙头策略", "category": "强势", "description": "强势龙头，趋势延续（追高需谨慎）"},
    {"key": "one_yang_three_yin", "name": "一阳夹三阴", "category": "形态", "description": "三阴后阳线反包，洗盘转强"},
    {"key": "box_oscillation", "name": "箱体震荡", "category": "形态", "description": "区间高抛低吸"},
    {"key": "platform_breakout", "name": "平台突破", "category": "形态", "description": "窄幅平台放量突破"},
    {"key": "chan_theory", "name": "缠论（简化）", "category": "理论", "description": "分型/笔/中枢与离开中枢"},
    {"key": "wave_theory", "name": "波浪理论（简化）", "category": "理论", "description": "5 浪上涨/调整结构"},
    {"key": "emotion_cycle", "name": "情绪周期", "category": "情绪", "description": "冰点/修复/升温/高潮/退潮"},
]

# 默认只启用一组核心策略，避免每次分析都跑全部 15 个
DEFAULT_STRATEGY_KEYS = [
    "bull_trend",
    "ma_golden_cross",
    "volume_breakout",
    "shrink_pullback",
    "bottom_volume",
    "box_oscillation",
]
