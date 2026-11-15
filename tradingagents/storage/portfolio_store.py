"""持仓与账户的本地存储（SQLite）。

项目此前只能对**单只股票**给出建议 —— 没有账户、没有持仓、没有成本，
因此"建议仓位 20%"这样的数字完全没有落点，也无法做组合级约束。

本模块提供持仓账本与账户状态，是组合风控引擎（``services/risk_engine.py``）
与模拟盘（``services/paper_trading.py``）的持久化底座。

表结构遵循同目录其它 store 的约定：``_connect()`` 复用 ``report_store`` 的
WAL 连接，``db_path()`` 走 ``TA_DB_PATH`` 环境变量，默认落在
``<cwd>/data/tradingagents.db``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .report_store import _connect, init_db


def _init_portfolio() -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL DEFAULT '',
                initial_cash REAL NOT NULL DEFAULT 100000,
                cash REAL NOT NULL DEFAULT 100000,
                currency TEXT NOT NULL DEFAULT 'CNY',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(portfolio_accounts)")
        }
        if "owner" not in columns:
            conn.execute(
                "ALTER TABLE portfolio_accounts ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                sector TEXT DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                avg_cost REAL NOT NULL DEFAULT 0,
                last_price REAL NOT NULL DEFAULT 0,
                stop_loss REAL,
                take_profit REAL,
                opened_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, ticker)
            )
            """
        )
        # 旧库补列：sector 用于行业暴露与行业压力测试。
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(portfolio_positions)")
        }
        if "sector" not in columns:
            conn.execute(
                "ALTER TABLE portfolio_positions ADD COLUMN sector TEXT DEFAULT ''"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                note TEXT,
                trade_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_portfolio_tx_account_date
            ON portfolio_transactions(account_id, trade_date)
            """
        )
        # 每日组合快照：绩效曲线、回撤和"当时仓位"都依赖它。
        # 此前只有实时快照，历史权益无法复原，也没法算组合最大回撤。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL,
                cash REAL NOT NULL DEFAULT 0,
                position_value REAL NOT NULL DEFAULT 0,
                equity REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(account_id, snapshot_date)
            )
            """
        )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 账户
# ---------------------------------------------------------------------------


def create_account(
    name: str = "默认账户",
    initial_cash: float = 100000.0,
    currency: str = "CNY",
    owner: str = "",
) -> dict[str, Any]:
    """创建账户；同名账户已存在时直接返回既有账户。"""
    _init_portfolio()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_accounts
                (name, owner, initial_cash, cash, currency, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                str(owner or ""),
                float(initial_cash),
                float(initial_cash),
                currency,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM portfolio_accounts WHERE name = ?", (name,)
        ).fetchone()
    return dict(row) if row else {}


def list_accounts(owner: str | None = None) -> list[dict[str, Any]]:
    _init_portfolio()
    with _connect() as conn:
        if owner is None:
            rows = conn.execute(
                "SELECT * FROM portfolio_accounts ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portfolio_accounts WHERE owner = ? ORDER BY id",
                (str(owner),),
            ).fetchall()
    return [dict(row) for row in rows]


def get_account(name: str = "默认账户") -> dict[str, Any] | None:
    _init_portfolio()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM portfolio_accounts WHERE name = ?", (name,)
        ).fetchone()
    return dict(row) if row else None


def update_account_cash(
    name: str, cash: float, initial_cash: float | None = None
) -> None:
    """更新账户现金余额。"""
    _init_portfolio()
    with _connect() as conn:
        if initial_cash is None:
            conn.execute(
                "UPDATE portfolio_accounts SET cash = ?, updated_at = ? WHERE name = ?",
                (float(cash), _now(), name),
            )
        else:
            conn.execute(
                """
                UPDATE portfolio_accounts
                SET cash = ?, initial_cash = ?, updated_at = ?
                WHERE name = ?
                """,
                (float(cash), float(initial_cash), _now(), name),
            )


# ---------------------------------------------------------------------------
# 持仓
# ---------------------------------------------------------------------------


