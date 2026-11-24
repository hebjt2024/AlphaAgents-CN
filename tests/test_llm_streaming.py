"""LLM 流式输出（invoke_stream + token sink）单元测试。

覆盖：
1. invoke_stream 流式路径：逐块 emit、累积后 content 完整、不调用 invoke；
2. runnable 无 stream()：回退到 invoke；
3. 流式抛异常：回退到 invoke；
4. sink 接收 token，sink 抛异常被吞掉；
5. tool_calls 属性透传；
6. 不支持 + 合并的伪对象：content 仍为完整文本。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tradingagents.llm_clients.streaming import (
    add_stream_sink,
    emit_token,
    invoke_stream,
    remove_stream_sink,
)


class _Chunk:
    """支持 ``+`` 合并的流式块（LangChain message 行为的简化版）。"""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = list(tool_calls or [])

    def __add__(self, other):
        return _Chunk(
            self.content + other.content,
            self.tool_calls + other.tool_calls,
        )


class _FakeStreamLLM:
    """伪 LLM：同时有 stream / invoke 两条路径。"""

    def __init__(self, chunks=None, error_on_stream=False, response_text="final"):
        self.chunks = chunks if chunks is not None else []
        self.error_on_stream = error_on_stream
        self.response_text = response_text
        self.stream_called = False
        self.invoke_called = False

    def stream(self, input, **kwargs):
        self.stream_called = True
        if self.error_on_stream:
            raise RuntimeError("boom")
        for chunk in self.chunks:
            yield chunk

    def invoke(self, input, **kwargs):
        self.invoke_called = True
        return SimpleNamespace(content=self.response_text, tool_calls=[])


class _FakeNonStreamLLM:
    """伪 LLM：没有 stream，只有 invoke。"""

    def __init__(self, response_text="final"):
        self.response_text = response_text
        self.invoke_called = False

    def invoke(self, input, **kwargs):
        self.invoke_called = True
        return SimpleNamespace(content=self.response_text, tool_calls=[])


class InvokeStreamTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        add_stream_sink(self._sink)

    def tearDown(self):
        remove_stream_sink(self._sink)

    def _sink(self, text):
        self.received.append(text)

    def test_stream_path_accumulates_and_emits(self):
        llm = _FakeStreamLLM(chunks=[_Chunk("你"), _Chunk("好"), _Chunk("世界")])
        result = invoke_stream(llm, "hi")
        self.assertEqual(result.content, "你好世界")
        self.assertEqual(self.received, ["你", "好", "世界"])
        # 流式路径成功：不调用 invoke
        self.assertTrue(llm.stream_called)
        self.assertFalse(llm.invoke_called)

    def test_fallback_when_no_stream(self):
        llm = _FakeNonStreamLLM(response_text="full")
        result = invoke_stream(llm, "hi")
        self.assertTrue(llm.invoke_called)
        self.assertEqual(result.content, "full")

    def test_fallback_when_stream_raises(self):
        llm = _FakeStreamLLM(chunks=[], error_on_stream=True)
        result = invoke_stream(llm, "hi")
        self.assertTrue(llm.invoke_called)
        self.assertEqual(result.content, "final")

    def test_fallback_when_stream_has_no_text(self):
        # 纯 tool_calls 场景（文本为空）→ invoke 回退，结果保留 tool_calls
        llm = _FakeStreamLLM(
            chunks=[_Chunk("", tool_calls=[{"id": "c1"}])],
            response_text="should-not-be-used",
        )
        result = invoke_stream(llm, "hi")
        self.assertTrue(llm.invoke_called)

    def test_tool_calls_preserved_in_merged_message(self):
        tool = {"id": "c1", "name": "get_stock_data"}
        llm = _FakeStreamLLM(chunks=[_Chunk("报告", tool_calls=[tool])])
        result = invoke_stream(llm, "hi")
        self.assertEqual(result.content, "报告")
        self.assertEqual(result.tool_calls, [tool])

    def test_chunk_without_add_support(self):
        # 对象不支持 + 合并时：用 text 侧的完整文本兜底
        class PlainChunk:
            def __init__(self, content):
                self.content = content

        llm = _FakeStreamLLM(chunks=[PlainChunk("a"), PlainChunk("b")])
        result = invoke_stream(llm, "hi")
        self.assertEqual(result.content, "ab")
        self.assertEqual(self.received, ["a", "b"])

    def test_emit_token_dedupes_empty(self):
        emit_token("")
        emit_token(None)
        self.assertEqual(self.received, [])

    def test_emit_token_ignores_sink_exceptions(self):
        def bad_sink(_):
            raise ValueError("sink broken")

        add_stream_sink(bad_sink)
        try:
            emit_token("x")  # 不抛异常
            self.assertEqual(self.received, ["x"])  # 正常 sink 仍收到
        finally:
            remove_stream_sink(bad_sink)


if __name__ == "__main__":
    unittest.main()
