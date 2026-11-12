"""分析任务持久化（SQLite）。

用于给 WebUI/外部集成提供可查询的 job_id、状态和结果。
WebSocket 仍然保留，用于实时进度；REST 任务接口用于轮询和自动化。

本模块同时承担 **任务队列的持久化后端**：

* ``status='queued'`` 的行是队列的等待区（重启后可恢复）；
* ``claim_job`` / ``claim_next_job`` 是原子的"认领"，保证一个任务只被一个
  worker 执行（进程内、跨进程都成立）；
* ``heartbeat_at`` 用于识别"进程被 kill 后遗留的 running 僵尸任务"。

Schema 迁移
-----------
历史版本的表由 ``CREATE TABLE IF NOT EXISTS`` 创建，``PRAGMA user_version``
始终为 0，项目里没有任何迁移机制。新增列必须走
:func:`_ensure_columns`：先用 ``PRAGMA table_info`` 判断列是否存在，
不存在再 ``ALTER TABLE ... ADD COLUMN``。该过程幂等，可重复执行，
也能安全作用于已经存在的老库。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .report_store import _connect, init_db

# 任务终态：队列等待/完成判定使用
TERMINAL_JOB_STATUSES = ("completed", "failed", "cancelled")

# 允许追加到 jobs 表的列（幂等迁移）
_JOB_COLUMN_MIGRATIONS: dict[str, str] = {
    "heartbeat_at": "TEXT",
    "updated_at": "TEXT",
    "kind": "TEXT NOT NULL DEFAULT 'analysis'",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
    # 任务归属的队列：避免不同用途的队列互相认领对方的任务
    "scope": "TEXT NOT NULL DEFAULT 'default'",
}


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """幂等地补齐缺失的列。

    ``table`` / ``columns`` 均来自模块内常量，不接受外部输入。
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created "
            "ON jobs(status, created_at ASC)"
        )
        # 首次 schema 迁移：对已存在的库补齐新列（幂等）
        _ensure_columns(conn, "jobs", _JOB_COLUMN_MIGRATIONS)


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
    # 新增字段（老行可能为 NULL，统一给出安全默认值）
    item["heartbeat_at"] = row["heartbeat_at"] if "heartbeat_at" in row.keys() else None
    item["updated_at"] = row["updated_at"] if "updated_at" in row.keys() else None
    item["kind"] = (row["kind"] if "kind" in row.keys() else None) or "analysis"
    item["attempts"] = int(row["attempts"]) if "attempts" in row.keys() else 0
    item["cancel_requested"] = bool(
        row["cancel_requested"] if "cancel_requested" in row.keys() else 0
    )
    item["scope"] = (row["scope"] if "scope" in row.keys() else None) or "default"
    if include_result:
        item["result"] = result
    return item


def create_job(
    company: str,
    trade_date: str,
    meta: dict[str, Any] | None = None,
    scope: str = "default",
) -> str:
    _init_jobs()
    job_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs
                (id, company, trade_date, status, created_at, meta_json,
                 updated_at, heartbeat_at, scope)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                company,
                trade_date,
                now,
                json.dumps(meta or {}, ensure_ascii=False),
                now,
                now,
                scope or "default",
            ),
        )
    return job_id


