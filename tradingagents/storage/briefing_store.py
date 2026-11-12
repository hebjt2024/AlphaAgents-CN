"""每日盘后简报的本地归档（SQLite）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_briefings() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                briefing_date TEXT NOT NULL UNIQUE,
                as_of TEXT,
                summary_json TEXT,
                markdown TEXT NOT NULL,
                source TEXT DEFAULT 'daily_briefing',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _summary(value: str | None) -> dict[str, Any]:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _row_to_dict(row, include_markdown: bool = False) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "briefing_date": row["briefing_date"],
        "as_of": row["as_of"],
        "summary": _summary(row["summary_json"]),
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_markdown:
        item["markdown"] = row["markdown"]
    return item


def save_briefing(briefing: dict[str, Any], source: str = "daily_briefing") -> int:
    """按日期归档简报；同一天重复归档会覆盖内容。"""
    _init_briefings()
    now = datetime.now().isoformat(timespec="seconds")
    date = str(
        briefing.get("as_of")
        or briefing.get("briefing_date")
        or datetime.now().strftime("%Y-%m-%d")
    )[:10]
    summary = json.dumps(briefing.get("summary") or {}, ensure_ascii=False)
    markdown = str(briefing.get("markdown") or "")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO briefings
                (briefing_date, as_of, summary_json, markdown, source,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(briefing_date) DO UPDATE SET
                as_of = excluded.as_of,
                summary_json = excluded.summary_json,
                markdown = excluded.markdown,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (date, date, summary, markdown, source, now, now),
        )
        row = conn.execute(
            "SELECT id FROM briefings WHERE briefing_date = ?", (date,)
        ).fetchone()
    return int(row["id"]) if row else 0


def list_briefings(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    _init_briefings()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, briefing_date, as_of, summary_json, source,
                   created_at, updated_at
            FROM briefings
            ORDER BY briefing_date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_briefing(briefing_id: int) -> dict[str, Any] | None:
    _init_briefings()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM briefings WHERE id = ?", (int(briefing_id),)
        ).fetchone()
    return _row_to_dict(row, include_markdown=True) if row else None


def delete_briefing(briefing_id: int) -> bool:
    _init_briefings()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM briefings WHERE id = ?", (int(briefing_id),)
        )
        return cursor.rowcount > 0
