"""共享回测引擎的单元测试（全部使用合成行情，不访问网络）。

覆盖 A 股执行约束与统计门槛：
  - 涨停不可买入 / 跌停不可卖出
  - T+1：当日买入不可当日卖出
  - 停牌缺口：缺口内不可成交，止损不按止损价成交
  - 100 股整手、资金不足不开仓
  - 单笔最低佣金 5 元
  - 滑点作用在成交价上
  - ATR 止损（而非固定 8%）
  - 有效样本量 < 原始观测数，样本量门槛标记
  - 样本内/样本外切分与排名依据
  - 买入持有基准随结果一起输出
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from tradingagents.services import backtest as backtest_module
from tradingagents.services import strategy_backtest as strategy_module
from tradingagents.services.backtest_engine import (
    MIN_EFFECTIVE_SAMPLE,
    CostModel,
    ExecutionRules,
    _t_two_sided_p,
    benjamini_hochberg,
    detect_gaps,
    limit_prices,
    one_sample_t_test,
    simulate_trades,
)

TICKER = "600519"  # 主板，涨跌停 10%


def _row(day: date, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "Date": pd.Timestamp(day),
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": 1000,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _flat_frame(days: int, price: float = 10.0, span: float = 0.05) -> pd.DataFrame:
    """低波动横盘行情（每根 bar 的振幅固定为 span）。"""
    start = date(2025, 1, 1)
    rows = []
    for index in range(days):
        rows.append(
            _row(
                start + timedelta(days=index),
                price,
                price + span,
                price - span,
                price,
            )
        )
    return _frame(rows)


# ---------------------------------------------------------------------------
# 涨跌停不可成交
# ---------------------------------------------------------------------------
class LimitPriceTests(unittest.TestCase):
    def test_limit_prices_round_half_up(self):
        limit_up, limit_down = limit_prices(10.0, 0.10)
        self.assertAlmostEqual(limit_up, 11.0, places=4)
        self.assertAlmostEqual(limit_down, 9.0, places=4)
        # 半分位必须按交易所规则四舍五入（银行家舍入会算成 10.12）
        limit_up, _ = limit_prices(10.13, 0.10)
        self.assertAlmostEqual(limit_up, 11.14, places=4)

    def test_buy_at_limit_up_is_not_filled(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            # 第二根 bar 开盘即涨停（前收 10.00 → 涨停价 11.00）
            _row(start + timedelta(days=1), 11.0, 11.2, 10.9, 11.0),
            _row(start + timedelta(days=2), 11.0, 11.1, 10.8, 10.9),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 0, 0], ticker=TICKER)

        self.assertEqual(result.trade_count, 0)
        self.assertEqual(len(result.rejected_fills), 1)
        rejected = result.rejected_fills[0]
        self.assertEqual(rejected["action"], "buy")
        self.assertEqual(rejected["reason"], "limit_up")

    def test_buy_below_limit_up_is_filled(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.5, 10.7, 10.4, 10.6),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 1], ticker=TICKER)
        self.assertEqual(result.trade_count, 1)

    def test_limit_pct_per_board(self):
        """不同板块的涨跌停幅度不同：主板 10%、创业板/科创板 20%、北交所 30%、ST 5%。"""
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 12.0, 12.1, 11.9, 12.0),
        ]
        frame = _frame(rows)
        # 创业板 20%：12.00 未触及 12.00 的涨停价？前收 10.00 → 涨停 12.00 → 封板
        growth = simulate_trades(frame, [1, 1], ticker="300750")
        self.assertEqual(growth.trade_count, 0)
        self.assertEqual(growth.rejected_fills[0]["reason"], "limit_up")

        # 主板 10%：涨停价 11.00，12.00 的开盘在理论涨停价之上属于异常数据
        # （长期停牌复牌重设参考价），不应按封板处理
        main = simulate_trades(frame, [1, 1], ticker="600519")
        self.assertEqual(main.trade_count, 1)

    def test_st_stock_limit_is_five_percent(self):
        from tradingagents.dataflows.ashare_rules import price_limit_pct

        self.assertAlmostEqual(price_limit_pct("600519", "ST 贵州"), 0.05, places=4)
        self.assertAlmostEqual(price_limit_pct("600519", "贵州茅台"), 0.10, places=4)
        self.assertAlmostEqual(price_limit_pct("300750", "宁德时代"), 0.20, places=4)
        self.assertAlmostEqual(price_limit_pct("688981", "中芯国际"), 0.20, places=4)
        self.assertAlmostEqual(price_limit_pct("830799", "艾融软件"), 0.30, places=4)

    def test_sell_at_limit_down_is_not_filled(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.1, 10.5, 10.0, 10.2),
            # 第三根 bar 开盘跌停（前收 10.20 → 跌停价 9.18），卖不掉
            _row(start + timedelta(days=2), 9.18, 9.2, 9.18, 9.18),
            # 第四根 bar 恢复成交，离场指令在这里执行
            _row(start + timedelta(days=3), 9.5, 9.6, 9.4, 9.5),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 0, 0, 0], ticker=TICKER)

        self.assertEqual(result.trade_count, 1)
        trade = result.trades[0]
        self.assertEqual(trade["exit_date"], "2025-01-04")
        self.assertNotEqual(trade["exit_date"], "2025-01-03")
        # 离场价是恢复交易当日的开盘价，而不是跌停那天的开盘价
        self.assertAlmostEqual(trade["exit_price"], 9.5 * 0.999, places=3)
        self.assertTrue(any("跌停" in note for note in result.notes))


# ---------------------------------------------------------------------------
# T+1
# ---------------------------------------------------------------------------
class TPlusOneTests(unittest.TestCase):
    def test_no_same_day_exit(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            # 买入当天直接暴跌击穿止损（止损价 = 10.00 * 0.97 = 9.70）
            _row(start + timedelta(days=1), 10.1, 10.2, 9.0, 9.2),
            _row(start + timedelta(days=2), 9.2, 9.3, 9.1, 9.2),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 1, 1], ticker=TICKER)

        self.assertEqual(result.trade_count, 1)
        trade = result.trades[0]
        self.assertEqual(trade["entry_date"], "2025-01-02")
        self.assertNotEqual(trade["exit_date"], trade["entry_date"])
        self.assertGreaterEqual(trade["hold_days"], 1)

    def test_t_plus_1_can_be_disabled(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.1, 10.2, 9.0, 9.2),
        ]
        frame = _frame(rows)
        rules = ExecutionRules(t_plus_1=False)
        result = simulate_trades(frame, [1, 1], rules=rules, ticker=TICKER)
        self.assertEqual(result.trades[0]["exit_date"], result.trades[0]["entry_date"])


# ---------------------------------------------------------------------------
# 整手 / 资金 / 佣金 / 滑点
# ---------------------------------------------------------------------------
class ExecutionCostTests(unittest.TestCase):
    def test_shares_are_whole_lots(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 1], ticker=TICKER)
        shares = result.trades[0]["shares"]
        self.assertGreater(shares, 0)
        self.assertEqual(shares % 100, 0)

    def test_insufficient_cash_does_not_open_position(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 1], initial_cash=500.0, ticker=TICKER)
        self.assertEqual(result.trade_count, 0)
        self.assertTrue(any("一手" in note for note in result.notes))

    def test_min_commission_is_five_yuan(self):
        model = CostModel(commission_rate=0.0003, min_commission=5.0)
        # 1000 元成交额：费率只有 0.3 元，必须抬到 5 元
        self.assertAlmostEqual(model.buy_cost(1000.0), 5.0 + 1000.0 * 0.00001, places=6)
        self.assertAlmostEqual(
            model.sell_cost(1000.0),
            5.0 + 1000.0 * (0.0005 + 0.00001),
            places=6,
        )
        # 大额成交按费率计费
        self.assertAlmostEqual(
            model.buy_cost(100000.0), 30.0 + 100000.0 * 0.00001, places=6
        )

    def test_min_commission_applied_in_simulation(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
        ]
        frame = _frame(rows)
        result = simulate_trades(
            frame,
            [1, 1],
            cost_model=CostModel(
                commission_rate=0.0,
                stamp_duty_rate=0.0,
                slippage_rate=0.0,
                min_commission=5.0,
                transfer_fee_rate=0.0,
            ),
            initial_cash=5000.0,
            ticker=TICKER,
        )
        trade = result.trades[0]
        # 买入 400 股 * 10 元 = 4000 元，最低佣金 5 元；卖出同样 5 元
        self.assertAlmostEqual(trade["cost"], 10.0, places=4)

    def test_slippage_moves_the_fill_price(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
        ]
        frame = _frame(rows)
        with_slippage = simulate_trades(
            frame,
            [1, 1],
            cost_model=CostModel(slippage_rate=0.001),
            ticker=TICKER,
        )
        without_slippage = simulate_trades(
            frame,
            [1, 1],
            cost_model=CostModel(slippage_rate=0.0),
            ticker=TICKER,
        )
        self.assertAlmostEqual(
            with_slippage.trades[0]["entry_price"], 10.0 * 1.001, places=4
        )
        self.assertAlmostEqual(
            without_slippage.trades[0]["entry_price"], 10.0, places=4
        )

    def test_position_size_follows_risk_budget(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
        ]
        frame = _frame(rows)
        # 单笔风险 1%，止损 3% → 仓位上限约 33%，而不是硬编码 100%
        rules = ExecutionRules(risk_per_trade=0.01, max_position_pct=1.0)
        result = simulate_trades(frame, [1, 1], rules=rules, ticker=TICKER)
        invested = result.trades[0]["shares"] * result.trades[0]["entry_price"]
        self.assertLess(invested, 100000.0 * 0.5)
        self.assertGreater(invested, 100000.0 * 0.2)


# ---------------------------------------------------------------------------
# ATR 止损
# ---------------------------------------------------------------------------
class AtrStopTests(unittest.TestCase):
    def test_stop_is_atr_based_not_flat_8_percent(self):
        start = date(2025, 1, 1)
        calm_rows = []
        wild_rows = []
        for index in range(30):
            day = start + timedelta(days=index)
            calm_rows.append(_row(day, 10.0, 10.05, 9.95, 10.0))
            wild_rows.append(_row(day, 10.0, 11.0, 9.0, 10.0))
        calm = _frame(calm_rows)
        wild = _frame(wild_rows)

        calm_result = simulate_trades(calm, [1] * 30, ticker=TICKER)
        wild_result = simulate_trades(wild, [1] * 30, ticker=TICKER)

        calm_stop = calm_result.trades[0]["stop_price"]
        wild_stop = wild_result.trades[0]["stop_price"]
        # 低波动：ATR 极小 → 落到 3% 的最小止损下限
        self.assertAlmostEqual(calm_stop, 10.0 * 0.97, places=2)
        # 高波动：ATR 极大 → 止损远低于 8%，证明不是固定 8%
        self.assertLess(wild_stop, 10.0 * 0.92)
        self.assertAlmostEqual(wild_stop, 10.0 - 2.0 * 2.0, places=1)

    def test_atr_stop_triggers_exit(self):
        start = date(2025, 1, 1)
        rows = [_row(start + timedelta(days=i), 10.0, 10.05, 9.95, 10.0) for i in range(20)]
        # 第 21 根直接跌破 3% 止损
        rows.append(_row(start + timedelta(days=20), 10.0, 10.05, 9.0, 9.1))
        frame = _frame(rows)
        result = simulate_trades(frame, [1] * 21, ticker=TICKER)
        self.assertEqual(result.trade_count, 1)
        self.assertEqual(result.trades[0]["exit_reason"], "止损")


# ---------------------------------------------------------------------------
# 停牌缺口
# ---------------------------------------------------------------------------
class SuspensionGapTests(unittest.TestCase):
    def test_gap_is_detected(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=2), 10.0, 10.1, 9.9, 10.0),
            # 跳过 2025-01-06 ~ 01-09（4 个工作日），模拟长期停牌
            _row(start + timedelta(days=9), 8.0, 8.2, 7.8, 8.1),
        ]
        frame = _frame(rows)
        gaps = detect_gaps(frame)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["index"], 3)
        self.assertEqual(gaps[0]["missing_sessions"], 4)

    def test_weekend_is_not_a_gap(self):
        start = date(2025, 1, 3)  # 周五
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=3), 10.0, 10.1, 9.9, 10.0),
        ]
        self.assertEqual(detect_gaps(_frame(rows)), [])

    def test_stop_inside_gap_is_not_executed_at_stop_price(self):
        start = date(2025, 1, 1)
        rows = [
            _row(start, 10.0, 10.1, 9.9, 10.0),
            _row(start + timedelta(days=1), 10.1, 10.2, 10.0, 10.2),
            _row(start + timedelta(days=2), 10.2, 10.3, 10.1, 10.2),
            # 停牌 4 个工作日后复牌，直接跳空跌穿止损（止损价 9.70）
            _row(start + timedelta(days=9), 8.0, 8.2, 7.8, 8.1),
        ]
        frame = _frame(rows)
        result = simulate_trades(frame, [1, 1, 1, 1], ticker=TICKER)

        self.assertEqual(result.trade_count, 1)
        trade = result.trades[0]
        # 止损距离由 ATR 决定（首根 bar 的 ATR = 0.2 → 2 × ATR = 4%）
        self.assertAlmostEqual(trade["stop_price"], 9.6, places=2)
        # 不能按止损价成交，只能按复牌开盘价（更差）成交
        self.assertAlmostEqual(trade["exit_price"], 8.0 * 0.999, places=3)
        self.assertLess(trade["exit_price"], trade["stop_price"])
        # 缺口收益记成一笔跳空变动，而不是拆成若干假想波动
        self.assertEqual(len(trade["gap_moves"]), 1)
        self.assertAlmostEqual(trade["gap_moves"][0]["move"], 8.0 / 10.2 - 1, places=4)


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------
class StatisticsTests(unittest.TestCase):
    def test_t_distribution_p_value_matches_critical_values(self):
        # 参考临界值：df=10 时 p=0.05 的临界 t = 2.228；大样本时接近 1.96
        self.assertAlmostEqual(_t_two_sided_p(2.228, 10), 0.05, places=3)
        self.assertAlmostEqual(_t_two_sided_p(1.96, 100000), 0.05, places=3)
        self.assertEqual(_t_two_sided_p(0.0, 5), 1.0)

    def test_t_test_detects_significant_mean(self):
        sample = [0.05, 0.06, 0.04, 0.07, 0.05, 0.06, 0.05, 0.04, 0.06, 0.05]
        t_stat, p_value = one_sample_t_test(sample)
        self.assertGreater(t_stat, 3.0)
        self.assertLess(p_value, 0.01)

    def test_t_test_on_constant_sample_is_not_significant(self):
        t_stat, p_value = one_sample_t_test([0.01] * 8)
        self.assertEqual(t_stat, 0.0)
        self.assertEqual(p_value, 1.0)

    def test_benjamini_hochberg_is_monotonic_and_bounded(self):
        adjusted = benjamini_hochberg([0.01, 0.02, 0.5, 0.9])
        self.assertEqual(len(adjusted), 4)
        for value in adjusted:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        # 小 p 值被上调，且不超过 Bonferroni 上界
        self.assertGreaterEqual(adjusted[0], 0.01)
        self.assertLessEqual(adjusted[0], 0.04)
        self.assertGreaterEqual(adjusted[0], adjusted[1])


# ---------------------------------------------------------------------------
# 策略回测：样本量门槛与排名依据
# ---------------------------------------------------------------------------
def _ramp_frame(rows: int = 400, step: float = 0.05, start_price: float = 10.0) -> pd.DataFrame:
    """稳步上涨的行情：多头趋势策略会在几乎每个交易日触发。"""
    start = date(2024, 1, 1)
    data = []
    price = start_price
    for index in range(rows):
        data.append(
            _row(
                start + timedelta(days=index),
                price,
                price + 0.1,
                price - 0.1,
                price,
            )
        )
        price += step
    return _frame(data)


def _flat_market_frame(rows: int = 400) -> pd.DataFrame:
    """横盘行情：几乎不会有策略触发，样本量必然不足。"""
    start = date(2024, 1, 1)
    data = []
    for index in range(rows):
        price = 10.0 + (0.02 if index % 2 == 0 else -0.02)
        data.append(_row(start + timedelta(days=index), price, price + 0.05, price - 0.05, price))
    return _frame(data)


class StrategyRankingTests(unittest.TestCase):
    def test_effective_sample_size_smaller_than_raw_count(self):
        frame = _ramp_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = strategy_module.run_strategy_backtest(
                "600519", "2025-01-01", ["bull_trend"], days=250
            )
        item = result["strategies"][0]
        horizon = item["forward"]["10"]
        self.assertGreater(horizon["count"], 0)
        self.assertLess(horizon["effective_count"], horizon["count"])
        self.assertAlmostEqual(
            horizon["effective_count"], horizon["count"] / 10, places=2
        )

    def test_ranking_uses_out_of_sample_when_available(self):
        frame = _ramp_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = strategy_module.run_strategy_backtest(
                "600519", "2025-01-01", ["bull_trend"], days=250
            )
        item = result["strategies"][0]
        self.assertFalse(item["sample_insufficient"])
        self.assertEqual(item["ranking_basis"], "out_of_sample_forward_10d")
        self.assertEqual(result["ranking_basis"], "out_of_sample_forward_10d")
        self.assertGreaterEqual(
            item["out_of_sample_effective_sample_size"], MIN_EFFECTIVE_SAMPLE
        )
        self.assertIn("forward_in_sample", item)
        self.assertIn("forward_out_of_sample", item)

    def test_insufficient_sample_is_flagged_and_ranked_last(self):
        frame = _ramp_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = strategy_module.run_strategy_backtest(
                "600519",
                "2025-01-01",
                ["bull_trend", "rsi_reversal"],
                days=250,
            )
        items = {item["key"]: item for item in result["strategies"]}
        quiet = items["rsi_reversal"]
        self.assertTrue(quiet["sample_insufficient"])
        self.assertEqual(quiet["ranking_basis"], "in_sample_only")
        self.assertIn("样本外有效样本量", quiet["note"])
        # 样本外合格的条目必须排在只有样本内统计的条目之前
        self.assertEqual(result["strategies"][0]["key"], "bull_trend")

    def test_flat_market_marks_every_strategy_insufficient(self):
        frame = _flat_market_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = strategy_module.run_strategy_backtest(
                "600519", "2025-01-01", ["bull_trend"], days=250
            )
        item = result["strategies"][0]
        self.assertTrue(item["sample_insufficient"])
        self.assertTrue(result["sample_insufficient"])
        self.assertEqual(result["ranking_basis"], "in_sample_only")

    def test_benchmark_is_reported(self):
        frame = _ramp_frame()
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=frame,
        ):
            result = strategy_module.run_strategy_backtest(
                "600519", "2025-01-01", ["bull_trend"], days=250
            )
        self.assertIn("benchmark_return", result)
        self.assertIn("benchmark", result)
        self.assertIn("total_return", result["benchmark"])


# ---------------------------------------------------------------------------
# 单策略回测入口
# ---------------------------------------------------------------------------
class RunBacktestTests(unittest.TestCase):
    def _csv_prices(self, rows: int = 120) -> str:
        lines = ["Date,Open,High,Low,Close,Volume"]
        price = 10.0
        current = date(2025, 1, 1)
        for index in range(rows):
            price += 0.1 if index < rows // 2 else -0.08
            lines.append(
                f"{current.isoformat()},{price:.2f},{price + 0.2:.2f},"
                f"{price - 0.2:.2f},{price:.2f},1000"
            )
            current += timedelta(days=1)
        return "\n".join(lines)

    def test_unknown_strategy_raises_instead_of_silent_ma_cross(self):
        with self.assertRaises(ValueError):
            backtest_module.run_backtest(
                "600519", "2025-01-01", "2025-04-30", strategy="does_not_exist"
            )

    def test_benchmark_is_reported(self):
        csv_text = self._csv_prices()
        with patch.object(backtest_module, "route_to_vendor", return_value=csv_text):
            result = backtest_module.run_backtest("600519", "2025-01-01", "2025-04-30")
        self.assertIn("benchmark", result)
        self.assertIn("total_return", result["benchmark"])
        self.assertIn("benchmark_return", result)

    def test_statistical_keys_are_present(self):
        csv_text = self._csv_prices()
        with patch.object(backtest_module, "route_to_vendor", return_value=csv_text):
            result = backtest_module.run_backtest("600519", "2025-01-01", "2025-04-30")
        for key in (
            "total_return",
            "max_drawdown",
            "sharpe",
            "win_rate",
            "trade_count",
            "equity_curve",
            "effective_sample_size",
            "sample_insufficient",
            "p_value",
            "p_value_adjusted",
            "ranking_basis",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["effective_sample_size"], result["trade_count"])

    def test_rejected_limit_up_fill_is_reported(self):
        # 前 21 根 bar 横盘，第 22 根收涨触发均线金叉 → 第 23 根开盘买入；
        # 第 23 根直接以涨停价开盘 → 买不到，必须被记录为被拒成交。
        lines = ["Date,Open,High,Low,Close,Volume"]
        current = date(2025, 1, 1)
        for _ in range(21):
            lines.append(
                f"{current.isoformat()},10.00,10.10,9.90,10.00,1000"
            )
            current += timedelta(days=1)
        lines.append(f"{current.isoformat()},10.50,11.10,10.40,11.00,1000")
        current += timedelta(days=1)
        # 前收 11.00 → 涨停价 12.10
        lines.append(f"{current.isoformat()},12.10,12.30,11.90,12.10,1000")
        with patch.object(
            backtest_module, "route_to_vendor", return_value="\n".join(lines)
        ):
            result = backtest_module.run_backtest("600519", "2025-01-01", "2025-01-23")
        self.assertTrue(result["rejected_fills"])
        self.assertEqual(result["rejected_fills"][0]["reason"], "limit_up")


if __name__ == "__main__":
    unittest.main()
