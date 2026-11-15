"""运行级可观测性：LLM token / 成本 / 延迟计量。

本模块是"与供应商无关"（provider-agnostic）的度量层，负责回答三个问题：

1. **花了多少钱？** —— 每次 LLM 调用的 token 用量、估算成本、币种。
2. **花了多长时间？** —— 每次 LLM 调用的墙钟延迟，以及每个分析阶段的耗时。
3. **谁产出了哪部分结论？** —— 每次调用记录了模型、供应商与所属阶段。

设计要点
--------
* **零侵入**：LLM 客户端在 :mod:`tradingagents.llm_clients` 层通过
  ``observe_llm_call`` 包装真正的补全调用；没有活动 run 时所有记录
  都是 no-op，因此不接入也能正常运行。
* **零依赖**：只使用 Python 标准库，方便被 CLI / WebUI / 定时任务复用。
* **可覆盖**：价格表是模块级字典（``MODEL_PRICING`` / ``MODEL_PRICING_CNY``），
  也可以通过 ``estimate_cost(..., pricing=...)`` 单次覆盖。

典型用法::

    from tradingagents.services.observability import (
        RunMetrics, set_current_metrics, clear_current_metrics, stage_timer,
    )

    metrics = RunMetrics(ticker="600519", trade_date="2026-11-12",
                         source="webui", horizon="medium")
    set_current_metrics(metrics)
    try:
        with stage_timer("market_analysis"):
            final_state, decision = graph.propagate(ticker, trade_date, horizon)
    finally:
        metrics.finish()
        clear_current_metrics()
        save_run_log(metrics.to_dict())

线程 / 并发注意事项
------------------
* 活动 run 存在 :class:`contextvars.ContextVar` 里：asyncio 任务之间天然隔离
  （每个 task 有自己的 context 副本）。
* **WebUI 在线程里跑分析**（不是 asyncio）。``ContextVar`` 不会自动传播到新线程，
  因此集成方需要在**真正执行 LLM 调用的那个线程内**调用
  :func:`set_current_metrics`（通常就是 ``run_analysis`` 内部）。
* 查找顺序是 ContextVar → thread-local → 进程级兜底：
  1. **ContextVar** —— 主路径。LangGraph 执行图节点时用 ``copy_context()``
     把上下文复制到工作线程，所以同步图执行同样能看到当前 run。
  2. **thread-local** —— 同一线程内有效，线程之间互相隔离。
  3. **进程级兜底** —— 仅当全局**只登记了一个**活动 run 时才返回它，
     这样两个并发 run 不会互相"抢"对方的度量对象（宁可不记录，也不记错）。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "AUTO_CURRENCY",
    "BudgetExceededError",
    "budget_policy",
    "budget_status",
    "enforce_budget",
    "CostEstimate",
    "RunMetrics",
    "DEFAULT_PRICING_CNY",
    "DEFAULT_PRICING_USD",
    "DEFAULT_CURRENCY",
    "MODEL_PRICING",
    "MODEL_PRICING_CNY",
    "aggregate",
    "clear_current_metrics",
    "estimate_cost",
    "get_current_metrics",
    "get_current_stage",
    "lookup_pricing",
    "record_llm_call",
    "set_current_metrics",
    "set_default_currency",
    "stage_timer",
]


# =============================================================================
# 价格表（每 100 万 token 的价格）
# =============================================================================
#
# 说明与免责：
#   * 表中价格是"参考价"，用于在没有账单接口时给出量级正确的成本估算。
#   * 价格随厂商调整而变化，请按最新价目表修正本表（直接修改字典即可，
#     见模块底部"如何修正价格"的说明）。
#   * 键为小写模型名；查询时会做归一化（去厂商前缀、转小写），
#     并支持前缀匹配（处理带日期后缀的模型名）。
#
# 字典结构：{"prompt": 每百万输入 token 价格, "completion": 每百万输出 token 价格}

#: 美元价格表（USD / 1M tokens）
MODEL_PRICING: dict[str, dict[str, float]] = {
    # ---- OpenAI ----
    "gpt-5.4-pro": {"prompt": 8.00, "completion": 32.00},
    "gpt-5.4": {"prompt": 2.00, "completion": 8.00},
    "gpt-5.2": {"prompt": 1.25, "completion": 10.00},
    "gpt-5.1": {"prompt": 1.25, "completion": 10.00},
    "gpt-5": {"prompt": 1.25, "completion": 10.00},
    "gpt-5-mini": {"prompt": 0.25, "completion": 2.00},
    "gpt-5-nano": {"prompt": 0.05, "completion": 0.40},
    "gpt-4.1": {"prompt": 2.00, "completion": 8.00},
    "gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60},
    "gpt-4.1-nano": {"prompt": 0.10, "completion": 0.40},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    # ---- Anthropic ----
    "claude-opus-4-6": {"prompt": 5.00, "completion": 25.00},
    "claude-opus-4-5": {"prompt": 5.00, "completion": 25.00},
    "claude-sonnet-4-6": {"prompt": 3.00, "completion": 15.00},
    "claude-sonnet-4-5": {"prompt": 3.00, "completion": 15.00},
    "claude-haiku-4-5": {"prompt": 1.00, "completion": 5.00},
    # ---- Google Gemini ----
    "gemini-3.1-pro-preview": {"prompt": 2.00, "completion": 12.00},
    "gemini-3.1-flash-lite-preview": {"prompt": 0.10, "completion": 0.40},
    "gemini-3-flash-preview": {"prompt": 0.30, "completion": 2.50},
    "gemini-2.5-pro": {"prompt": 1.25, "completion": 10.00},
    "gemini-2.5-flash": {"prompt": 0.30, "completion": 2.50},
    "gemini-2.5-flash-lite": {"prompt": 0.10, "completion": 0.40},
    # ---- xAI Grok ----
    "grok-4-0709": {"prompt": 3.00, "completion": 15.00},
    "grok-4-fast-reasoning": {"prompt": 0.20, "completion": 0.50},
    "grok-4-fast-non-reasoning": {"prompt": 0.20, "completion": 0.50},
    "grok-4-1-fast-reasoning": {"prompt": 0.20, "completion": 0.50},
    "grok-4-1-fast-non-reasoning": {"prompt": 0.20, "completion": 0.50},
}

#: 人民币价格表（CNY / 1M tokens）—— MiniMax 等国产模型的计价单位是人民币。
#: 注意：这些同样是参考价，请按官方价目表修正。
MODEL_PRICING_CNY: dict[str, dict[str, float]] = {
    "minimax-m3": {"prompt": 3.60, "completion": 10.80},
    "minimax-m3.1-flash": {"prompt": 0.72, "completion": 2.88},
    "minimax-m2": {"prompt": 3.60, "completion": 10.80},
    "minimax-m1": {"prompt": 4.00, "completion": 12.00},
}

#: 未知模型的兜底价格（"中档"参考价，USD / 1M tokens）
DEFAULT_PRICING_USD: dict[str, float] = {"prompt": 0.50, "completion": 1.50}

#: 未知模型的兜底价格（CNY / 1M tokens），按 1 USD ≈ 7.2 CNY 折算
DEFAULT_PRICING_CNY: dict[str, float] = {"prompt": 3.60, "completion": 10.80}


#: 表示"自动"：先查美元价格表，再查人民币价格表，最后用兜底价
AUTO_CURRENCY = "AUTO"


def _default_currency() -> str:
    """默认币种，可用环境变量 ``TA_COST_CURRENCY`` 覆盖。

    ``TA_COST_CURRENCY=CNY`` 强制所有成本按人民币计价；不设置时为 ``"AUTO"``，
    即按模型所在价格表自动选择（美元表优先，其次人民币表）。
    """
    value = (os.getenv("TA_COST_CURRENCY") or AUTO_CURRENCY).strip().upper()
    return value or AUTO_CURRENCY


#: 默认币种（模块加载时读取环境变量，之后可用 :func:`set_default_currency` 修改）
DEFAULT_CURRENCY: str = _default_currency()


def set_default_currency(currency: str) -> str:
    """设置默认币种（``"USD"`` / ``"CNY"`` / ``"AUTO"``），返回规范化后的币种。

    只影响之后新建的 :class:`RunMetrics`；已有 run 的币种在创建时已固定。
    """
    global DEFAULT_CURRENCY
    value = (currency or AUTO_CURRENCY).strip().upper() or AUTO_CURRENCY
    DEFAULT_CURRENCY = value
    return value


# =============================================================================
# 成本估算
# =============================================================================
class CostEstimate(float):
    """成本估算结果。

    它是 ``float`` 的子类，因此可以直接参与数值运算（``est > 0``），
    同时携带额外元信息：

    * ``currency`` —— 币种（``"USD"`` / ``"CNY"``）
    * ``is_estimated`` —— True 表示模型不在价格表中，金额是兜底估算
    * ``model`` —— 实际使用的模型名
    """

    __slots__ = ("currency", "is_estimated", "model")

    def __new__(
        cls,
        value: float,
        *,
        currency: str = "USD",
        is_estimated: bool = False,
        model: str = "",
    ) -> CostEstimate:
        obj = super().__new__(cls, value)
        obj.currency = currency
        obj.is_estimated = is_estimated
        obj.model = model
        return obj

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试展示
        return (
            f"CostEstimate({float(self):.6f}, currency={self.currency!r}, "
            f"is_estimated={self.is_estimated!r}, model={self.model!r})"
        )


def _normalize_model(model: str | None) -> str:
    """归一化模型名：转小写、去掉厂商前缀（``anthropic/claude-x`` → ``claude-x``）。"""
    name = (model or "").strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def _lookup(table: dict[str, dict[str, float]], model: str | None) -> tuple[str, dict[str, float]] | None:
    """在价格表中查找模型，返回 ``(键, 价格)``；找不到返回 ``None``。

    匹配顺序：
    1. 精确匹配（归一化后的小写名）
    2. 前缀匹配（取表中"最长"的匹配前缀，处理 ``claude-sonnet-4-5-20250929``）
    3. 子串匹配（兜底，处理厂商加后缀的命名）
    """
    name = _normalize_model(model)
    if not name:
        return None
    if name in table:
        return name, table[name]

    best: tuple[str, dict[str, float]] | None = None
    for key, price in table.items():
        if name.startswith(key):
            if best is None or len(key) > len(best[0]):
                best = (key, price)
    if best is not None:
        return best

    for key, price in table.items():
        if key in name:
            return key, price
    return None


def lookup_pricing(
    model: str | None,
    *,
    currency: str | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
    cny_pricing: dict[str, dict[str, float]] | None = None,
) -> tuple[str, dict[str, float], str, bool] | None:
    """查找模型价格，返回 ``(价格键, 价格, 币种, 是否为估算值)``。

    返回 ``None`` 表示该模型在对应币种的价格表里没有条目。

    Args:
        model: 模型名（大小写不敏感，可带厂商前缀）
        currency: 目标币种；``None`` / ``"AUTO"`` 表示自动（美元表优先，
            其次人民币表）
        pricing: 覆盖默认的美元价格表
        cny_pricing: 覆盖默认的人民币价格表
    """
    usd_table = MODEL_PRICING if pricing is None else pricing
    cny_table = MODEL_PRICING_CNY if cny_pricing is None else cny_pricing

    wanted = (currency or "").strip().upper()
    if wanted and wanted != AUTO_CURRENCY:
        if wanted == "CNY":
            found = _lookup(cny_table, model)
            if found is not None:
                return found[0], found[1], "CNY", False
            return None
        found = _lookup(usd_table, model)
        if found is not None:
            return found[0], found[1], wanted, False
        return None

    # AUTO：美元表优先，其次人民币表（MiniMax 等国产模型在人民币表里）
    found = _lookup(usd_table, model)
    if found is not None:
        return found[0], found[1], "USD", False
    found = _lookup(cny_table, model)
    if found is not None:
        return found[0], found[1], "CNY", False
    return None


def estimate_cost(
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    currency: str | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
    cny_pricing: dict[str, dict[str, float]] | None = None,
) -> CostEstimate:
    """估算一次（或一批）token 消耗的成本。

    Args:
        model: 模型名；不在价格表中时使用"中档"兜底价并标记 ``is_estimated=True``
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        currency: 目标币种（``"USD"`` / ``"CNY"``）；``None`` 表示跟随价格表
        pricing: 覆盖默认美元价格表（单次调用覆盖）
        cny_pricing: 覆盖默认人民币价格表（单次调用覆盖）

    Returns:
        :class:`CostEstimate`（``float`` 子类），带 ``currency`` / ``is_estimated``。

    Examples:
        >>> est = estimate_cost("gpt-4o-mini", 1_000, 1_000)
        >>> est > 0
        True
        >>> estimate_cost("who-knows-model", 1_000, 1_000).is_estimated
        True
    """
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))

    wanted = (currency or "").strip().upper()
    found = lookup_pricing(
        model,
        currency=wanted or None,
        pricing=pricing,
        cny_pricing=cny_pricing,
    )

    if found is None:
        if wanted == "CNY":
            price = DEFAULT_PRICING_CNY if cny_pricing is None else _default_for(cny_pricing)
            cur = "CNY"
        else:
            # AUTO 或 USD：未知模型按"中档美元价"兜底
            price = DEFAULT_PRICING_USD if pricing is None else _default_for(pricing)
            cur = "USD"
        estimated = True
        key = ""
    else:
        key, price, cur, estimated = found

    cost = (
        prompt / 1_000_000 * float(price.get("prompt", 0.0))
        + completion / 1_000_000 * float(price.get("completion", 0.0))
    )
    return CostEstimate(
        cost,
        currency=cur,
        is_estimated=estimated,
        model=_normalize_model(model) or (key or ""),
    )


def _default_for(table: dict[str, dict[str, float]]) -> dict[str, float]:
    """为自定义价格表生成兜底价（用表内中位数，避免用户被硬编码价格误导）。"""
    if not table:
        return dict(DEFAULT_PRICING_USD)
    prompts = sorted(float(v.get("prompt", 0.0)) for v in table.values())
    completions = sorted(float(v.get("completion", 0.0)) for v in table.values())
    mid = len(prompts) // 2
    return {"prompt": prompts[mid], "completion": completions[mid]}


# =============================================================================
# RunMetrics
# =============================================================================
@dataclass
class RunMetrics:
    """一次分析运行的度量累加器。

    典型生命周期::

        metrics = RunMetrics(ticker="600519", trade_date="2026-11-12")
        set_current_metrics(metrics)
        ...
        metrics.finish()
        save_run_log(metrics.to_dict())
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ticker: str = ""
    trade_date: str = ""
    source: str = ""
    horizon: str = ""
    currency: str = field(default_factory=lambda: DEFAULT_CURRENCY)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    duration_ms: float = 0.0
    #: 本次运行的主要供应商 / 模型（取自调用次数最多的那一个）
    provider: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()
        self._resolved_currency: str | None = None
        if not self.totals:
            self.totals = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost": 0.0,
                "call_count": 0,
                "cost_is_estimated": False,
            }

    # ------------------------------------------------------------------
    # 累加
    # ------------------------------------------------------------------
    def add_call(
        self,
        *,
        stage: str | None = None,
        model: str = "",
        provider: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        ok: bool = True,
        error: str = "",
        tokens_estimated: bool = False,
        cost: float | None = None,
        cost_is_estimated: bool | None = None,
    ) -> dict[str, Any]:
        """累加一次 LLM 调用记录并返回该记录（可直接 JSON 序列化）。

        Args:
            stage: 所属阶段名；``None`` 时取当前阶段（见 :func:`stage_timer`）
            model: 模型名
            provider: 供应商名（``"openai"`` / ``"anthropic"`` / ``"google"`` ...）
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            latency_ms: 调用墙钟耗时
            ok: 是否成功；失败时 ``error`` 记录错误信息
            error: 错误信息（失败时）
            tokens_estimated: token 数是否为估算值
            cost: 覆盖自动计算的成本
            cost_is_estimated: 覆盖自动计算的"成本为估算"标记
        """
        stage_name = stage or get_current_stage() or "default"
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))

        if cost is None:
            estimate = estimate_cost(
                model, prompt, completion, currency=self.currency
            )
            amount = float(estimate)
            estimated = bool(estimate.is_estimated)
            cost_currency = estimate.currency
        else:
            amount = float(cost)
            estimated = bool(cost_is_estimated)
            cost_currency = self.currency
        record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "stage": stage_name,
            "model": model or "",
            "provider": provider or "",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "latency_ms": round(float(latency_ms or 0.0), 3),
            "ok": bool(ok),
            "error": (error or "")[:500],
            "tokens_estimated": bool(tokens_estimated),
            "cost": round(amount, 8),
            "currency": cost_currency,
            "cost_is_estimated": estimated,
        }

        with self._lock:
            self.llm_calls.append(record)
            if self._resolved_currency is None and cost_currency:
                self._resolved_currency = cost_currency
            # 记录主要供应商 / 模型（取调用次数最多的组合）
            if model:
                counts: dict[tuple[str, str], int] = {}
                for item in self.llm_calls:
                    key = (item.get("provider") or "", item.get("model") or "")
                    counts[key] = counts.get(key, 0) + 1
                best = max(counts.items(), key=lambda pair: pair[1])[0]
                self.provider, self.model = best
            totals = self.totals
            totals["prompt_tokens"] = totals.get("prompt_tokens", 0) + prompt
            totals["completion_tokens"] = totals.get("completion_tokens", 0) + completion
            totals["total_tokens"] = totals.get("total_tokens", 0) + prompt + completion
            totals["estimated_cost"] = round(
                float(totals.get("estimated_cost", 0.0)) + amount, 8
            )
            totals["call_count"] = totals.get("call_count", 0) + 1
            totals["cost_is_estimated"] = bool(
                totals.get("cost_is_estimated", False) or estimated
            )

            stage_entry = self.stages.setdefault(
                stage_name,
                {"duration_ms": 0.0, "calls": 0, "tokens": 0, "cost": 0.0},
            )
            stage_entry["calls"] = stage_entry.get("calls", 0) + 1
            stage_entry["tokens"] = stage_entry.get("tokens", 0) + prompt + completion
            stage_entry["cost"] = round(
                float(stage_entry.get("cost", 0.0)) + amount, 8
            )
        return record

    def record_stage(self, stage: str, duration_ms: float) -> dict[str, Any]:
        """把一个阶段的墙钟耗时累加进 ``stages``。"""
        with self._lock:
            entry = self.stages.setdefault(
                stage,
                {"duration_ms": 0.0, "calls": 0, "tokens": 0, "cost": 0.0},
            )
            entry["duration_ms"] = round(
                float(entry.get("duration_ms", 0.0)) + float(duration_ms or 0.0), 3
            )
            return dict(entry)

    def finish(self) -> RunMetrics:
        """结束计时：写入 ``finished_at`` 与 ``duration_ms``。"""
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.duration_ms = round((time.perf_counter() - self._t0) * 1000, 3)
        return self

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """返回可 JSON 序列化的快照。"""
        totals = dict(self.totals)
        totals.setdefault("prompt_tokens", 0)
        totals.setdefault("completion_tokens", 0)
        totals.setdefault("total_tokens", 0)
        totals.setdefault("estimated_cost", 0.0)
        totals.setdefault("call_count", 0)
        totals.setdefault("cost_is_estimated", False)
        # 币种可能是 AUTO（自动选择），落库时用实际结算币种
        currency = self.currency
        if not currency or currency == AUTO_CURRENCY:
            currency = self._resolved_currency or "USD"
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "trade_date": self.trade_date,
            "source": self.source,
            "horizon": self.horizon,
            "provider": self.provider,
            "model": self.model,
            "currency": currency,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "totals": totals,
            "stages": {k: dict(v) for k, v in self.stages.items()},
            "llm_calls": [dict(c) for c in self.llm_calls],
        }


