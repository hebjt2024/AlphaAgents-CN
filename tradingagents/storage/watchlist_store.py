"""自选股本地存储（SQLite）。

多用户模式下，自选股按 ``owner`` 隔离；单用户/旧数据 owner 为空字符串。
旧表只有 ``ticker UNIQUE``，无法让两个用户添加同一只股票，因此首次启动时
会重建表并迁移旧数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_watchlist() -> None:
    init_db()
    with _connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(watchlist)")}
        if columns and "owner" not in columns:
            conn.execute("ALTER TABLE watchlist RENAME TO watchlist_legacy")
            conn.execute(
                """
                CREATE TABLE watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    owner TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, owner)
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO watchlist (ticker, name, owner, created_at)
                SELECT ticker, name, '', created_at FROM watchlist_legacy
                """
            )
            conn.execute("DROP TABLE watchlist_legacy")
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    owner TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(ticker, owner)
                )
                """
            )


def add_watch(ticker: str, name: str = "", owner: str = "") -> dict[str, Any]:
    _init_watchlist()
    owner = str(owner or "")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO watchlist (ticker, name, owner, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker, owner) DO UPDATE SET name = excluded.name
            """,
            (ticker, name, owner, datetime.now().isoformat(timespec="seconds")),
        )
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ? AND owner = ?",
            (ticker, owner),
        ).fetchone()
    return dict(row) if row else {"ticker": ticker, "name": name, "owner": owner}


def list_watchlist(owner: str | None = "") -> list[dict[str, Any]]:
    _init_watchlist()
    with _connect() as conn:
        if owner is None:
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY created_at DESC, id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE owner = ? ORDER BY created_at DESC, id DESC",
                (str(owner),),
            ).fetchall()
    return [dict(row) for row in rows]


def remove_watch(ticker: str, owner: str = "") -> bool:
    _init_watchlist()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE ticker = ? AND owner = ?",
            (ticker, str(owner or "")),
        )
        return cursor.rowcount > 0


def get_watch(ticker: str, owner: str = "") -> dict[str, Any] | None:
    _init_watchlist()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ? AND owner = ?",
            (ticker, str(owner or "")),
        ).fetchone()
    return dict(row) if row else None