def upsert_position(
    account_name: str,
    ticker: str,
    *,
    quantity: float,
    avg_cost: float,
    last_price: float | None = None,
    name: str = "",
    sector: str = "",
    stop_loss: float | None = None,
    take_profit: float | None = None,
    opened_at: str | None = None,
) -> dict[str, Any]:
    """新增或更新一条持仓（按账户 + 代码唯一）。"""
    _init_portfolio()
    account = get_account(account_name)
    if not account:
        raise ValueError(f"账户不存在：{account_name}")
    account_id = int(account["id"])
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_positions
                (account_id, ticker, name, sector, quantity, avg_cost,
                 last_price, stop_loss, take_profit, opened_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, ticker) DO UPDATE SET
                name = excluded.name,
                sector = CASE
                    WHEN excluded.sector IS NULL OR excluded.sector = ''
                    THEN portfolio_positions.sector
                    ELSE excluded.sector
                END,
                quantity = excluded.quantity,
                avg_cost = excluded.avg_cost,
                last_price = excluded.last_price,
                stop_loss = excluded.stop_loss,
                take_profit = excluded.take_profit,
                opened_at = COALESCE(excluded.opened_at, portfolio_positions.opened_at),
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                ticker,
                name,
                sector,
                float(quantity),
                float(avg_cost),
                float(last_price if last_price is not None else avg_cost),
                stop_loss,
                take_profit,
                opened_at or now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT p.* FROM portfolio_positions p
            JOIN portfolio_accounts a ON a.id = p.account_id
            WHERE a.name = ? AND p.ticker = ?
            """,
            (account_name, ticker),
        ).fetchone()
    return dict(row) if row else {}


def list_positions(account_name: str = "默认账户") -> list[dict[str, Any]]:
    _init_portfolio()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.* FROM portfolio_positions p
            JOIN portfolio_accounts a ON a.id = p.account_id
            WHERE a.name = ?
            ORDER BY (p.quantity * p.last_price) DESC, p.ticker
            """,
            (account_name,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_position(account_name: str, ticker: str) -> bool:
    """清空并删除一条持仓。"""
    _init_portfolio()
    account = get_account(account_name)
    if not account:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM portfolio_positions WHERE account_id = ? AND ticker = ?",
            (int(account["id"]), ticker),
        )
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# 交易流水
# ---------------------------------------------------------------------------


def add_transaction(
    account_name: str,
    ticker: str,
    side: str,
    quantity: float,
    price: float,
    fee: float = 0.0,
    realized_pnl: float = 0.0,
    note: str = "",
    trade_date: str = "",
) -> dict[str, Any]:
    """记录一笔成交。"""
    _init_portfolio()
    account = get_account(account_name)
    if not account:
        raise ValueError(f"账户不存在：{account_name}")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_transactions
                (account_id, ticker, side, quantity, price, fee,
                 realized_pnl, note, trade_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(account["id"]),
                ticker,
                side.upper(),
                float(quantity),
                float(price),
                float(fee),
                float(realized_pnl),
                note,
                trade_date or now[:10],
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM portfolio_transactions WHERE id = last_insert_rowid()"
        ).fetchone()
    return dict(row) if row else {}


def list_transactions(
    account_name: str = "默认账户", limit: int = 200
) -> list[dict[str, Any]]:
    _init_portfolio()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT t.* FROM portfolio_transactions t
            JOIN portfolio_accounts a ON a.id = t.account_id
            WHERE a.name = ?
            ORDER BY t.trade_date DESC, t.id DESC
            LIMIT ?
            """,
            (account_name, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 行情刷新 / 每日快照 / 绩效
# ---------------------------------------------------------------------------


def update_position_prices(
    account_name: str, prices: dict[str, float]
) -> list[dict[str, Any]]:
    """按 ``{ticker: price}`` 批量刷新持仓最新价。

    WHY：持仓表里的 ``last_price`` 此前只在录入/成交时写入，组合市值、
    浮盈和仓位风控都会一直停留在旧价格。这里用一次事务批量更新，避免
    逐只持仓打开连接。
    """
    if not prices:
        return list_positions(account_name)
    _init_portfolio()
    account = get_account(account_name)
    if not account:
        return []
    now = _now()
    with _connect() as conn:
        for ticker, price in prices.items():
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            conn.execute(
                """
                UPDATE portfolio_positions
                SET last_price = ?, updated_at = ?
                WHERE account_id = ? AND ticker = ?
                """,
                (value, now, int(account["id"]), str(ticker)),
            )
    return list_positions(account_name)


def save_portfolio_snapshot(
    account_name: str,
    snapshot_date: str,
    cash: float,
    position_value: float,
    equity: float,
) -> dict[str, Any]:
    """写入/覆盖某账户某日的组合快照。"""
    _init_portfolio()
    account = get_account(account_name)
    if not account:
        raise ValueError(f"账户不存在：{account_name}")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (account_id, snapshot_date, cash, position_value, equity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, snapshot_date) DO UPDATE SET
                cash = excluded.cash,
                position_value = excluded.position_value,
                equity = excluded.equity,
                created_at = excluded.created_at
            """,
            (
                int(account["id"]),
                str(snapshot_date)[:10],
                float(cash),
                float(position_value),
                float(equity),
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT s.* FROM portfolio_snapshots s
            JOIN portfolio_accounts a ON a.id = s.account_id
            WHERE a.name = ? AND s.snapshot_date = ?
            """,
            (account_name, str(snapshot_date)[:10]),
        ).fetchone()
    return dict(row) if row else {}


def list_portfolio_snapshots(
    account_name: str = "默认账户", limit: int = 500
) -> list[dict[str, Any]]:
    """按日期升序返回组合快照，供绩效曲线与回撤计算使用。"""
    _init_portfolio()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM portfolio_snapshots s
            JOIN portfolio_accounts a ON a.id = s.account_id
            WHERE a.name = ?
            ORDER BY s.snapshot_date DESC
            LIMIT ?
            """,
            (account_name, int(limit)),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]