# =============================================================================
# 活动 run 注册表
# =============================================================================
# 三层查找（自上而下）：
#   1. ContextVar —— asyncio 任务之间天然隔离；LangGraph 执行节点时用
#      ``copy_context()`` 把上下文复制到工作线程，因此同步图执行同样可见。
#   2. thread-local —— 线程之间隔离；WebUI 在线程里跑分析时，
#      在**执行 LLM 调用的那个线程内**调用 set_current_metrics() 即可。
#   3. 进程级兜底 —— 仅当全局只登记了一个活动 run 时才使用，
#      避免两个并发 run 互相"抢"对方的度量对象。
_current_metrics: ContextVar[RunMetrics | None] = ContextVar(
    "alphaagents_current_metrics", default=None
)
_current_stage: ContextVar[str | None] = ContextVar(
    "alphaagents_current_stage", default=None
)
_thread_state = threading.local()
#: 线程 id → RunMetrics（用于进程级兜底时判断"是否只有一个活动 run"）
#: 用弱引用，线程退出而未调用 clear_current_metrics() 时不会泄漏内存。
_thread_metrics: weakref.WeakValueDictionary[int, RunMetrics] = (
    weakref.WeakValueDictionary()
)
_registry_lock = threading.Lock()


def get_current_metrics() -> RunMetrics | None:
    """返回当前活动 run 的度量对象；没有则返回 ``None``。

    查找顺序：ContextVar → thread-local → 进程级兜底（仅当全局只有一个
    活动 run 时）。
    """
    metrics = _current_metrics.get()
    if metrics is not None:
        return metrics
    metrics = getattr(_thread_state, "metrics", None)
    if metrics is not None:
        return metrics
    with _registry_lock:
        if len(_thread_metrics) == 1:
            return next(iter(_thread_metrics.values()))
    return None


