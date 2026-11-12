"""自选股策略触发预警。

在批量策略扫描的基础上，只保留有触发信号的标的，并生成适合
WebUI 展示、Markdown 导出和 Webhook 推送的结构化结果。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ALERT_TITLE = "自选股策略预警"
DISCLAIMER = "策略信号为规则化近似，仅供研究参考，不构成投资建议。"


def _direction_label(value: str) -> str:
    return {"bullish": "偏多", "bearish": "偏空"}.get(value, "中性")


def _rank_consensus(value: str) -> int:
    return {"偏多": 0, "偏空": 1}.get(value, 2)


def build_strategy_alerts(
    tickers: list[str] | None = None,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
    only_triggered: bool = True,
) -> dict[str, Any]:
    """扫描自选股并返回触发预警。

    ``tickers`` 为空时读取本地自选股；``only_triggered`` 为 True 时
    只保留至少触发一个策略的标的。
    """
    from tradingagents.storage.watchlist_store import list_watchlist
    from tradingagents.strategies.scanner import scan_watchlist

    if tickers is None:
        watch_items = list_watchlist()
    else:
        watch_items = [{"ticker": ticker, "name": ""} for ticker in tickers if ticker]

    symbols: list[str] = []
    names: dict[str, str] = {}
    for item in watch_items:
        ticker = str(item.get("ticker") or "").strip()
        if not ticker or ticker in names:
            continue
        symbols.append(ticker)
        names[ticker] = str(item.get("name") or "").strip()

    summary = {
        "total": len(symbols),
        "triggered": 0,
        "bullish": 0,
        "bearish": 0,
        "neutral": 0,
        "signals": 0,
    }
    if not symbols:
        return {
            "success": True,
            "items": [],
            "errors": [],
            "summary": summary,
            "as_of": "",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "message": "自选股为空，请先添加股票。",
            "disclaimer": DISCLAIMER,
        }

    rows = scan_watchlist(symbols, end_date, strategy_keys)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for row in rows:
        ticker = row.get("ticker") or ""
        if row.get("error"):
            errors.append({"ticker": ticker, "error": row["error"]})
            continue

        triggered = int(row.get("triggered") or 0)
        if only_triggered and triggered <= 0:
            continue

        item = dict(row)
        item["name"] = names.get(ticker, "")
        item["direction_label"] = _direction_label(item.get("consensus") or "")
        if triggered > 0:
            summary["triggered"] += 1
            summary["signals"] += triggered
            consensus = item.get("consensus") or "中性"
            if consensus == "偏多":
                summary["bullish"] += 1
            elif consensus == "偏空":
                summary["bearish"] += 1
            else:
                summary["neutral"] += 1
        if not only_triggered or triggered > 0:
            items.append(item)

    items.sort(
        key=lambda item: (
            _rank_consensus(item.get("consensus") or ""),
            int(item.get("triggered") or 0),
            float(item.get("strength") or 0),
        )
    )
    as_of = max((str(item.get("as_of") or "") for item in items), default="")

    message = (
        f"发现 {len(items)} 只自选股触发策略信号。"
        if items
        else "本次扫描未发现触发策略信号的自选股。"
    )
    return {
        "success": True,
        "items": items,
        "errors": errors,
        "summary": summary,
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "message": message,
        "disclaimer": DISCLAIMER,
    }


def format_strategy_alerts_markdown(alerts: dict[str, Any]) -> str:
    """把预警结果格式化为 Markdown，用于通知或导出。"""
    summary = alerts.get("summary") or {}
    items = alerts.get("items") or []
    lines = [
        f"### {ALERT_TITLE}",
        f"- 扫描时间：{alerts.get('generated_at', '')}",
        f"- 行情日期：{alerts.get('as_of') or '未知'}",
        f"- 自选股：{summary.get('total', 0)} 只 · 触发 {summary.get('triggered', 0)} 只"
        f"（偏多 {summary.get('bullish', 0)} / 偏空 {summary.get('bearish', 0)}）",
        f"- 触发信号：{summary.get('signals', 0)} 个",
        "",
    ]

    if not items:
        lines.append(alerts.get("message") or "本次扫描未发现触发信号。")
        lines.append("")
    for item in items:
        change = float(item.get("change_pct") or 0)
        lines.append(
            f"- **{item.get('ticker', '')} {item.get('name', '')}**"
            f"（{item.get('consensus', '中性')}，强度 {item.get('strength', 0)}%）"
            f"：{item.get('close', '-')}（{change:+.2f}%）"
        )
        for signal in item.get("top_signals") or []:
            lines.append(
                f"  - {signal.get('name', '')}"
                f"（{_direction_label(str(signal.get('direction') or ''))}，"
                f"{signal.get('score', 0)} 分）：{signal.get('summary', '')}"
            )

    lines.extend(["", f"> {alerts.get('disclaimer') or DISCLAIMER}"])
    return "\n".join(lines)


def send_strategy_alerts(
    tickers: list[str] | None = None,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
    only_triggered: bool = True,
    notify_empty: bool = False,
) -> dict[str, Any]:
    """扫描并推送预警到已配置的 Webhook 渠道。

    默认在没有任何触发信号时不推送，避免定时任务产生噪音；
    需要“无信号也通知”时可传 ``notify_empty=True``。
    """
    from tradingagents.notify import send_notification

    alerts = build_strategy_alerts(
        tickers=tickers,
        end_date=end_date,
        strategy_keys=strategy_keys,
        only_triggered=only_triggered,
    )
    if not alerts["items"] and not notify_empty:
        alerts["notify"] = {
            "skipped": alerts.get("message") or "本次扫描未发现触发信号。"
        }
        return alerts

    text = format_strategy_alerts_markdown(alerts)
    alerts["notify"] = send_notification(ALERT_TITLE, text)
    return alerts
