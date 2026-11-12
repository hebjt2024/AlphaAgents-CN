"""策略族（family）定义与族内聚合。

为什么需要这一层
----------------
项目有 15 个策略，但它们**不是 15 个独立假设**。实测触发集合的一致性：

- ``ma_golden_cross`` 与 ``macd_cross`` 的触发集合**完全相同**
  （前者已经要求 ``macd_line > signal_line``，MA5/MA10 金叉本身又是
  MACD 的前置信号）；
- ``platform_breakout`` 是 ``volume_breakout`` 的**严格子集**
  （两者都要求 ``close > max(High[-21:-1])`` 且量比 ≥ 1.5，前者只多一个
  窄幅平台过滤条件）；
- ``dragon_head`` 是 ``bull_trend`` 的**严格子集**
  （两者都要求均线多头排列 + MA20 向上，前者只多���动量过滤）；
- ``box_oscillation`` 与 ``rsi_reversal`` 的触发高度重合。

旧实现把 15 个相关信号的分数**直接相加**当作"共识"，等于把同一个假设
数了三到四票，系统性高估共识强度。

本模块把这些策略按**可分辨的假设**分组：族内只取最高分（一个族最多算
一票），族间再求和。这样"触发策略 6 个"如果全在趋势族里，只会得到
"独立信号族 1 个"。
"""

from __future__ import annotations

#: 策略 key → 策略族 key。
#:
#: 归并依据是「触发条件是否本质等价」，而非名称相似：
#:   - 趋势跟随族：均线排列、均线金叉、MACD 金叉、龙头（后者的触发条件
#:     是前者的超集）；
#:   - 突破族：放量突破与平台突破（后者是前者的子集）；
#:   - 反转族：底部放量、RSI 反转、布林反弹、箱体震荡（箱体下沿 + 阳线
#:     与 RSI 超卖拐头在触发日上高度重合）；
#:   - 回调族：缩量回踩；
#:   - 结构族：缠论与波浪（都基于波段结构）；
#:   - 情绪族：情绪周期。
STRATEGY_FAMILIES: dict[str, str] = {
    # 趋势跟随
    "bull_trend": "trend",
    "ma_golden_cross": "trend",
    "macd_cross": "trend",
    "dragon_head": "trend",
    # 突破
    "volume_breakout": "breakout",
    "platform_breakout": "breakout",
    # 反转
    "bottom_volume": "reversal",
    "rsi_reversal": "reversal",
    "boll_rebound": "reversal",
    "box_oscillation": "reversal",
    # 回调
    "shrink_pullback": "pullback",
    # 形态（一阳夹三阴是独立的 K 线组合形态，与其他策略无可证重叠，
    # 单独成族以保留其独立性）
    "one_yang_three_yin": "pattern",
    # 结构
    "chan_theory": "structure",
    "wave_theory": "structure",
    # 情绪
    "emotion_cycle": "sentiment",
}

#: 策略族 key → 中文标签。
FAMILY_LABELS: dict[str, str] = {
    "trend": "趋势跟随",
    "breakout": "突破",
    "reversal": "反转",
    "pullback": "回调",
    "pattern": "形态",
    "structure": "结构",
    "sentiment": "情绪",
}

#: 未登记的策略默认归入的族。
DEFAULT_FAMILY = "other"


def family_of(key: str) -> str:
    """返回策略所属的族。"""
    return STRATEGY_FAMILIES.get(str(key or ""), DEFAULT_FAMILY)


def family_label(family: str) -> str:
    """返回策略族的中文标签。"""
    return FAMILY_LABELS.get(family, family)


def aggregate_by_family(signals: list) -> dict:
    """把触发信号按族聚合，族内取最高分（一个族最多算一票）。

    参数
    ----
    signals:
        ``StrategySignal`` 列表（可以是全部信号，内部只取已触发的）。

    返回
    ----
    dict，包含：
        - ``bull_score`` / ``bear_score``: 族内去重后的多空净分
        - ``net_score``: ``bull_score - bear_score``
        - ``raw_net_score``: 未去重的原始信号求和（仅供对照展示）
        - ``families``: 参与计票的族明细
        - ``family_count``: 有触发信号的族数量（= 独立假设数量）
        - ``signals``: 参与计票的触发信号明细
    """
    families: dict[str, dict] = {}
    raw_bull = 0
    raw_bear = 0

    for signal in signals:
        if not getattr(signal, "triggered", False):
            continue
        direction = getattr(signal, "direction", "neutral")
        if direction not in ("bullish", "bearish"):
            continue
        score = float(getattr(signal, "score", 0) or 0)
        key = getattr(signal, "key", "")
        family = family_of(key)

        bucket = families.setdefault(
            family,
            {
                "family": family,
                "label": family_label(family),
                "bull_score": 0.0,
                "bear_score": 0.0,
                "bull_signals": [],
                "bear_signals": [],
            },
        )
        if direction == "bullish":
            raw_bull += score
            bucket["bull_signals"].append(
                {"key": key, "name": getattr(signal, "name", key), "score": score}
            )
            bucket["bull_score"] = max(bucket["bull_score"], score)
        else:
            raw_bear += score
            bucket["bear_signals"].append(
                {"key": key, "name": getattr(signal, "name", key), "score": score}
            )
            bucket["bear_score"] = max(bucket["bear_score"], score)

    bull_score = sum(item["bull_score"] for item in families.values())
    bear_score = sum(item["bear_score"] for item in families.values())

    for item in families.values():
        item["direction"] = (
            "bullish"
            if item["bull_score"] > item["bear_score"]
            else ("bearish" if item["bear_score"] > item["bull_score"] else "neutral")
        )
        item["net"] = round(item["bull_score"] - item["bear_score"], 1)

    return {
        "bull_score": round(bull_score, 1),
        "bear_score": round(bear_score, 1),
        "net_score": round(bull_score - bear_score, 1),
        "raw_net_score": round(raw_bull - raw_bear, 1),
        "raw_bull_score": round(raw_bull, 1),
        "raw_bear_score": round(raw_bear, 1),
        "families": sorted(
            families.values(), key=lambda item: abs(item["net"]), reverse=True
        ),
        "family_count": len(families),
    }