class BudgetExceededError(RuntimeError):
    """单次分析超出成本 / token 预算。"""

    def __init__(self, status: dict[str, Any]):
        self.status = status
        reasons = "；".join(status.get("reasons") or ["超出预算"])
        super().__init__(f"LLM 预算超限：{reasons}")


def budget_policy() -> dict[str, float | int | None]:
    """读取单次运行预算（环境变量）。

    - ``LLM_MAX_COST_PER_RUN``：单次分析成本上限（与 cost 同币种）
    - ``LLM_MAX_TOKENS_PER_RUN``：单次分析 token 上限
    两者都不设置时表示不限制。
    """

    def _float(name: str) -> float | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    def _int(name: str) -> int | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            value = int(float(raw))
        except ValueError:
            return None
        return value if value > 0 else None

    return {
        "max_cost_per_run": _float("LLM_MAX_COST_PER_RUN"),
        "max_tokens_per_run": _int("LLM_MAX_TOKENS_PER_RUN"),
    }


def budget_status(metrics: RunMetrics | None = None) -> dict[str, Any]:
    """返回当前 run 的预算使用情况与是否超限。"""
    metrics = metrics or get_current_metrics()
    policy = budget_policy()
    if metrics is None:
        return {
            **policy,
            "cost": 0.0,
            "tokens": 0,
            "currency": DEFAULT_CURRENCY,
            "exceeded": False,
            "reasons": [],
        }
    totals = metrics.totals or {}
    cost = float(totals.get("estimated_cost", 0.0))
    tokens = int(totals.get("total_tokens", 0))
    reasons: list[str] = []
    max_cost = policy["max_cost_per_run"]
    max_tokens = policy["max_tokens_per_run"]
    if max_cost is not None and cost > float(max_cost):
        reasons.append(f"成本 {cost:.4f} 超过上限 {float(max_cost):.4f}")
    if max_tokens is not None and tokens > int(max_tokens):
        reasons.append(f"token {tokens} 超过上限 {int(max_tokens)}")
    return {
        **policy,
        "cost": round(cost, 8),
        "tokens": tokens,
        "currency": metrics.currency,
        "exceeded": bool(reasons),
        "reasons": reasons,
    }


