"""每日盘后投研简报：市场复盘 + 自选股策略预警。

把两个独立快照合并成一份可推送、可复制的 Markdown 简报。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _top_name(items: list[dict[str, Any]] | None) -> str:
    if not items:
        return ""
    item = items[0] or {}
    name = str(item.get("name") or "")
    change = item.get("change_pct")
    if change is None:
        return name
    return f"{name}（{float(change):+.2f}%）"


def build_daily_briefing(date: str | None = None) -> dict[str, Any]:
    """生成每日盘后简报。"""
    from tradingagents.services.market_review import build_market_review
    from tradingagents.services.strategy_alerts import build_strategy_alerts

    review = build_market_review(date)
    alerts = build_strategy_alerts(end_date=date, only_triggered=True)

    review_summary = review.get("summary") or {}
    breadth = review.get("breadth") or {}
    sectors = review.get("sectors") or {}
    concept = sectors.get("concept") or {}
    alert_summary = alerts.get("summary") or {}
    errors = alerts.get("errors") or []

    summary = {
        "tone": review_summary.get("tone") or "未知",
        "up": breadth.get("up", 0),
        "down": breadth.get("down", 0),
        "limit_up": breadth.get("limit_up", 0),
        "limit_down": breadth.get("limit_down", 0),
        "alerts": alert_summary.get("triggered", 0),
        "signals": alert_summary.get("signals", 0),
        "top_sector": _top_name(sectors.get("top")),
        "top_concept": _top_name(concept.get("top")),
        "top_inflow": review_summary.get("top_inflow", ""),
        "top_outflow": review_summary.get("top_outflow", ""),
    }
    success = bool(review.get("success") or alerts.get("items"))
    as_of = (
        review.get("as_of")
        or alerts.get("as_of")
        or datetime.now().strftime("%Y-%m-%d")
    )
    warnings = list(review.get("warnings") or [])
    if errors:
        warnings.append(f"自选股扫描失败 {len(errors)} 只")
    warnings.extend(alerts.get("warnings") or [])

    result = {
        "success": success,
        "as_of": as_of,
        "requested_date": date[:10] if date else "",
        "summary": summary,
        "review": review,
        "alerts": alerts,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": "本简报由规则化策略与实时行情生成，仅供研究参考，不构成投资建议。",
    }
    result["markdown"] = format_daily_briefing_markdown(result)
    return result


def format_daily_briefing_markdown(briefing: dict[str, Any]) -> str:
    """把每日简报格式化为 Markdown。"""
    as_of = briefing.get("as_of") or datetime.now().strftime("%Y-%m-%d")
    summary = briefing.get("summary") or {}
    review = briefing.get("review") or {}
    alerts = briefing.get("alerts") or {}

    lines = [
        f"# 每日盘后投研简报 {as_of}",
        "",
        "## 今日概览",
        f"- 市场情绪：**{summary.get('tone', '未知')}**"
        f"（上涨 {summary.get('up', 0)} / 下跌 {summary.get('down', 0)}，"
        f"涨停 {summary.get('limit_up', 0)} / 跌停 {summary.get('limit_down', 0)}）",
    ]
    if summary.get("top_sector"):
        lines.append(f"- 行业领涨：{summary['top_sector']}")
    if summary.get("top_concept"):
        lines.append(f"- 概念领涨：{summary['top_concept']}")
    if summary.get("top_inflow"):
        lines.append(f"- 资金净流入：{summary['top_inflow']}")
    if summary.get("top_outflow"):
        lines.append(f"- 资金净流出：{summary['top_outflow']}")
    lines.append(
        f"- 自选股预警：{summary.get('alerts', 0)} 只触发，"
        f"{summary.get('signals', 0)} 个信号"
    )
    lines.append("")

    review_md = review.get("markdown") or ""
    if review_md:
        lines.extend([review_md, ""])

    alerts_md = alerts.get("markdown") or ""
    if not alerts_md:
        from tradingagents.services.strategy_alerts import (
            format_strategy_alerts_markdown,
        )

        alerts_md = format_strategy_alerts_markdown(alerts)
    if alerts_md:
        lines.extend([alerts_md, ""])

    lines.append("## 明日关注")
    items = alerts.get("items") or []
    if items:
        for item in items[:5]:
            signals = item.get("top_signals") or []
            names = "、".join(
                str(signal.get("name") or "") for signal in signals[:2]
            )
            lines.append(
                f"- {item.get('ticker', '')} {item.get('name', '')}"
                f"（{item.get('consensus', '中性')}）：{names or '触发策略信号'}"
            )
    else:
        lines.append("- 暂无自选股触发信号，继续观察。")
    lines.append("")

    warnings = briefing.get("warnings") or []
    if warnings:
        lines.append("## 数据提示")
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")

    lines.append(f"> {briefing.get('disclaimer', '仅供研究参考。')}")
    return "\n".join(lines)


def archive_daily_briefing(
    date: str | None = None, source: str = "webui"
) -> dict[str, Any]:
    """生成并归档每日简报；同一天重复归档会覆盖。"""
    briefing = build_daily_briefing(date)
    if not briefing.get("success"):
        return briefing
    from tradingagents.storage import save_briefing

    briefing["briefing_id"] = save_briefing(briefing, source=source)
    return briefing


def send_daily_briefing(
    date: str | None = None, source: str = "scheduler"
) -> dict[str, Any]:
    """生成、归档并推送每日盘后简报。"""
    briefing = build_daily_briefing(date)
    if not briefing.get("success"):
        briefing["notify"] = {"skipped": "市场与自选股数据均不可用"}
        return briefing

    try:
        from tradingagents.storage import save_briefing

        briefing["briefing_id"] = save_briefing(briefing, source=source)
    except Exception as exc:
        briefing.setdefault("warnings", []).append(f"简报归档失败：{exc}")

    from tradingagents.notify import send_notification

    title = f"每日盘后简报 {briefing.get('as_of', '')}".strip()
    text = briefing.get("markdown") or format_daily_briefing_markdown(briefing)
    briefing["notify"] = send_notification(title, text)
    return briefing
