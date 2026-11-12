"""定时分析任务存储（SQLite）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_scheduled() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                schedule_time TEXT NOT NULL DEFAULT '18:00',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(ticker, schedule_time)
            )
            """
        )


def _normalize_time(value: str | None) -> str:
    text = str(value or "18:00").strip()
    try:
        hour, minute = text.split(":", 1)
        h, m = int(hour), int(minute)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception:
        pass
    return "18:00"


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "name": row["name"] or "",
        "schedule_time": row["schedule_time"],
        "enabled": bool(row["enabled"]),
        "last_run_date": row["last_run_date"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def add_scheduled_job(
    ticker: str,
    name: str = "",
    schedule_time: str = "18:00",
) -> dict[str, Any]:
    _init_scheduled()
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("股票代码不能为空")
    name = str(name or "").strip()
    time_value = _normalize_time(schedule_time)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduled_jobs
                (ticker, name, schedule_time, enabled, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(ticker, schedule_time) DO UPDATE SET
                name = excluded.name,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (ticker, name, time_value, now, now),
        )
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE ticker = ? AND schedule_time = ?",
            (ticker, time_value),
        ).fetchone()
    return _row_to_dict(row)


def list_scheduled_jobs() -> list[dict[str, Any]]:
    _init_scheduled()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY schedule_time, id"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_scheduled_job(job_id: int) -> dict[str, Any] | None:
    _init_scheduled()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (int(job_id),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_scheduled_job(
    job_id: int,
    name: str | None = None,
    schedule_time: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    _init_scheduled()
    fields = []
    params: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        params.append(str(name).strip())
    if schedule_time is not None:
        fields.append("schedule_time = ?")
        params.append(_normalize_time(schedule_time))
    if enabled is not None:
        fields.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not fields:
        return get_scheduled_job(job_id)
    fields.append("updated_at = ?")
    params.append(datetime.now().isoformat(timespec="seconds"))
    params.append(int(job_id))
    with _connect() as conn:
        conn.execute(
            f"UPDATE scheduled_jobs SET {', '.join(fields)} WHERE id = ?",
            params,
        )
    return get_scheduled_job(job_id)


def delete_scheduled_job(job_id: int) -> bool:
    _init_scheduled()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM scheduled_jobs WHERE id = ?", (int(job_id),)
        )
        return cursor.rowcount > 0


def mark_scheduled_run(job_id: int, run_date: str) -> None:
    _init_scheduled()
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET last_run_date = ?, updated_at = ? "
            "WHERE id = ?",
            (
                run_date,
                datetime.now().isoformat(timespec="seconds"),
                int(job_id),
            ),
        )


def due_scheduled_jobs(now_time: str, today: str) -> list[dict[str, Any]]:
    _init_scheduled()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scheduled_jobs
            WHERE enabled = 1
              AND schedule_time <= ?
              AND (last_run_date IS NULL OR last_run_date != ?)
            ORDER BY schedule_time, id
            """,
            (now_time, today),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]
