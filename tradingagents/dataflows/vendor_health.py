"""数据源健康度：限流熔断与冷却。

WHY 需要这一层
--------------
东方财富（AkShare 背后的实际数据源）是按 **IP** 限流的。一旦触发，官方口径是
"等 5 分钟，反复触发可能要 40 分钟以上；等待期间不要重复调用，否则会延长限流"。

但本系统之前的做法恰好相反：每次取数都会连续重试 3 次（指数退避），4 个分析师
又会并行调用同一批接口 —— 于是一次限流会被放大成十几次请求，反过来延长封禁。

本模块提供一个**进程内的熔断器**：某个"数据源 + 数据项"被判定为限流后，
在冷却期内直接跳过，不再发起任何请求；冷却结束自动恢复（半开）。

设计取舍
--------
1. **按 (vendor, method) 隔离**：push2 被限流不代表 push2his 也被限流，
   按数据项隔离可以少误伤可用接口。
2. **进程内**：WebUI / 定时任务都在同一进程内跑分析，进程内状态已能覆盖。
   跨进程共享需要落库，属于后续优化，不在本模块范围内。
3. **不吞异常**：这里只决定"要不要跳过"，具体失败信息仍由路由层拼装。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


#: 限流冷却时间（秒）。与东方财富官方提示的"约 5 分钟"对齐。
RATE_LIMIT_COOLDOWN = _env_float("DATA_VENDOR_COOLDOWN_SECONDS", 300.0)

#: 请求超时冷却时间（秒）。超时多为瞬时网络问题，短冷却即可。
TIMEOUT_COOLDOWN = _env_float("DATA_VENDOR_TIMEOUT_COOLDOWN_SECONDS", 60.0)

#: 其它硬错误冷却时间（秒）。
ERROR_COOLDOWN = _env_float("DATA_VENDOR_ERROR_COOLDOWN_SECONDS", 60.0)

#: 总开关：环境变量 DATA_VENDOR_BREAKER=0 可关闭熔断（排障时用）。
BREAKER_ENABLED = _env_bool("DATA_VENDOR_BREAKER", True)


class _VendorState:
    """单个 (vendor, method) 的熔断状态。"""

    __slots__ = ("until", "reason", "failures")

    def __init__(self) -> None:
        self.until: float = 0.0
        self.reason: str = ""
        self.failures: int = 0


_states: dict[tuple[str, str], _VendorState] = {}
_lock = threading.Lock()


def _key(vendor: str, method: str | None) -> tuple[str, str]:
    return (str(vendor or ""), str(method or ""))


def _cooldown_for(reason: str) -> float:
    if reason == "rate_limited":
        return RATE_LIMIT_COOLDOWN
    if reason == "timeout":
        return TIMEOUT_COOLDOWN
    return ERROR_COOLDOWN


def is_available(vendor: str, method: str | None = None) -> bool:
    """当前是否允许调用该数据源（冷却期内返回 False）。"""
    if not BREAKER_ENABLED:
        return True
    key = _key(vendor, method)
    now = time.time()
    with _lock:
        state = _states.get(key)
        if state is None:
            return True
        if state.until <= now:
            # 冷却结束：清理状态，恢复（半开）调用
            _states.pop(key, None)
            return True
        return False


def cooldown_remaining(vendor: str, method: str | None = None) -> float:
    """剩余冷却秒数（未熔断返回 0）。"""
    key = _key(vendor, method)
    now = time.time()
    with _lock:
        state = _states.get(key)
        if state is None or state.until <= now:
            return 0.0
        return round(state.until - now, 1)


def mark_failure(vendor: str, method: str | None, reason: str) -> float:
    """记录一次失败，并按原因开启冷却。

    返回本次采用的冷却秒数（0 表示未开启冷却）。
    """
    if not BREAKER_ENABLED:
        return 0.0
    cooldown = _cooldown_for(reason)
    if cooldown <= 0:
        return 0.0
    key = _key(vendor, method)
    now = time.time()
    with _lock:
        state = _states.get(key) or _VendorState()
        # 已有更长冷却时不要缩短
        if state.until > now:
            return round(state.until - now, 1)
        state.failures += 1
        state.until = now + cooldown
        state.reason = reason
        _states[key] = state
        failures = state.failures
    # 预期内的限流用 INFO：这不是故障，只是需要等待。
    # 真正"整条链路都失败"才由路由层打 WARNING。
    if reason == "rate_limited":
        logger.info(
            "数据源 %s(%s) 触发限流，冷却 %.0fs 内跳过（第 %d 次）",
            vendor,
            method or "-",
            cooldown,
            failures,
        )
    else:
        logger.info(
            "数据源 %s(%s) 失败（%s），冷却 %.0fs 内跳过",
            vendor,
            method or "-",
            reason,
            cooldown,
        )
    return cooldown


def mark_success(vendor: str, method: str | None = None) -> None:
    """记录一次成功，清除该数据项的熔断状态。"""
    key = _key(vendor, method)
    with _lock:
        _states.pop(key, None)


def reset() -> None:
    """清空全部熔断状态（测试用）。"""
    with _lock:
        _states.clear()


def snapshot() -> dict[str, dict[str, Any]]:
    """返回当前熔断快照，供健康检查/接口展示。"""
    now = time.time()
    with _lock:
        return {
            f"{vendor}::{method or '-'}": {
                "remaining": round(max(0.0, state.until - now), 1),
                "reason": state.reason,
                "failures": state.failures,
            }
            for (vendor, method), state in _states.items()
            if state.until > now
        }


__all__ = [
    "BREAKER_ENABLED",
    "RATE_LIMIT_COOLDOWN",
    "TIMEOUT_COOLDOWN",
    "ERROR_COOLDOWN",
    "cooldown_remaining",
    "is_available",
    "mark_failure",
    "mark_success",
    "reset",
    "snapshot",
]
