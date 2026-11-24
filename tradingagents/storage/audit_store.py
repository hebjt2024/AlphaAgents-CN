"""SQLite 审计事件存储（``tradingagents.storage`` 层）。

表 ``audit_logs`` 结构与历史版本（``webui/backend/users.py``）一致，两边都
用 :func:`tradingagents.storage.report_store._connect` 连接共享数据库
（``data/tradingagents.db``）。

``webui``（多用户 / RBAC 场景的 HTTP 审计）与核心业务（如分析运行）
都通过本模块写入，写入失败绝不影响主流程（所有入口尽量吞掉异常）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .report_store import _connect

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_audit_db() -> None:
    """创建 ``audit_logs`` 表与索引（idempotent）。"""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                role TEXT,
                action TEXT NOT NULL,
                method TEXT,
                path TEXT,
                status INTEGER,
                ip TEXT,
                request_id TEXT,
                detail_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_time
            ON audit_logs(created_at DESC)
            """
        )


def append_audit_event(
    *,
    username: str | None = "",
    role: str | None = "",
    action: str,
    method: str = "",
    path: str = "",
    status: int | None = None,
    ip: str = "",
    request_id: str = "",
    detail: dict[str, Any] | None = None,
) -> int | None:
    """写入一条审计事件；失败返回 ``None``（绝不抛异常，不影响主流程）。

    Returns
    ----
    新行 ``id``，或 ``None``（写入失败 / 不可用）。
    """
    try:
        init_audit_db()
        with _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_logs
                    (username, role, action, method, path, status, ip,
                     request_id, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(username or ""),
                    str(role or ""),
                    str(action)[:200],
                    str(method)[:10],
                    str(path)[:500],
                    status,
                    str(ip)[:200],
                    str(request_id)[:100],
                    json.dumps(detail or {}, ensure_ascii=False),
                    _now(),
                ),
            )
            return int(cursor.lastrowid)
    except Exception:
        logger.warning("审计事件写入失败", exc_info=True)
        return None


def list_audit_events(
    limit: int = 100, *, username: str | None = None
) -> list[dict[str, Any]]:
    """按 ``created_at DESC, id DESC`` 读取审计事件（解析 ``detail`` 字段）。

    Args:
        limit: 最多返回条数，夹紧到 [1, 1000]。
        username: 可选，只看某个用户。
    """
    init_audit_db()
    clauses: list[str] = []
    params: list[Any] = []
    if username:
        clauses.append("username = ?")
        params.append(str(username))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_logs {where} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.pop("detail_json", "{}") or "{}")
        except Exception:
            item["detail"] = {}
        result.append(item)
    return result


__all__ = ["append_audit_event", "init_audit_db", "list_audit_events"]
