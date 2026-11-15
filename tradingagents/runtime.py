"""进程级运行时共享对象。

分析并发模型
------------
历史实现只有一把进程级 ``threading.Lock``（:data:`ANALYSIS_LOCK`），整个进程同时
只能跑一个分析，并且调用方拿不到"排队中"的状态反馈。

现在的分工是：

* :data:`ANALYSIS_LOCK` 仍然存在，语义保持不变：可以 ``with ANALYSIS_LOCK:``，
  也可以 ``acquire()`` / ``release()`` / ``locked()``。旧调用方
  （``tradingagents/services/runner.py``、``webui/backend/server.py``）无需修改。
* 这把锁变成 **按配置键（analysis key）细分** 的可重入锁：默认键为 ``None``，
  完全等价于历史行为（全局串行）；只有当调用方显式指定不同的配置键时，
  不同配置之间才允许并发（详见 :func:`analysis_key`）。
  之所以按"配置"而不是按"股票"分键，是因为 TradingAgents 的数据源配置与记忆
  是进程级全局状态，同一套配置并发执行会互相覆盖。
* 真正的"排队 + 有界并发 + 取消 + 回收"能力由
  :mod:`tradingagents.services.job_queue` 的 worker 池提供：任务先落库
  （``jobs.status='queued'``），再由固定数量的 worker 认领执行。
  全局锁只作为"共享配置"的安全网，不再承担排队职责。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# 当前线程/协程绑定的分析配置键；None 表示"默认全局域"。
_ANALYSIS_KEY: ContextVar[str | None] = ContextVar(
    "tradingagents_analysis_key", default=None
)

#: 当前分析日期（YYYY-MM-DD）。工具据此过滤“未来数据”，
#: 例如公告/研报按披露日、宏观数据按发布日。
_CURRENT_TRADE_DATE: ContextVar[str | None] = ContextVar(
    "tradingagents_trade_date", default=None
)


def set_current_trade_date(trade_date: str | None) -> str | None:
    previous = _CURRENT_TRADE_DATE.get()
    _CURRENT_TRADE_DATE.set(str(trade_date)[:10] if trade_date else None)
    return previous


def get_current_trade_date() -> str | None:
    return _CURRENT_TRADE_DATE.get()


def clear_current_trade_date() -> None:
    _CURRENT_TRADE_DATE.set(None)


class AnalysisLock:
    """按配置键细分的可重入分析锁。

    与 ``threading.Lock`` 的差异：

    * **可重入**：同一线程重复 ``acquire()`` 不会死锁（计数式释放）。
    * **按 key 分域**：不同 key 的持有者互不阻塞；``locked()`` 语义保持为
      "当前是否有任何分析正在运行"。
    * ``acquire(blocking=True, timeout=-1)`` 与 ``threading.Lock`` 参数一致。

    锁本身只提供"共享配置互斥"，**不负责排队**：排队由任务队列负责。
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        # key -> [owner_ident, depth]
        self._holders: dict[str | None, list] = {}

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _key() -> str | None:
        return _ANALYSIS_KEY.get()

    # ------------------------------------------------------------------ 公开
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        key = self._key()
        ident = threading.get_ident()
        with self._cond:
            holder = self._holders.get(key)
            if holder is not None and holder[0] == ident:
                holder[1] += 1
                return True
            deadline = (
                None
                if timeout is None or timeout < 0
                else time.monotonic() + float(timeout)
            )
            while True:
                if key not in self._holders:
                    self._holders[key] = [ident, 1]
                    return True
                if not blocking:
                    return False
                if deadline is None:
                    self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)

    def release(self) -> None:
        key = self._key()
        ident = threading.get_ident()
        with self._cond:
            holder = self._holders.get(key)
            if holder is None or holder[0] != ident:
                raise RuntimeError("cannot release an un-acquired analysis lock")
            holder[1] -= 1
            if holder[1] <= 0:
                del self._holders[key]
            self._cond.notify_all()

    def locked(self) -> bool:
        """是否存在任意配置域的分析正在运行（兼容历史语义）。"""
        with self._cond:
            return bool(self._holders)

    def holder_keys(self) -> list[str | None]:
        """当前被占用的配置键列表（诊断用）。"""
        with self._cond:
            return list(self._holders)

    def __enter__(self) -> AnalysisLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __repr__(self) -> str:  # pragma: no cover - 诊断信息
        return f"<AnalysisLock holders={len(self._holders)}>"


# 分析任务串行锁：TradingAgents 的配置与记忆是进程级全局状态，
# 同一套配置下多个分析并发执行会互相覆盖数据源配置，因此默认仍按"全局域"串行。
# 需要让不同配置并发时，用 analysis_key("config-id") 包住分析即可。
ANALYSIS_LOCK = AnalysisLock()


def current_analysis_key() -> str | None:
    """返回当前绑定的分析配置键（None 表示默认全局域）。"""
    return _ANALYSIS_KEY.get()


def set_analysis_key(key: str | None) -> object:
    """绑定当前上下文的分析配置键，返回可用于 :func:`reset_analysis_key` 的令牌。"""
    return _ANALYSIS_KEY.set(key)


def reset_analysis_key(token: object) -> None:
    """恢复上一个分析配置键。"""
    try:
        _ANALYSIS_KEY.reset(token)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - 令牌来自其它上下文时忽略
        _ANALYSIS_KEY.set(None)


@contextmanager
def analysis_key(key: str | None) -> Iterator[str | None]:
    """上下文管理器：在代码块内把分析绑定到指定的配置键。

    >>> with analysis_key("provider-a"):
    ...     with ANALYSIS_LOCK:      # 只与同 key 的分析互斥
    ...         ...
    """
    token = _ANALYSIS_KEY.set(key)
    try:
        yield key
    finally:
        reset_analysis_key(token)
