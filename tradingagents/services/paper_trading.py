"""模拟盘（Paper Trading）服务：把决策卡变成可执行、可追踪的模拟交易。

WHY：系统此前只能产出研究报告与一张"结构化决策卡"（建议入场/止损/目标/仓位），
但没有任何可以落地的执行层 —— 没有下单、没有持仓、没有成交模拟，因此
"这份决策到底能不能执行"从未被验证过。

本模块是**共享回测引擎之上的委托/持仓层**：

  - 复用 ``services/backtest_engine.py`` 的成本模型与撮合规则，不重写 A 股
    约束，避免项目里出现第二套口径（历史上正因如此导致"回测结论"与
    "线上行为"从来不是同一个东西）；
  - 在其上补齐回测引擎不需要、但真实交易必须有的状态：挂单、冻结资金、
    T+1 可卖数量、持仓批次、逐日盯市、组合级权重上限、逐笔台账；
  - 价格数据一律**注入**（行情表或价格字典），核心撮合逻辑完全离线可测，
    联网取数只保留一个薄封装（``load_price_frames``）。

核心 A 股约束（全部在本模块内实现，涨跌停判定复用引擎）：

  - **T+1**：当日买入当日不可卖。持仓按"批次（lot）"记录买入日，
    次一交易日自动解禁；
  - **涨跌停不可成交**：涨停板无法买入、跌停板无法卖出（价格判定复用
    ``backtest_engine._at_limit``，与回测口径完全一致）；
  - **100 股整手**：买入数量向下取整到整手，不足一手直接拒单；卖出零股
    仅允许"一次性全部卖出"（A 股允许卖出零股，但不能拆着卖）；
  - **停牌**：当日无行情的标的不撮合、不盯市，挂单保持等待；
  - **成本**：佣金（含 5 元最低）、卖出印花税、过户费，滑点作用在成交价上。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from tradingagents.dataflows.ashare_rules import price_limit_pct
from tradingagents.services.backtest_engine import (
    CostModel,
    ExecutionRules,
    _at_limit,
    limit_prices,
)

logger = logging.getLogger(__name__)

#: A 股一手 = 100 股。
LOT_SIZE = 100

#: 价格比较容差（A 股报价按分四舍五入）。
_PRICE_EPS = 1e-6

#: 组合约束默认值：单标的上限与总仓位上限。
DEFAULT_MAX_SINGLE_POSITION_PCT = 0.20
DEFAULT_MAX_TOTAL_POSITION_PCT = 1.00

#: 权重校验容差：滑点与费用会让"下单权重"与"成交权重"漂移约 0.1%~0.2%，
#: 没有容差就会把"建议仓位恰好等于上限"的正常委托误判为超限。
_WEIGHT_TOLERANCE = 0.005

#: 行情列名别名（兼容 AkShare 的中文列）。
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "open": ("Open", "开盘"),
    "high": ("High", "最高"),
    "low": ("Low", "最低"),
    "close": ("Close", "收盘"),
    "volume": ("Volume", "成交量"),
    "name": ("Name", "名称", "股票名称"),
    "ticker": ("Ticker", "代码", "symbol"),
}

_RENAME_COLUMNS = {
    "日期": "Date",
    "开盘": "Open",
    "最高": "High",
    "最低": "Low",
    "收盘": "Close",
    "成交量": "Volume",
    "名称": "Name",
    "代码": "Ticker",
}


def _date_text(value: Any) -> str:
    """把任意日期输入规范化为 ``YYYY-MM-DD`` 字符串。"""
    if value is None:
        return datetime.now().strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value[:10]
    return pd.Timestamp(value).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Lot:
    """持仓批次：T+1 解禁的最小单位。

    WHY：只用"持仓总量"无法表达 T+1 —— 同一天里既有昨天买的（可卖）也有
    今天买的（不可卖）。因此每次买入都记为一个批次，解禁日 = 买入日的
    下一个交易日。
    """

    quantity: int
    price: float
    acquired_at: str
    sellable: bool = False


@dataclass
class Order:
    """一张委托单。

    WHY 保留"被拒"状态而不是直接丢弃：模拟盘的价值就是让用户看到
    "我本来想买，但今天涨停买不到" —— 拒单原因本身就是结论。
    """

    id: str
    ticker: str
    side: str  # buy / sell
    order_type: str = "market"  # market / limit
    quantity: int = 0
    limit_price: float | None = None
    submitted_at: str = ""
    status: str = "pending"  # pending / filled / rejected / cancelled
    filled_at: str | None = None
    fill_price: float | None = None
    reason: str = ""
    cost: dict[str, float] = field(default_factory=dict)
    note: str = ""
    name: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    frozen_amount: float = 0.0
    filled_quantity: int = 0
    requested_quantity: int = 0
    #: 提交时所处的会话序号（已完成的交易日数）。
    session_created: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "name": self.name,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "requested_quantity": self.requested_quantity,
            "filled_quantity": self.filled_quantity,
            "limit_price": self.limit_price,
            "submitted_at": self.submitted_at,
            "status": self.status,
            "filled_at": self.filled_at,
            "fill_price": self.fill_price,
            "reason": self.reason,
            "cost": dict(self.cost),
            "note": self.note,
        }


@dataclass
class Position:
    """一个标的的持仓。"""

    ticker: str
    quantity: int = 0
    avg_cost: float = 0.0
    opened_at: str = ""
    last_price: float = 0.0
    unrealised_pnl: float = 0.0
    unrealised_pnl_pct: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    lots: list[Lot] = field(default_factory=list)
    name: str = ""
    entry_fees: float = 0.0
    #: 已触发但因 T+1 / 跌停无法成交的离场指令（下次可成交时强制离场）。
    pending_exit: str = ""

    @property
    def sellable_qty(self) -> int:
        """当前可卖数量（T+1 已解禁的部分）。"""
        return sum(lot.quantity for lot in self.lots if lot.sellable)

    @property
    def market_value(self) -> float:
        """持仓市值；未盯市时用成本价兜底。"""
        price = self.last_price if self.last_price > 0 else self.avg_cost
        return self.quantity * price


@dataclass
class Account:
    """账户快照。"""

    cash: float = 0.0
    initial_cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    equity: float = 0.0
    total_pnl: float = 0.0
    frozen_cash: float = 0.0
    total_fees: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash": round(self.cash, 2),
            "equity": round(self.equity, 2),
            "initial_cash": round(self.initial_cash, 2),
            "total_pnl": round(self.total_pnl, 2),
            "frozen_cash": round(self.frozen_cash, 2),
            "total_fees": round(self.total_fees, 2),
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "position_count": len(self.positions),
        }


# ---------------------------------------------------------------------------
# 模拟撮合主体
# ---------------------------------------------------------------------------
class PaperBroker:
    """模拟撮合器：挂单 → 成交 → 持仓 → 盯市 → 组合约束。

    设计原则：**价格必须注入**。所有行情通过 ``process_day(date, frame)``
    或 ``mark_to_market(date, prices)`` 喂进来，核心逻辑不联网、可离线复现。
    """

    def __init__(
        self,
        initial_cash: float = 100000.0,
        *,
        cost_model: CostModel | None = None,
        rules: ExecutionRules | None = None,
        max_single_position_pct: float = DEFAULT_MAX_SINGLE_POSITION_PCT,
        max_total_position_pct: float = DEFAULT_MAX_TOTAL_POSITION_PCT,
        ticker: str = "",
        name: str = "",
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.rules = rules or ExecutionRules()
        self.initial_cash = float(initial_cash)
        self.max_single_position_pct = float(max_single_position_pct)
        self.max_total_position_pct = float(max_total_position_pct)
        #: 单标的行情模式下使用的默认代码（``process_day`` 收到无 Ticker 列的行情时使用）。
        self.default_ticker = str(ticker or "")
        self.default_name = str(name or "")

        self._cash = float(initial_cash)
        self._frozen_cash = 0.0
        self._total_fees = 0.0
        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._ledger: list[dict[str, Any]] = []
        self._equity_curve: list[dict[str, Any]] = []
        self._notes: list[str] = []
        self._closed_trades: list[float] = []
        self._last_close: dict[str, float] = {}
        self._last_close_date: dict[str, str] = {}
        #: 行情表自带的前收盘价 ``(日期, 价格)``，用于涨跌停判定。
        self._prev_close_hint: dict[str, tuple[str, float]] = {}
        self._order_seq = 0
        self._current_date = ""
        #: 已完成交易日数（用于避免"当日提交、当日成交"的前视偏差）。
        self._day_index = 0
        #: 最后一个已完成交易日的日期。
        self._last_processed_date = ""

    # ------------------------------------------------------------------
    # 对外读取
    # ------------------------------------------------------------------
    def positions(self) -> dict[str, Position]:
        """返回当前持仓（按代码索引）。"""
        return self._positions

    def account(self) -> Account:
        """返回账户快照。"""
        position_value = sum(pos.market_value for pos in self._positions.values())
        equity = self._cash + position_value
        wins = sum(1 for value in self._closed_trades if value > 0)
        return Account(
            cash=self._cash,
            initial_cash=self.initial_cash,
            positions=dict(self._positions),
            orders=list(self._orders),
            equity=equity,
            total_pnl=equity - self.initial_cash,
            frozen_cash=self._frozen_cash,
            total_fees=self._total_fees,
            trade_count=len(self._closed_trades),
            win_rate=(wins / len(self._closed_trades)) if self._closed_trades else 0.0,
        )

    @property
    def equity_curve(self) -> list[dict[str, Any]]:
        """逐日净值曲线：``[{"date", "equity", "cash", "position_value"}]``。"""
        return list(self._equity_curve)

    def report(self, last_orders: int = 20) -> dict[str, Any]:
        """输出完整模拟盘报告（账户 / 持仓 / 委托 / 台账 / 净值）。"""
        account = self.account()
        position_rows = [
            {
                "ticker": pos.ticker,
                "name": pos.name,
                "quantity": pos.quantity,
                "sellable_qty": pos.sellable_qty,
                "avg_cost": round(pos.avg_cost, 4),
                "last_price": round(pos.last_price, 4),
                "market_value": round(pos.market_value, 2),
                "unrealised_pnl": round(pos.unrealised_pnl, 2),
                "unrealised_pnl_pct": round(pos.unrealised_pnl_pct, 4),
                "stop_loss": round(pos.stop_loss, 4),
                "take_profit": round(pos.take_profit, 4),
                "opened_at": pos.opened_at,
            }
            for pos in sorted(account.positions.values(), key=lambda item: item.ticker)
        ]
        return {
            "account": account.to_dict(),
            "positions": position_rows,
            "orders": [order.to_dict() for order in account.orders[-last_orders:]],
            "trades": list(self._ledger),
            "equity_curve": list(self._equity_curve),
            "notes": list(self._notes),
        }

    # ------------------------------------------------------------------
    # 委托
    # ------------------------------------------------------------------
    def submit_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        *,
        order_type: str = "market",
        limit_price: float | None = None,
        name: str = "",
        reason: str = "",
        note: str = "",
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        price: float | None = None,
        submitted_at: str | None = None,
    ) -> Order:
        """提交一张委托单，返回 ``Order``（无论是否被拒）。

        WHY 总是返回订单对象而不是抛异常：模拟盘要能如实记录"这笔单被拒了
        以及为什么"，拒单本身就是对决策的反馈。
        """
        ticker = str(ticker or "").strip()
        side = str(side or "").strip().lower()
        order_type = str(order_type or "market").strip().lower()
        submitted = (
            _date_text(submitted_at) if submitted_at else (self._current_date or _date_text(None))
        )
        order = Order(
            id=self._next_order_id(),
            ticker=ticker,
            side=side,
            order_type=order_type,
            quantity=int(quantity or 0),
            requested_quantity=int(quantity or 0),
            limit_price=float(limit_price) if limit_price else None,
            submitted_at=submitted,
            reason=reason,
            note=note,
            name=name or self.default_name,
            stop_loss=float(stop_loss or 0.0),
            take_profit=float(take_profit or 0.0),
            session_created=self._day_index,
        )
        self._orders.append(order)

        if not ticker:
            return self._reject(order, "缺少标的代码")
        if side not in ("buy", "sell"):
            return self._reject(order, f"不支持的委托方向：{side}")
        if order_type not in ("market", "limit"):
            return self._reject(order, f"不支持的委托类型：{order_type}")
        if order.quantity <= 0:
            return self._reject(order, "委托数量必须为正")

        if side == "buy":
            return self._prepare_buy(order, price)
        return self._prepare_sell(order)

    def cancel_order(self, order_id: str) -> bool:
        """撤销未成交委托，返回是否撤销成功。"""
        for order in self._orders:
            if order.id == order_id and order.status == "pending":
                order.status = "cancelled"
                order.note = "用户撤单"
                self._unfreeze(order)
                return True
        return False

    def _prepare_buy(self, order: Order, price: float | None) -> Order:
        """买单前置校验：整手 → 资金 → 组合权重。"""
        lot = int(getattr(self.rules, "lot_size", LOT_SIZE) or LOT_SIZE)
        # 整手：向下取整到整手（A 股买入必须是 100 股的整数倍）
        order.quantity = (order.quantity // lot) * lot
        if order.quantity < lot:
            return self._reject(
                order, f"买入数量不足一手（{lot} 股），已按整手下取整后为 0"
            )

        reference = order.limit_price or price or self._last_close.get(order.ticker)
        if not reference or reference <= 0:
            # 没有参考价（既无委托价也无历史收盘）：无法预估资金占用。
            # WHY 不直接拒单：市价单的真实成交价此刻未知，拒单会把"缺数据"
            # 伪装成"资金不足"。改为放行到成交环节再按实际价格校验。
            order.note = "缺少参考价，资金与仓位校验推迟到成交环节"
            return order

        estimated_fill = reference * (1 + self.cost_model.slippage_rate)
        estimated_amount = order.quantity * estimated_fill
        estimated_fee = self.cost_model.buy_cost(estimated_amount)
        needed = estimated_amount + estimated_fee
        available = self._cash - self._frozen_cash
        if needed > available + 1e-6:
            return self._reject(
                order,
                f"资金不足：需要 {needed:.2f} 元，可用 {available:.2f} 元",
            )

        ok, guard_reason = self._check_position_guards(
            order.ticker, order.quantity, estimated_fill
        )
        if not ok:
            return self._reject(order, guard_reason)

        order.frozen_amount = needed
        self._frozen_cash += needed
        order.note = order.note or "已报单，等待成交"
        return order

    def _prepare_sell(self, order: Order) -> Order:
        """卖单前置校验：持仓、可卖量（T+1）、零股规则。"""
        position = self._positions.get(order.ticker)
        if position is None or position.quantity <= 0:
            return self._reject(order, "无可卖持仓")
        if order.quantity > position.quantity:
            return self._reject(
                order, f"卖出数量超过持仓（持仓 {position.quantity} 股）"
            )
        lot = int(getattr(self.rules, "lot_size", LOT_SIZE) or LOT_SIZE)
        # A 股允许卖出零股，但只能一次性全部卖出，不能拆着卖
        if order.quantity % lot != 0 and order.quantity != position.quantity:
            return self._reject(order, "零股卖出必须一次性全部卖出")
        if order.quantity > position.sellable_qty:
            # T+1：可卖数量不足时**不直接拒单**，挂单等待次日解禁。
            # WHY：真实交易里 T+1 是"今天买不了、明天可以"，不是"下单无效"。
            order.note = (
                f"T+1 锁定：当前可卖 {position.sellable_qty} 股，"
                f"委托 {order.quantity} 股，待解禁后成交"
            )
        return order

    def _reject(self, order: Order, reason: str) -> Order:
        order.status = "rejected"
        order.reason = reason
        self._notes.append(f"{order.submitted_at} {order.ticker} {order.side} 被拒：{reason}")
        logger.info("委托被拒 %s %s %s：%s", order.id, order.ticker, order.side, reason)
        return order

    # ------------------------------------------------------------------
    # 组合级约束
    # ------------------------------------------------------------------
    def _check_position_guards(
        self, ticker: str, quantity: int, fill_price: float
    ) -> tuple[bool, str]:
        """单标的权重上限与总仓位上限校验。

        口径：``权重 = 持仓市值 / 成交前权益``，分母用**成交前**权益
        （现金 + 当前持仓市值）。WHY：买入让现金转成持仓，权益基本不变
        （只少了手续费），用成交后权益会让"刚好卡在上限"的委托被拒。

        容差 ``_WEIGHT_TOLERANCE``：滑点与费用会让"下单时的权重"与
        "成交后的权重"产生约 0.1%~0.2% 的漂移，没有容差就会把
        "建议仓位恰好等于上限"的正常委托误判为超限。
        """
        equity = self._equity()
        if equity <= 0:
            return False, "账户权益为 0，无法下单"
        position = self._positions.get(ticker)
        existing_value = position.market_value if position else 0.0
        new_weight = (existing_value + quantity * fill_price) / equity
        if new_weight > self.max_single_position_pct + _WEIGHT_TOLERANCE:
            return False, (
                f"单标的仓位上限 {self.max_single_position_pct:.0%}，"
                f"下单后权重 {new_weight:.1%}"
            )
        total_value = sum(pos.market_value for pos in self._positions.values())
        total_weight = (total_value + quantity * fill_price) / equity
        if total_weight > self.max_total_position_pct + _WEIGHT_TOLERANCE:
            return False, (
                f"总仓位上限 {self.max_total_position_pct:.0%}，"
                f"下单后仓位 {total_weight:.1%}"
            )
        return True, ""

    # ------------------------------------------------------------------
    # 逐日推进
    # ------------------------------------------------------------------
    def process_day(self, date: Any, frame: Any) -> None:
        """推进一个交易日。

        参数
        ----
        date:
            交易日。
        frame:
            当日行情。可以是
            （a）带 ``Ticker`` 列的多标的行情表；
            （b）单标的行情表（用 ``PaperBroker(default_ticker=...)`` 指定代码；
                若给了多行，则优先取 ``date`` 当天的行，其前一行收盘价作为
                涨跌停基准）；
            （c）``{ticker: {"open", "high", "low", "close"}}`` 字典。

        执行顺序：T+1 解禁 → 挂单撮合 → 止损/止盈 → 收盘盯市。
        """
        date_text = _date_text(date)
        if self._last_processed_date and date_text <= self._last_processed_date:
            # WHY 直接报错：同一个交易日被推进两次会让挂单成交两次、
            # 净值曲线出现重复点，且"当日提交、当日成交"会造成前视偏差。
            raise ValueError(
                f"交易日必须严格递增：当前 {date_text}，上一个已处理交易日 "
                f"{self._last_processed_date}"
            )
        self._current_date = date_text
        self._day_index += 1
        day_index = self._day_index
        bars = self._normalize_day_bars(date_text, frame)

        # 1) T+1 解禁：买入日早于今天的批次自动变为可卖
        self._unlock_lots(date_text)

        # 2) 撮合挂单
        for order in list(self._orders):
            if order.status != "pending":
                continue
            # 防前视偏差：当日提交的委托当日不成交（最早次日开盘成交）
            if order.session_created >= day_index:
                continue
            bar = bars.get(order.ticker)
            if bar is None:
                # 停牌/无行情：不撮合，挂单继续等待
                order.note = "当日无行情（停牌或非交易标的），未撮合"
                continue
            self._try_fill_order(order, bar, date_text)

        # 3) 止损 / 止盈
        for ticker, position in list(self._positions.items()):
            bar = bars.get(ticker)
            if bar is None:
                continue
            self._check_stop_target(ticker, position, bar, date_text)

        # 4) 收盘盯市 + 净值
        self.mark_to_market(date_text, {t: bar["close"] for t, bar in bars.items()})
        self._last_processed_date = date_text

    def mark_to_market(self, date: Any, prices: Mapping[str, float]) -> None:
        """按给定价格盯市并记录一个净值点。"""
        date_text = _date_text(date)
        for ticker, price in (prices or {}).items():
            if price is None:
                continue
            value = float(price)
            if value <= 0:
                continue
            self._last_close[ticker] = value
            self._last_close_date[ticker] = date_text
            position = self._positions.get(ticker)
            if position is None:
                continue
            position.last_price = value
            position.unrealised_pnl = (
                (position.last_price - position.avg_cost) * position.quantity
                - position.entry_fees
            )
            base = position.avg_cost * position.quantity
            position.unrealised_pnl_pct = (
                position.unrealised_pnl / base if base > 0 else 0.0
            )

        position_value = sum(pos.market_value for pos in self._positions.values())
        equity = self._cash + position_value
        self._equity_curve.append(
            {
                "date": date_text,
                "equity": round(equity, 2),
                "cash": round(self._cash, 2),
                "position_value": round(position_value, 2),
            }
        )

    # ------------------------------------------------------------------
    # 撮合细节
    # ------------------------------------------------------------------
    def _unlock_lots(self, date_text: str) -> None:
        """T+1 解禁：买入日早于今天的批次变为可卖。"""
        for position in self._positions.values():
            for lot in position.lots:
                if not lot.sellable and lot.acquired_at < date_text:
                    lot.sellable = True

    def _try_fill_order(self, order: Order, bar: dict[str, Any], date_text: str) -> None:
        """尝试撮合一张挂单。"""
        if order.side == "buy":
            self._try_fill_buy(order, bar, date_text)
        else:
            self._try_fill_sell(order, bar, date_text)

    def _prev_close(self, ticker: str, date_text: str) -> float | None:
        """上一交易日收盘价（涨跌停基准）。

        优先用盯市时记录的上一交易日收盘价；行情表自带的前收盘价作为补充，
        但必须早于当前交易日，否则会把当日收盘当成前收。
        """
        recorded_date = self._last_close_date.get(ticker)
        if recorded_date and recorded_date < date_text:
            return self._last_close.get(ticker)
        hint = self._prev_close_hint.get(ticker)
        if hint and hint[0] < date_text:
            return hint[1]
        return None

    def _limit_prices(
        self, ticker: str, name: str, date_text: str
    ) -> tuple[float | None, float | None]:
        """按上一交易日收盘价计算涨停价 / 跌停价。"""
        prev_close = self._prev_close(ticker, date_text)
        if not prev_close or prev_close <= 0:
            return None, None
        limit_pct = price_limit_pct(ticker, name or self.default_name)
        return limit_prices(prev_close, limit_pct)

    def _record_prev_close(self, ticker: str, date_text: str, price: float) -> None:
        """记录行情表自带的前收盘价（其所属日期早于当前交易日）。"""
        self._prev_close_hint[ticker] = (date_text, price)

    def _fee_breakdown(self, amount: float, side: str) -> dict[str, float]:
        """费用明细（口径与 ``CostModel`` 完全一致）。"""
        commission = max(self.cost_model.min_commission, amount * self.cost_model.commission_rate)
        stamp_duty = (
            amount * self.cost_model.stamp_duty_rate if side == "sell" else 0.0
        )
        transfer_fee = amount * self.cost_model.transfer_fee_rate
        return {
            "commission": round(commission, 4),
            "stamp_duty": round(stamp_duty, 4),
            "transfer_fee": round(transfer_fee, 4),
            "total_fee": round(commission + stamp_duty + transfer_fee, 4),
        }

    def _try_fill_buy(self, order: Order, bar: dict[str, Any], date_text: str) -> None:
        """买入撮合：涨停不可买；限价单需盘中触及限价。"""
        limit_up, _ = self._limit_prices(order.ticker, order.name, date_text)
        open_price = bar["open"]
        if self.rules.limit_price_blocked and _at_limit(open_price, limit_up, upward=True):
            order.status = "rejected"
            order.reason = "limit_up"
            order.note = (
                f"开盘价 {open_price:.2f} 触及涨停价 {limit_up:.2f}，涨停封板无法买入"
            )
            self._unfreeze(order)
            self._notes.append(f"{date_text} {order.ticker} 买入被拒：{order.note}")
            logger.info("买入被拒 %s %s：%s", date_text, order.ticker, order.note)
            return

        if order.order_type == "limit":
            limit_price = order.limit_price or 0.0
            if limit_price <= 0 or bar["low"] > limit_price + _PRICE_EPS:
                order.note = "未触及限价，继续挂单"
                return
            base_price = min(open_price, limit_price)
        else:
            base_price = open_price

        fill_price = base_price * (1 + self.cost_model.slippage_rate)
        quantity = order.quantity
        amount = quantity * fill_price
        fee = self.cost_model.buy_cost(amount)
        # 成交价可能高于预估价（跳空高开），成交环节必须按真实价格复核资金
        available = self._cash - self._frozen_cash + order.frozen_amount
        if amount + fee > available + 1e-6:
            order.status = "rejected"
            order.reason = "insufficient_cash"
            order.note = f"成交需 {amount + fee:.2f} 元，可用 {available:.2f} 元"
            self._unfreeze(order)
            self._notes.append(f"{date_text} {order.ticker} 买入被拒：{order.note}")
            return

        # 成交价变化可能突破组合权重上限，按真实成交价再校验一次
        ok, guard_reason = self._check_position_guards(
            order.ticker, quantity, fill_price
        )
        if not ok:
            order.status = "rejected"
            order.reason = "position_limit"
            order.note = guard_reason
            self._unfreeze(order)
            self._notes.append(f"{date_text} {order.ticker} 买入被拒：{guard_reason}")
            return

        self._cash -= amount + fee
        self._total_fees += fee
        self._unfreeze(order)
        order.filled_at = date_text
        order.fill_price = round(fill_price, 4)
        order.filled_quantity = quantity
        order.status = "filled"
        order.cost = {
            "amount": round(amount, 2),
            "slippage_cost": round((fill_price - base_price) * quantity, 4),
            "slippage_rate": self.cost_model.slippage_rate,
            **self._fee_breakdown(amount, "buy"),
        }

        position = self._positions.get(order.ticker)
        if position is None:
            position = Position(ticker=order.ticker, opened_at=date_text, name=order.name)
            self._positions[order.ticker] = position
        old_qty = position.quantity
        position.avg_cost = (
            (position.avg_cost * old_qty + fill_price * quantity) / (old_qty + quantity)
            if old_qty + quantity > 0
            else fill_price
        )
        position.quantity = old_qty + quantity
        position.entry_fees += fee
        # T+1：当日买入的批次当日不可卖
        position.lots.append(
            Lot(
                quantity=quantity,
                price=fill_price,
                acquired_at=date_text,
                sellable=False,
            )
        )
        if order.stop_loss > 0:
            position.stop_loss = order.stop_loss
        if order.take_profit > 0:
            position.take_profit = order.take_profit
        if position.last_price <= 0:
            position.last_price = fill_price
        position.unrealised_pnl = (
            (position.last_price - position.avg_cost) * position.quantity
            - position.entry_fees
        )

        self._ledger.append(
            {
                "date": date_text,
                "ticker": order.ticker,
                "name": position.name,
                "side": "buy",
                "quantity": quantity,
                "price": round(fill_price, 4),
                "amount": round(amount, 2),
                "fee": round(fee, 2),
                "cash_after": round(self._cash, 2),
                "reason": order.reason or "买入成交",
                "realized_pnl": 0.0,
                "position_quantity": position.quantity,
            }
        )
        logger.info(
            "买入成交 %s %s %d 股 @ %.4f（费用 %.2f）",
            date_text,
            order.ticker,
            quantity,
            fill_price,
            fee,
        )

    def _try_fill_sell(self, order: Order, bar: dict[str, Any], date_text: str) -> None:
        """卖出撮合：跌停不可卖；T+1 未解禁则延后。"""
        _, limit_down = self._limit_prices(order.ticker, order.name, date_text)
        open_price = bar["open"]
        if self.rules.limit_price_blocked and _at_limit(open_price, limit_down, upward=False):
            order.status = "rejected"
            order.reason = "limit_down"
            order.note = (
                f"开盘价 {open_price:.2f} 触及跌停价 {limit_down:.2f}，跌停封板无法卖出"
            )
            self._notes.append(f"{date_text} {order.ticker} 卖出被拒：{order.note}")
            logger.info("卖出被拒 %s %s：%s", date_text, order.ticker, order.note)
            return

        position = self._positions.get(order.ticker)
        if position is None or position.quantity <= 0:
            order.status = "cancelled"
            order.reason = "no_position"
            order.note = "已无持仓，委托作废"
            return

        remaining = order.quantity - order.filled_quantity
        sellable = position.sellable_qty
        if sellable <= 0:
            order.note = "T+1 锁定，等待下一交易日解禁"
            return

        quantity = min(remaining, sellable)
        if order.order_type == "limit":
            limit_price = order.limit_price or 0.0
            if limit_price <= 0 or bar["high"] < limit_price - _PRICE_EPS:
                order.note = "未触及限价，继续挂单"
                return
            base_price = max(open_price, limit_price)
        else:
            base_price = open_price

        fill_price = base_price * (1 - self.cost_model.slippage_rate)
        amount = quantity * fill_price
        realized = self._execute_sell(
            ticker=order.ticker,
            quantity=quantity,
            fill_price=fill_price,
            date_text=date_text,
            reason=order.reason or "卖出成交",
            name=order.name,
        )

        order.filled_at = date_text
        order.fill_price = round(fill_price, 4)
        order.filled_quantity = order.filled_quantity + quantity
        order.cost = {
            "amount": round(amount, 2),
            "slippage_cost": round((base_price - fill_price) * quantity, 4),
            "slippage_rate": self.cost_model.slippage_rate,
            **self._fee_breakdown(amount, "sell"),
        }
        if order.filled_quantity >= order.quantity:
            order.status = "filled"
            order.note = order.note or "全部成交"
        else:
            order.note = (
                f"部分成交 {order.filled_quantity} 股，剩余 {order.quantity - order.filled_quantity} 股待成交"
            )
        logger.info(
            "卖出成交 %s %s %d 股 @ %.4f（已实现 %.2f）",
            date_text,
            order.ticker,
            quantity,
            fill_price,
            realized,
        )

    def _execute_sell(
        self,
        *,
        ticker: str,
        quantity: int,
        fill_price: float,
        date_text: str,
        reason: str,
        name: str = "",
    ) -> float:
        """执行一笔卖出（更新现金、持仓、批次、台账），返回已实现盈亏。"""
        position = self._positions.get(ticker)
        if position is None or position.quantity <= 0:
            return 0.0
        quantity = min(quantity, position.quantity)

        amount = quantity * fill_price
        fee = self.cost_model.sell_cost(amount)
        self._cash += amount - fee
        self._total_fees += fee

        # 买入费用按卖出比例分摊，保证持仓级盈亏净额与现金变化一致
        allocated_entry_fee = (
            position.entry_fees * quantity / position.quantity if position.quantity else 0.0
        )
        realized = (fill_price - position.avg_cost) * quantity - allocated_entry_fee - fee
        position.entry_fees -= allocated_entry_fee
        position.quantity -= quantity
        self._consume_lots(position, quantity)
        self._closed_trades.append(realized)

        self._ledger.append(
            {
                "date": date_text,
                "ticker": ticker,
                "name": name or position.name,
                "side": "sell",
                "quantity": quantity,
                "price": round(fill_price, 4),
                "amount": round(amount, 2),
                "fee": round(fee, 2),
                "cash_after": round(self._cash, 2),
                "reason": reason,
                "realized_pnl": round(realized, 2),
                "position_quantity": position.quantity,
            }
        )
        if position.quantity <= 0:
            self._positions.pop(ticker, None)
        return realized

    @staticmethod
    def _consume_lots(position: Position, quantity: int) -> None:
        """FIFO 扣减持仓批次。"""
        remaining = quantity
        while remaining > 0 and position.lots:
            lot = position.lots[0]
            if lot.quantity <= remaining:
                remaining -= lot.quantity
                position.lots.pop(0)
            else:
                lot.quantity -= remaining
                remaining = 0

    # ------------------------------------------------------------------
    # 止损 / 止盈
    # ------------------------------------------------------------------
    def _check_stop_target(
        self, ticker: str, position: Position, bar: dict[str, Any], date_text: str
    ) -> None:
        """检查止损/止盈并在可成交时离场。

        WHY 保守处理：同一根 bar 内无法区分"先触止损还是先触止盈"，因此
        **止损优先**；跳空穿越止损时按开盘价成交（止损价在缺口内不可达）；
        跌停封板时无法离场，延后到下一个可成交日；T+1 未解禁时同样延后。
        """
        if position.quantity <= 0:
            return
        stop_enabled = bool(getattr(self.rules, "stop_enabled", True))
        target_enabled = bool(getattr(self.rules, "target_enabled", True))
        if not stop_enabled and not target_enabled:
            return

        open_price = bar["open"]
        _, limit_down = self._limit_prices(ticker, position.name, date_text)
        blocked = self.rules.limit_price_blocked and _at_limit(
            open_price, limit_down, upward=False
        )

        reason = ""
        base_price = 0.0
        if position.pending_exit:
            # 上一交易日已触发止损/止盈但未能成交 → 今日无条件离场
            reason = position.pending_exit
            base_price = open_price
        elif (
            stop_enabled
            and position.stop_loss > 0
            and bar["low"] <= position.stop_loss + _PRICE_EPS
        ):
            reason, base_price = "止损", min(open_price, position.stop_loss)
        elif (
            target_enabled
            and position.take_profit > 0
            and bar["high"] >= position.take_profit - _PRICE_EPS
        ):
            reason, base_price = "止盈", max(open_price, position.take_profit)

        if not reason:
            return
        if blocked:
            self._notes.append(f"{date_text} {ticker} 跌停封板，{reason}离场延后")
            # 离场指令挂起：下一次可成交时必须离场，不能因为价格已回到止损位
            # 之上就"忘记"触发过止损。
            position.pending_exit = reason
            return
        if position.sellable_qty <= 0:
            self._notes.append(
                f"{date_text} {ticker} 触发{reason}，但 T+1 未解禁，延后离场"
            )
            position.pending_exit = reason
            return

        quantity = position.sellable_qty
        fill_price = base_price * (1 - self.cost_model.slippage_rate)
        order = Order(
            id=self._next_order_id(),
            ticker=ticker,
            side="sell",
            order_type="market",
            quantity=quantity,
            requested_quantity=quantity,
            submitted_at=date_text,
            filled_at=date_text,
            fill_price=round(fill_price, 4),
            filled_quantity=quantity,
            status="filled",
            reason=reason,
            name=position.name,
        )
        self._orders.append(order)
        realized = self._execute_sell(
            ticker=ticker,
            quantity=quantity,
            fill_price=fill_price,
            date_text=date_text,
            reason=reason,
            name=position.name,
        )
        order.cost = {
            "amount": round(quantity * fill_price, 2),
            "slippage_cost": round((base_price - fill_price) * quantity, 4),
            "slippage_rate": self.cost_model.slippage_rate,
            **self._fee_breakdown(quantity * fill_price, "sell"),
        }
        logger.info(
            "%s离场 %s %s %d 股 @ %.4f（已实现 %.2f）",
            reason,
            date_text,
            ticker,
            quantity,
            fill_price,
            realized,
        )

    # ------------------------------------------------------------------
    # 行情解析
    # ------------------------------------------------------------------
    def _normalize_day_bars(
        self, date_text: str, frame: Any
    ) -> dict[str, dict[str, Any]]:
        """把多种形态的行情输入统一成 ``{ticker: bar}``。"""
        bars: dict[str, dict[str, Any]] = {}
        if frame is None:
            return bars
        if isinstance(frame, pd.DataFrame):
            if frame.empty:
                return bars
            data = frame.rename(columns=_RENAME_COLUMNS)
            if "Ticker" in data.columns:
                for ticker, group in data.groupby("Ticker", sort=False):
                    row, prev_close, prev_date = self._day_row(group, date_text)
                    if row is None:
                        continue
                    bar = self._bar_from_row(row)
                    if bar:
                        bars[str(ticker)] = bar
                    if prev_close is not None and prev_date:
                        self._record_prev_close(str(ticker), prev_date, prev_close)
            else:
                ticker = self._infer_ticker()
                if not ticker:
                    logger.warning("行情缺少 Ticker 列且无法推断标的，已忽略当日行情")
                    return bars
                row, prev_close, prev_date = self._day_row(data, date_text)
                if row is not None:
                    bar = self._bar_from_row(row)
                    if bar:
                        bars[ticker] = bar
                if prev_close is not None and prev_date:
                    self._record_prev_close(ticker, prev_date, prev_close)
        elif isinstance(frame, Mapping):
            for ticker, payload in frame.items():
                bar = self._bar_from_payload(payload)
                if bar:
                    bars[str(ticker)] = bar
        return bars

    @staticmethod
    def _day_row(
        data: pd.DataFrame, date_text: str
    ) -> tuple[Any, float | None, str]:
        """取出"当日行"与"上一交易日收盘价（及其日期）"。"""
        if "Date" in data.columns:
            dates = pd.to_datetime(data["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
            mask = dates == date_text
            if mask.any():
                today = data[mask]
                prior = data[dates < date_text]
                prev_close = None
                prev_date = ""
                if not prior.empty:
                    prev_close = float(prior.iloc[-1]["Close"])
                    prev_date = str(dates[dates < date_text].iloc[-1])
                return today.iloc[-1], prev_close, prev_date
        # 没有 Date 列或当天无数据：取最后一行，其前一行收盘价作为基准
        prev_close = None
        prev_date = ""
        if len(data) >= 2:
            prev_close = float(data["Close"].iloc[-2])
            if "Date" in data.columns:
                dates = pd.to_datetime(data["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                prev_date = str(dates.iloc[-2])
        return data.iloc[-1], prev_close, prev_date

    def _infer_ticker(self) -> str:
        """推断单标的行情对应的代码。"""
        if self.default_ticker:
            return self.default_ticker
        candidates = set(self._positions)
        candidates.update(
            order.ticker for order in self._orders if order.status == "pending"
        )
        if len(candidates) == 1:
            return next(iter(candidates))
        return ""

    @staticmethod
    def _bar_from_row(row: Any) -> dict[str, Any] | None:
        """从行情行构造 bar。"""
        close = 0.0
        for alias in _COLUMN_ALIASES["close"]:
            if alias in row:
                try:
                    close = float(row[alias])
                except (TypeError, ValueError):
                    close = 0.0
                break
        if close <= 0:
            return None

        def _value(key: str) -> float:
            for alias in _COLUMN_ALIASES[key]:
                if alias in row:
                    try:
                        return float(row[alias])
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        name = ""
        for alias in _COLUMN_ALIASES["name"]:
            if alias in row:
                name = str(row[alias])
                break
        return {
            "open": _value("open") or close,
            "high": _value("high") or close,
            "low": _value("low") or close,
            "close": close,
            "name": name,
        }

    @staticmethod
    def _bar_from_payload(payload: Any) -> dict[str, Any] | None:
        """从字典（单标的行情）构造 bar。"""
        if payload is None:
            return None
        if isinstance(payload, (int, float)):
            close = float(payload)
            return {"open": close, "high": close, "low": close, "close": close, "name": ""}
        if not isinstance(payload, Mapping):
            return None
        close = 0.0
        for alias in _COLUMN_ALIASES["close"]:
            if alias in payload:
                try:
                    close = float(payload[alias])
                except (TypeError, ValueError):
                    close = 0.0
                break
        if close <= 0:
            return None

        def _value(key: str) -> float:
            for alias in _COLUMN_ALIASES[key]:
                if alias in payload:
                    try:
                        return float(payload[alias])
                    except (TypeError, ValueError):
                        return 0.0
            return 0.0

        name = ""
        for alias in _COLUMN_ALIASES["name"]:
            if alias in payload:
                name = str(payload[alias])
                break
        return {
            "open": _value("open") or close,
            "high": _value("high") or close,
            "low": _value("low") or close,
            "close": close,
            "name": name,
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _equity(self) -> float:
        return self._cash + sum(pos.market_value for pos in self._positions.values())

    def _unfreeze(self, order: Order) -> None:
        """释放挂单占用的冻结资金。"""
        if order.frozen_amount:
            self._frozen_cash = max(0.0, self._frozen_cash - order.frozen_amount)
            order.frozen_amount = 0.0

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"PO{self._order_seq:06d}"


def _account_equity(account: Account) -> float:
    """从账户快照推导权益。

    WHY 不直接信任 ``account.equity``：调用方可能传入手工构造的
    ``Account``（只填了 cash 与持仓），此时权益必须由"现金 + 持仓市值"
    现算，否则仓位会被算成 0。
    """
    if account.equity > 0:
        return float(account.equity)
    position_value = sum(pos.market_value for pos in account.positions.values())
    return float(account.cash) + position_value


# ---------------------------------------------------------------------------
# 决策卡 → 委托
# ---------------------------------------------------------------------------
def order_from_decision_plan(
    plan: Mapping[str, Any] | None,
    ticker: str,
    account: Account | None = None,
    *,
    broker: PaperBroker | None = None,
    name: str = "",
    price: float | None = None,
    submitted_at: str | None = None,
) -> Order | None:
    """把结构化决策卡转成一张具体委托单。

    映射规则（与 ``services/decision_plan.py`` 的口径一致）：

      - ``HOLD`` / 空决策 → 不下单（返回 ``None``）；
      - ``BUY`` / ``OVERWEIGHT`` → 买入委托，数量 = ``position_pct * 权益``
        向下取整到整手，限价 = 决策卡参考入场价，并带上止损/目标；
      - ``SELL`` / ``UNDERWEIGHT`` → 卖出委托（**A 股不可做空**，只卖出现有
        持仓；T+1 锁定时挂单等待解禁）；
      - ``position_pct == 0`` → 不下单。决策卡在"最小信号数门槛"或
        "规则/模型方向冲突"时把仓位置 0，这两种情况必须尊重，不能自行
        补一个仓位出来（卖出方向除外：A 股不可做空，卖出决策本就对应
        清仓/减仓而非新仓位）；
      - ``conflict == True`` → 不下单（方向冲突守卫）；
      - 资金不足 / 突破组合权重上限 → 返回一张 ``rejected`` 的委托单，
        附带明确原因。

    参数
    ----
    plan:
        决策卡字典（``build_decision_plan`` 的输出）。
    ticker:
        标的代码。
    account:
        账户快照；传入 ``broker`` 时可省略（自动取 ``broker.account()``）。
    broker:
        若提供，则委托通过该模拟盘提交（自动套用整手、资金、权重上限校验）。
    """
    plan = plan or {}
    decision = str(plan.get("decision") or "HOLD").upper()
    if broker is not None and account is None:
        account = broker.account()
    account = account or Account()

    if decision in ("", "HOLD", "NEUTRAL", "WATCH"):
        logger.debug("决策为 %s，不产生委托", decision)
        return None
    if bool(plan.get("conflict")):
        logger.info("决策卡标记方向冲突，暂停下单：%s", ticker)
        return None

    entry = float(plan.get("entry") or 0.0) or float(price or 0.0)
    stop_loss = float(plan.get("stop_loss") or 0.0)
    take_profit = float(plan.get("take_profit") or 0.0)

    if decision in ("BUY", "OVERWEIGHT", "ADD", "INCREASE"):
        position_pct = float(plan.get("position_pct") or 0.0)
        if position_pct <= 0:
            # 仓位门槛 / 冲突守卫已把仓位置 0 —— 尊重决策卡，不自行补仓。
            logger.info("决策卡仓位为 0（门槛或冲突守卫），不下单：%s", ticker)
            return None
        if entry <= 0:
            logger.info("决策卡缺少参考价，无法下单：%s", ticker)
            return None
        equity = _account_equity(account)
        if equity <= 0:
            logger.info("账户权益为 0，无法下单：%s", ticker)
            return None
        budget = equity * position_pct
        lot = int(getattr(broker.rules if broker else None, "lot_size", LOT_SIZE) or LOT_SIZE)
        quantity = int(budget / entry / lot) * lot
        reason = (
            f"决策卡 {decision}：建议仓位 {position_pct:.1%}，"
            f"参考价 {entry:.2f}，止损 {stop_loss:.2f}，目标 {take_profit:.2f}"
        )
        if broker is not None:
            # 交给模拟盘走完整校验（整手 → 资金 → 仓位上限），拒单原因统一留痕
            return broker.submit_order(
                ticker,
                "buy",
                quantity if quantity > 0 else 1,
                order_type="limit",
                limit_price=entry,
                name=name or broker.default_name,
                reason=reason,
                stop_loss=stop_loss,
                take_profit=take_profit,
                price=entry,
                submitted_at=submitted_at,
            )
        if quantity < lot:
            order = Order(
                id="PLAN",
                ticker=ticker,
                side="buy",
                order_type="limit",
                quantity=quantity,
                requested_quantity=quantity,
                limit_price=entry,
                submitted_at=_date_text(submitted_at) if submitted_at else "",
                reason=reason,
                name=name,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            order.status = "rejected"
            order.reason = (
                f"资金不足一手：按 {position_pct:.1%} 仓位仅可买入 {budget:.2f} 元，"
                f"不足 {lot} 股"
            )
            return order
        order = Order(
            id="PLAN",
            ticker=ticker,
            side="buy",
            order_type="limit",
            quantity=quantity,
            requested_quantity=quantity,
            limit_price=entry,
            submitted_at=_date_text(submitted_at) if submitted_at else "",
            reason=reason,
            name=name,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        model = broker.cost_model if broker is not None else CostModel()
        estimated = quantity * entry * (1 + model.slippage_rate)
        needed = estimated + model.buy_cost(estimated)
        available = account.cash - account.frozen_cash
        if needed > available + 1e-6:
            order.status = "rejected"
            order.reason = f"资金不足：需要 {needed:.2f} 元，可用 {available:.2f} 元"
        return order

    if decision in ("SELL", "UNDERWEIGHT", "REDUCE", "EXIT"):
        position = account.positions.get(ticker)
        if position is None or position.quantity <= 0:
            logger.info("无可卖持仓，卖出决策不产生委托：%s", ticker)
            return None
        # 优先卖可卖部分；T+1 锁定时先挂整仓委托，等待次日解禁。
        quantity = position.sellable_qty or position.quantity
        reason = (
            f"决策卡 {decision}：清仓/减仓，止损 {stop_loss:.2f}，目标 {take_profit:.2f}"
        )
        if broker is not None:
            return broker.submit_order(
                ticker,
                "sell",
                quantity,
                order_type="market",
                name=name or position.name or broker.default_name,
                reason=reason,
                submitted_at=submitted_at,
            )
        order = Order(
            id="PLAN",
            ticker=ticker,
            side="sell",
            order_type="market",
            quantity=quantity,
            requested_quantity=quantity,
            submitted_at=_date_text(submitted_at) if submitted_at else "",
            reason=reason,
            name=name or position.name,
        )
        if quantity > position.sellable_qty:
            order.note = "T+1 锁定，待解禁后成交"
        return order

    logger.info("未知决策 %s，不产生委托", decision)
    return None


# ---------------------------------------------------------------------------
# 实盘取数封装（唯一允许联网的地方）
# ---------------------------------------------------------------------------
def load_price_frames(
    tickers: list[str], start_date: str, end_date: str
) -> dict[str, pd.DataFrame]:
    """按标的取日线行情（联网，仅供实盘/模拟盘脚本使用）。

    WHY 单独放在这里：核心撮合必须离线可测，只有"取数"这一步允许联网。
    ``_load_prices`` 是 ``services/backtest.py`` 的内部函数，这里只做只读
    复用，避免项目里出现第二套行情清洗口径。
    """
    # 延迟导入：核心逻辑不依赖网络层，也避免导入期副作用
    from tradingagents.services.backtest import _load_prices

    frames: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        frames[ticker] = _load_prices(ticker, start_date, end_date)
    return frames


def advance_broker(broker: PaperBroker, frames: Mapping[str, pd.DataFrame]) -> PaperBroker:
    """把多标的行情按交易日推进模拟盘。

    每个交易日把所有标的的当日行拼成一张带 ``Ticker`` 列的表，交给
    ``PaperBroker.process_day`` 处理；某标的当日无行即视为停牌。
    """
    dates: set[str] = set()
    for frame in frames.values():
        if frame is None or frame.empty:
            continue
        for value in frame["Date"]:
            dates.add(pd.Timestamp(value).strftime("%Y-%m-%d"))

    for date_text in sorted(dates):
        rows: list[dict[str, Any]] = []
        for ticker, frame in frames.items():
            if frame is None or frame.empty:
                continue
            day = frame[frame["Date"].dt.strftime("%Y-%m-%d") == date_text]
            if day.empty:
                continue
            row = day.iloc[-1].to_dict()
            row["Ticker"] = ticker
            rows.append(row)
        if not rows:
            continue
        broker.process_day(date_text, pd.DataFrame(rows))
    return broker


__all__ = [
    "Account",
    "Lot",
    "Order",
    "PaperBroker",
    "Position",
    "advance_broker",
    "load_price_frames",
    "order_from_decision_plan",
]
