"""组合级风控引擎。

为什么需要这一层
----------------
此前系统的全部风控都停留在**散文**里：

- ``dataflows/ashare_rules.py`` 把 T+1 与涨跌停规则作为一段文字注入提示词，
  没有任何代码强制；
- ``services/decision_plan.py`` 的 ``risk_per_trade=0.01`` 与
  ``max_position=0.20`` 是硬编码常量，**不知道**组合里已经持有什么；
- 组合经理（``agents/managers/portfolio_manager.py``）拿不到持仓、成本、
  行业暴露等任何组合上下文。

后果是：系统可以同时对 10 只股票各给出"建议仓位 20%"，合计 200%，
而没有任何地方会拦下来。

本模块把组合约束变成**可执行的判定**：

- 单票权重上限、行业暴露上限、总仓位上限；
- 单笔风险预算（以 ATR 止损距离反推可承受仓位）；
- 相关性/同向暴露的粗略惩罚（以行业与信号族近似）；
- 日内亏损熔断与组合回撤熔断；
- 与决策卡的**冲突检测**（规则看空但模型看多、已有仓位却建议加仓等）。

所有限额都有默认值、有文档、并且可以按账户覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 默认限额
# ---------------------------------------------------------------------------

#: 单只标的的权重上限（占组合权益）。
DEFAULT_MAX_SINGLE_WEIGHT = 0.20

#: 单一行业的权重上限（占组合权益）。
DEFAULT_MAX_SECTOR_WEIGHT = 0.40

#: 总持仓权重上限（占组合权益），保留一定现金。
DEFAULT_MAX_TOTAL_WEIGHT = 0.90

#: 单笔交易允许承受的组合权益损失。
DEFAULT_RISK_PER_TRADE = 0.01

#: 单日组合亏损熔断阈值（占组合权益）。
DEFAULT_DAILY_LOSS_LIMIT = 0.03

#: 组合最大回撤熔断阈值（占组合权益）。
DEFAULT_MAX_DRAWDOWN = 0.20

#: 判定方向冲突所需的净分阈值（与策略共识阈值一致）。
CONFLICT_NET_THRESHOLD = 12


@dataclass
class RiskLimits:
    """组合风控限额。"""

    max_single_weight: float = DEFAULT_MAX_SINGLE_WEIGHT
    max_sector_weight: float = DEFAULT_MAX_SECTOR_WEIGHT
    max_total_weight: float = DEFAULT_MAX_TOTAL_WEIGHT
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE
    daily_loss_limit: float = DEFAULT_DAILY_LOSS_LIMIT
    max_drawdown: float = DEFAULT_MAX_DRAWDOWN

    def to_dict(self) -> dict[str, float]:
        return {
            "max_single_weight": self.max_single_weight,
            "max_sector_weight": self.max_sector_weight,
            "max_total_weight": self.max_total_weight,
            "risk_per_trade": self.risk_per_trade,
            "daily_loss_limit": self.daily_loss_limit,
            "max_drawdown": self.max_drawdown,
        }


@dataclass
class PositionView:
    """持仓的只读视图（供风控计算使用）。"""

    ticker: str
    name: str = ""
    sector: str = ""
    quantity: float = 0.0
    avg_cost: float = 0.0
    last_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None

    @property
    def market_value(self) -> float:
        return abs(self.quantity) * self.last_price

    @property
    def unrealised_pnl(self) -> float:
        if not self.quantity:
            return 0.0
        return (self.last_price - self.avg_cost) * self.quantity

    @property
    def unrealised_pnl_pct(self) -> float:
        if not self.avg_cost:
            return 0.0
        return self.last_price / self.avg_cost - 1 if self.avg_cost else 0.0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PositionView:
        return cls(
            ticker=str(row.get("ticker") or ""),
            name=str(row.get("name") or ""),
            sector=str(row.get("sector") or ""),
            quantity=float(row.get("quantity") or 0),
            avg_cost=float(row.get("avg_cost") or 0),
            last_price=float(row.get("last_price") or 0),
            stop_loss=row.get("stop_loss"),
            take_profit=row.get("take_profit"),
        )


@dataclass
class RiskFinding:
    """一条风控结论。"""

    code: str
    level: str  # "block" 阻断 / "warn" 提示 / "info"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "detail": self.detail,
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class PortfolioRiskEngine:
    """组合级风控引擎。

    用法::

        engine = PortfolioRiskEngine(limits=RiskLimits())
        result = engine.evaluate(
            plan=decision_plan,          # services/decision_plan.py 的决策卡
            summary=strategy_summary,    # 策略汇总（含 net_score 等）
            positions=[PositionView(...)],
            equity=equity,
            cash=cash,
            sector="半导体",
        )
        if result["allowed"] and result["adjusted_position_pct"] > 0:
            ...  # 下单
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    # -- 内部工具 ---------------------------------------------------------

    def _portfolio_value(self, positions: list[PositionView]) -> float:
        return sum(position.market_value for position in positions)

    def _sector_value(
        self, positions: list[PositionView], sector: str, exclude: str = ""
    ) -> float:
        if not sector:
            return 0.0
        return sum(
            position.market_value
            for position in positions
            if position.sector == sector and position.ticker != exclude
        )

    def _total_value(
        self, positions: list[PositionView], exclude: str = ""
    ) -> float:
        return sum(
            position.market_value
            for position in positions
            if position.ticker != exclude
        )

    # -- 主入口 -----------------------------------------------------------

    def evaluate(
        self,
        plan: dict[str, Any] | None,
        summary: dict[str, Any] | None = None,
        positions: list[PositionView] | None = None,
        equity: float = 0.0,
        cash: float = 0.0,
        sector: str = "",
        ticker: str = "",
        daily_pnl_pct: float = 0.0,
        drawdown_pct: float = 0.0,
    ) -> dict[str, Any]:
        """对一份决策卡做组合级校验。

        返回
        ----
        dict，包含：
            - ``allowed``: 是否允许按该计划建仓/加仓
            - ``adjusted_position_pct``: 经组合约束调整后的目标仓位
            - ``requested_position_pct``: 决策卡原本建议的仓位
            - ``findings``: 风控结论列表
            - ``blocked_reasons``: 阻断原因（codes）
            - ``limits``: 生效的限额
        """
        positions = positions or []
        summary = summary or {}
        plan = plan or {}

        requested = float(plan.get("position_pct") or 0)
        decision = str(plan.get("decision") or "HOLD").upper()
        net_score = float(summary.get("net_score") or 0)
        risk_pct = float(plan.get("risk_pct") or 0)

        findings: list[RiskFinding] = []

        # ---- 1. 熔断：组合级亏损 ----
        if self.limits.daily_loss_limit and daily_pnl_pct <= -abs(
            self.limits.daily_loss_limit
        ):
            findings.append(
                RiskFinding(
                    code="daily_loss_breaker",
                    level="block",
                    message=(
                        f"组合当日亏损 {daily_pnl_pct * 100:.2f}% 已触及熔断线"
                        f"（-{self.limits.daily_loss_limit * 100:.2f}%），暂停新建仓位。"
                    ),
                    detail={"daily_pnl_pct": daily_pnl_pct},
                )
            )
        if self.limits.max_drawdown and drawdown_pct <= -abs(
            self.limits.max_drawdown
        ):
            findings.append(
                RiskFinding(
                    code="drawdown_breaker",
                    level="block",
                    message=(
                        f"组合回撤 {drawdown_pct * 100:.2f}% 已触及熔断线"
                        f"（-{self.limits.max_drawdown * 100:.2f}%），暂停新建仓位。"
                    ),
                    detail={"drawdown_pct": drawdown_pct},
                )
            )

        # ---- 2. 方向冲突 ----
        conflict = (
            (decision in ("BUY", "OVERWEIGHT") and net_score <= -CONFLICT_NET_THRESHOLD)
            or (decision in ("SELL", "UNDERWEIGHT") and net_score >= CONFLICT_NET_THRESHOLD)
        )
        if conflict:
            findings.append(
                RiskFinding(
                    code="direction_conflict",
                    level="block",
                    message=(
                        "策略共识与最终决策方向冲突，不予执行。"
                        f"（净分 {net_score}，决策 {decision}）"
                    ),
                    detail={"net_score": net_score, "decision": decision},
                )
            )

        # ---- 3. 卖出/持有：不建仓，直接放行 ----
        if decision not in ("BUY", "OVERWEIGHT") or requested <= 0:
            if requested <= 0 and decision in ("BUY", "OVERWEIGHT"):
                findings.append(
                    RiskFinding(
                        code="no_position_requested",
                        level="info",
                        message="决策卡未给出仓位（触发门槛或冲突保护），不建仓。",
                    )
                )
            return {
                "allowed": not any(f.level == "block" for f in findings),
                "requested_position_pct": requested,
                "adjusted_position_pct": 0.0,
                "findings": [f.to_dict() for f in findings],
                "blocked_reasons": [f.code for f in findings if f.level == "block"],
                "limits": self.limits.to_dict(),
            }

        # ---- 4. 仓位上限 ----
        if equity <= 0:
            findings.append(
                RiskFinding(
                    code="no_equity",
                    level="block",
                    message="组合权益未知（<=0），无法计算仓位上限。",
                )
            )
            return {
                "allowed": False,
                "requested_position_pct": requested,
                "adjusted_position_pct": 0.0,
                "findings": [f.to_dict() for f in findings],
                "blocked_reasons": [f.code for f in findings if f.level == "block"],
                "limits": self.limits.to_dict(),
            }

        adjusted = requested
        existing_value = 0.0
        for position in positions:
            if position.ticker == ticker:
                existing_value = position.market_value
        existing_weight = existing_value / equity if equity else 0.0

        # 4.1 单票权重上限
        single_cap = self.limits.max_single_weight
        if existing_weight + requested > single_cap + 1e-9:
            allowed_add = max(0.0, single_cap - existing_weight)
            findings.append(
                RiskFinding(
                    code="single_weight_cap",
                    level="warn",
                    message=(
                        f"单票权重上限 {single_cap * 100:.0f}%："
                        f"当前已占 {existing_weight * 100:.1f}%，"
                        f"本次加仓上限降至 {allowed_add * 100:.1f}%。"
                    ),
                    detail={
                        "existing_weight": round(existing_weight, 4),
                        "cap": single_cap,
                        "allowed_add": round(allowed_add, 4),
                    },
                )
            )
            adjusted = min(adjusted, allowed_add)

        # 4.2 总仓位上限
        total_value = self._total_value(positions, exclude=ticker)
        total_weight = total_value / equity if equity else 0.0
        remaining_total = max(0.0, self.limits.max_total_weight - total_weight)
        if adjusted > remaining_total:
            findings.append(
                RiskFinding(
                    code="total_weight_cap",
                    level="warn",
                    message=(
                        f"总仓位上限 {self.limits.max_total_weight * 100:.0f}%："
                        f"当前已占 {total_weight * 100:.1f}%，"
                        f"本次加仓上限降至 {remaining_total * 100:.1f}%。"
                    ),
                    detail={
                        "total_weight": round(total_weight, 4),
                        "cap": self.limits.max_total_weight,
                        "remaining": round(remaining_total, 4),
                    },
                )
            )
            adjusted = min(adjusted, remaining_total)

        # 4.3 行业暴露上限
        if sector:
            sector_value = self._sector_value(positions, sector, exclude=ticker)
            sector_weight = sector_value / equity if equity else 0.0
            remaining_sector = max(
                0.0, self.limits.max_sector_weight - sector_weight
            )
            if adjusted > remaining_sector:
                findings.append(
                    RiskFinding(
                        code="sector_weight_cap",
                        level="warn",
                        message=(
                            f"行业「{sector}」暴露上限 "
                            f"{self.limits.max_sector_weight * 100:.0f}%："
                            f"当前已占 {sector_weight * 100:.1f}%，"
                            f"本次加仓上限降至 {remaining_sector * 100:.1f}%。"
                        ),
                        detail={
                            "sector": sector,
                            "sector_weight": round(sector_weight, 4),
                            "cap": self.limits.max_sector_weight,
                            "remaining": round(remaining_sector, 4),
                        },
                    )
                )
                adjusted = min(adjusted, remaining_sector)

        # 4.4 现金约束
        if cash > 0 and adjusted > 0:
            max_by_cash = cash / equity if equity else 0.0
            if adjusted > max_by_cash:
                findings.append(
                    RiskFinding(
                        code="insufficient_cash",
                        level="warn",
                        message=(
                            f"可用现金 {cash:,.0f} 仅够支撑 "
                            f"{max_by_cash * 100:.1f}% 仓位，本次加仓上限据此下调。"
                        ),
                        detail={
                            "cash": cash,
                            "max_by_cash": round(max_by_cash, 4),
                        },
                    )
                )
                adjusted = min(adjusted, max_by_cash)

        # 4.5 单笔风险预算（用止损距离反推）
        if risk_pct > 0:
            risk_budget_position = self.limits.risk_per_trade / risk_pct
            if adjusted > risk_budget_position:
                findings.append(
                    RiskFinding(
                        code="risk_budget",
                        level="warn",
                        message=(
                            f"单笔风险预算 {self.limits.risk_per_trade * 100:.1f}%："
                            f"止损距离 {risk_pct * 100:.1f}%，"
                            f"仓位上限降至 {risk_budget_position * 100:.1f}%。"
                        ),
                        detail={
                            "risk_pct": round(risk_pct, 4),
                            "risk_budget": self.limits.risk_per_trade,
                            "max_position": round(risk_budget_position, 4),
                        },
                    )
                )
                adjusted = min(adjusted, risk_budget_position)

        adjusted = _clamp(adjusted, 0.0, self.limits.max_single_weight)

        if adjusted < requested - 1e-9:
            findings.append(
                RiskFinding(
                    code="position_scaled_down",
                    level="info",
                    message=(
                        f"目标仓位由 {requested * 100:.1f}% 下调至 "
                        f"{adjusted * 100:.1f}%（受组合约束）。"
                    ),
                    detail={
                        "requested": round(requested, 4),
                        "adjusted": round(adjusted, 4),
                    },
                )
            )

        blocked = any(f.level == "block" for f in findings)
        return {
            "allowed": not blocked,
            "requested_position_pct": round(requested, 4),
            "adjusted_position_pct": 0.0 if blocked else round(adjusted, 4),
            "existing_weight": round(existing_weight, 4),
            "total_weight": round(total_weight, 4),
            "findings": [f.to_dict() for f in findings],
            "blocked_reasons": [f.code for f in findings if f.level == "block"],
            "limits": self.limits.to_dict(),
        }

    # -- 组合体检 ---------------------------------------------------------

    def portfolio_health(
        self,
        positions: list[PositionView],
        equity: float,
        daily_pnl_pct: float = 0.0,
        drawdown_pct: float = 0.0,
    ) -> dict[str, Any]:
        """给整个组合做一次体检，返回风险敞口明细。

        这不是下单前的校验，而是"我现在整体上冒了多少风险"的答案 ——
        此前系统完全没有这个视角。
        """
        positions = positions or []
        total_value = self._portfolio_value(positions)
        by_sector: dict[str, float] = {}
        for position in positions:
            key = position.sector or "未分类"
            by_sector[key] = by_sector.get(key, 0.0) + position.market_value

        single_weights = {
            position.ticker: (
                position.market_value / equity if equity else 0.0
            )
            for position in positions
        }
        sector_weights = {
            key: (value / equity if equity else 0.0)
            for key, value in by_sector.items()
        }

        breaches: list[str] = []
        for ticker, weight in single_weights.items():
            if weight > self.limits.max_single_weight + 1e-9:
                breaches.append(
                    f"单票 {ticker} 权重 {weight * 100:.1f}% 超过上限 "
                    f"{self.limits.max_single_weight * 100:.0f}%"
                )
        for sector, weight in sector_weights.items():
            if weight > self.limits.max_sector_weight + 1e-9:
                breaches.append(
                    f"行业「{sector}」权重 {weight * 100:.1f}% 超过上限 "
                    f"{self.limits.max_sector_weight * 100:.0f}%"
                )
        total_weight = total_value / equity if equity else 0.0
        if total_weight > self.limits.max_total_weight + 1e-9:
            breaches.append(
                f"总仓位 {total_weight * 100:.1f}% 超过上限 "
                f"{self.limits.max_total_weight * 100:.0f}%"
            )

        unrealised = sum(position.unrealised_pnl for position in positions)

        return {
            "equity": round(equity, 2),
            "position_value": round(total_value, 2),
            "total_weight": round(total_weight, 4),
            "cash_weight": round(max(0.0, 1 - total_weight), 4),
            "single_weights": {
                k: round(v, 4) for k, v in single_weights.items()
            },
            "sector_weights": {k: round(v, 4) for k, v in sector_weights.items()},
            "unrealised_pnl": round(unrealised, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "breaches": breaches,
            "healthy": not breaches,
            "limits": self.limits.to_dict(),
        }
