"""决策 → 结果 的对齐评估服务。

WHY：项目此前只能"生成研究"，不能"回答研究对不对"。``storage/report_store.py``
里已经存下了每次分析的 (ticker, trade_date, decision)，但没有任何东西把它们
和之后的真实价格对齐——于是"命中率"这种最关键的指标无从谈起。

本模块提供这条闭环：

    历史报告 (reports 表)
        → evaluate_decisions()  按前瞻周期取价，算出前瞻收益
        → evaluate_decision()   判断该决策在每个周期上是否"正确"
        → build_evaluation_table()  汇总命中率 / 平均收益 / 分标的拆解

【正确性判定口径】
    - 看多（BUY / OVERWEIGHT / 买入 / 增持）：前瞻收益 > 0 记为正确
    - 看空（SELL / UNDERWEIGHT / 卖出 / 减持）：前瞻收益 < 0 记为正确
    - 持有（HOLD / 持有 / 观望）：|前瞻收益| <= hold_band 记为正确
      （band 默认 3%：在噪声区间内"按兵不动"也算对）

【有效样本量（必须看）】
    前瞻周期之间是**重叠**的：同一天的决策会同时进入 5/10/20 日三个窗口，
    三个窗口高度相关。因此"45 条统计量"不等于"45 个独立样本"。
    本模块同时给出：
      - raw_count：原始条数
      - effective_sample_size：按区间调度算出的**互不重叠**条数
    并在有效样本量低于 ``backtest_engine.MIN_EFFECTIVE_SAMPLE``（8）时打上
    ``sample_insufficient`` 标记 —— 样本太小的命中率不允许被当作结论。

只依赖 pandas + 标准库，不引入第三方依赖；行情读取复用
``services/backtest.py::_load_prices``（只读引用，不修改其行为）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from tradingagents.services.backtest import _load_prices
from tradingagents.services.backtest_engine import MIN_EFFECTIVE_SAMPLE
from tradingagents.storage.memory_store import save_memory

logger = logging.getLogger(__name__)

#: 持有决策的"正确"带宽：|前瞻收益| <= 该值即视为"按兵不动是对的"。
HOLD_BAND: float = 0.03

#: 默认前瞻周期（交易日）。
DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20)

#: 决策 → 方向。方向决定"涨算对"还是"跌算对"。
#: BUY/OVERWEIGHT → long；SELL/UNDERWEIGHT → short；HOLD → flat。
_LONG_WORDS = ("buy", "overweight", "买入", "增持", "建仓", "超配", "高配", "看多")
_SHORT_WORDS = ("sell", "underweight", "卖出", "减持", "清仓", "减配", "低配", "看空")
_HOLD_WORDS = ("hold", "持有", "观望", "维持", "中性", "neutral")


# ---------------------------------------------------------------------------
# 决策归一化
# ---------------------------------------------------------------------------
def normalize_decision(raw: object) -> str:
    """
    把决策文本归一化到 ``BUY`` / ``OVERWEIGHT`` / ``HOLD`` / ``UNDERWEIGHT`` /
    ``SELL`` 之一；无法识别时返回 ``"UNKNOWN"``。

    WHY 不直接用 ``graph/signal_processing.py``：评估服务要能独立运行
    （例如离线跑历史报告），不应该把整个 LangGraph 依赖拖进来。
    """
    if raw is None:
        return "UNKNOWN"
    text = str(raw).strip()
    if not text:
        return "UNKNOWN"
    upper = text.upper()
    for word in ("OVERWEIGHT", "UNDERWEIGHT", "BUY", "SELL", "HOLD"):
        if word in upper:
            return word
    lowered = text.lower()
    for word in _LONG_WORDS:
        if word in lowered:
            return "OVERWEIGHT" if word in ("超配", "高配", "overweight") else "BUY"
    for word in _SHORT_WORDS:
        if word in lowered:
            return "UNDERWEIGHT" if word in ("低配", "减配", "underweight") else "SELL"
    for word in _HOLD_WORDS:
        if word in lowered:
            return "HOLD"
    return "UNKNOWN"


def decision_direction(decision: str) -> str:
    """把归一化决策映射到方向：``long`` / ``short`` / ``flat`` / ``unknown``。"""
    if decision in ("BUY", "OVERWEIGHT"):
        return "long"
    if decision in ("SELL", "UNDERWEIGHT"):
        return "short"
    if decision == "HOLD":
        return "flat"
    return "unknown"


# ---------------------------------------------------------------------------
# 日期与行情辅助
# ---------------------------------------------------------------------------
def _parse_date(value: Any) -> date | None:
    """把各种日期表示（字符串 / date / Timestamp）解析成 ``date``。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except Exception:
        return None


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """把行情整理成按日期升序、含 Date/Close 两列的 DataFrame。"""
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=["Date", "Close"])
    data = frame.copy()
    if "Date" not in data.columns:
        if data.index.name in ("Date", "日期"):
            data = data.reset_index()
            if "日期" in data.columns:
                data = data.rename(columns={"日期": "Date"})
    if "Date" not in data.columns or "Close" not in data.columns:
        raise ValueError("行情数据缺少 Date/Close 列")
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data["Close"] = pd.to_numeric(data["Close"], errors="coerce")
    data = data.dropna(subset=["Date", "Close"])
    data = data[data["Close"] > 0]
    data = data.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
    return data.reset_index(drop=True)


