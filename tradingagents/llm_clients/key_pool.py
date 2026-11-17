"""同一模型的 API Key 池与轮询调用。

只解决“多个 Key 时提高并发/可用性”，不改变模型、提示词或数据口径。
没有配置多 Key 时，调用方应继续使用原来的单 Key LLM，不经过本模块。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from langchain_core.runnables import RunnableLambda


def _split_keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        text = str(value)
        for separator in (",", ";", "\n", "\r", "\t", " "):
            text = text.replace(separator, "\n")
        raw = text.splitlines()
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def resolve_api_keys(
    provider: str,
    base_url: str | None = None,
    explicit: Any = None,
) -> list[str]:
    """解析同一提供商的多个 Key。

    优先级：
    1. 显式传入且包含多个 Key；
    2. 显式传入的单个 Key（尊重调用方）；
    3. 环境变量中的多 Key（``*_API_KEYS``）与单 Key 别名。
    """
    explicit_keys = _split_keys(explicit)
    if len(explicit_keys) > 1:
        return explicit_keys

    from .auth import _env_aliases

    keys: list[str] = []
    seen: set[str] = set()
    for name in _env_aliases(provider, base_url):
        for key in _split_keys(os.getenv(name)):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    if keys:
        return keys
    if explicit_keys:
        return explicit_keys
    return []


def is_transient_llm_error(exc: BaseException) -> bool:
    """是否是可以通过换 Key/重试解决的临时错误。"""
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    markers = (
        "ratelimit",
        "rate limit",
        "429",
        "overloaded",
        "timeout",
        "connection",
        "connecterror",
        "apiconnection",
        "apitimeout",
        "internalserver",
        "service unavailable",
        "502",
        "503",
        "504",
        "temporarily",
    )
    return any(marker in text for marker in markers)


class KeyPool:
    """线程安全的 Key 轮询池，失败 Key 进入短时冷却。"""

    def __init__(self, keys: list[str], cooldown_seconds: float = 60.0):
        self.keys = list(keys)
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._cooldown_until: dict[int, float] = {}
        self._cursor = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.keys)

    def acquire(self) -> tuple[int, str]:
        with self._lock:
            now = time.time()
            count = len(self.keys)
            if count == 0:
                raise RuntimeError("API Key 池为空")
            best_index = self._cursor % count
            best_until = self._cooldown_until.get(best_index, 0.0)
            for offset in range(count):
                index = (self._cursor + offset) % count
                until = self._cooldown_until.get(index, 0.0)
                if until <= now:
                    self._cursor = (index + 1) % count
                    return index, self.keys[index]
                if until < best_until:
                    best_index, best_until = index, until
            # 全部冷却时使用最早恢复的 Key，避免直接失败。
            self._cursor = (best_index + 1) % count
            return best_index, self.keys[best_index]

    def mark_failure(self, index: int, seconds: float | None = None) -> None:
        with self._lock:
            wait = self.cooldown_seconds if seconds is None else max(1.0, seconds)
            self._cooldown_until[index] = time.time() + wait

    def mark_success(self, index: int) -> None:
        with self._lock:
            self._cooldown_until.pop(index, None)


class RotatingChatModel:
    """把同一个模型的多 Key 客户端包装成可被 LangChain 管道调用的对象。"""

    def __init__(self, base_llm, pool: KeyPool):
        self.base_llm = base_llm
        self.pool = pool

    def __getattr__(self, name: str):
        return getattr(self.base_llm, name)

    @staticmethod
    def _to_messages(value):
        if hasattr(value, "to_messages"):
            return value.to_messages()
        if isinstance(value, dict) and "messages" in value:
            return value["messages"]
        return value

    def _invoke_once(self, index: int, key: str, messages, tools, tool_kwargs, config):
        client = self.base_llm.model_copy(update={"api_key": key})
        runnable = client.bind_tools(tools, **tool_kwargs) if tools else client
        return runnable.invoke(messages, config=config) if config else runnable.invoke(messages)

    def _invoke(self, value, tools=None, tool_kwargs=None, config=None):
        messages = self._to_messages(value)
        attempts = max(1, len(self.pool))
        last_result = None
        last_error: BaseException | None = None
        for _ in range(attempts):
            index, key = self.pool.acquire()
            try:
                result = self._invoke_once(
                    index,
                    key,
                    messages,
                    tools,
                    tool_kwargs or {},
                    config,
                )
            except Exception as exc:
                last_error = exc
                if not is_transient_llm_error(exc):
                    raise
                self.pool.mark_failure(index)
                continue
            content = str(getattr(result, "content", "") or "").strip()
            tool_calls = getattr(result, "tool_calls", None) or []
            if content or tool_calls:
                self.pool.mark_success(index)
                return result
            # 空正文（常见于思考 token 吃光额度）：换下一个 Key 再试。
            self.pool.mark_failure(index, seconds=30)
            last_result = result
        if last_result is not None:
            return last_result
        if last_error is not None:
            raise last_error
        raise RuntimeError("所有 API Key 均未返回有效内容")

    def bind_tools(self, tools, **kwargs):
        def _bound(value, config=None):
            return self._invoke(
                value, tools=tools, tool_kwargs=kwargs, config=config
            )

        return RunnableLambda(_bound)

    def invoke(self, input, config=None, **kwargs):
        return self._invoke(input, config=config)


__all__ = [
    "KeyPool",
    "RotatingChatModel",
    "is_transient_llm_error",
    "resolve_api_keys",
]
