"""分析任务持久化（SQLite）。

用于给 WebUI/外部集成提供可查询的 job_id、状态和结果。
WebSocket 仍然保留，用于实时进度；REST 任务接口用于轮询和自动化。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_jobs() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                result_json TEXT,
                error TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_created "
            "ON jobs(created_at DESC)"
        )


def _row_to_dict(row, include_result: bool = True) -> dict[str, Any]:
    result = None
    if include_result and row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except Exception:
            result = None
    meta = {}
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    item = {
        "job_id": row["id"],
        "company": row["company"],
        "trade_date": row["trade_date"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error": row["error"] or "",
        "meta": meta,
    }
    if include_result:
        item["result"] = result
    return item


def create_job(
    company: str, trade_date: str, meta: dict[str, Any] | None = None
) -> str:
    _init_jobs()
    job_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
                (id, company, trade_date, status, created_at, meta_json)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (
                job_id,
                company,
                trade_date,
                now,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
    return job_id


def mark_job_running(job_id: str) -> None:
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? "
            "WHERE id = ? AND status IN ('queued', 'running')",
            (now, job_id),
        )


def finish_job(
    job_id: str, result: dict[str, Any], status: str = "completed"
) -> None:
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, result_json = ?, "
            "error = '' WHERE id = ?",
            (
                status,
                now,
                json.dumps(result, ensure_ascii=False),
                job_id,
            ),
        )


def fail_job(job_id: str, error: str) -> None:
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
            "WHERE id = ?",
            (now, error[:1000], job_id),
        )


def get_job(job_id: str, include_result: bool = True) -> dict[str, Any] | None:
    _init_jobs()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_dict(row, include_result=include_result) if row else None


def list_jobs(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    _init_jobs()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            ORDER BY created_at DESC, rowid DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(row, include_result=False) for row in rows]


def delete_job(job_id: str) -> bool:
    _init_jobs()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0
