"""自选股本地存储（SQLite）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_watchlist() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                name TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def add_watch(ticker: str, name: str = "") -> dict[str, Any]:
    _init_watchlist()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, name, created_at) VALUES (?, ?, ?)",
            (ticker, name, datetime.now().isoformat(timespec="seconds")),
        )
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
    return dict(row) if row else {"ticker": ticker, "name": name}


def list_watchlist() -> list[dict[str, Any]]:
    _init_watchlist()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def remove_watch(ticker: str) -> bool:
    _init_watchlist()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE ticker = ?", (ticker,)
        )
        return cursor.rowcount > 0


def get_watch(ticker: str) -> dict[str, Any] | None:
    _init_watchlist()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
    return dict(row) if row else None
