"""基于 SQLite 的运行度量存储（token / 成本 / 延迟）。

与 :mod:`tradingagents.storage.report_store` 等其他存储保持完全一致的约定：

* 使用标准库 ``sqlite3``，不引入额外依赖
* 数据库路径通过环境变量 ``TA_DB_PATH`` 覆盖，默认 ``<cwd>/data/tradingagents.db``
* 通过 ``_connect()`` 上下文管理器建立连接，启用 WAL 与 ``synchronous=NORMAL``
* 表结构使用 ``CREATE TABLE IF NOT EXISTS``，可在启动时安全重复调用

保存的是一次分析运行的度量快照（见
:mod:`tradingagents.services.observability` 的 ``RunMetrics.to_dict()``）：
调用次数、token 用量、估算成本、各阶段耗时与逐次调用的明细。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .report_store import _connect, init_db


def _init_run_logs() -> None:
    """确保 run_logs 表存在（可重复调用）。"""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_logs (
                run_id TEXT PRIMARY KEY,
                ticker TEXT,
                trade_date TEXT,
                source TEXT,
                provider TEXT,
                model TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration_ms REAL,
                call_count INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                estimated_cost REAL,
                currency TEXT,
                cost_is_estimated INTEGER,
                stages_json TEXT,
                llm_calls_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_logs_ticker "
            "ON run_logs(ticker, started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_logs_started "
            "ON run_logs(started_at DESC)"
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """把一行数据库记录还原成字典（JSON 字段会被解析）。"""
    stages: Any = {}
    try:
        stages = json.loads(row["stages_json"] or "{}")
    except Exception:
        stages = {}
    calls: Any = []
    try:
        calls = json.loads(row["llm_calls_json"] or "[]")
    except Exception:
        calls = []
    return {
        "run_id": row["run_id"],
        "ticker": row["ticker"] or "",
        "trade_date": row["trade_date"] or "",
        "source": row["source"] or "",
        "provider": row["provider"] or "",
        "model": row["model"] or "",
        "started_at": row["started_at"] or "",
        "finished_at": row["finished_at"] or "",
        "duration_ms": row["duration_ms"] or 0.0,
        "call_count": row["call_count"] or 0,
        "prompt_tokens": row["prompt_tokens"] or 0,
        "completion_tokens": row["completion_tokens"] or 0,
        "total_tokens": row["total_tokens"] or 0,
        "estimated_cost": row["estimated_cost"] or 0.0,
        "currency": row["currency"] or "USD",
        "cost_is_estimated": bool(row["cost_is_estimated"]),
        "stages": stages,
        "llm_calls": calls,
    }


def save_run_log(metrics: dict[str, Any]) -> str:
    """保存一次运行的度量快照，返回 ``run_id``。

    Args:
        metrics: ``RunMetrics.to_dict()`` 的输出（或字段兼容的字典）
    """
    _init_run_logs()
    totals = metrics.get("totals") or {}
    calls = metrics.get("llm_calls") or []
    provider = metrics.get("provider") or ""
    model = metrics.get("model") or ""
    if (not provider or not model) and calls:
        # 没有显式记录供应商/模型时，用调用次数最多的那个作为代表
        counts: dict[tuple[str, str], int] = {}
        for call in calls:
            key = (call.get("provider") or "", call.get("model") or "")
            counts[key] = counts.get(key, 0) + 1
        if counts:
            best = max(counts.items(), key=lambda item: item[1])[0]
            provider = provider or best[0]
            model = model or best[1]

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_logs
                (run_id, ticker, trade_date, source, provider, model,
                 started_at, finished_at, duration_ms, call_count,
                 prompt_tokens, completion_tokens, total_tokens,
                 estimated_cost, currency, cost_is_estimated,
                 stages_json, llm_calls_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.get("run_id") or "",
                metrics.get("ticker") or "",
                metrics.get("trade_date") or "",
                metrics.get("source") or "",
                provider,
                model,
                metrics.get("started_at") or "",
                metrics.get("finished_at") or "",
                float(metrics.get("duration_ms") or 0.0),
                int(totals.get("call_count", 0) or 0),
                int(totals.get("prompt_tokens", 0) or 0),
                int(totals.get("completion_tokens", 0) or 0),
                int(totals.get("total_tokens", 0) or 0),
                float(totals.get("estimated_cost", 0.0) or 0.0),
                metrics.get("currency") or "USD",
                1 if totals.get("cost_is_estimated") else 0,
                json.dumps(metrics.get("stages") or {}, ensure_ascii=False),
                json.dumps(calls, ensure_ascii=False),
            ),
        )
    return metrics.get("run_id") or ""


def list_run_logs(
    ticker: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """列出运行记录（按开始时间倒序）。"""
    _init_run_logs()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sql = "SELECT * FROM run_logs"
    params: list[Any] = []
    if ticker:
        sql += " WHERE ticker = ?"
        params.append(ticker)
    sql += " ORDER BY started_at DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_run_log(run_id: str) -> dict[str, Any] | None:
    """按 run_id 获取单条运行记录。"""
    _init_run_logs()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_logs WHERE run_id = ?", (str(run_id),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def aggregate_run_logs() -> dict[str, Any]:
    """聚合所有运行记录。

    Returns:
        ``{"totals": {...}, "by_model": {...}, "by_source": {...}}``

        * ``totals``：累计的调用次数、token 用量、成本与平均耗时
        * ``by_model``：按模型拆分的调用次数 / token / 成本
        * ``by_source``：按来源（cli / webui / scheduler ...）拆分
    """
    _init_run_logs()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT provider, model, source,
                   COUNT(*) AS runs,
                   SUM(call_count) AS call_count,
                   SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(total_tokens) AS total_tokens,
                   SUM(estimated_cost) AS estimated_cost,
                   AVG(duration_ms) AS avg_duration_ms
            FROM run_logs
            GROUP BY provider, model, source
            """
        ).fetchall()

    totals = {
        "runs": 0,
        "call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "avg_duration_ms": 0.0,
    }
    by_model: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}

    for row in rows:
        runs = int(row["runs"] or 0)
        call_count = int(row["call_count"] or 0)
        prompt_tokens = int(row["prompt_tokens"] or 0)
        completion_tokens = int(row["completion_tokens"] or 0)
        total_tokens = int(row["total_tokens"] or 0)
        cost = float(row["estimated_cost"] or 0.0)
        avg_duration = float(row["avg_duration_ms"] or 0.0)

        totals["runs"] += runs
        totals["call_count"] += call_count
        totals["prompt_tokens"] += prompt_tokens
        totals["completion_tokens"] += completion_tokens
        totals["total_tokens"] += total_tokens
        totals["estimated_cost"] = round(totals["estimated_cost"] + cost, 8)
        totals["avg_duration_ms"] = round(
            totals["avg_duration_ms"] + avg_duration * runs, 3
        )

        model_key = f"{row['provider'] or 'unknown'}/{row['model'] or 'unknown'}"
        entry = by_model.setdefault(
            model_key,
            {
                "provider": row["provider"] or "",
                "model": row["model"] or "",
                "runs": 0,
                "call_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost": 0.0,
            },
        )
        entry["runs"] += runs
        entry["call_count"] += call_count
        entry["prompt_tokens"] += prompt_tokens
        entry["completion_tokens"] += completion_tokens
        entry["total_tokens"] += total_tokens
        entry["estimated_cost"] = round(entry["estimated_cost"] + cost, 8)

        source_key = row["source"] or "unknown"
        source_entry = by_source.setdefault(
            source_key,
            {
                "runs": 0,
                "call_count": 0,
                "total_tokens": 0,
                "estimated_cost": 0.0,
            },
        )
        source_entry["runs"] += runs
        source_entry["call_count"] += call_count
        source_entry["total_tokens"] += total_tokens
        source_entry["estimated_cost"] = round(source_entry["estimated_cost"] + cost, 8)

    if totals["runs"]:
        totals["avg_duration_ms"] = round(
            totals["avg_duration_ms"] / totals["runs"], 3
        )
    totals["estimated_cost"] = round(totals["estimated_cost"], 8)

    return {"totals": totals, "by_model": by_model, "by_source": by_source}