def ensure_queued_job(
    job_id: str,
    company: str = "",
    trade_date: str = "",
    meta: dict[str, Any] | None = None,
    kind: str = "analysis",
    scope: str = "default",
) -> str:
    """确保存在一条 ``status='queued'`` 的任务行，返回 job_id。

    任务队列用固定 job_id 提交任务时使用；已存在则保持原状态不变。
    """
    if not job_id:
        raise ValueError("job_id 不能为空")
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            payload = dict(meta or {})
            payload.setdefault("kind", kind)
            conn.execute(
                """
                INSERT INTO jobs
                    (id, company, trade_date, status, created_at, meta_json,
                     updated_at, heartbeat_at, kind, scope)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    company,
                    trade_date,
                    now,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                    kind,
                    scope or "default",
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
            "UPDATE jobs SET status = 'running', started_at = ?, "
            "heartbeat_at = ?, updated_at = ? "
            "WHERE id = ? AND status IN ('queued', 'running')",
            (now, now, now, job_id),
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
            "heartbeat_at = ?, updated_at = ?, error = '' WHERE id = ?",
            (
                status,
                now,
                json.dumps(result, ensure_ascii=False),
                now,
                now,
                job_id,
            ),
        )


def mark_job_failed(job_id: str, error: str) -> None:
    """把任务标记为失败（队列入口的规范名称）。"""
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, updated_at = ?, "
            "error = ? WHERE id = ?",
            (
                now,
                now,
                str(error)[:1000],
                job_id,
            ),
        )


def fail_job(job_id: str, error: str) -> None:
    """兼容旧接口，等价于 :func:`mark_job_failed`。"""
    mark_job_failed(job_id, error)


def mark_job_cancelled(job_id: str, reason: str = "") -> None:
    """把任务标记为已取消。"""
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ?, updated_at = ?, "
            "error = ? WHERE id = ?",
            (now, now, str(reason)[:1000], job_id),
        )


def touch_job(job_id: str) -> None:
    """刷新心跳时间戳，用于识别僵尸 running 任务。"""
    if not job_id:
        return
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET heartbeat_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (now, now, job_id),
        )


def request_cancel(job_id: str) -> bool:
    """请求取消任务（跨进程可见）。"""
    if not job_id:
        return False
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET cancel_requested = 1, updated_at = ? "
            "WHERE id = ? AND status IN ('queued', 'running')",
            (now, job_id),
        )
        return cursor.rowcount > 0


def is_cancel_requested(job_id: str) -> bool:
    if not job_id:
        return False
    _init_jobs()
    with _connect() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        return False
    return bool(row["cancel_requested"])


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


# ---------------------------------------------------------------- 队列接口


def list_queued_jobs(limit: int = 100, scope: str | None = None) -> list[dict[str, Any]]:
    """返回等待区（``status='queued'``）的任务，按创建时间 FIFO 排序。

    ``scope`` 用于按队列过滤，避免不同用途的队列互相恢复对方的任务。
    """
    _init_jobs()
    limit = max(1, min(int(limit), 1000))
    sql = "SELECT * FROM jobs WHERE status = 'queued'"
    params: list[Any] = []
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    sql += " ORDER BY created_at ASC, rowid ASC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row, include_result=False) for row in rows]


def list_running_jobs() -> list[dict[str, Any]]:
    _init_jobs()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'running' "
            "ORDER BY started_at ASC, rowid ASC"
        ).fetchall()
    return [_row_to_dict(row, include_result=False) for row in rows]


def claim_job(job_id: str) -> bool:
    """原子认领指定任务。

    只有把 ``status`` 从 ``queued`` 改成 ``running`` 成功的一方拿到执行权，
    因此多进程 / 多 worker 不会重复执行同一个任务。
    """
    if not job_id:
        return False
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, heartbeat_at = ?, "
            "updated_at = ?, attempts = attempts + 1 "
            "WHERE id = ? AND status = 'queued'",
            (now, now, now, job_id),
        )
        return cursor.rowcount > 0


def claim_next_job(max_attempts: int = 5, scope: str | None = None) -> dict[str, Any] | None:
    """原子认领下一个等待中的任务，返回任务详情；没有则返回 ``None``。

    ``scope`` 用于按队列过滤：不同用途的队列不会互相认领任务。
    """
    _init_jobs()
    now = datetime.now().isoformat(timespec="seconds")
    sql = "SELECT * FROM jobs WHERE status = 'queued'"
    params: list[Any] = []
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    sql += " ORDER BY created_at ASC, rowid ASC LIMIT 1"
    for _ in range(max(1, int(max_attempts))):
        with _connect() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, heartbeat_at = ?, "
                "updated_at = ?, attempts = attempts + 1 "
                "WHERE id = ? AND status = 'queued'",
                (now, now, now, row["id"]),
            )
            if cursor.rowcount > 0:
                claimed = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                return _row_to_dict(claimed, include_result=True)
    return None


def reap_stale_jobs(
    max_age_seconds: int = 1800, now: datetime | None = None
) -> list[str]:
    """回收僵尸 running 任务。

    进程被 SIGKILL / 断电时，``jobs`` 行会永远停在 ``status='running'``。
    这里把心跳超过 ``max_age_seconds`` 的 running 任务标记为 failed，
    并写明原因，返回被回收的 job_id 列表。
    """
    _init_jobs()
    reference = now or datetime.now()
    cutoff = (reference - timedelta(seconds=max(1, int(max_age_seconds)))).isoformat(
        timespec="seconds"
    )
    reason = (
        f"任务心跳超时（超过 {int(max_age_seconds)} 秒无心跳），"
        "判定为进程异常退出，自动回收"
    )
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'running'
              AND COALESCE(heartbeat_at, started_at, created_at) < ?
            """,
            (cutoff,),
        ).fetchall()
        stale = [row["id"] for row in rows]
        if stale:
            stamp = reference.isoformat(timespec="seconds")
            conn.executemany(
                "UPDATE jobs SET status = 'failed', finished_at = ?, updated_at = ?, "
                "error = ? WHERE id = ?",
                [(stamp, stamp, reason, job_id) for job_id in stale],
            )
    return stale


# ---------------------------------------------------------------- 去重辅助


def find_duplicate_report(ticker: str, trade_date: str) -> int | None:
    """查找同一 ``(ticker, trade_date)`` 已存在的报告 id。

    ``report_store`` 目前没有 ``(ticker, trade_date)`` 唯一约束，重复执行同一
    分析会产生多条报告行。这里提供一个查询辅助，供调度器/队列在写入前判重。

    真正的修复需要在 ``report_store.save_report`` 上加
    ``UNIQUE(ticker, trade_date)`` 并改成 ``INSERT ... ON CONFLICT DO UPDATE``
    （详见交付说明），本函数只是"只读判重"，不做去重写入。
    """
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM reports
            WHERE ticker = ? AND trade_date = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (ticker, trade_date),
        ).fetchone()
    return int(row["id"]) if row else None
