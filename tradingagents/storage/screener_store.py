"""每日选股候选池归档（SQLite）。

选股结果是"某一天、某套参数"的快照。只把最新结果放在内存里会导致：

- 第二天打开页面看不到昨天选了什么；
- 无法复盘"候选池后来涨了还是跌了"；
- 多套风格（价值/成长/动量）无法并存。

因此每次运行都把参数、摘要和 Top-N 明细写入 ``screener_runs``。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from .report_store import _connect, init_db


def _init_screener() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS screener_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                style TEXT NOT NULL,
                params_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                items_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_screener_runs_date
            ON screener_runs(run_date DESC, id DESC)
            """
        )


def _row_to_dict(row, include_items: bool = True) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "run_date": row["run_date"],
        "style": row["style"],
        "created_at": row["created_at"],
    }
    for key in ("params", "summary"):
        try:
            data[key] = json.loads(row[f"{key}_json"] or "{}")
        except Exception:
            data[key] = {}
    if include_items:
        try:
            data["items"] = json.loads(row["items_json"] or "[]")
        except Exception:
            data["items"] = []
    return data


def save_screener_run(
    style: str,
    params: dict[str, Any],
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    run_date: str | None = None,
) -> int:
    _init_screener()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO screener_runs
                (run_date, style, params_json, summary_json, items_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (run_date or date.today().isoformat())[:10],
                str(style),
                json.dumps(params or {}, ensure_ascii=False),
                json.dumps(summary or {}, ensure_ascii=False),
                json.dumps(items or [], ensure_ascii=False),
                now,
            ),
        )
        return int(cursor.lastrowid)


def list_screener_runs(limit: int = 20) -> list[dict[str, Any]]:
    _init_screener()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM screener_runs
            ORDER BY run_date DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [_row_to_dict(row, include_items=False) for row in rows]


def get_screener_run(run_id: int) -> dict[str, Any] | None:
    _init_screener()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM screener_runs WHERE id = ?", (int(run_id),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_latest_screener_run(
    run_date: str | None = None, style: str | None = None
) -> dict[str, Any] | None:
    _init_screener()
    clauses: list[str] = []
    params: list[Any] = []
    if run_date:
        clauses.append("run_date = ?")
        params.append(str(run_date)[:10])
    if style:
        clauses.append("style = ?")
        params.append(str(style))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM screener_runs {where} ORDER BY run_date DESC, id DESC LIMIT 1",
            params,
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_screener_run(run_id: int) -> bool:
    _init_screener()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM screener_runs WHERE id = ?", (int(run_id),)
        )
    return cursor.rowcount > 0
