"""定时分析任务存储（SQLite）。

除原有"定时任务"表外，本模块还提供调度器需要的两块基础设施：

* ``scheduler_state``：键值对存储，用来持久化调度器状态（例如每日批次的
  ``last_run_date``、重试次数、下次重试时间），解决"重启后重复跑当天批次"。
* ``scheduler_leases``：SQLite 实现的租约（lease），保证多进程/多 worker 下
  只有一个进程真正执行每日批次。

Schema 迁移
-----------
新增列通过 :func:`_ensure_columns` 幂等补齐（``PRAGMA table_info`` +
``ALTER TABLE ADD COLUMN``），可重复执行，也能安全作用于老库。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .report_store import _connect, init_db

# 允许追加到 scheduled_jobs 表的列（幂等迁移）
_SCHEDULED_COLUMN_MIGRATIONS: dict[str, str] = {
    "claimed_at": "TEXT",
    "claimed_by": "TEXT",
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT",
}

DEFAULT_CLAIM_TTL_SECONDS = 3600
DEFAULT_LEASE_TTL_SECONDS = 600


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """幂等地补齐缺失的列（``table``/``columns`` 均为模块内常量）。"""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


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
        # 首次 schema 迁移：对已存在的库补齐新列（幂等）
        _ensure_columns(conn, "scheduled_jobs", _SCHEDULED_COLUMN_MIGRATIONS)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_leases (
                key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
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
    item = {
        "id": row["id"],
        "ticker": row["ticker"],
        "name": row["name"] or "",
        "schedule_time": row["schedule_time"],
        "enabled": bool(row["enabled"]),
        "last_run_date": row["last_run_date"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    keys = row.keys()
    item["claimed_at"] = row["claimed_at"] if "claimed_at" in keys else None
    item["claimed_by"] = row["claimed_by"] if "claimed_by" in keys else None
    item["retry_count"] = int(row["retry_count"]) if "retry_count" in keys else 0
    item["last_error"] = (row["last_error"] if "last_error" in keys else "") or ""
    return item


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


def mark_scheduled_failure(job_id: int, error: str) -> None:
    """记录一次失败：累加重试次数并保存错误原因（不写 ``last_run_date``）。

    只有成功才会调用 :func:`mark_scheduled_run`，失败的任务因此仍然是"待运行"，
    下一轮 tick 会重新执行（受重试次数上限与退避时间约束）。
    """
    _init_scheduled()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET retry_count = retry_count + 1, "
            "last_error = ?, updated_at = ? WHERE id = ?",
            (str(error)[:500], now, int(job_id)),
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


# ------------------------------------------------------------------ 抢占


def claim_scheduled_job(
    job_id: int,
    owner: str,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    """尝试获得某个定时任务的执行权。

    采用乐观锁：只有当任务没有被别人抢占（或抢占已过期）时才成功。
    任务行不存在时视为"无人占用"，直接放行。
    """
    _init_scheduled()
    reference = now or datetime.now()
    stamp = reference.isoformat(timespec="seconds")
    cutoff = (
        reference - timedelta(seconds=max(1, int(ttl_seconds)))
    ).isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            "SELECT claimed_by, claimed_at FROM scheduled_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if row is None:
            return True
        cursor = conn.execute(
            """
            UPDATE scheduled_jobs SET claimed_by = ?, claimed_at = ?, updated_at = ?
            WHERE id = ?
              AND (claimed_by IS NULL OR claimed_by = ''
                   OR claimed_at IS NULL OR claimed_at <= ?)
            """,
            (owner, stamp, stamp, int(job_id), cutoff),
        )
        return cursor.rowcount > 0


def release_scheduled_claim(job_id: int, owner: str) -> None:
    """释放定时任务的抢占标记。"""
    _init_scheduled()
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_jobs SET claimed_by = '', claimed_at = NULL, "
            "updated_at = ? WHERE id = ? AND claimed_by = ?",
            (datetime.now().isoformat(timespec="seconds"), int(job_id), owner),
        )


# ------------------------------------------------------------ 键值状态存储


def get_state(key: str, default: str | None = None) -> str | None:
    """读取调度器状态（字符串）。"""
    if not key:
        return default
    _init_scheduled()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM scheduler_state WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return default
    return row["value"] if row["value"] is not None else default


def set_state(key: str, value: str) -> None:
    """写入调度器状态（字符串）。"""
    if not key:
        raise ValueError("state key 不能为空")
    _init_scheduled()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO scheduler_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, str(value), now),
        )


def delete_state(key: str) -> None:
    _init_scheduled()
    with _connect() as conn:
        conn.execute("DELETE FROM scheduler_state WHERE key = ?", (key,))


def get_state_json(key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取调度器状态（JSON 对象）。"""
    raw = get_state(key)
    if not raw:
        return dict(default or {})
    try:
        data = json.loads(raw)
    except Exception:
        return dict(default or {})
    if not isinstance(data, dict):
        return dict(default or {})
    return data


def set_state_json(key: str, value: dict[str, Any]) -> None:
    """写入调度器状态（JSON 对象）。"""
    set_state(key, json.dumps(value or {}, ensure_ascii=False))


# -------------------------------------------------------------------- 租约


def acquire_lease(
    key: str,
    owner: str,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    """尝试获取租约，返回是否成功。

    租约存在 ``scheduler_leases`` 表里，用一条
    ``INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at <= ?`` 完成：
    只有"没有租约"或"租约已过期"时才会被改写，因此多个进程同时调用时
    只有一个能拿到租约。
    """
    if not key or not owner:
        return False
    _init_scheduled()
    reference = now or datetime.now()
    stamp = reference.isoformat(timespec="seconds")
    expires = (
        reference + timedelta(seconds=max(1, int(ttl_seconds)))
    ).isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scheduler_leases (key, owner, claimed_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                owner = excluded.owner,
                claimed_at = excluded.claimed_at,
                expires_at = excluded.expires_at
            WHERE scheduler_leases.expires_at <= ?
            """,
            (key, owner, stamp, expires, stamp),
        )
        return cursor.rowcount > 0


def renew_lease(
    key: str,
    owner: str,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    """续约已有租约（只有持有者本人可以续约）。"""
    if not key or not owner:
        return False
    _init_scheduled()
    reference = now or datetime.now()
    stamp = reference.isoformat(timespec="seconds")
    expires = (
        reference + timedelta(seconds=max(1, int(ttl_seconds)))
    ).isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE scheduler_leases SET claimed_at = ?, expires_at = ? "
            "WHERE key = ? AND owner = ?",
            (stamp, expires, key, owner),
        )
        return cursor.rowcount > 0


def release_lease(key: str, owner: str) -> bool:
    """释放租约（只有持有者本人可以释放）。"""
    if not key or not owner:
        return False
    _init_scheduled()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM scheduler_leases WHERE key = ? AND owner = ?",
            (key, owner),
        )
        return cursor.rowcount > 0


def get_lease(key: str) -> dict[str, Any] | None:
    """查看租约当前状态（诊断用）。"""
    if not key:
        return None
    _init_scheduled()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scheduler_leases WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    return {
        "key": row["key"],
        "owner": row["owner"],
        "claimed_at": row["claimed_at"],
        "expires_at": row["expires_at"],
    }


def new_owner_id() -> str:
    """生成一个进程级唯一的租约持有者标识。"""
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
