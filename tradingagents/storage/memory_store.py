"""代理情景记忆持久化（SQLite）。

WHY：``tradingagents/agents/utils/memory.py`` 里的 ``FinancialSituationMemory``
只存在于内存里，而 ``services/runner.py`` 每次分析都会新建一个
``TradingAgentsGraph``，于是"记忆"随对象一起消亡——学习循环从未真正闭合。
本模块把角色的（情景，建议）落到 SQLite，使记忆能跨进程、跨分析存活。

表 ``agent_memories``：
    id           自增主键
    role         角色：bull / bear / invest_judge / trader / portfolio_manager
    situation   市场状况文本（检索键）
    recommendation  对应建议/结论文本（检索命中后注入提示词）
    ticker       标的（可空）
    trade_date   交易日（可空）
    similarity   写入时记录的相似度元信息（默认 0）
    meta_json    附加元数据（可空）
    created_at   写入时间

实现沿用 ``report_store`` / ``job_store`` 的既有约定：``_connect()`` 上下文
管理器、WAL pragma、``init_db()`` 幂等建表、``TA_DB_PATH`` 环境变量覆盖、
默认 ``<cwd>/data/tradingagents.db``。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .report_store import _connect, init_db

#: 记忆角色白名单。与 ``tradingagents/graph/reflection.py`` 的
#: ``MEMORY_ROLES``、``tradingagents/graph/trading_graph.py`` 的记忆实例一一对应。
MEMORY_ROLES: tuple[str, ...] = (
    "bull",
    "bear",
    "invest_judge",
    "trader",
    "portfolio_manager",
)


def _init_memories() -> None:
    """幂等创建 ``agent_memories`` 表与索引。"""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                situation TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                ticker TEXT,
                trade_date TEXT,
                similarity REAL DEFAULT 0,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_memories_role "
            "ON agent_memories(role, created_at DESC)"
        )


def _row_to_dict(row) -> dict[str, Any]:
    """把一行 sqlite3.Row 转成普通字典。"""
    meta: dict[str, Any] = {}
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": row["id"],
        "role": row["role"],
        "situation": row["situation"],
        "recommendation": row["recommendation"],
        "ticker": row["ticker"] or "",
        "trade_date": row["trade_date"] or "",
        "similarity": float(row["similarity"] or 0.0),
        "meta": meta,
        "created_at": row["created_at"],
    }


def save_memory(
    role: str,
    situation: str,
    recommendation: str,
    ticker: str | None = None,
    trade_date: str | None = None,
    similarity: float = 0.0,
    meta: dict[str, Any] | None = None,
) -> int:
    """写入一条角色记忆，返回自增 id。

    参数：
        role: 角色标识（见 :data:`MEMORY_ROLES`）
        situation: 市场状况文本（BM25 检索的文档）
        recommendation: 对应建议文本（命中后注入提示词）
        ticker / trade_date: 可选的来源信息
        similarity: 可选的相似度元信息
        meta: 可选的附加元数据（JSON 序列化）
    """
    _init_memories()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agent_memories
                (role, situation, recommendation, ticker, trade_date,
                 similarity, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(role),
                str(situation),
                str(recommendation),
                ticker,
                trade_date,
                float(similarity or 0.0),
                json.dumps(meta or {}, ensure_ascii=False),
                now,
            ),
        )
        return int(cursor.lastrowid)


def list_memories(
    role: str | None = None, limit: int = 200, offset: int = 0
) -> list[dict[str, Any]]:
    """列出记忆（按写入时间倒序）。

    参数：
        role: 只列出某个角色的记忆，None 表示全部
        limit: 返回条数上限（1~1000）
        offset: 偏移量
    """
    _init_memories()
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    sql = "SELECT * FROM agent_memories"
    params: list[Any] = []
    if role:
        sql += " WHERE role = ?"
        params.append(role)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_memories(role: str | None = None) -> int:
    """统计记忆条数（可按角色过滤）。"""
    _init_memories()
    sql = "SELECT COUNT(*) FROM agent_memories"
    params: list[Any] = []
    if role:
        sql += " WHERE role = ?"
        params.append(role)
    with _connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def clear_memories(role: str | None = None) -> int:
    """清空记忆，返回被删除的条数。

    ``role=None`` 表示清空全部角色（危险操作，调用方需明确意图）。
    """
    _init_memories()
    sql = "DELETE FROM agent_memories"
    params: list[Any] = []
    if role:
        sql += " WHERE role = ?"
        params.append(role)
    with _connect() as conn:
        cursor = conn.execute(sql, params)
        return int(cursor.rowcount or 0)


def load_all_memories(limit_per_role: int = 200) -> dict[str, list[dict[str, Any]]]:
    """把全部角色记忆读出来，返回 ``{role: [记忆字典, ...]}``。

    参数：
        limit_per_role: 每个角色最多读取的条数（按时间倒序）
    """
    _init_memories()
    result: dict[str, list[dict[str, Any]]] = {role: [] for role in MEMORY_ROLES}
    for role in MEMORY_ROLES:
        result[role] = list_memories(role=role, limit=limit_per_role)
    # 兜底：出现白名单之外的角色时也一并带出（不丢数据）
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT role FROM agent_memories"
        ).fetchall()
    for row in rows:
        role = row["role"]
        if role not in result:
            result[role] = list_memories(role=role, limit=limit_per_role)
    return result
