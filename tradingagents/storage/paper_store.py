"""模拟盘状态的持久化（SQLite）。

模拟撮合器 ``services/paper_trading.py::PaperBroker`` 本身是纯内存对象：
这样核心逻辑可以离线复现、单元测试不需要数据库。但 WebUI 需要跨请求
保留账户、持仓、委托与净值曲线，因此这里只做一件事——把 broker 的
完整状态序列化成 JSON，按账户名存取。

为什么用 JSON 快照而不是把委托/持仓拆表：

- 模拟盘的状态是**强一致**的（现金冻结、T+1 批次、委托与持仓互相
  依赖），拆表后每次请求都要重建对象并处理部分写入；
- 单用户/小团队场景下数据量很小（一个账户通常几十条委托）；
- 状态结构由 ``PaperBroker.to_state()`` / ``from_state()`` 统一定义，
  避免存储层与撮合层各维护一套字段。

后续如果要做多账户高频写入，再迁移为事件溯源（orders/events 表）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_paper() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_accounts (
                name TEXT PRIMARY KEY,
                owner TEXT NOT NULL DEFAULT '',
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(paper_accounts)")
        }
        if "owner" not in columns:
            conn.execute(
                "ALTER TABLE paper_accounts ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
            )


def load_paper_state(name: str = "默认模拟盘") -> dict[str, Any] | None:
    """读取模拟盘状态；不存在返回 ``None``。"""
    _init_paper()
    with _connect() as conn:
        row = conn.execute(
            "SELECT state_json FROM paper_accounts WHERE name = ?", (name,)
        ).fetchone()
    if not row:
        return None
    try:
        state = json.loads(row["state_json"] or "{}")
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def save_paper_state(
    name: str, state: dict[str, Any], owner: str = ""
) -> None:
    """覆盖保存模拟盘状态。"""
    _init_paper()
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_accounts (name, owner, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner = CASE
                    WHEN excluded.owner IS NULL OR excluded.owner = ''
                    THEN paper_accounts.owner
                    ELSE excluded.owner
                END,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (name, str(owner or ""), payload, now),
        )


def delete_paper_state(name: str = "默认模拟盘") -> bool:
    _init_paper()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM paper_accounts WHERE name = ?", (name,)
        )
    return cursor.rowcount > 0


def get_paper_owner(name: str) -> str:
    _init_paper()
    with _connect() as conn:
        row = conn.execute(
            "SELECT owner FROM paper_accounts WHERE name = ?", (str(name),)
        ).fetchone()
    return str(row["owner"] or "") if row else ""


def list_paper_accounts(owner: str | None = None) -> list[dict[str, Any]]:
    _init_paper()
    with _connect() as conn:
        if owner is None:
            rows = conn.execute(
                "SELECT name, owner, updated_at FROM paper_accounts ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, owner, updated_at FROM paper_accounts WHERE owner = ? ORDER BY name",
                (str(owner),),
            ).fetchall()
    return [dict(row) for row in rows]
