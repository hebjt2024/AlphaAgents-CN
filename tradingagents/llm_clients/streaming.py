"""LLM 流式调用辅助。

把所有 LLM 客户端（``NormalizedChatOpenAI`` / ``ChatAnthropic`` / ``ChatGoogleGenerativeAI``）
已有的 ``.stream()`` 能力转换成一致的接口：

- ``invoke_stream(runnable, input)``：像 ``invoke`` 一样返回最终消息
  （``.content`` 含全部文本、``.tool_calls`` 保持原样），但生成过程中
  每个文本块会经 ``emit_token`` 广播给登记的 sink；
- ``add_stream_sink(fn)`` / ``remove_stream_sink(fn)``：登记/注销全局 sink
  （如 WebUI / CLI 的进度显示器），``fn(text)`` 收到文本块；
- ``emit_token(text)``：广播到全部 sink（sink 抛异常不影响主流程）。

策略（结果与 ``invoke`` 一致，流式只作为观察与进度通道）：

1. runnable 有可调用 ``.stream()``：逐块收集文本并 emit，累积最终消息；
2. 流式异常 / 无文本结果：回退到 ``runnable.invoke(input, **kwargs)``。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from .base_client import flatten_text, normalize_content

#: 全局流式 token sink 列表。每个 sink 是 ``fn(text)`` 形式的可调用对象。
_SINKS: list[Callable[[str], None]] = []


def add_stream_sink(sink: Callable[[str], None]) -> None:
    """登记全局流式 token sink（同一 sink 重复登记会自动去重）。"""
    if sink not in _SINKS:
        _SINKS.append(sink)


def remove_stream_sink(sink: Callable[[str], None]) -> None:
    """注销一个已登记的全局流式 token sink。"""
    while sink in _SINKS:
        _SINKS.remove(sink)


def emit_token(text: str) -> None:
    """广播一个文本块给全部 sink；空文本直接忽略；sink 异常被吞掉。"""
    if not text:
        return
    for sink in list(_SINKS):
        try:
            sink(text)
        except Exception:
            pass


def _merge_chunks(final: Any, chunk: Any) -> Any | None:
    """尝试把两个流式块合并（LangChain message 支持 ``+``）。"""
    try:
        return final + chunk
    except TypeError:
        return chunk if getattr(chunk, "content", None) else None


def _finalize(final: Any, full_text: str) -> Any:
    """确保返回的消息 content 含全部文本，并归一化为纯字符串。"""
    if final is None:
        return SimpleNamespace(content=full_text)
    try:
        final = normalize_content(final)
    except Exception:
        pass
    current = getattr(final, "content", None)
    if isinstance(current, str):
        if len(current) < len(full_text):
            final.content = full_text
    elif not current and full_text:
        final.content = full_text
    return final


def invoke_stream(runnable: Any, input: Any, **kwargs: Any) -> Any:
    """调用 runnable，优先以流式方式执行，token 经 :func:`emit_token` 广播。

    参数与 ``runnable.invoke`` 一致；返回值形状与 ``invoke`` 一致
    （``.content`` 为含全部文本的字符串，``.tool_calls`` 可用）。
    """
    stream_fn = getattr(runnable, "stream", None)
    if callable(stream_fn):
        try:
            parts: list[str] = []
            final = None
            for chunk in stream_fn(input, **kwargs):
                text = flatten_text(chunk)
                if text:
                    parts.append(text)
                    emit_token(text)
                if final is None:
                    final = chunk
                else:
                    merged = _merge_chunks(final, chunk)
                    if merged is not None:
                        final = merged
            if parts:
                return _finalize(final, "".join(parts))
        except Exception:
            # 流式失败回退到普通调用，保证业务不受影响
            pass
    return runnable.invoke(input, **kwargs)


__all__ = [
    "add_stream_sink",
    "emit_token",
    "invoke_stream",
    "remove_stream_sink",
]
