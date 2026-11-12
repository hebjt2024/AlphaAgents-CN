"""A 股估值 / 事件 / 财务排雷工具（供 LLM 分析师调用）。

为什么需要这一层
----------------
A 股有几个**对股价影响很大、但此前分析师完全看不到**的信息：

- **估值历史分位**：能拿到 PE/PB 的绝对值，却不知道"现在处于近 5 年
  什么位置"。28 倍 PE 对茅台是便宜，对银行是贵，对成长股又另说 ——
  没有分位就无法判断。
- **业绩预告 / 业绩快报**：A 股强制披露制度，预增预减是全年最强的
  业绩催化之一，且披露时间窗固定。
- **限售解禁**：A 股特有的股本供给压力，解禁前后常伴随抛压。
- **财务排雷**：商誉减值、股权质押、应收账款激增、经营现金流与净利润
  背离 —— A 股爆雷的四大前兆。
- **事件日历**：把上面几项合并成"未来 N 天要发生什么"。

这些能力已经在 ``dataflows/ashare_events.py`` 实现，本模块只做一件事：
把它们包装成 LangChain 工具，并转成适合 LLM 阅读的中文文本。

注意：全部工具都在函数内部延迟导入，避免 agent 构造阶段就加载
akshare（会拖慢启动）。
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _render(result: dict, *, title: str, max_items: int = 8) -> str:
    """把结构化结果渲染成 LLM 易读的中文文本。

    统一的渲染口径可以避免每个工具各写一套，也保证 ``warnings``
    一定出现在文本末尾（分析师需要知道哪些数据缺失）。
    """
    if not isinstance(result, dict):
        return f"{title}：数据格式异常。"

    lines: list[str] = [f"## {title}"]
    if not result.get("success"):
        message = result.get("summary") or "数据获取失败"
        lines.append(f"- 状态：{message}")
        warnings = result.get("warnings") or []
        if warnings:
            lines.append("- 提示：" + "；".join(str(w) for w in warnings))
        return "\n".join(lines)

    summary = result.get("summary")
    if summary:
        lines.append(f"- {summary}")

    for key, label in (
        ("pe_ttm", "PE-TTM"),
        ("pb", "PB"),
    ):
        block = result.get(key)
        if isinstance(block, dict) and block.get("value") is not None:
            lines.append(
                f"- {label}：{block.get('value')}，"
                f"分位 {block.get('percentile')}%"
                f"（区间 {block.get('min')} ~ {block.get('max')}，"
                f"中位数 {block.get('median')}）"
            )

    if result.get("level"):
        lines.append(f"- 判定：{result['level']}")
    if result.get("risk_score") is not None:
        lines.append(f"- 风险分：{result['risk_score']}/100")

    checks = result.get("checks") or []
    if checks:
        lines.append("")
        lines.append("### 检查项")
        for check in checks[:max_items]:
            lines.append(
                f"- {check.get('name')}：{check.get('level')}"
                f" · {check.get('message')}"
            )

    items = result.get("items") or []
    if items:
        lines.append("")
        lines.append(f"### 明细（共 {len(items)} 条）")
        for item in items[:max_items]:
            if isinstance(item, dict):
                parts = [
                    f"{k}={v}"
                    for k, v in item.items()
                    if v not in (None, "", 0) and not isinstance(v, (dict, list))
                ]
                lines.append("- " + "，".join(parts) if parts else "- （空）")

    events = result.get("events") or []
    if events:
        lines.append("")
        lines.append(f"### 事件日历（共 {len(events)} 项）")
        for event in events[:max_items]:
            lines.append(
                f"- {event.get('date')} · {event.get('type')} · "
                f"{event.get('title')}（{event.get('detail')}）"
            )

    if result.get("nearest_days") is not None:
        lines.append(f"- 距最近一次解禁：{result['nearest_days']} 天")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("### 数据提示")
        for warning in warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)


@tool
def get_valuation_percentile(
    ticker: Annotated[str, "A 股股票代码，如 600519 / sh600519 / 600519.SH"],
    curr_date: Annotated[str, "当前分析日期 yyyy-mm-dd；不传则用最新数据"] = None,
    years: Annotated[int, "回溯窗口年数，默认 5"] = 5,
) -> str:
    """查询个股估值（PE-TTM / PB）在历史窗口中的分位水平。

    这是判断"贵不贵"的关键工具。绝对估值本身没有意义：
    PE 28 倍对某些行业是低估，对另一些是高估；只有放进**自身历史**
    才知道当前处于什么位置。

    返回：当前 PE/PB、历史区间（最小/最大/中位数/20 与 80 分位）、
    当前值的历史分位（0-100），以及对"低估/偏低/中性/偏高/高估"的判定。

    使用建议：
    - 做基本面分析时，先看分位再谈贵贱；
    - 分位 <20% 且基本面未恶化 → 可能是估值修复机会；
    - 分位 >80% 且增长未超预期 → 注意估值回归风险。
    """
    from tradingagents.dataflows import ashare_events

    try:
        result = ashare_events.get_valuation_percentile(
            ticker, curr_date=curr_date, years=years
        )
    except Exception as exc:  # 工具层不允许把异常抛给 LLM
        logger.warning("估值分位查询失败: %s", exc)
        return f"估值分位数据暂时不可用（{type(exc).__name__}: {exc}）"
    return _render(result, title=f"{ticker} 估值历史分位")


@tool
def get_earnings_forecast(
    ticker: Annotated[str, "A 股股票代码"],
    curr_date: Annotated[str, "当前分析日期 yyyy-mm-dd"] = None,
) -> str:
    """查询个股的业绩预告与业绩快报。

    A 股的业绩预告 / 快报是**强制披露**制度，预增预减往往是全年最强
    的股价催化之一。如果公司已发布预告，务必在基本面分析中引用。

    返回：报告期、预告/快报类型、指标（净利润/营收等）、同比变动、
    公告日期与变动原因。

    使用建议：
    - 预告大幅预增且股价尚未反应 → 正面催化；
    - 预告预减或首亏 → 需要显著下调盈利预期；
    - 若查询不到，说明公司未触发强制预告条件（并非数据错误）。
    """
    from tradingagents.dataflows import ashare_events

    try:
        result = ashare_events.get_earnings_forecast(ticker, curr_date=curr_date)
    except Exception as exc:
        logger.warning("业绩预告查询失败: %s", exc)
        return f"业绩预告数据暂时不可用（{type(exc).__name__}: {exc}）"
    return _render(result, title=f"{ticker} 业绩预告 / 快报")


@tool
def get_financial_red_flags(
    ticker: Annotated[str, "A 股股票代码"],
    curr_date: Annotated[str, "当前分析日期 yyyy-mm-dd"] = None,
) -> str:
    """财务排雷：商誉、股权质押、应收账款、经营现金流四项检查。

    A 股爆雷大多有前兆，这四项是最常见的先行指标：
    - **商誉**占净资产过高 → 减值风险（2018-2019 年爆雷主因）；
    - **股权质押**比例过高 → 大股东平仓风险；
    - **应收账款**增速远超营收 → 收入质量存疑；
    - **经营现金流**长期低于净利润 → 利润没有变成钱。

    返回每项检查的等级（ok/warn/danger）、数值、说明，以及
    综合风险分（0-100，越高越危险）与中文判定。

    使用建议：出现 danger 时必须在报告中明确提示风险；
    数据不足时工具会标注"数据不足，无法判断"，不要据此下结论。
    """
    from tradingagents.dataflows import ashare_events

    try:
        result = ashare_events.get_financial_red_flags(
            ticker, curr_date=curr_date
        )
    except Exception as exc:
        logger.warning("财务排雷查询失败: %s", exc)
        return f"财务排雷数据暂时不可用（{type(exc).__name__}: {exc}）"
    return _render(result, title=f"{ticker} 财务风险排查")


@tool
def get_lockup_expiry(
    ticker: Annotated[str, "A 股股票代码"],
    months: Annotated[int, "向后查看的月数，默认 6"] = 6,
) -> str:
    """查询个股的限售股解禁安排。

    限售解禁是 A 股特有的股本供给压力：解禁前后往往出现抛压，
    是明确的**回避窗口**。给出买卖建议前应确认近期是否临近解禁。

    返回：解禁日期、解禁数量、占总股本比例、解禁市值、股份类型，
    以及距最近一次解禁的天数。

    使用建议：
    - 距解禁 <= 14 天且解禁比例较大 → 建议偏谨慎、避免追高；
    - 解禁规模占总股本 <1% 通常影响有限。
    """
    from tradingagents.dataflows import ashare_events

    try:
        result = ashare_events.get_lockup_expiry(ticker, months=months)
    except Exception as exc:
        logger.warning("限售解禁查询失败: %s", exc)
        return f"限售解禁数据暂时不可用（{type(exc).__name__}: {exc}）"
    return _render(result, title=f"{ticker} 限售解禁")


@tool
def get_event_calendar(
    ticker: Annotated[str, "A 股股票代码"],
    days: Annotated[int, "向后查看的天数，默认 30"] = 30,
) -> str:
    """查询个股未来一段时间的事件日历。

    把限售解禁、财报披露、除权除息、业绩预告窗口合并成一份
    "接下来会发生什么"的清单。做择时判断时，先看看有没有已知事件
    可以解释或预示波动。

    返回：按日期升序的事件列表（日期、类型、标题、细节、重要度），
    以及各类事件的数量统计。
    """
    from tradingagents.dataflows import ashare_events

    try:
        result = ashare_events.build_event_calendar(ticker, days=days)
    except Exception as exc:
        logger.warning("事件日历查询失败: %s", exc)
        return f"事件日历数据暂时不可用（{type(exc).__name__}: {exc}）"
    return _render(result, title=f"{ticker} 未来事件日历")


#: 供分析师按需组合的工具集合。
EVENT_TOOLS = [
    get_valuation_percentile,
    get_earnings_forecast,
    get_financial_red_flags,
    get_lockup_expiry,
    get_event_calendar,
]
