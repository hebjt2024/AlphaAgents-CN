"""多用户、会话、角色与审计日志（SQLite）。

默认单用户模式完全不变：只有设置 ``WEBUI_MULTI_USER=1`` 才会启用登录、
会话和 RBAC。这样不会破坏本机单用户启动流程。

角色
----
* ``admin``：全部权限，含用户管理与审计查询；
* ``analyst``：可运行分析、修改自选股/组合/告警/选股等业务数据；
* ``viewer``：只读（GET / HEAD / OPTIONS），不能触发分析或写操作。

数据边界
--------
本版本是**共享研究工作区 + 审计**：报告、自选股、默认组合仍共享，
组合可通过 ``account`` 参数做逻辑隔离。完整的多租户隔离（每用户
独立报告库/自选股/组合）需要更大范围的 schema 迁移，未在本版实现。
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from tradingagents.storage.report_store import _connect, init_db
from webui.backend.api_config import load_config

logger = logging.getLogger(__name__)

ROLES = ("admin", "analyst", "viewer")
READ_METHODS = ("GET", "HEAD", "OPTIONS")


def _init_users() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'analyst',
                display_name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                risk_ack_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                ip TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_username
            ON sessions(username)
            """
        )
        # NOTE：audit_logs 表与索引由 tradingagents.storage.audit_store 拥有
        # （见 append_audit_event / list_audit_events）；核心业务（如分析运行）
        # 也通过它写入，这里不再重复建表。



def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), bytes.fromhex(salt), 260000
    )
    return digest.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    candidate, _ = _hash_password(password, salt)
    return secrets.compare_digest(candidate, stored_hash)


def _row_to_user(row) -> dict[str, Any]:
    data = dict(row)
    data["enabled"] = bool(data.get("enabled"))
    data.pop("password_hash", None)
    data.pop("salt", None)
    return data


def create_user(
    username: str,
    password: str,
    *,
    role: str = "analyst",
    display_name: str = "",
) -> dict[str, Any]:
    _init_users()
    username = str(username or "").strip()
    if not username:
        raise ValueError("用户名不能为空")
    if role not in ROLES:
        raise ValueError(f"角色必须是 {', '.join(ROLES)}")
    if len(str(password or "")) < 8:
        raise ValueError("密码至少 8 位")
    password_hash, salt = _hash_password(password)
    now = _now()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, salt, role, display_name, enabled,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (username, password_hash, salt, role, display_name, now, now),
            )
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"用户名已存在：{username}") from exc
    return _row_to_user(row)


def list_users() -> list[dict[str, Any]]:
    _init_users()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at"
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def get_user(username: str) -> dict[str, Any] | None:
    _init_users()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (str(username),)
        ).fetchone()
    return _row_to_user(row) if row else None


def get_user_with_secret(username: str) -> dict[str, Any] | None:
    _init_users()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (str(username),)
        ).fetchone()
    return dict(row) if row else None


def _enabled_admin_count() -> int:
    _init_users()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND enabled = 1"
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def update_user(
    username: str,
    *,
    role: str | None = None,
    enabled: bool | None = None,
    display_name: str | None = None,
    password: str | None = None,
) -> dict[str, Any] | None:
    _init_users()
    current = get_user(username)
    if current and current.get("role") == "admin" and (
        (role is not None and role != "admin") or enabled is False
    ):
        if _enabled_admin_count() <= 1:
            raise ValueError("不能降级/禁用最后一个启用的管理员")
    fields: list[str] = []
    params: list[Any] = []
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"角色必须是 {', '.join(ROLES)}")
        fields.append("role = ?")
        params.append(role)
    if enabled is not None:
        fields.append("enabled = ?")
        params.append(1 if enabled else 0)
    if display_name is not None:
        fields.append("display_name = ?")
        params.append(display_name)
    if password is not None:
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        password_hash, salt = _hash_password(password)
        fields.extend(["password_hash = ?", "salt = ?"])
        params.extend([password_hash, salt])
    if not fields:
        return get_user(username)
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(str(username))
    with _connect() as conn:
        conn.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE username = ?", params
        )
        username_value = str(username)
        if enabled is False:
            conn.execute(
                "DELETE FROM sessions WHERE username = ?", (username_value,)
            )
        if password is not None:
            conn.execute(
                "DELETE FROM sessions WHERE username = ?", (username_value,)
            )
    return get_user(username)


