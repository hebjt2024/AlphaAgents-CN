"""P2 可观测性测试：token / 成本 / 延迟计量。

全部离线运行：LLM 客户端用假的实现替身，不发起任何网络请求。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tradingagents.llm_clients.base_client import (
    BaseLLMClient,
    estimate_tokens,
    extract_token_usage,
    observe_llm_call,
)
from tradingagents.services.observability import (
    MODEL_PRICING,
    RunMetrics,
    clear_current_metrics,
    estimate_cost,
    get_current_metrics,
    record_llm_call,
    set_current_metrics,
    stage_timer,
)
from tradingagents.storage import (
    aggregate_run_logs,
    get_run_log,
    list_run_logs,
    save_run_log,
)


class _FakeResponse:
    """模拟 LangChain 的 AIMessage：只暴露 usage_metadata。"""

    def __init__(self, content="", usage_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata
        self.response_metadata = {}


class _FakeClient(BaseLLMClient):
    """假的 LLM 客户端：invoke 直接返回预置响应，不触网。"""

    provider = "openai"
    model = "gpt-4o-mini"

    def __init__(self, response=None, error=None, **kwargs):
        super().__init__(self.model, None, **kwargs)
        self._response = response
        self._error = error

    def get_llm(self):
        return self

    def validate_model(self) -> bool:
        return True

    def invoke(self, input, config=None, **kwargs):
        with observe_llm_call(
            provider=self.provider,
            model=self.model,
            prompt_input=input,
        ) as obs:
            if self._error is not None:
                raise self._error
            response = self._response
            obs["response"] = response
            return response


class RunMetricsTests(unittest.TestCase):
    def setUp(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)

    def test_accumulates_tokens_cost_and_calls(self):
        metrics = RunMetrics(ticker="600519", trade_date="2026-11-12")
        metrics.add_call(
            stage="market",
            model="gpt-4o-mini",
            provider="openai",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=120.0,
        )
        metrics.add_call(
            stage="market",
            model="gpt-4o-mini",
            provider="openai",
            prompt_tokens=2000,
            completion_tokens=1000,
            latency_ms=80.0,
        )

        self.assertEqual(metrics.totals["call_count"], 2)
        self.assertEqual(metrics.totals["prompt_tokens"], 3000)
        self.assertEqual(metrics.totals["completion_tokens"], 1500)
        self.assertEqual(metrics.totals["total_tokens"], 4500)
        self.assertGreater(metrics.totals["estimated_cost"], 0)
        self.assertEqual(len(metrics.llm_calls), 2)

        # 阶段维度也同步累加
        stage = metrics.stages["market"]
        self.assertEqual(stage["calls"], 2)
        self.assertEqual(stage["tokens"], 4500)
        self.assertGreater(stage["cost"], 0)

    def test_to_dict_is_json_serialisable(self):
        import json

        metrics = RunMetrics(ticker="000001", source="cli")
        metrics.add_call(
            model="claude-sonnet-4-6",
            provider="anthropic",
            prompt_tokens=10,
            completion_tokens=20,
            latency_ms=5.0,
        )
        payload = metrics.to_dict()
        # 可以直接序列化
        json.dumps(payload)
        self.assertEqual(payload["totals"]["call_count"], 1)
        self.assertEqual(payload["llm_calls"][0]["model"], "claude-sonnet-4-6")
        self.assertTrue(payload["run_id"])
        # 供应商 / 模型会回填到顶层，方便落库与报表
        self.assertEqual(payload["provider"], "anthropic")
        self.assertEqual(payload["model"], "claude-sonnet-4-6")


class EstimateCostTests(unittest.TestCase):
    def test_known_model_returns_positive_cost(self):
        estimate = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
        self.assertGreater(estimate, 0)
        self.assertFalse(estimate.is_estimated)
        self.assertEqual(estimate.currency, "USD")

        expected = (
            MODEL_PRICING["gpt-4o-mini"]["prompt"]
            + MODEL_PRICING["gpt-4o-mini"]["completion"]
        )
        self.assertAlmostEqual(float(estimate), expected, places=6)

    def test_unknown_model_is_flagged_estimated(self):
        estimate = estimate_cost("totally-unknown-model-xyz", 1000, 1000)
        self.assertGreater(estimate, 0)
        self.assertTrue(estimate.is_estimated)

    def test_minimax_uses_cny_table(self):
        estimate = estimate_cost("MiniMax-M3", 1_000_000, 1_000_000)
        self.assertGreater(estimate, 0)
        self.assertEqual(estimate.currency, "CNY")
        self.assertFalse(estimate.is_estimated)

    def test_pricing_override(self):
        table = {"my-model": {"prompt": 10.0, "completion": 20.0}}
        estimate = estimate_cost("my-model", 1_000_000, 1_000_000, pricing=table)
        self.assertAlmostEqual(float(estimate), 30.0, places=6)
        self.assertFalse(estimate.is_estimated)


class TokenEstimatorTests(unittest.TestCase):
    def test_chinese_text(self):
        # 12 个汉字 -> 约 12 token
        text = "今天天气不错适合买入股票"
        self.assertEqual(estimate_tokens(text), len(text))

    def test_english_text(self):
        # 拉丁字符按 4 字符 / token 估算
        self.assertEqual(estimate_tokens("a" * 40), 10)
        self.assertEqual(estimate_tokens(""), 0)

    def test_mixed_text(self):
        text = "AI" + "股" * 10
        self.assertEqual(estimate_tokens(text), 10 + 1)

    def test_extract_token_usage_prefers_usage_metadata(self):
        response = _FakeResponse(
            content="hi",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        )
        prompt, completion, estimated = extract_token_usage(response)
        self.assertEqual((prompt, completion), (11, 7))
        self.assertFalse(estimated)

    def test_extract_token_usage_missing_returns_estimated_flag(self):
        response = _FakeResponse(content="hi", usage_metadata=None)
        prompt, completion, estimated = extract_token_usage(response)
        self.assertEqual((prompt, completion), (0, 0))
        self.assertTrue(estimated)


class StageTimerTests(unittest.TestCase):
    def setUp(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)

    def test_stage_timer_records_duration(self):
        metrics = RunMetrics()
        set_current_metrics(metrics)
        with stage_timer("market_analysis"):
            pass
        self.assertIn("market_analysis", metrics.stages)
        self.assertGreaterEqual(metrics.stages["market_analysis"]["duration_ms"], 0)
        self.assertIn("duration_ms", metrics.stages["market_analysis"])

    def test_stage_timer_attaches_calls_to_stage(self):
        metrics = RunMetrics()
        set_current_metrics(metrics)
        with stage_timer("analyst"):
            record_llm_call(
                model="gpt-4o-mini",
                provider="openai",
                prompt_tokens=10,
                completion_tokens=5,
            )
        self.assertEqual(metrics.stages["analyst"]["calls"], 1)
        self.assertEqual(metrics.stages["analyst"]["tokens"], 15)


class CurrentMetricsRegistryTests(unittest.TestCase):
    def setUp(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)

    def test_set_get_clear(self):
        self.assertIsNone(get_current_metrics())
        metrics = RunMetrics(ticker="600519")
        set_current_metrics(metrics)
        self.assertIs(get_current_metrics(), metrics)
        clear_current_metrics()
        self.assertIsNone(get_current_metrics())

    def test_contextvar_isolates_two_concurrent_runs(self):
        """两个并发 run（各自在线程中）不能互相污染。"""
        import threading

        first = RunMetrics(ticker="600519")
        second = RunMetrics(ticker="000001")
        contaminated = []

        def worker(metrics):
            set_current_metrics(metrics)
            # 让两个线程交错执行，反复确认读到的始终是自己那一份
            for _ in range(200):
                if get_current_metrics() is not metrics:
                    contaminated.append(metrics)
                    return

        t1 = threading.Thread(target=worker, args=(first,))
        t2 = threading.Thread(target=worker, args=(second,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(contaminated, [])
        clear_current_metrics()


class LlmClientInstrumentationTests(unittest.TestCase):
    def setUp(self):
        clear_current_metrics()
        self.addCleanup(clear_current_metrics)
        self.metrics = RunMetrics(ticker="600519")
        set_current_metrics(self.metrics)

    def test_call_with_usage_metadata_is_recorded(self):
        response = _FakeResponse(
            content="分析结果",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
        client = _FakeClient(response=response)
        client.invoke("分析贵州茅台")

        self.assertEqual(len(self.metrics.llm_calls), 1)
        call = self.metrics.llm_calls[0]
        self.assertEqual(call["prompt_tokens"], 100)
        self.assertEqual(call["completion_tokens"], 50)
        self.assertEqual(call["total_tokens"], 150)
        self.assertFalse(call["tokens_estimated"])
        self.assertTrue(call["ok"])
        self.assertEqual(call["model"], "gpt-4o-mini")
        self.assertEqual(call["provider"], "openai")
        self.assertGreater(call["latency_ms"], 0)
        self.assertGreater(call["cost"], 0)

    def test_call_without_usage_falls_back_to_estimator(self):
        response = _FakeResponse(content="这是一段很长的中文分析结果。", usage_metadata=None)
        client = _FakeClient(response=response)
        client.invoke("分析贵州茅台的基本面")

        call = self.metrics.llm_calls[0]
        self.assertTrue(call["tokens_estimated"])
        self.assertGreater(call["prompt_tokens"], 0)
        self.assertGreater(call["completion_tokens"], 0)

    def test_failing_call_is_recorded_and_does_not_raise(self):
        client = _FakeClient(error=RuntimeError("boom"))

        # 业务异常必须原样抛出（语义不变），但度量要记录下来
        with self.assertRaises(RuntimeError):
            client.invoke("分析贵州茅台")

        self.assertEqual(len(self.metrics.llm_calls), 1)
        call = self.metrics.llm_calls[0]
        self.assertFalse(call["ok"])
        self.assertIn("boom", call["error"])

    def test_no_active_run_is_noop(self):
        clear_current_metrics()
        response = _FakeResponse(
            content="x",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )
        client = _FakeClient(response=response)
        client.invoke("hello")  # 不应抛异常
        self.assertIsNone(get_current_metrics())


class RunLogStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "runs.db")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _metrics(self, ticker="600519", source="cli", model="gpt-4o-mini"):
        metrics = RunMetrics(
            ticker=ticker,
            trade_date="2026-11-12",
            source=source,
            horizon="medium",
        )
        metrics.add_call(
            stage="market",
            model=model,
            provider="openai",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=120.0,
        )
        metrics.finish()
        return metrics

    def test_save_list_get(self):
        metrics = self._metrics()
        run_id = save_run_log(metrics.to_dict())
        self.assertEqual(run_id, metrics.run_id)

        items = list_run_logs()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ticker"], "600519")
        self.assertEqual(items[0]["call_count"], 1)
        self.assertEqual(items[0]["prompt_tokens"], 1000)
        self.assertEqual(items[0]["total_tokens"], 1500)
        self.assertGreater(items[0]["estimated_cost"], 0)
        self.assertEqual(items[0]["currency"], "USD")

        detail = get_run_log(run_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["run_id"], run_id)
        self.assertEqual(len(detail["llm_calls"]), 1)
        self.assertIn("market", detail["stages"])

        self.assertIsNone(get_run_log("does-not-exist"))

    def test_list_filters_by_ticker(self):
        save_run_log(self._metrics(ticker="600519").to_dict())
        save_run_log(self._metrics(ticker="000001").to_dict())
        items = list_run_logs(ticker="600519")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ticker"], "600519")

    def test_aggregate(self):
        save_run_log(self._metrics(ticker="600519", source="cli").to_dict())
        save_run_log(self._metrics(ticker="000001", source="webui").to_dict())

        summary = aggregate_run_logs()
        self.assertEqual(summary["totals"]["runs"], 2)
        self.assertEqual(summary["totals"]["call_count"], 2)
        self.assertEqual(summary["totals"]["total_tokens"], 3000)
        self.assertGreater(summary["totals"]["estimated_cost"], 0)

        self.assertIn("openai/gpt-4o-mini", summary["by_model"])
        self.assertEqual(summary["by_model"]["openai/gpt-4o-mini"]["runs"], 2)
        self.assertIn("cli", summary["by_source"])
        self.assertIn("webui", summary["by_source"])
        self.assertEqual(summary["by_source"]["cli"]["runs"], 1)


if __name__ == "__main__":
    unittest.main()
