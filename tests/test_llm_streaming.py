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

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tradingagents.llm_clients.base_client import observe_llm_call
from tradingagents.llm_clients.streaming import (
    add_stream_sink,
    emit_token,
    invoke_stream,
    remove_stream_sink,
)
from tradingagents.services.observability import (
    BudgetExceededError,
    RunMetrics,
    clear_current_metrics,
    set_current_metrics,
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
        self.stream_count = 0
        self.invoke_called = False

    def stream(self, input, **kwargs):
        self.stream_called = True
        self.stream_count += 1
        if self.error_on_stream:
            raise RuntimeError("boom")
        yield from self.chunks

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


class _ChainRunnable:
    """带 bound / steps 的链式 runnable，用于校验身份递归识别。"""

    def __init__(self, *, bound=None, steps=None, text="链式输出"):
        if bound is not None:
            self.bound = bound
        if steps is not None:
            self.steps = steps
        self.text = text
        self.stream_calls = 0
        self.invoke_called = False

    def stream(self, input, **kwargs):
        self.stream_calls += 1
        yield _Chunk(self.text)

    def invoke(self, input, **kwargs):
        self.invoke_called = True
        return SimpleNamespace(content=self.text, tool_calls=[])


class _ObservedStreamLLM(_FakeStreamLLM):
    """模拟真实客户端：invoke 也会走 observe_llm_call 记录一次调用。"""

    def __init__(self, *args, usage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usage = usage or {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }

    def invoke(self, input, **kwargs):
        self.invoke_called = True
        with observe_llm_call(
            provider=self.observability_provider,
            model=self.observability_model,
            prompt_input=input,
        ) as box:
            response = SimpleNamespace(
                content=self.response_text,
                tool_calls=[],
                usage_metadata=dict(self.usage),
            )
            box["response"] = response
            return response


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

    def test_pure_tool_calls_are_a_valid_stream_result(self):
        llm = _FakeStreamLLM(
            chunks=[_Chunk("", tool_calls=[{"id": "c1"}])],
            response_text="should-not-be-used",
        )
        result = invoke_stream(llm, "hi")
        self.assertFalse(llm.invoke_called)
        self.assertEqual(result.tool_calls, [{"id": "c1"}])

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

    def test_successful_stream_records_final_usage_once(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        chunk = _Chunk("分析完成")
        chunk.usage_metadata = {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        }
        llm = _FakeStreamLLM(chunks=[chunk])
        llm.observability_provider = "openai"
        llm.observability_model = "gpt-4o-mini"

        result = invoke_stream(llm, "hi")

        self.assertEqual(result.content, "分析完成")
        self.assertFalse(llm.invoke_called)
        self.assertEqual(metrics.totals["call_count"], 1)
        self.assertEqual(metrics.totals["total_tokens"], 150)
        self.assertGreaterEqual(metrics.llm_calls[0]["latency_ms"], 0)

    def test_stream_without_observability_attributes_is_noop(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        llm = _FakeStreamLLM(chunks=[_Chunk("ok")])

        invoke_stream(llm, "hi")

        self.assertEqual(metrics.totals["call_count"], 0)

    def test_failed_stream_fallback_does_not_double_count_success(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        llm = _FakeStreamLLM(error_on_stream=True)
        llm.observability_provider = "openai"
        llm.observability_model = "gpt-4o-mini"

        result = invoke_stream(llm, "hi")

        self.assertEqual(result.content, "final")
        self.assertTrue(llm.invoke_called)
        self.assertLessEqual(
            sum(1 for call in metrics.llm_calls if call.get("ok")),
            1,
        )

    def test_bound_runnable_identity_is_recorded_once(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        chain = _ChainRunnable(
            bound=SimpleNamespace(
                observability_provider="openai",
                observability_model="gpt-4o-mini",
            )
        )

        result = invoke_stream(chain, "hi")

        self.assertEqual(result.content, "链式输出")
        self.assertFalse(chain.invoke_called)
        self.assertEqual(metrics.totals["call_count"], 1)
        self.assertEqual(metrics.llm_calls[0]["provider"], "openai")
        self.assertEqual(metrics.llm_calls[0]["model"], "gpt-4o-mini")

    def test_sequence_steps_identity_is_recorded_once(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        inner = SimpleNamespace(
            bound=SimpleNamespace(
                observability_provider="anthropic",
                observability_model="claude-sonnet-4-5",
            )
        )
        chain = _ChainRunnable(steps=[object(), inner])

        invoke_stream(chain, "hi")

        self.assertEqual(metrics.totals["call_count"], 1)
        self.assertEqual(metrics.llm_calls[0]["provider"], "anthropic")
        self.assertEqual(metrics.llm_calls[0]["model"], "claude-sonnet-4-5")

    def test_budget_exceeded_propagates_and_streams_once(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        chunk = _Chunk("很长的输出")
        chunk.usage_metadata = {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        }
        llm = _FakeStreamLLM(chunks=[chunk])
        llm.observability_provider = "openai"
        llm.observability_model = "gpt-4o-mini"

        with patch.dict(os.environ, {"LLM_MAX_TOKENS_PER_RUN": "100"}):
            with self.assertRaises(BudgetExceededError):
                invoke_stream(llm, "hi")

        self.assertEqual(llm.stream_count, 1)
        self.assertFalse(llm.invoke_called)

    def test_failed_stream_then_invoke_records_both_attempts(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        llm = _ObservedStreamLLM(error_on_stream=True, response_text="final")
        llm.observability_provider = "openai"
        llm.observability_model = "gpt-4o-mini"

        result = invoke_stream(llm, "hi")

        self.assertEqual(result.content, "final")
        self.assertTrue(llm.invoke_called)
        self.assertEqual(len(metrics.llm_calls), 2)
        self.assertEqual(
            sorted(call["ok"] for call in metrics.llm_calls),
            [False, True],
        )
        self.assertEqual(metrics.totals["call_count"], 2)



if __name__ == "__main__":
    unittest.main()
