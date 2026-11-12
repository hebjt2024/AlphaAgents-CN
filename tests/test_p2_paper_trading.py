"""模拟盘（Paper Trading）单元测试（全部使用合成行情，不访问网络）。

覆盖：
  - 买入成交价与现金扣减（含佣金/过户费/滑点）
  - T+1：当日买入当日不可卖，次日可卖
  - 涨停不可买入 / 跌停不可卖出（且记录原因）
  - 100 股整手：向下取整、不足一手拒单
  - 资金不足拒单
  - 单标的仓位上限与总仓位上限
  - 止损离场 / 止盈离场
  - 决策卡 → 委托：HOLD 无单、仓位 0 无单、BUY 买入、SELL 卖出
  - 报告结构与净值曲线
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from tradingagents.services.backtest_engine import CostModel
from tradingagents.services.paper_trading import (
    PaperBroker,
    advance_broker,
    order_from_decision_plan,
)

TICKER = "600519"  # 主板，涨跌停 10%
TICKER2 = "000001"
START = date(2025, 1, 6)


def _row(day: date, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "Date": pd.Timestamp(day),
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": 100000,
    }


def _day(day: date, open_: float, high: float, low: float, close: float) -> pd.DataFrame:
    """单日行情（单标的，用 ``PaperBroker(default_ticker=...)`` 指定代码）。"""
    return pd.DataFrame([_row(day, open_, high, low, close)])


def _multi_day(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _broker(cash: float = 100000.0, **kwargs) -> PaperBroker:
    kwargs.setdefault("ticker", TICKER)
    return PaperBroker(cash, **kwargs)


def _plan(decision: str, **kwargs) -> dict:
    plan = {
        "decision": decision,
        "entry": 10.0,
        "stop_loss": 9.0,
        "take_profit": 12.0,
        "position_pct": 0.2,
        "conflict": False,
        "risk_reward": 2.0,
    }
    plan.update(kwargs)
    return plan


# ---------------------------------------------------------------------------
# 买入成交与现金扣减
# ---------------------------------------------------------------------------
class BuyFillTests(unittest.TestCase):
    def test_buy_fills_at_expected_price_and_debits_cash_with_fees(self):
        broker = _broker(100000.0)
        cost_model = CostModel()
        order = broker.submit_order(TICKER, "buy", 1000, price=10.0)
        self.assertEqual(order.status, "pending")

        broker.process_day(START, _day(START, 10.0, 10.2, 9.9, 10.1))

        self.assertEqual(order.status, "filled")
        expected_fill = 10.0 * (1 + cost_model.slippage_rate)
        self.assertAlmostEqual(order.fill_price, round(expected_fill, 4), places=4)

        amount = 1000 * expected_fill
        fee = cost_model.buy_cost(amount)
        self.assertAlmostEqual(broker.account().cash, 100000.0 - amount - fee, places=6)

        position = broker.positions()[TICKER]
        self.assertEqual(position.quantity, 1000)
        self.assertAlmostEqual(position.avg_cost, expected_fill, places=6)
        self.assertAlmostEqual(broker.account().total_fees, fee, places=6)

    def test_min_commission_applies_to_small_orders(self):
        """小额成交也要收 5 元最低佣金。"""
        broker = _broker(100000.0)
        order = broker.submit_order(TICKER, "buy", 100, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        fee = broker.account().total_fees
        self.assertGreaterEqual(fee, 5.0)
        self.assertEqual(order.status, "filled")

    def test_frozen_cash_released_after_fill(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        self.assertGreater(broker.account().frozen_cash, 0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        self.assertAlmostEqual(broker.account().frozen_cash, 0.0, places=6)


# ---------------------------------------------------------------------------
# T+1
# ---------------------------------------------------------------------------
class TPlusOneTests(unittest.TestCase):
    def test_shares_bought_today_are_not_sellable_until_next_day(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))

        position = broker.positions()[TICKER]
        self.assertEqual(position.quantity, 1000)
        self.assertEqual(position.sellable_qty, 0, "当日买入不可当日卖出")

        # 次日：批次解禁
        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 10.0, 10.1, 9.9, 10.0))
        position = broker.positions()[TICKER]
        self.assertEqual(position.sellable_qty, 1000, "次日应可卖")

    def test_sell_order_on_locked_shares_waits_until_unlock(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))

        sell = broker.submit_order(TICKER, "sell", 1000, price=10.0)
        self.assertEqual(sell.status, "pending")
        self.assertIn("T+1", sell.note)

        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 10.0, 10.1, 9.9, 10.0))
        self.assertEqual(sell.status, "filled")
        self.assertNotIn(TICKER, broker.positions())


# ---------------------------------------------------------------------------
# 涨跌停不可成交
# ---------------------------------------------------------------------------
class LimitPriceTests(unittest.TestCase):
    def test_buy_at_limit_up_is_rejected_with_reason(self):
        broker = _broker(100000.0)
        # 第一天收盘 10.00，建立前收盘基准
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.00))

        order = broker.submit_order(TICKER, "buy", 1000, price=11.0)
        next_day = START + timedelta(days=1)
        # 次日开盘即涨停（前收 10.00 → 涨停价 11.00）
        broker.process_day(next_day, _day(next_day, 11.0, 11.0, 10.9, 11.0))

        self.assertEqual(order.status, "rejected")
        self.assertEqual(order.reason, "limit_up")
        self.assertIn("涨停", order.note)
        self.assertEqual(broker.positions().get(TICKER), None)

    def test_sell_at_limit_down_is_rejected_with_reason(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.00))

        sell = broker.submit_order(TICKER, "sell", 1000, price=9.0)
        next_day = START + timedelta(days=1)
        # 次日开盘即跌停（前收 10.00 → 跌停价 9.00）
        broker.process_day(next_day, _day(next_day, 9.0, 9.0, 9.0, 9.0))

        self.assertEqual(sell.status, "rejected")
        self.assertEqual(sell.reason, "limit_down")
        self.assertIn("跌停", sell.note)
        # 持仓保留（卖不掉不等于平仓）
        self.assertEqual(broker.positions()[TICKER].quantity, 1000)


# ---------------------------------------------------------------------------
# 整手与资金
# ---------------------------------------------------------------------------
class LotAndCashTests(unittest.TestCase):
    def test_buy_quantity_is_rounded_down_to_whole_lot(self):
        broker = _broker(100000.0)
        order = broker.submit_order(TICKER, "buy", 350, price=10.0)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.quantity, 300)
        self.assertEqual(order.requested_quantity, 350)

    def test_buy_below_one_lot_is_rejected(self):
        broker = _broker(100000.0)
        order = broker.submit_order(TICKER, "buy", 50, price=10.0)
        self.assertEqual(order.status, "rejected")
        self.assertIn("一手", order.reason)

    def test_insufficient_cash_is_rejected_with_reason(self):
        broker = _broker(5000.0)
        order = broker.submit_order(TICKER, "buy", 1000, price=10.0)
        self.assertEqual(order.status, "rejected")
        self.assertIn("资金不足", order.reason)
        self.assertEqual(len(broker.positions()), 0)

    def test_frozen_cash_counts_against_available_cash(self):
        """挂单冻结资金后，第二笔买单不能再动用同一笔钱。"""
        broker = _broker(12000.0, max_single_position_pct=1.0, max_total_position_pct=1.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        second = broker.submit_order(TICKER2, "buy", 1000, price=10.0)
        self.assertEqual(second.status, "rejected")
        self.assertIn("资金不足", second.reason)

    def test_odd_lot_sell_must_be_full_position(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 10.0, 10.1, 9.9, 10.0))

        # 卖出零股但不是全部持仓 → 拒绝
        odd = broker.submit_order(TICKER, "sell", 50, price=10.0)
        self.assertEqual(odd.status, "rejected")
        self.assertIn("零股", odd.reason)


# ---------------------------------------------------------------------------
# 组合级上限
# ---------------------------------------------------------------------------
class PositionLimitTests(unittest.TestCase):
    def test_single_name_position_cap_is_enforced(self):
        broker = _broker(100000.0, max_single_position_pct=0.20)
        # 30000 元 ≈ 30% 权益，超过单标的上限 20%
        order = broker.submit_order(TICKER, "buy", 3000, price=10.0)
        self.assertEqual(order.status, "rejected")
        self.assertIn("单标的仓位上限", order.reason)

    def test_total_position_cap_is_enforced(self):
        broker = _broker(100000.0, max_single_position_pct=1.0, max_total_position_pct=0.50)
        first = broker.submit_order(TICKER, "buy", 3000, price=10.0)  # 30%
        self.assertEqual(first.status, "pending")
        broker.process_day(
            START,
            pd.DataFrame(
                [
                    {**_row(START, 10.0, 10.1, 9.9, 10.0), "Ticker": TICKER},
                    {**_row(START, 10.0, 10.1, 9.9, 10.0), "Ticker": TICKER2},
                ]
            ),
        )
        second = broker.submit_order(TICKER2, "buy", 3000, price=10.0)  # 合计 60% > 50%
        self.assertEqual(second.status, "rejected")
        self.assertIn("总仓位上限", second.reason)


# ---------------------------------------------------------------------------
# 止损 / 止盈
# ---------------------------------------------------------------------------
class StopTargetTests(unittest.TestCase):
    def test_stop_loss_triggers_exit(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0, stop_loss=9.5)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))

        next_day = START + timedelta(days=1)
        # 次日最低价 9.0 击穿止损 9.5 → 以止损价离场
        broker.process_day(next_day, _day(next_day, 10.0, 10.0, 9.0, 9.2))

        self.assertNotIn(TICKER, broker.positions())
        sells = [row for row in broker.report()["trades"] if row["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["reason"], "止损")
        self.assertAlmostEqual(sells[0]["price"], 9.5 * (1 - 0.001), places=4)

    def test_take_profit_triggers_exit(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0, take_profit=12.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))

        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 11.0, 12.5, 10.8, 12.0))

        self.assertNotIn(TICKER, broker.positions())
        sells = [row for row in broker.report()["trades"] if row["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["reason"], "止盈")
        self.assertAlmostEqual(sells[0]["price"], 12.0 * (1 - 0.001), places=4)

    def test_stop_loss_not_triggered_when_price_holds(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0, stop_loss=9.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 10.0, 10.2, 9.5, 10.1))
        self.assertEqual(broker.positions()[TICKER].quantity, 1000)

    def test_stop_triggered_on_buy_day_is_deferred_by_t_plus_1(self):
        """买入当日即击穿止损：T+1 不可卖 → 离场顺延到次日。"""
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0, stop_loss=9.5)
        # 当日最低 9.0 < 止损 9.5，但当日买入不可卖
        broker.process_day(START, _day(START, 10.0, 10.0, 9.0, 9.2))
        self.assertEqual(broker.positions()[TICKER].quantity, 1000)
        self.assertEqual(broker.positions()[TICKER].pending_exit, "止损")

        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 9.2, 9.4, 9.0, 9.3))
        self.assertNotIn(TICKER, broker.positions())
        sells = [row for row in broker.report()["trades"] if row["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["reason"], "止损")


# ---------------------------------------------------------------------------
# 决策卡 → 委托
# ---------------------------------------------------------------------------
class DecisionPlanOrderTests(unittest.TestCase):
    def test_hold_produces_no_order(self):
        broker = _broker(100000.0)
        order = order_from_decision_plan(
            _plan("HOLD", position_pct=0.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNone(order)
        self.assertEqual(len(broker.report()["orders"]), 0)

    def test_zero_position_pct_produces_no_order(self):
        """仓位门槛/冲突守卫把仓位置 0 时，不得自行补仓。"""
        broker = _broker(100000.0)
        order = order_from_decision_plan(
            _plan("BUY", position_pct=0.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNone(order)
        self.assertEqual(len(broker.report()["orders"]), 0)

    def test_conflict_produces_no_order(self):
        broker = _broker(100000.0)
        order = order_from_decision_plan(
            _plan("BUY", conflict=True), TICKER, broker.account(), broker=broker
        )
        self.assertIsNone(order)

    def test_buy_produces_order_sized_by_position_pct(self):
        broker = _broker(100000.0)
        order = order_from_decision_plan(
            _plan("BUY", position_pct=0.10, entry=10.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.side, "buy")
        self.assertEqual(order.status, "pending")
        # 100000 * 10% = 10000 元 → 1000 股
        self.assertEqual(order.quantity, 1000)
        self.assertEqual(order.limit_price, 10.0)
        self.assertEqual(order.stop_loss, 9.0)
        self.assertEqual(order.take_profit, 12.0)

    def test_buy_order_is_rejected_when_cash_insufficient(self):
        """已被挂单冻结的资金不能重复使用 → 拒单并给出原因。"""
        broker = _broker(100000.0, max_single_position_pct=1.0, max_total_position_pct=1.0)
        # 第一笔挂单冻结 8 万现金
        first = broker.submit_order(TICKER, "buy", 8000, price=10.0)
        self.assertEqual(first.status, "pending")
        self.assertGreater(broker.account().frozen_cash, 70000.0)

        # 决策卡再要求买 20% 权益（2 万元）→ 可用资金不足
        order = order_from_decision_plan(
            _plan("BUY", position_pct=0.20, entry=10.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.status, "rejected")
        self.assertIn("资金不足", order.reason)

    def test_sell_produces_sell_order_for_sellable_position(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        next_day = START + timedelta(days=1)
        broker.process_day(next_day, _day(next_day, 10.0, 10.1, 9.9, 10.0))

        order = order_from_decision_plan(
            _plan("SELL", position_pct=0.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.side, "sell")
        self.assertEqual(order.quantity, 1000)
        self.assertEqual(order.status, "pending")

    def test_sell_without_position_produces_no_order(self):
        broker = _broker(100000.0)
        order = order_from_decision_plan(
            _plan("SELL", position_pct=0.0), TICKER, broker.account(), broker=broker
        )
        self.assertIsNone(order)


# ---------------------------------------------------------------------------
# 报告与净值曲线
# ---------------------------------------------------------------------------
class ReportTests(unittest.TestCase):
    def test_report_structure(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0, stop_loss=9.0, take_profit=12.0)
        broker.process_day(START, _day(START, 10.0, 10.2, 9.9, 10.0))

        report = broker.report()
        self.assertIn("account", report)
        self.assertIn("positions", report)
        self.assertIn("orders", report)
        self.assertIn("trades", report)
        self.assertIn("equity_curve", report)

        account = report["account"]
        for key in ("cash", "equity", "total_pnl", "total_fees", "win_rate"):
            self.assertIn(key, account)

        row = report["positions"][0]
        for key in (
            "quantity",
            "sellable_qty",
            "avg_cost",
            "last_price",
            "unrealised_pnl",
            "stop_loss",
            "take_profit",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["quantity"], 1000)
        self.assertEqual(row["sellable_qty"], 0)
        self.assertEqual(row["stop_loss"], 9.0)
        self.assertEqual(row["take_profit"], 12.0)

        self.assertEqual(len(report["orders"]), 1)
        self.assertEqual(len(report["trades"]), 1)
        self.assertEqual(report["trades"][0]["side"], "buy")

    def test_equity_curve_grows_over_time(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        rows = [
            _row(START, 10.0, 10.1, 9.9, 10.0),
            _row(START + timedelta(days=1), 10.0, 10.5, 9.9, 10.4),
            _row(START + timedelta(days=2), 10.4, 10.9, 10.3, 10.8),
        ]
        for row in rows:
            broker.process_day(row["Date"], pd.DataFrame([row]))

        curve = broker.equity_curve
        self.assertEqual(len(curve), 3)
        for point in curve:
            self.assertIn("date", point)
            self.assertIn("equity", point)
            self.assertIn("cash", point)
            self.assertIn("position_value", point)
        self.assertGreater(curve[-1]["equity"], curve[0]["equity"])
        self.assertGreater(broker.account().total_pnl, 0)

    def test_account_totals_are_consistent(self):
        broker = _broker(100000.0)
        broker.submit_order(TICKER, "buy", 1000, price=10.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.5))
        account = broker.account()
        position_value = sum(pos.market_value for pos in account.positions.values())
        self.assertAlmostEqual(account.equity, account.cash + position_value, places=6)
        self.assertAlmostEqual(
            account.total_pnl, account.equity - account.initial_cash, places=6
        )

    def test_processing_the_same_day_twice_is_rejected(self):
        """同日重复推进会造成重复成交，必须显式报错。"""
        broker = _broker(100000.0)
        broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))
        with self.assertRaises(ValueError):
            broker.process_day(START, _day(START, 10.0, 10.1, 9.9, 10.0))


# ---------------------------------------------------------------------------
# 停牌与撤单
# ---------------------------------------------------------------------------
class SuspensionAndCancelTests(unittest.TestCase):
    def test_suspended_ticker_does_not_fill_and_is_not_marked(self):
        """停牌日无行情行 → 不成交、不盯市，挂单保持等待。"""
        broker = _broker(100000.0)
        order = broker.submit_order(TICKER, "buy", 1000, price=10.0)
        # 第二日无行情（停牌）
        next_day = START + timedelta(days=1)
        broker.process_day(next_day, pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"]))
        self.assertEqual(order.status, "pending")
        self.assertIn("无行情", order.note)
        self.assertEqual(len(broker.positions()), 0)

        # 复牌后成交
        resume = START + timedelta(days=2)
        broker.process_day(resume, _day(resume, 10.0, 10.1, 9.9, 10.0))
        self.assertEqual(order.status, "filled")

    def test_cancel_order_releases_frozen_cash(self):
        broker = _broker(100000.0)
        order = broker.submit_order(TICKER, "buy", 1000, price=10.0)
        self.assertGreater(broker.account().frozen_cash, 0)
        self.assertTrue(broker.cancel_order(order.id))
        self.assertEqual(order.status, "cancelled")
        self.assertAlmostEqual(broker.account().frozen_cash, 0.0, places=6)


# ---------------------------------------------------------------------------
# 多标的推进辅助函数
# ---------------------------------------------------------------------------
class AdvanceBrokerTests(unittest.TestCase):
    def test_advance_broker_walks_multiple_tickers(self):
        broker = PaperBroker(100000.0)
        frames = {
            TICKER: pd.DataFrame(
                [
                    _row(START, 10.0, 10.1, 9.9, 10.0),
                    _row(START + timedelta(days=1), 10.0, 10.4, 9.9, 10.3),
                ]
            ),
            TICKER2: pd.DataFrame(
                [
                    _row(START, 20.0, 20.2, 19.9, 20.0),
                    _row(START + timedelta(days=1), 20.0, 20.1, 19.8, 19.9),
                ]
            ),
        }
        advance_broker(broker, frames)
        curve = broker.equity_curve
        self.assertEqual(len(curve), 2)
        self.assertEqual(curve[0]["date"], START.strftime("%Y-%m-%d"))
        # 第二个交易日收盘：600519 涨、000001 跌
        self.assertEqual(len(broker.positions()), 0)


if __name__ == "__main__":
    unittest.main()
