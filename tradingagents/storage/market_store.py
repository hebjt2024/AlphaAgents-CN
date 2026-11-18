"""本地行情仓库（SQLite）。

为什么需要这一层
----------------
此前系统每次分析、回测、评估都直接调用 AkShare：

- 数据源限流/接口变更会让整条链路失败；
- 同一份历史行情被重复下载，回测不可复现；
- 离线时只剩两条模拟数据兜底，无法做严肃研究。

本模块只负责**持久化**：把标准化后的日线 OHLCV 按
``(ticker, trade_date, adjust)`` 写入独立 SQLite 库。取数、补洞与
质量检查在 ``services/market_data.py`` / ``services/data_quality.py``。

独立数据库（默认 ``data/market.db``）而不是塞进报告库：

- 行情数据量大，备份/清理策略与报告不同；
- 删除/重建行情缓存不应该影响报告、组合与任务表；
- 可以用 ``TA_MARKET_DB`` 指向 SSD/内存盘。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

#: 行情缓存开关：测试默认关闭，生产默认开启。
MARKET_CACHE_ENV = "TA_MARKET_CACHE"
#: 行情库路径；默认 <cwd>/data/market.db。
MARKET_DB_ENV = "TA_MARKET_DB"


def market_cache_enabled() -> bool:
    """是否启用本地行情缓存（每次调用读取，便于测试切换）。"""
    return os.getenv(MARKET_CACHE_ENV, "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def market_db_path() -> Path:
    override = os.getenv(MARKET_DB_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd() / "data" / "market.db"


@contextmanager
def _connect():
    path = market_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # 与 report_store 一致：Windows 绑定挂载上 WAL/-shm 会随机报
    # "disk I/O error"，改用 DELETE 日志模式保证稳定（自用/小范围够用）。
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()


def _init_market() -> None:
    path = str(market_db_path())
    if path in _INITIALIZED_PATHS:
        return
    with _INIT_LOCK:
        if path in _INITIALIZED_PATHS:
            return
        _init_market_schema()
        _INITIALIZED_PATHS.add(path)


def _init_market_schema() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                ticker TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                adjust TEXT NOT NULL DEFAULT 'qfq',
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticker, trade_date, adjust)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_bars_date
            ON daily_bars(trade_date)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                ticker TEXT NOT NULL,
                adjust TEXT NOT NULL DEFAULT 'qfq',
                first_date TEXT,
                last_date TEXT,
                last_sync_at TEXT,
                last_source TEXT,
                PRIMARY KEY (ticker, adjust)
            )
            """
        )


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_daily_bars(
    ticker: str,
    frame: pd.DataFrame,
    *,
    adjust: str = "qfq",
    source: str = "unknown",
) -> int:
    """把标准化行情写入本地库，返回写入/更新的行数。

    ``frame`` 需要包含 ``Date/Open/High/Low/Close/Volume``；
    ``Amount`` 可选。重复日期按主键覆盖，保证同一份行情只有一份。
    """
    code = str(ticker or "").strip()
    if not code or frame is None or frame.empty:
        return 0
    if "Date" not in frame.columns or "Close" not in frame.columns:
        raise ValueError("行情缺少 Date/Close 列")

    _init_market()
    now = _now()
    rows: list[tuple[Any, ...]] = []
    dates: list[str] = []
    for _, item in frame.iterrows():
        try:
            trade_date = _date_text(item.get("Date"))
        except Exception:
            continue
        close = item.get("Close")
        if close is None or pd.isna(close) or float(close) <= 0:
            continue
        values = []
        for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
            value = item.get(column)
            try:
                values.append(None if value is None or pd.isna(value) else float(value))
            except (TypeError, ValueError):
                values.append(None)
        rows.append(
            (
                code,
                trade_date,
                str(adjust or "qfq"),
                *values,
                source,
                now,
            )
        )
        dates.append(trade_date)

    if not rows:
        return 0

    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO daily_bars
                (ticker, trade_date, adjust, open, high, low, close,
                 volume, amount, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date, adjust) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                amount = excluded.amount,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        first, last = min(dates), max(dates)
        conn.execute(
            """
            INSERT INTO sync_state
                (ticker, adjust, first_date, last_date, last_sync_at, last_source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, adjust) DO UPDATE SET
                first_date = MIN(COALESCE(sync_state.first_date, excluded.first_date), excluded.first_date),
                last_date = MAX(COALESCE(sync_state.last_date, excluded.last_date), excluded.last_date),
                last_sync_at = excluded.last_sync_at,
                last_source = excluded.last_source
            """,
            (code, str(adjust or "qfq"), first, last, now, source),
        )
    return len(rows)


