"""市场情绪历史快照的本地存储（SQLite）。

``services/market_review.py`` 只提供实时快照，无法回答“现在是情绪冰点
还是高潮”。本模块把每日复盘落库为时间序列，供
``services/market_sentiment.py`` 计算分位与趋势。

表结构遵循同目录其它 store 的约定：``_connect()`` 复用
``report_store`` 的 WAL 连接，``db_path()`` 走 ``TA_DB_PATH`` 环境变量，
默认落在 ``<cwd>/data/tradingagents.db``。同一交易日重复写入为 upsert，
不会产生重复行。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_market_sentiment() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL UNIQUE,
                up_count INTEGER,
                down_count INTEGER,
                flat_count INTEGER,
                limit_up INTEGER,
                limit_down INTEGER,
                total_amount REAL,
                tone TEXT,
                tone_text TEXT,
                index_json TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sentiment_date "
            "ON market_sentiment(trade_date)"
        )


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any) -> int:
    return int(_number(value))


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    """把复盘 payload 归一化成表字段。兼容顶层字段与 ``breadth`` 嵌套。"""
    payload = payload or {}
    breadth = payload.get("breadth") or {}
    summary = payload.get("summary") or {}
    indices = payload.get("indices") or []

    def pick(key: str, fallback: Any) -> Any:
        value = payload.get(key)
        return value if value is not None else fallback

    up = pick("up_count", breadth.get("up"))
    down = pick("down_count", breadth.get("down"))
    flat = pick("flat_count", breadth.get("flat"))
    limit_up = pick("limit_up", breadth.get("limit_up"))
    limit_down = pick("limit_down", breadth.get("limit_down"))

    total_amount = payload.get("total_amount")
    if total_amount is None:
        turnover = [
            _number(item.get("turnover_yi"))
            for item in indices
            if isinstance(item, dict)
        ]
        total_amount = round(sum(turnover), 2)

    return {
        "up_count": _integer(up),
        "down_count": _integer(down),
        "flat_count": _integer(flat),
        "limit_up": _integer(limit_up),
        "limit_down": _integer(limit_down),
        "total_amount": _number(total_amount),
        "tone": str(payload.get("tone") or summary.get("tone") or ""),
        "tone_text": str(
            payload.get("tone_text") or summary.get("text") or ""
        ),
        "index_json": json.dumps(indices, ensure_ascii=False),
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trade_date": row["trade_date"],
        "up_count": _integer(row["up_count"]),
        "down_count": _integer(row["down_count"]),
        "flat_count": _integer(row["flat_count"]),
        "limit_up": _integer(row["limit_up"]),
        "limit_down": _integer(row["limit_down"]),
        "total_amount": round(_number(row["total_amount"]), 2),
        "tone": row["tone"] or "",
        "tone_text": row["tone_text"] or "",
        "indices": _loads(row["index_json"], []),
        "payload": _loads(row["payload_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_sentiment_snapshot(
    trade_date: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """按交易日写入情绪快照；同日重复写入覆盖内容（upsert）。"""
    _init_market_sentiment()
    date = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    fields = _extract(payload)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO market_sentiment
                (trade_date, up_count, down_count, flat_count, limit_up,
                 limit_down, total_amount, tone, tone_text, index_json,
                 payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                up_count = excluded.up_count,
                down_count = excluded.down_count,
                flat_count = excluded.flat_count,
                limit_up = excluded.limit_up,
                limit_down = excluded.limit_down,
                total_amount = excluded.total_amount,
                tone = excluded.tone,
                tone_text = excluded.tone_text,
                index_json = excluded.index_json,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                date,
                fields["up_count"],
                fields["down_count"],
                fields["flat_count"],
                fields["limit_up"],
                fields["limit_down"],
                fields["total_amount"],
                fields["tone"],
                fields["tone_text"],
                fields["index_json"],
                fields["payload_json"],
                now,
                now,
            ),
        )
    return get_sentiment_snapshot(date) or {}


def list_sentiment_snapshots(days: int = 60) -> list[dict[str, Any]]:
    """返回最近 ``days`` 条快照，按交易日升序排列。"""
    _init_market_sentiment()
    days = max(1, min(int(days), 10000))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM market_sentiment
                ORDER BY trade_date DESC
                LIMIT ?
            ) ORDER BY trade_date ASC
            """,
            (days,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_sentiment_snapshot(trade_date: str) -> dict[str, Any] | None:
    _init_market_sentiment()
    date = str(trade_date or "")[:10]
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM market_sentiment WHERE trade_date = ?", (date,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_sentiment_snapshot(trade_date: str) -> bool:
    _init_market_sentiment()
    date = str(trade_date or "")[:10]
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM market_sentiment WHERE trade_date = ?", (date,)
        )
        return cursor.rowcount > 0
