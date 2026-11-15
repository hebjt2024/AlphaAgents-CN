"""社媒舆情每日快照（SQLite）。

单日热度只能回答"现在热不热"，回答不了"是不是异常升温"：

- 一只股票从无人问津到进入人气榜前 50，比一直前 10 更值得关注；
- 情绪从 -0.6 回到 +0.2，可能对应预期反转；
- 没有历史基线，就无法区分"正常热门"与"突然爆量"。

因此每次显式快照（或定时任务）都把热度、情绪、人气排名、微博情绪写入本表。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from .report_store import _connect, init_db


def _init_social() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                heat_score REAL NOT NULL DEFAULT 0,
                sentiment_score REAL NOT NULL DEFAULT 50,
                guba_rank INTEGER,
                weibo_rate REAL,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(ticker, snapshot_date)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_social_snapshots_ticker_date
            ON social_snapshots(ticker, snapshot_date DESC)
            """
        )


def _row_to_dict(row) -> dict[str, Any]:
    data = dict(row)
    payload = data.pop("payload_json", None)
    try:
        data["payload"] = json.loads(payload or "{}")
    except Exception:
        data["payload"] = {}
    return data


def save_social_snapshot(
    ticker: str,
    *,
    snapshot_date: str | None = None,
    heat_score: float,
    sentiment_score: float,
    guba_rank: int | None = None,
    weibo_rate: float | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _init_social()
    now = datetime.now().isoformat(timespec="seconds")
    day = (snapshot_date or date.today().isoformat())[:10]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO social_snapshots
                (ticker, snapshot_date, heat_score, sentiment_score,
                 guba_rank, weibo_rate, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, snapshot_date) DO UPDATE SET
                heat_score = excluded.heat_score,
                sentiment_score = excluded.sentiment_score,
                guba_rank = excluded.guba_rank,
                weibo_rate = excluded.weibo_rate,
                payload_json = excluded.payload_json,
                created_at = excluded.created_at
            """,
            (
                str(ticker).strip(),
                day,
                float(heat_score),
                float(sentiment_score),
                guba_rank,
                weibo_rate,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM social_snapshots WHERE ticker = ? AND snapshot_date = ?",
            (str(ticker).strip(), day),
        ).fetchone()
    return _row_to_dict(row) if row else {}


def list_social_snapshots(ticker: str, days: int = 60) -> list[dict[str, Any]]:
    _init_social()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM social_snapshots
            WHERE ticker = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (str(ticker).strip(), max(1, min(int(days), 365))),
        ).fetchall()
    return [_row_to_dict(row) for row in reversed(rows)]


def get_latest_social_snapshot(ticker: str) -> dict[str, Any] | None:
    _init_social()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM social_snapshots
            WHERE ticker = ?
            ORDER BY snapshot_date DESC, id DESC LIMIT 1
            """,
            (str(ticker).strip(),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def social_baseline(ticker: str, days: int = 20) -> dict[str, Any]:
    """返回最近 N 条快照的热度/情绪均值，用于异常检测。"""
    rows = list_social_snapshots(ticker, days)
    if not rows:
        return {"count": 0, "heat_avg": None, "sentiment_avg": None}
    return {
        "count": len(rows),
        "heat_avg": round(sum(float(item["heat_score"]) for item in rows) / len(rows), 2),
        "sentiment_avg": round(
            sum(float(item["sentiment_score"]) for item in rows) / len(rows), 2
        ),
    }
