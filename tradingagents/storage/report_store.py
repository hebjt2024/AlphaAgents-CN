"""基于 SQLite 的报告存储。

不引入额外依赖，使用 Python 标准库 sqlite3。
数据库路径可通过环境变量 TA_DB_PATH 覆盖，默认 <repo>/data/tradingagents.db。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


def db_path() -> Path:
    override = os.getenv("TA_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    # 默认放在当前工作目录下的 data/，与 CLI 的 eval_results 行为保持一致。
    return Path.cwd() / "data" / "tradingagents.db"


@contextmanager
def _connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                decision TEXT,
                decision_text TEXT,
                reports_json TEXT NOT NULL,
                meta_json TEXT,
                source TEXT DEFAULT 'unknown',
                owner TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
        if "owner" not in columns:
            conn.execute(
                "ALTER TABLE reports ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_ticker_date "
            "ON reports(ticker, trade_date DESC)"
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    reports = {}
    try:
        reports = json.loads(row["reports_json"] or "{}")
    except Exception:
        reports = {}
    meta = {}
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "trade_date": row["trade_date"],
        "decision": row["decision"],
        "decision_text": row["decision_text"],
        "reports": reports,
        "meta": meta,
        "source": row["source"],
        "owner": row["owner"] if "owner" in row.keys() else "",
        "created_at": row["created_at"],
    }


def save_report(
    ticker: str,
    trade_date: str,
    decision: str,
    decision_text: str,
    reports: dict[str, Any],
    source: str = "unknown",
    meta: dict[str, Any] | None = None,
    owner: str = "",
) -> int:
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports
                (ticker, trade_date, decision, decision_text, reports_json,
                 meta_json, source, owner, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                trade_date,
                decision,
                decision_text,
                json.dumps(reports, ensure_ascii=False),
                json.dumps(meta or {}, ensure_ascii=False),
                source,
                str(owner or ""),
                now,
            ),
        )
        return int(cursor.lastrowid)


def list_reports(
    ticker: str | None = None,
    limit: int = 50,
    offset: int = 0,
    owner: str | None = None,
) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    clauses = []
    params: list[Any] = []
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker)
    if owner is not None:
        clauses.append("owner = ?")
        params.append(str(owner))
    sql = "SELECT * FROM reports"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_report(
    report_id: int, owner: str | None = None
) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        if owner is None:
            row = conn.execute(
                "SELECT * FROM reports WHERE id = ?", (int(report_id),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM reports WHERE id = ? AND owner = ?",
                (int(report_id), str(owner)),
            ).fetchone()
    return _row_to_dict(row) if row else None


def delete_report(report_id: int, owner: str | None = None) -> bool:
    init_db()
    with _connect() as conn:
        if owner is None:
            cursor = conn.execute(
                "DELETE FROM reports WHERE id = ?", (int(report_id),)
            )
        else:
            cursor = conn.execute(
                "DELETE FROM reports WHERE id = ? AND owner = ?",
                (int(report_id), str(owner)),
            )
        return cursor.rowcount > 0
