"""盘中告警规则与事件存储（SQLite）。

两类数据：

- ``alert_rules``：用户配置的规则（价格上穿/下穿、涨跌幅、量比、换手率等）；
- ``alert_events``：每次触发的事件（可确认、可推送）。

与 ``strategy_alerts`` 的区别：

- ``strategy_alerts`` 是**收盘后**对自选股做策略扫描，输出 Markdown；
- 本模块是**盘中**按价格/量能条件触发，强调去重、冷却与可确认。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_alerts() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                rule_type TEXT NOT NULL,
                threshold REAL NOT NULL,
                cooldown_minutes INTEGER NOT NULL DEFAULT 30,
                enabled INTEGER NOT NULL DEFAULT 1,
                note TEXT,
                last_triggered_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(ticker, rule_type, threshold)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                ticker TEXT NOT NULL,
                rule_type TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'warn',
                message TEXT NOT NULL,
                price REAL,
                change_pct REAL,
                payload_json TEXT,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0,
                triggered_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alert_events_time
            ON alert_events(triggered_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alert_events_ticker_type
            ON alert_events(ticker, rule_type, triggered_at DESC)
            """
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict[str, Any]:
    data = dict(row)
    data["enabled"] = bool(data.get("enabled"))
    data["acknowledged"] = bool(data.get("acknowledged"))
    data["notified"] = bool(data.get("notified"))
    payload = data.pop("payload_json", None)
    try:
        data["payload"] = json.loads(payload or "{}")
    except Exception:
        data["payload"] = {}
    return data


def add_alert_rule(
    ticker: str,
    rule_type: str,
    threshold: float,
    *,
    name: str = "",
    cooldown_minutes: int = 30,
    note: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    _init_alerts()
    code = str(ticker or "").strip()
    if not code:
        raise ValueError("股票代码不能为空")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO alert_rules
                (ticker, name, rule_type, threshold, cooldown_minutes,
                 enabled, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, rule_type, threshold) DO UPDATE SET
                name = excluded.name,
                cooldown_minutes = excluded.cooldown_minutes,
                enabled = excluded.enabled,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                code,
                name,
                str(rule_type),
                float(threshold),
                max(1, int(cooldown_minutes)),
                1 if enabled else 0,
                note,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM alert_rules
            WHERE ticker = ? AND rule_type = ? AND threshold = ?
            """,
            (code, str(rule_type), float(threshold)),
        ).fetchone()
    return _row_to_dict(row)


def list_alert_rules(enabled_only: bool = False) -> list[dict[str, Any]]:
    _init_alerts()
    clause = "WHERE enabled = 1" if enabled_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM alert_rules {clause} ORDER BY ticker, rule_type, threshold"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_alert_rule(rule_id: int) -> dict[str, Any] | None:
    _init_alerts()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM alert_rules WHERE id = ?", (int(rule_id),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_alert_rule(
    rule_id: int,
    *,
    threshold: float | None = None,
    cooldown_minutes: int | None = None,
    enabled: bool | None = None,
    name: str | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    _init_alerts()
    fields: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("threshold", threshold),
        ("cooldown_minutes", int(cooldown_minutes) if cooldown_minutes is not None else None),
        ("enabled", 1 if enabled else 0 if enabled is not None else None),
        ("name", name),
        ("note", note),
    ):
        if value is None:
            continue
        fields.append(f"{column} = ?")
        params.append(value)
    if not fields:
        return get_alert_rule(rule_id)
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(rule_id))
    with _connect() as conn:
        conn.execute(
            f"UPDATE alert_rules SET {', '.join(fields)} WHERE id = ?", params
        )
    return get_alert_rule(rule_id)


def delete_alert_rule(rule_id: int) -> bool:
    _init_alerts()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM alert_rules WHERE id = ?", (int(rule_id),)
        )
    return cursor.rowcount > 0


def record_alert_event(
    *,
    ticker: str,
    rule_type: str,
    message: str,
    rule_id: int | None = None,
    level: str = "warn",
    price: float | None = None,
    change_pct: float | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _init_alerts()
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO alert_events
                (rule_id, ticker, rule_type, level, message, price,
                 change_pct, payload_json, triggered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_id,
                str(ticker),
                str(rule_type),
                level,
                message,
                price,
                change_pct,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        event_id = int(cursor.lastrowid)
        if rule_id:
            conn.execute(
                "UPDATE alert_rules SET last_triggered_at = ?, updated_at = ? WHERE id = ?",
                (now, now, int(rule_id)),
            )
        row = conn.execute(
            "SELECT * FROM alert_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_dict(row)


def list_alert_events(
    limit: int = 100,
    *,
    unacked_only: bool = False,
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    _init_alerts()
    clauses: list[str] = []
    params: list[Any] = []
    if unacked_only:
        clauses.append("acknowledged = 0")
    if ticker:
        clauses.append("ticker = ?")
        params.append(str(ticker).strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM alert_events {where} ORDER BY triggered_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def ack_alert_event(event_id: int) -> bool:
    _init_alerts()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE alert_events SET acknowledged = 1 WHERE id = ?",
            (int(event_id),),
        )
    return cursor.rowcount > 0


def ack_all_alert_events() -> int:
    _init_alerts()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE alert_events SET acknowledged = 1 WHERE acknowledged = 0"
        )
    return int(cursor.rowcount or 0)


def mark_alert_notified(event_id: int) -> None:
    _init_alerts()
    with _connect() as conn:
        conn.execute(
            "UPDATE alert_events SET notified = 1 WHERE id = ?",
            (int(event_id),),
        )


def last_event_at(
    ticker: str, rule_type: str, rule_id: int | None = None
) -> str | None:
    _init_alerts()
    clauses = ["ticker = ?", "rule_type = ?"]
    params: list[Any] = [str(ticker).strip(), str(rule_type)]
    if rule_id is not None:
        clauses.append("rule_id = ?")
        params.append(int(rule_id))
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT triggered_at FROM alert_events
            WHERE {' AND '.join(clauses)}
            ORDER BY triggered_at DESC, id DESC LIMIT 1
            """,
            params,
        ).fetchone()
    return row["triggered_at"] if row else None


def alert_summary() -> dict[str, Any]:
    _init_alerts()
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        rules = conn.execute("SELECT COUNT(*) AS n FROM alert_rules WHERE enabled = 1").fetchone()
        today_events = conn.execute(
            "SELECT COUNT(*) AS n FROM alert_events WHERE triggered_at >= ?",
            (today,),
        ).fetchone()
        unacked = conn.execute(
            "SELECT COUNT(*) AS n FROM alert_events WHERE acknowledged = 0"
        ).fetchone()
    return {
        "enabled_rules": int(rules["n"] or 0) if rules else 0,
        "today_events": int(today_events["n"] or 0) if today_events else 0,
        "unacknowledged": int(unacked["n"] or 0) if unacked else 0,
    }
