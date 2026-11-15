"""盘中告警引擎：规则判定 → 冷却去重 → 落库 → 可选推送。

为什么单独一层
--------------
``storage/alert_store.py`` 只负责存取，``dataflows/realtime.py`` 只负责
行情；把"什么时候算触发、多久内不重复提醒、是否推送"放在这里，才能让
WebUI、CLI、定时器共用同一套口径。

规则类型与单位
--------------
- ``price_above`` / ``price_below``：价格（元）
- ``change_pct_above`` / ``change_pct_below``：涨跌幅（%，例如 5 / -5）
- ``volume_ratio_above``：量比（倍）
- ``turnover_above``：换手率（%）
- ``amount_above``：成交额（亿元）

除用户规则外，引擎还会检查组合持仓的止损/止盈（``stop_loss`` /
``take_profit``），不需要用户逐只配置。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from tradingagents.dataflows.ashare_rules import normalize_code
from tradingagents.dataflows.realtime import get_realtime_quotes
from tradingagents.storage import (
    last_event_at,
    list_alert_rules,
    list_positions,
    record_alert_event,
)

logger = logging.getLogger(__name__)

#: 规则类型元数据：键 → (中文名, 行情字段, 单位, 方向)
RULE_TYPES: dict[str, dict[str, Any]] = {
    "price_above": {"label": "价格上穿", "field": "price", "unit": "元", "op": ">="},
    "price_below": {"label": "价格下穿", "field": "price", "unit": "元", "op": "<="},
    "change_pct_above": {
        "label": "涨幅超过",
        "field": "change_pct",
        "unit": "%",
        "op": ">=",
    },
    "change_pct_below": {
        "label": "跌幅超过",
        "field": "change_pct",
        "unit": "%",
        "op": "<=",
    },
    "volume_ratio_above": {
        "label": "量比超过",
        "field": "volume_ratio",
        "unit": "倍",
        "op": ">=",
    },
    "turnover_above": {
        "label": "换手率超过",
        "field": "turnover",
        "unit": "%",
        "op": ">=",
    },
    "amount_above": {
        "label": "成交额超过",
        "field": "amount",
        "unit": "亿元",
        "op": ">=",
    },
}

#: 默认冷却时间（分钟）。
DEFAULT_COOLDOWN_MINUTES = 30

#: 组合止损/止盈提醒的冷却时间（分钟）。
PORTFOLIO_COOLDOWN_MINUTES = 60


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _in_cooldown(
    ticker: str, rule_type: str, minutes: int, rule_id: int | None = None
) -> bool:
    last = _parse_time(last_event_at(ticker, rule_type, rule_id))
    if last is None:
        return False
    return datetime.now() - last < timedelta(minutes=max(1, int(minutes)))


def _rule_value(rule: dict[str, Any], quote: dict[str, Any]) -> float | None:
    meta = RULE_TYPES.get(str(rule.get("rule_type")))
    if not meta:
        return None
    value = quote.get(meta["field"])
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if meta["field"] == "amount":
        number /= 1e8
    return number


def _evaluate_rule(
    rule: dict[str, Any], quote: dict[str, Any]
) -> tuple[bool, str, str]:
    meta = RULE_TYPES.get(str(rule.get("rule_type")))
    if not meta:
        return False, "", "info"
    value = _rule_value(rule, quote)
    if value is None:
        return False, "", "info"
    threshold = float(rule.get("threshold") or 0)
    triggered = (
        value >= threshold if meta["op"] == ">=" else value <= threshold
    )
    if not triggered:
        return False, "", "info"
    label = meta["label"]
    unit = meta["unit"]
    message = (
        f"{rule.get('name') or rule.get('ticker')} {label} {threshold:g}{unit}"
        f"（当前 {value:g}{unit}）"
    )
    level = "danger" if "下穿" in label or "跌幅" in label else "warn"
    return True, message, level


def _portfolio_events(
    quotes: dict[str, dict[str, Any]], account: str | None = None
) -> list[dict[str, Any]]:
    """根据持仓的止损/止盈生成事件（不需要用户配置规则）。"""
    events: list[dict[str, Any]] = []
    try:
        positions = list_positions(account or "默认账户")
    except Exception:
        logger.debug("读取组合持仓失败，跳过持仓告警", exc_info=True)
        return events

    for position in positions:
        ticker = normalize_code(str(position.get("ticker") or ""))
        if not ticker:
            continue
        quote = quotes.get(ticker) or {}
        price = quote.get("price") or position.get("last_price")
        try:
            current = float(price)
        except (TypeError, ValueError):
            continue
        if current <= 0:
            continue
        name = position.get("name") or ticker

        stop_loss = position.get("stop_loss")
        if stop_loss and current <= float(stop_loss):
            if not _in_cooldown(ticker, "stop_loss", PORTFOLIO_COOLDOWN_MINUTES):
                events.append(
                    {
                        "ticker": ticker,
                        "rule_type": "stop_loss",
                        "level": "danger",
                        "message": f"{name} 现价 {current:.2f} 已触及止损 {float(stop_loss):.2f}",
                        "price": current,
                        "change_pct": quote.get("change_pct"),
                        "payload": {"stop_loss": float(stop_loss)},
                    }
                )

        take_profit = position.get("take_profit")
        if take_profit and current >= float(take_profit):
            if not _in_cooldown(ticker, "take_profit", PORTFOLIO_COOLDOWN_MINUTES):
                events.append(
                    {
                        "ticker": ticker,
                        "rule_type": "take_profit",
                        "level": "warn",
                        "message": f"{name} 现价 {current:.2f} 已触及目标 {float(take_profit):.2f}",
                        "price": current,
                        "change_pct": quote.get("change_pct"),
                        "payload": {"take_profit": float(take_profit)},
                    }
                )
    return events


def _notify_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"skipped": "无触发事件"}
    try:
        from tradingagents.notify import send_notification

        lines = [
            f"- {item['ticker']} {item['message']}" for item in events[:20]
        ]
        result = send_notification(
            f"盘中告警：{len(events)} 条触发",
            "\n".join(lines),
        )
        return result
    except Exception as exc:
        logger.warning("告警推送失败: %s", exc)
        return {"error": str(exc)}


def check_alerts(
    *,
    notify: bool = False,
    tickers: list[str] | None = None,
    include_portfolio: bool = True,
    account: str | None = None,
) -> dict[str, Any]:
    """执行一次盘中检查，返回行情快照、触发事件与推送结果。"""
    rules = list_alert_rules(enabled_only=True)
    wanted = {
        normalize_code(str(item))
        for item in (tickers or [])
        if str(item or "").strip()
    }
    wanted.update(normalize_code(str(rule.get("ticker") or "")) for rule in rules)
    wanted.discard("")

    if include_portfolio:
        try:
            for position in list_positions(account or "默认账户"):
                code = normalize_code(str(position.get("ticker") or ""))
                if code:
                    wanted.add(code)
        except Exception:
            logger.debug("读取组合持仓失败", exc_info=True)

    quotes = get_realtime_quotes(sorted(wanted))
    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for rule in rules:
        ticker = normalize_code(str(rule.get("ticker") or ""))
        quote = quotes.get(ticker)
        if not quote:
            skipped.append(
                {"rule_id": rule.get("id"), "ticker": ticker, "reason": "无实时行情"}
            )
            continue
        triggered, message, level = _evaluate_rule(rule, quote)
        if not triggered:
            continue
        cooldown = int(rule.get("cooldown_minutes") or DEFAULT_COOLDOWN_MINUTES)
        if _in_cooldown(
            ticker,
            str(rule.get("rule_type")),
            cooldown,
            int(rule.get("id")) if rule.get("id") is not None else None,
        ):
            skipped.append(
                {"rule_id": rule.get("id"), "ticker": ticker, "reason": "冷却中"}
            )
            continue
        events.append(
            record_alert_event(
                rule_id=rule.get("id"),
                ticker=ticker,
                rule_type=str(rule.get("rule_type")),
                message=message,
                level=level,
                price=quote.get("price"),
                change_pct=quote.get("change_pct"),
                payload={"rule": rule, "quote": quote},
            )
        )

    if include_portfolio:
        for item in _portfolio_events(quotes, account=account):
            events.append(record_alert_event(**item))

    notify_result: dict[str, Any] = {"skipped": "未请求推送"}
    if notify and events:
        notify_result = _notify_events(events)
        delivered = any(
            isinstance(value, dict) and value.get("ok")
            for value in notify_result.values()
        )
        if delivered:
            try:
                from tradingagents.storage import mark_alert_notified

                for event in events:
                    if event.get("id"):
                        mark_alert_notified(int(event["id"]))
            except Exception:
                logger.debug("标记告警已推送失败", exc_info=True)

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "quotes": quotes,
        "rules": len(rules),
        "events": events,
        "triggered": len(events),
        "skipped": skipped,
        "notify": notify_result,
    }


__all__ = ["RULE_TYPES", "DEFAULT_COOLDOWN_MINUTES", "check_alerts"]