def enforce_budget(metrics: RunMetrics | None = None) -> None:
    """超限时抛出 :class:`BudgetExceededError`；未配置预算时 no-op。"""
    status = budget_status(metrics)
    if status.get("exceeded"):
        raise BudgetExceededError(status)


def set_current_metrics(metrics: RunMetrics | None) -> RunMetrics | None:
    """设置当前活动 run（返回之前活动的对象，便于手动还原）。

    * asyncio 场景：只影响当前 task（ContextVar 语义）。
    * 线程场景：同时写入 thread-local，保证同一线程内的 LLM 调用能取到。
    * 进程级兜底登记：用于"主线程设置、子线程执行"这种无法传参的场景；
      当同时存在多个活动 run 时，兜底会自动失效（宁可不记录，也不记错）。
    """
    previous = get_current_metrics()
    _current_metrics.set(metrics)
    _thread_state.metrics = metrics
    with _registry_lock:
        thread_id = threading.get_ident()
        if metrics is None:
            _thread_metrics.pop(thread_id, None)
        else:
            _thread_metrics[thread_id] = metrics
    return previous


def clear_current_metrics() -> None:
    """清除当前活动 run（调用方通常在 ``finally`` 中执行）。"""
    _current_metrics.set(None)
    _thread_state.metrics = None
    with _registry_lock:
        _thread_metrics.pop(threading.get_ident(), None)