def delete_user(username: str) -> bool:
    _init_users()
    current = get_user(username)
    if (
        current
        and current.get("role") == "admin"
        and current.get("enabled")
        and _enabled_admin_count() <= 1
    ):
        raise ValueError("不能删除最后一个启用的管理员")
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE username = ?", (str(username),))
        cursor = conn.execute(
            "DELETE FROM users WHERE username = ?", (str(username),)
        )
    return cursor.rowcount > 0


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = get_user_with_secret(username)
    if not user or not user.get("enabled"):
        return None
    if not _verify_password(password, user["password_hash"], user["salt"]):
        return None
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE username = ?",
            (now, now, user["username"]),
        )
    return _row_to_user(user)


def create_session(
    username: str,
    *,
    ip: str = "",
    user_agent: str = "",
    days: int | None = None,
) -> str:
    _init_users()
    config = load_config()
    ttl = days if days is not None else config.session_days
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (token, username, created_at, expires_at, last_seen_at, ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                str(username),
                now.isoformat(timespec="seconds"),
                (now + timedelta(days=max(1, int(ttl)))).isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                ip[:200],
                user_agent[:300],
            ),
        )
    return token


def get_session_user(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    _init_users()
    now = datetime.now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.role, u.enabled, u.display_name, u.risk_ack_at
            FROM sessions s JOIN users u ON u.username = s.username
            WHERE s.token = ?
            """,
            (str(token),),
        ).fetchone()
        if not row:
            return None
        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires) <= now:
            conn.execute("DELETE FROM sessions WHERE token = ?", (str(token),))
            return None
        if not bool(row["enabled"]):
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token = ?",
            (now.isoformat(timespec="seconds"), str(token)),
        )
    return {
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"] or row["username"],
        "risk_ack_at": row["risk_ack_at"],
        "session_expires_at": row["expires_at"],
    }


def revoke_session(token: str) -> bool:
    _init_users()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE token = ?", (str(token),)
        )
    return cursor.rowcount > 0


def revoke_user_sessions(username: str) -> int:
    _init_users()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM sessions WHERE username = ?", (str(username),)
        )
    return int(cursor.rowcount or 0)


def ack_risk(username: str) -> dict[str, Any] | None:
    _init_users()
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET risk_ack_at = ?, updated_at = ? WHERE username = ?",
            (now, now, str(username)),
        )
    return get_user(username)


def audit_log(
    *,
    username: str | None,
    role: str | None,
    action: str,
    method: str = "",
    path: str = "",
    status: int | None = None,
    ip: str = "",
    request_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """记录审计事件（落地到 SQLite，失败绝不影响业务）。

    现由 :mod:`tradingagents.storage.audit_store` 拥有表结构与写入；
    此处保留原签名以兼容既有调用方（middleware / server）。
    """
    from tradingagents.storage.audit_store import append_audit_event

    append_audit_event(
        username=username,
        role=role,
        action=action,
        method=method,
        path=path,
        status=status,
        ip=ip,
        request_id=request_id,
        detail=detail,
    )


def list_audit_logs(
    limit: int = 100, *, username: str | None = None
) -> list[dict[str, Any]]:
    """读取审计事件（现由 storage.audit_store 拥有）。"""
    from tradingagents.storage.audit_store import list_audit_events

    return list_audit_events(max(1, min(int(limit), 1000)), username=username)


def ensure_bootstrap_admin() -> dict[str, Any] | None:
    """多用户模式下首次启动创建 admin；密码来自环境变量或随机生成。"""
    config = load_config()
    if not config.multi_user:
        return None
    if list_users():
        return None
    username = (os.getenv("WEBUI_ADMIN_USER") or "admin").strip() or "admin"
    password = (os.getenv("WEBUI_ADMIN_PASSWORD") or "").strip()
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    create_user(username, password, role="admin", display_name="管理员")
    if generated:
        path = os.path.join(
            os.getcwd(), "data", "initial_admin_password.txt"
        )
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"{username}\n{password}\n")
            os.chmod(path, 0o600)
            logger.warning(
                "已创建初始管理员 %s，随机密码写入 %s（请登录后立即修改）",
                username,
                path,
            )
        except Exception:
            logger.warning(
                "已创建初始管理员 %s，随机密码：%s（请立即修改）",
                username,
                password,
            )
    else:
        logger.info("已创建初始管理员: %s", username)
    return {"username": username, "generated": generated}


__all__ = [
    "ROLES",
    "READ_METHODS",
    "ack_risk",
    "audit_log",
    "authenticate",
    "create_session",
    "create_user",
    "delete_user",
    "ensure_bootstrap_admin",
    "get_session_user",
    "get_user",
    "list_audit_logs",
    "list_users",
    "revoke_session",
    "revoke_user_sessions",
    "update_user",
]
