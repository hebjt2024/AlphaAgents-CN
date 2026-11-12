"""P0 回归测试：规则层与 LLM 工具层的口径必须一致。

背景：此前规则层自行实现了 RSI/ATR，与 LLM 通过 ``get_indicators`` 看到的
stockstats 口径不同 —— RSI 在 30/70 带上有 15.6% 的 K 线给出**相反**判定，
ATR 也存在系统性偏差。同一份报告里出现互相矛盾的指标，是最容易被用户
识破的"不专业"。

本文件用 stockstats 作为参照源，固化"两者必须逐点一致"这一约束。
"""

import unittest

import numpy as np
import pandas as pd
import stockstats

from tradingagents.strategies.base import atr, rsi_series
from tradingagents.strategies.scanner import _strength_from_scores


def _make_frame(n: int = 400, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.standard_normal(n) * 1.3))
    return pd.DataFrame(
        {
            "Close": close,
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 1.5,
            "Low": close - 1.5,
            "Volume": rng.integers(1_000_000, 4_000_000, n),
        }
    )


def _stockstats_frame(df: pd.DataFrame):
    renamed = df.rename(
        columns={
            "Close": "close",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Volume": "volume",
        }
    ).copy()
    frame = stockstats.StockDataFrame.retype(renamed)
    frame.index = pd.to_datetime(frame.index)
    return frame


class IndicatorParityTests(unittest.TestCase):
    def test_rsi_matches_stockstats_point_by_point(self):
        df = _make_frame()
        ref = _stockstats_frame(df)

        rule = rsi_series(df["Close"], 14).reset_index(drop=True)
        expected = pd.to_numeric(ref["rsi_14"], errors="coerce").reset_index(drop=True)

        aligned = pd.concat([rule.rename("rule"), expected.rename("ref")], axis=1).dropna()
        self.assertGreater(len(aligned), 300)

        max_diff = float((aligned["rule"] - aligned["ref"]).abs().max())
        self.assertLess(max_diff, 1e-9, f"RSI 与 stockstats 偏差过大：{max_diff}")

    def test_rsi_band_never_contradicts_stockstats(self):
        df = _make_frame()
        ref = _stockstats_frame(df)

        rule = rsi_series(df["Close"], 14).reset_index(drop=True)
        expected = pd.to_numeric(ref["rsi_14"], errors="coerce").reset_index(drop=True)
        aligned = pd.concat([rule.rename("rule"), expected.rename("ref")], axis=1).dropna()

        oversold = (aligned["rule"] < 30) != (aligned["ref"] < 30)
        overbought = (aligned["rule"] > 70) != (aligned["ref"] > 70)
        self.assertEqual(
            int(oversold.sum() + overbought.sum()),
            0,
            "规则层与 stockstats 对 RSI 超买超卖的判定出现分歧",
        )

    def test_atr_matches_stockstats(self):
        df = _make_frame()
        ref = _stockstats_frame(df)
        self.assertAlmostEqual(atr(df, 14), float(ref["atr_14"].iloc[-1]), places=9)

    def test_rsi_edge_cases(self):
        flat = pd.Series([10.0] * 30)
        self.assertEqual(rsi_series(flat).iloc[-1], 50.0)
        rising = pd.Series(range(1, 31), dtype=float)
        self.assertEqual(rsi_series(rising).iloc[-1], 100.0)
        falling = pd.Series(range(30, 0, -1), dtype=float)
        self.assertEqual(rsi_series(falling).iloc[-1], 0.0)
        short = pd.Series([10.0, 11.0])
        self.assertEqual(rsi_series(short).iloc[-1], 50.0)


class StrengthDegeneracyTests(unittest.TestCase):
    """P0-3：单边触发时强度不应恒为 100%。"""

    def test_single_signal_strength_is_not_maximum(self):
        strength = _strength_from_scores(bull_score=85, bear_score=0, triggered=1)
        self.assertLess(strength, 100)
        self.assertGreater(strength, 0)

    def test_strength_scales_with_signal_count(self):
        one = _strength_from_scores(bull_score=85, bear_score=0, triggered=1)
        two = _strength_from_scores(bull_score=85, bear_score=0, triggered=2)
        three = _strength_from_scores(bull_score=85, bear_score=0, triggered=3)
        self.assertLess(one, two)
        self.assertLess(two, three)

    def test_opposing_signals_have_zero_strength(self):
        self.assertEqual(_strength_from_scores(60, 60, 2), 0)

    def test_fully_aligned_signals_reach_max_strength(self):
        self.assertEqual(_strength_from_scores(85, 0, 3), 100)


if __name__ == "__main__":
    unittest.main()