def get_current_stage() -> str | None:
    """返回当前阶段名（由 :func:`stage_timer` 设置）。"""
    return _current_stage.get()


def record_llm_call(
    *,
    stage: str | None = None,
    model: str = "",
    provider: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    ok: bool = True,
    error: str = "",
    tokens_estimated: bool = False,
    metrics: RunMetrics | None = None,
) -> dict[str, Any] | None:
    """记录一次 LLM 调用；没有活动 run 时返回 ``None``（no-op）。

    该函数永远不会抛出异常 —— 可观测性不应影响业务主流程。
    """
    try:
        target = metrics or get_current_metrics()
        if target is None:
            return None
        return target.add_call(
            stage=stage,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            ok=ok,
            error=error,
            tokens_estimated=tokens_estimated,
        )
    except Exception:  # pragma: no cover - 度量失败绝不影响主流程
        return None


@contextmanager
def stage_timer(stage: str, metrics: RunMetrics | None = None) -> Iterator[RunMetrics | None]:
    """测量一个阶段的墙钟耗时，并把耗时归集到 ``RunMetrics.stages``。

    在 ``with`` 块内发生的 LLM 调用会自动归到该阶段名下。

    Args:
        stage: 阶段名（例如 ``"market_analysis"``）
        metrics: 指定度量对象；``None`` 表示使用当前活动 run
    """
    target = metrics or get_current_metrics()
    token = _current_stage.set(stage)
    started = time.perf_counter()
    try:
        yield target
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        _current_stage.reset(token)
        if target is not None:
            try:
                target.record_stage(stage, duration_ms)
            except Exception:  # pragma: no cover
                pass


# =============================================================================
# 聚合辅助（供上层/报表使用）
# =============================================================================
def aggregate(metrics: RunMetrics | dict[str, Any]) -> dict[str, Any]:
    """把一次 run 的度量聚合成"按模型 / 按供应商 / 按阶段"的报表。

    Args:
        metrics: :class:`RunMetrics` 或其 ``to_dict()`` 快照
    """
    calls = metrics.llm_calls if isinstance(metrics, RunMetrics) else metrics.get("llm_calls", [])
    by_model: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    for call in calls or []:
        for bucket, key in ((by_model, call.get("model") or "unknown"),
                            (by_provider, call.get("provider") or "unknown")):
            entry = bucket.setdefault(
                key,
                {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
            )
            entry["calls"] += 1
            entry["prompt_tokens"] += int(call.get("prompt_tokens") or 0)
            entry["completion_tokens"] += int(call.get("completion_tokens") or 0)
            entry["cost"] = round(entry["cost"] + float(call.get("cost") or 0.0), 8)
    return {"by_model": by_model, "by_provider": by_provider}