def _last_true_index(mask) -> int:
    """返回布尔序列中最后一个 True 的下标（没有则返回 -1）。"""
    indices = [index for index, flag in enumerate(mask.tolist()) if flag]
    return indices[-1] if indices else -1


def _entry_index(dates: pd.Series, entry_date: date) -> int | None:
    """
    找到决策日对应的 bar 下标。

    规则：取"不晚于决策日"的最后一根 bar。决策日若是非交易日（周末/节假日），
    则退回到上一个交易日——决策实际上是基于那个交易日的信息做出的。
    """
    stamps = dates.dt.date
    index = _last_true_index(stamps <= entry_date)
    return index if index >= 0 else None


# ---------------------------------------------------------------------------
# 单条决策评估
# ---------------------------------------------------------------------------
def evaluate_decision(
    ticker: str,
    decision: str,
    entry_date: str,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    *,
    price_loader: Callable[[str, str, str], pd.DataFrame] | None = None,
    hold_band: float = HOLD_BAND,
    frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    评估单条决策在各个前瞻周期上的表现。

    参数：
        ticker: 标的代码
        decision: 决策（中文或英文均可，内部会归一化）
        entry_date: 决策日期（``YYYY-MM-DD``）
        horizons: 前瞻周期（交易日），默认 (5, 10, 20)
        price_loader: 行情读取函数，签名 ``(ticker, start_date, end_date) -> DataFrame``。
            默认使用 ``services.backtest._load_prices``；测试可注入合成行情。
        hold_band: 持有决策的正确带宽，默认 3%
        frame: 直接给定行情（优先于 price_loader，便于测试）

    返回：
        dict: 评估行::

            {
              "ticker": "600519",
              "decision": "买入",              # 原始决策文本
              "normalized_decision": "BUY",    # 归一化决策
              "direction": "long",             # long / short / flat / unknown
              "entry_date": "2024-01-02",
              "entry_price": 1680.0,           # 决策日收盘价（参考价）
              "entry_index": 0,
              "horizons": {
                "5": {"horizon": 5, "available": True, "exit_date": "2024-01-09",
                      "exit_price": 1710.0, "forward_return": 0.017857, "correct": True},
                ...
              },
              "correct": True,                 # 多数可用周期是否认可
              "correct_horizons": 2,
              "available_horizons": 3,
              "notes": [],
            }
    """
    normalized = normalize_decision(decision)
    direction = decision_direction(normalized)
    parsed_entry = _parse_date(entry_date)

    row: dict[str, Any] = {
        "ticker": ticker,
        "decision": str(decision or ""),
        "normalized_decision": normalized,
        "direction": direction,
        "entry_date": parsed_entry.isoformat() if parsed_entry else str(entry_date or ""),
        "entry_price": None,
        "entry_index": None,
        "horizons": {},
        "correct": None,
        "correct_horizons": 0,
        "available_horizons": 0,
        "notes": [],
    }

    if parsed_entry is None:
        row["notes"].append("决策日期无法解析")
        return row
    if direction == "unknown":
        row["notes"].append("决策无法归一化，跳过评估")

    horizons = [int(item) for item in (horizons or DEFAULT_HORIZONS)]
    if not horizons:
        row["notes"].append("未指定前瞻周期")
        return row

    # 取行情：需要覆盖 entry_date 之后最长周期的时间跨度
    loader = price_loader or _load_prices
    max_horizon = max(horizons)
    # 交易日 → 自然日：按 A 股一年约 245 个交易日、365 个自然日粗略换算
    start = parsed_entry - timedelta(days=10)
    end = parsed_entry + timedelta(days=int(max_horizon * 1.6) + 30)

    data: pd.DataFrame | None = None
    if frame is not None:
        data = _normalize_frame(frame)
    else:
        try:
            data = _normalize_frame(
                loader(ticker, start.isoformat(), end.isoformat())
            )
        except Exception as exc:
            row["notes"].append(f"行情获取失败：{exc}")
            return row

    if data.empty:
        row["notes"].append("行情数据为空")
        return row

    index = _entry_index(data["Date"], parsed_entry)
    if index is None:
        row["notes"].append("决策日之前没有可用行情")
        return row

    entry_price = float(data["Close"].iloc[index])
    row["entry_index"] = index
    row["entry_price"] = entry_price

    dates = data["Date"]
    closes = data["Close"].astype(float)

    for horizon in horizons:
        exit_index = index + horizon
        if exit_index >= len(data):
            row["horizons"][str(horizon)] = {
                "horizon": horizon,
                "available": False,
                "exit_date": None,
                "exit_price": None,
                "forward_return": None,
                "correct": None,
            }
            row["notes"].append(f"{horizon} 个交易日的行情尚未产生")
            continue

        exit_price = float(closes.iloc[exit_index])
        forward_return = exit_price / entry_price - 1.0 if entry_price else None
        correct = _judge(direction, forward_return, hold_band)
        row["horizons"][str(horizon)] = {
            "horizon": horizon,
            "available": True,
            "exit_date": dates.iloc[exit_index].date().isoformat(),
            "exit_price": round(exit_price, 4),
            "forward_return": round(forward_return, 6) if forward_return is not None else None,
            "correct": correct,
        }

    available = [
        item
        for item in row["horizons"].values()
        if item.get("available") and item.get("correct") is not None
    ]
    row["available_horizons"] = len(available)
    row["correct_horizons"] = sum(1 for item in available if item.get("correct"))
    if available:
        # 只要多数可用周期认可，就认为整条决策"正确"
        row["correct"] = row["correct_horizons"] * 2 > len(available)
    return row


def _judge(direction: str, forward_return: float | None, hold_band: float) -> bool | None:
    """按方向判断某一周期的结果是否正确。"""
    if forward_return is None or direction == "unknown":
        return None
    if direction == "long":
        return forward_return > 0
    if direction == "short":
        return forward_return < 0
    if direction == "flat":
        return abs(forward_return) <= hold_band
    return None


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def effective_sample_size(rows: Iterable[dict[str, Any]], horizon: int) -> int:
    """
    计算某个周期上的**有效（非重叠）样本量**。

    做法：把每条样本看成时间区间 ``[entry_date, exit_date]``，用区间调度
    （贪心选最早结束且不与已选区间重叠者）算出最多能放下多少个互不重叠
    的区间。这就是"独立样本"的上限——重叠窗口共享同一段行情，不构成
    独立证据。

    参数：
        rows: 评估行列表
        horizon: 前瞻周期（交易日）

    返回：
        int: 互不重叠的样本条数
    """
    intervals: list[tuple[date, date]] = []
    for row in rows or []:
        horizon_data = (row.get("horizons") or {}).get(str(horizon)) or {}
        if not horizon_data.get("available"):
            continue
        start = _parse_date(row.get("entry_date"))
        end = _parse_date(horizon_data.get("exit_date"))
        if start is None or end is None:
            continue
        intervals.append((start, end))

    if not intervals:
        return 0

    intervals.sort(key=lambda item: (item[1], item[0]))
    count = 0
    last_end: date | None = None
    for start, end in intervals:
        if last_end is None or start >= last_end:
            count += 1
            last_end = end
    return count


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    把一批评估行聚合成 {count, hits, hit_rate, avg_forward_return}。

    - count / hits / hit_rate 基于每行的 ``correct``（多数周期认可即算正确）
    - avg_forward_return 是这批行里**所有可用周期**前瞻收益的均值
      （同一行可能被计入多个周期，因此它不是"按行"的均值）
    """
    count = len(rows)
    hits = sum(1 for row in rows if row.get("correct") is True)
    returns = [
        float(item["forward_return"])
        for row in rows
        for item in (row.get("horizons") or {}).values()
        if item.get("available") and item.get("forward_return") is not None
    ]
    avg_return = sum(returns) / len(returns) if returns else 0.0
    return {
        "count": count,
        "hits": hits,
        "hit_rate": round(hits / count, 4) if count else 0.0,
        "avg_forward_return": round(avg_return, 6),
    }


def build_evaluation_table(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """
    把评估行聚合成一张对齐表。

    输出结构::

        {
          "total": 12,                          # 原始条数
          "raw_count": 12,
          "effective_sample_size": 5,           # 互不重叠的有效样本量
          "min_effective_sample": 8,
          "sample_insufficient": True,          # 有效样本量不足，指标不可当结论
          "by_decision": {"BUY": {"count":…, "hits":…, "hit_rate":…, "avg_forward_return":…}},
          "by_horizon":  {"5": {…同上…, "effective_sample_size":…, "sample_insufficient":…}},
          "by_ticker":   {"600519": {…}},
          "overall":     {…},
        }

    参数：
        rows: :func:`evaluate_decision` 产生的评估行
    """
    rows = list(rows or [])
    horizons = sorted(
        {
            int(item.get("horizon"))
            for row in rows
            for item in (row.get("horizons") or {}).values()
            if item.get("horizon") is not None
        }
    )

    # ---- 分组 ----
    by_decision: dict[str, list[dict[str, Any]]] = {}
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    by_horizon: dict[str, list[dict[str, Any]]] = {str(h): [] for h in horizons}
    for row in rows:
        by_decision.setdefault(row.get("normalized_decision") or "UNKNOWN", []).append(row)
        by_ticker.setdefault(row.get("ticker") or "", []).append(row)
        for horizon_data in (row.get("horizons") or {}).values():
            if horizon_data.get("available"):
                by_horizon.setdefault(str(horizon_data["horizon"]), []).append(row)

    # ---- 有效样本量（按最长周期取，最保守）----
    if horizons:
        longest = str(max(horizons))
        effective = effective_sample_size(rows, max(horizons))
    else:
        longest = ""
        effective = 0

    horizon_summary: dict[str, Any] = {}
    for horizon in horizons:
        bucket = by_horizon.get(str(horizon), [])
        summary = _aggregate(bucket)
        horizon_effective = effective_sample_size(rows, horizon)
        summary["effective_sample_size"] = horizon_effective
        summary["sample_insufficient"] = horizon_effective < MIN_EFFECTIVE_SAMPLE
        summary["min_effective_sample"] = MIN_EFFECTIVE_SAMPLE
        horizon_summary[str(horizon)] = summary

    return {
        "total": len(rows),
        "raw_count": len(rows),
        "effective_sample_size": effective,
        "min_effective_sample": MIN_EFFECTIVE_SAMPLE,
        "sample_insufficient": effective < MIN_EFFECTIVE_SAMPLE,
        "longest_horizon": longest,
        "by_decision": {
            key: _aggregate(bucket) for key, bucket in by_decision.items()
        },
        "by_horizon": horizon_summary,
        "by_ticker": {key: _aggregate(bucket) for key, bucket in by_ticker.items()},
        "overall": _aggregate(rows),
    }


# ---------------------------------------------------------------------------
# 结果回写：让"决策 → 结果"真正闭环
# ---------------------------------------------------------------------------
def situation_from_report(report: dict[str, Any]) -> str:
    """
    从 ``reports`` 表的一行构造"市场状况"摘要。

    为什么要重建：记忆检索时，分析师用的查询串是"四份报告的拼接"，只有用
    同样的文本作为记忆的键，评估结果才可能被下一次检索命中。

    参数：
        report: ``report_store.list_reports()`` 返回的一行

    返回：
        str: 市场状况摘要
    """
    report = report or {}
    reports = report.get("reports") or {}
    parts = [
        f"标的：{report.get('ticker') or ''}｜交易日：{report.get('trade_date') or ''}"
    ]
    for key, label in (
        ("market_report", "市场分析"),
        ("sentiment_report", "舆情分析"),
        ("news_report", "新闻分析"),
        ("fundamentals_report", "基本面分析"),
    ):
        text = str(reports.get(key) or "").strip()
        if text:
            parts.append(f"[{label}] {text[:500]}")
    return "\n".join(parts)


def outcome_text(row: dict[str, Any]) -> str:
    """
    把一条评估行渲染成"这次决策后来怎么样"的文本。

    示例：
        ``决策：BUY｜5日收益 +3.20%（正确）｜10日收益 -1.10%（错误）``
    """
    row = row or {}
    decision = row.get("normalized_decision") or row.get("decision") or "未知"
    parts = [f"决策：{decision}"]
    horizons = row.get("horizons") or {}
    for key in sorted(horizons, key=lambda item: int(item)):
        data = horizons[key] or {}
        if not data.get("available"):
            continue
        forward_return = data.get("forward_return")
        if forward_return is None:
            continue
        mark = "正确" if data.get("correct") else "错误"
        parts.append(f"{key}日收益 {forward_return:+.2%}（{mark}）")
    return "｜".join(parts)


def record_evaluation_outcomes(
    rows: Sequence[dict[str, Any]],
    situations: dict[tuple[str, str], str] | None = None,
    roles: Sequence[str] = ("portfolio_manager", "trader"),
) -> int:
    """
    把评估结果写回记忆库，使"决策 → 结果 → 下次可检索"闭环成立。

    WHY：只做评估不做回写，系统仍然学不到任何东西——下一次分析检索到的
    还是"只有决策、没有结果"的旧条目，无法判断上次的做法对不对。

    参数：
        rows: 评估行
        situations: ``{(ticker, entry_date): 市场状况摘要}``，缺省时用简短标识
        roles: 写入哪些角色记忆

    返回：
        int: 实际写入的条数
    """
    written = 0
    for row in rows or []:
        ticker = str(row.get("ticker") or "")
        entry_date = str(row.get("entry_date") or "")
        situation = (situations or {}).get((ticker, entry_date)) or (
            f"标的：{ticker}｜交易日：{entry_date}"
        )
        recommendation = outcome_text(row)
        for role in roles:
            try:
                save_memory(
                    role=role,
                    situation=situation,
                    recommendation=recommendation,
                    ticker=ticker,
                    trade_date=entry_date,
                    meta={"source": "evaluation"},
                )
                written += 1
            except Exception as exc:  # 回写失败不影响评估结果
                logger.warning("评估结果回写记忆失败（role=%s）：%s", role, exc)
    return written


# ---------------------------------------------------------------------------
# 入口：从历史报告生成对齐表
# ---------------------------------------------------------------------------
def _prefetch_frames(
    reports: Sequence[dict[str, Any]],
    horizons: Sequence[int],
    loader: Callable[[str, str, str], pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """按标的预取一次宽区间行情，避免每条报告都发起一次网络请求。

    WHY：``evaluate_reports`` 默认会逐条调用 ``evaluate_decision``，而后者
    每次都会拉一次行情。50 条报告就是 50 次 AkShare 请求，既慢又容易触发
    限流。这里按 (ticker, 最早决策日 - 10 天, 最晚决策日 + 最长周期) 取一次，
    后续所有报告复用同一张表。
    """
    wanted: dict[str, list[date]] = {}
    for report in reports or []:
        code = str(report.get("ticker") or "").strip()
        entry = _parse_date(report.get("trade_date"))
        if code and entry is not None:
            wanted.setdefault(code, []).append(entry)
    if not wanted:
        return {}

    max_horizon = max([int(item) for item in (horizons or DEFAULT_HORIZONS)] or [20])
    tail_days = int(max_horizon * 1.6) + 30
    frames: dict[str, pd.DataFrame] = {}
    for code, dates in wanted.items():
        start = min(dates) - timedelta(days=10)
        end = max(dates) + timedelta(days=tail_days)
        try:
            frame = _normalize_frame(
                loader(code, start.isoformat(), end.isoformat())
            )
        except Exception as exc:  # 单标的取数失败不影响其他标的
            logger.warning("评估预取行情失败（%s）：%s", code, exc)
            continue
        if frame.empty:
            continue
        frames[code] = frame
    return frames


def evaluate_reports(
    ticker: str | None = None,
    limit: int = 50,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    *,
    price_loader: Callable[[str, str, str], pd.DataFrame] | None = None,
    hold_band: float = HOLD_BAND,
    reports: Sequence[dict[str, Any]] | None = None,
    write_memory: bool = False,
    memory_roles: Sequence[str] = ("portfolio_manager", "trader"),
    prefetch: bool = True,
    owner: str | None = None,
) -> dict[str, Any]:
    """
    读取历史报告（``storage/report_store.py`` 的 ``reports`` 表）并生成对齐表。

    报告表里已经存了 ``ticker`` / ``trade_date`` / ``decision``，正好是评估
    所需的三要素。

    参数：
        ticker: 只评估某个标的，None 表示全部
        limit: 最多读取多少条报告
        horizons: 前瞻周期
        price_loader: 行情读取函数（测试可注入合成行情）
        hold_band: 持有决策的正确带宽
        reports: 直接给定报告列表（优先于读库，便于测试）
        write_memory: 是否把评估结果回写进记忆库（默认关闭）
        memory_roles: 回写时写入哪些角色记忆
        prefetch: 是否按标的预取一次宽区间行情（默认开启，能显著减少网络请求）

    返回：
        dict: ``{"rows": [评估行...], "summary": 对齐表}``
    """
    if reports is None:
        from tradingagents.storage.report_store import list_reports

        reports = list_reports(ticker=ticker, limit=limit, owner=owner)

    loader = price_loader or _load_prices
    frames = (
        _prefetch_frames(reports, horizons, loader) if prefetch else {}
    )

    rows: list[dict[str, Any]] = []
    situations: dict[tuple[str, str], str] = {}
    for report in reports or []:
        code = report.get("ticker") or ""
        trade_date = report.get("trade_date") or ""
        decision = report.get("decision") or ""
        if not code or not trade_date:
            continue
        try:
            row = evaluate_decision(
                ticker=code,
                decision=decision,
                entry_date=trade_date,
                horizons=horizons,
                price_loader=price_loader,
                hold_band=hold_band,
                frame=frames.get(str(code)),
            )
        except Exception as exc:  # 单条失败不影响整体
            logger.warning("评估失败（%s %s）：%s", code, trade_date, exc)
            continue
        # 把报告 id / 来源带进评估行，前端才能从"指标"跳回"原始报告"。
        if report.get("id") is not None:
            row["report_id"] = report.get("id")
        row["source"] = report.get("source") or ""
        rows.append(row)
        situations[(str(code), str(trade_date))] = situation_from_report(report)

    summary = build_evaluation_table(rows)

    if write_memory:
        written = record_evaluation_outcomes(
            rows, situations=situations, roles=memory_roles
        )
        summary["memory_written"] = written

    return {"rows": rows, "summary": summary}