def load_daily_bars(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """从本地库读取行情；没有数据返回空 DataFrame（列已就位）。"""
    code = str(ticker or "").strip()
    columns = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount"]
    if not code:
        return pd.DataFrame(columns=columns)
    _init_market()
    clauses = ["ticker = ?", "adjust = ?"]
    params: list[Any] = [code, str(adjust or "qfq")]
    if start_date:
        clauses.append("trade_date >= ?")
        params.append(str(start_date)[:10])
    if end_date:
        clauses.append("trade_date <= ?")
        params.append(str(end_date)[:10])
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT trade_date, open, high, low, close, volume, amount
            FROM daily_bars
            WHERE {' AND '.join(clauses)}
            ORDER BY trade_date
            """,
            params,
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=columns)
    data = [
        {
            "Date": item["trade_date"],
            "Open": item["open"],
            "High": item["high"],
            "Low": item["low"],
            "Close": item["close"],
            "Volume": item["volume"],
            "Amount": item["amount"],
        }
        for item in rows
    ]
    frame = pd.DataFrame(data, columns=columns)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Date"]).reset_index(drop=True)


def get_sync_state(ticker: str, *, adjust: str = "qfq") -> dict[str, Any] | None:
    _init_market()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sync_state WHERE ticker = ? AND adjust = ?",
            (str(ticker or "").strip(), str(adjust or "qfq")),
        ).fetchone()
    return dict(row) if row else None


def covered_range(
    ticker: str, *, adjust: str = "qfq"
) -> tuple[str, str] | None:
    """返回本地覆盖的 (最早日期, 最晚日期)，没有数据返回 ``None``。"""
    _init_market()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT MIN(trade_date) AS first_date, MAX(trade_date) AS last_date
            FROM daily_bars WHERE ticker = ? AND adjust = ?
            """,
            (str(ticker or "").strip(), str(adjust or "qfq")),
        ).fetchone()
    if not row or not row["first_date"] or not row["last_date"]:
        return None
    return row["first_date"], row["last_date"]


def missing_ranges(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    adjust: str = "qfq",
) -> list[tuple[str, str]]:
    """计算需要在线补洞的日期区间（只处理首尾，内部缺口交给质量检查）。

    WHY 不逐日补齐：一次补 2 年内部缺口会产生几十次请求；首尾补洞已经
    能覆盖"本地没有/已经过期"这两个最常见场景。内部缺口由
    ``services/data_quality.py`` 标记，确认是停牌还是数据缺失后再补。
    """
    start = str(start_date)[:10]
    end = str(end_date)[:10]
    if not start or not end or start > end:
        return []
    covered = covered_range(ticker, adjust=adjust)
    if covered is None:
        return [(start, end)]
    first, last = covered
    ranges: list[tuple[str, str]] = []
    if start < first:
        ranges.append((start, (pd.Timestamp(first) - timedelta(days=1)).strftime("%Y-%m-%d")))
    if end > last:
        ranges.append(((pd.Timestamp(last) + timedelta(days=1)).strftime("%Y-%m-%d"), end))
    return ranges


def list_market_symbols() -> list[dict[str, Any]]:
    """列出本地已缓存的标的及覆盖范围。"""
    _init_market()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.ticker, s.adjust, s.first_date, s.last_date,
                   s.last_sync_at, s.last_source,
                   COUNT(b.trade_date) AS bars
            FROM sync_state s
            LEFT JOIN daily_bars b
              ON b.ticker = s.ticker AND b.adjust = s.adjust
            GROUP BY s.ticker, s.adjust
            ORDER BY s.ticker
            """
        ).fetchall()
    return [dict(row) for row in rows]


def market_stats() -> dict[str, Any]:
    """本地行情库概览：路径、标的数、K 线数、最新日期。"""
    _init_market()
    with _connect() as conn:
        summary = conn.execute(
            """
            SELECT COUNT(DISTINCT ticker) AS symbols,
                   COUNT(*) AS bars,
                   MIN(trade_date) AS first_date,
                   MAX(trade_date) AS last_date
            FROM daily_bars
            """
        ).fetchone()
    return {
        "path": str(market_db_path()),
        "enabled": market_cache_enabled(),
        "symbols": int(summary["symbols"] or 0) if summary else 0,
        "bars": int(summary["bars"] or 0) if summary else 0,
        "first_date": (summary["first_date"] if summary else None) or "",
        "last_date": (summary["last_date"] if summary else None) or "",
    }


def delete_market_data(ticker: str | None = None, *, adjust: str = "qfq") -> int:
    """删除某标的（或全库）行情，返回删除的 K 线数。"""
    _init_market()
    with _connect() as conn:
        if ticker:
            cursor = conn.execute(
                "DELETE FROM daily_bars WHERE ticker = ? AND adjust = ?",
                (str(ticker).strip(), str(adjust or "qfq")),
            )
            conn.execute(
                "DELETE FROM sync_state WHERE ticker = ? AND adjust = ?",
                (str(ticker).strip(), str(adjust or "qfq")),
            )
        else:
            cursor = conn.execute("DELETE FROM daily_bars")
            conn.execute("DELETE FROM sync_state")
        return int(cursor.rowcount or 0)
