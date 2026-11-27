"""分析可信度、共享生命周期与并发取数回归测试。"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tradingagents.dataflows import interface
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import _prefetch_analyst_material
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.runtime import get_current_trade_date, set_current_trade_date
from tradingagents.services.decision_schema import parse_structured_decision
from tradingagents.services.decision_validation import (
    BLOCKED_DECISION_TEXT,
    assess_analysis_evidence,
)
from tradingagents.services.observability import (
    RunMetrics,
    clear_current_metrics,
    get_current_metrics,
    set_current_metrics,
)
from tradingagents.services.plain_language import build_plain_summary
from webui.backend import server


def _analysis_state(decision: str = "BUY", *, as_of: str = "2026-11-20") -> dict:
    state = Propagator().create_initial_state("600519", "2026-11-20", "medium")
    structured = {
        "decision": decision,
        "confidence": 68,
        "summary": "核心证据支持当前评级",
        "key_points": ["趋势与基本面相互验证"],
        "citations": ["行情：2026-11-20", "公告：2026-11-19"],
        "risk_flags": ["波动风险"],
    }
    state.update(
        {
            "market_report": "市场报告：收盘价 1688.00，趋势温和向上。",
            "final_trade_decision": (
                "## 1. 评级（Rating）\n- " + decision + "\n\n"
                "```json\n" + json.dumps(structured, ensure_ascii=False) + "\n```"
            ),
            "strategy_report": "策略扫描成功",
            "strategy_result": {
                "success": True,
                "as_of": as_of,
                "price": {"close": 1688.0, "atr": 28.0},
                "summary": {
                    "consensus": "偏多",
                    "net_score": 18.0,
                    "strength": 62.0,
                    "triggered": 3,
                    "family_count": 2,
                },
                "signals": [],
            },
        }
    )
    return state


class _FakeGraph:
    def __init__(self, state: dict, *, error: Exception | None = None):
        self.state = state
        self.error = error
        self.stream_modes = []
        self.context_seen = {}

    def invoke(self, _state, **_kwargs):
        self.context_seen = {
            "trade_date": get_current_trade_date(),
            "metrics": get_current_metrics(),
        }
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.state)

    def stream(self, _state, **kwargs):
        self.stream_modes.append(kwargs.get("stream_mode"))
        self.context_seen = {
            "trade_date": get_current_trade_date(),
            "metrics": get_current_metrics(),
        }
        if self.error is not None:
            raise self.error
        yield "updates", {"Market Analyst": {"market_report": "市场报告已更新"}}
        yield "values", copy.deepcopy(self.state)


def _bare_graph(tmp_path, state: dict, *, fallback: str = "HOLD") -> TradingAgentsGraph:
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {"run_source": "test", "project_dir": str(tmp_path)}
    graph.propagator = Propagator()
    graph.graph = _FakeGraph(state)
    graph.debug = False
    graph.selected_analysts = ["market"]
    graph.ticker = None
    graph.curr_state = None
    graph.last_run_metrics = None
    graph.log_states_dict = {}
    graph.signal_calls = []
    graph.log_calls = []
    graph.memory_calls = []
    graph.process_signal = lambda text: graph.signal_calls.append(text) or fallback
    graph._log_state = lambda *args: graph.log_calls.append(args)
    graph.remember_analysis = lambda final_state: graph.memory_calls.append(final_state)
    return graph


def test_stream_analysis_shares_context_metrics_and_finalization(tmp_path, monkeypatch):
    state = _analysis_state()
    graph = _bare_graph(tmp_path, state)
    saved_logs = []
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.save_run_log",
        saved_logs.append,
    )
    clear_current_metrics()
    previous_date = set_current_trade_date("2020-01-01")
    chunks = []

    try:
        final_state, decision = graph.stream_analysis(
            "600519",
            "2026-11-20",
            on_chunk=lambda mode, payload: chunks.append((mode, payload)),
        )
        assert get_current_metrics() is None
        assert get_current_trade_date() == "2020-01-01"
    finally:
        clear_current_metrics()
        set_current_trade_date(previous_date)

    assert decision == "BUY"
    assert final_state["model_decision"] == "BUY"
    assert final_state["analysis_completed"] is True
    assert final_state["evidence_assessment"]["level"] == "ok"
    assert [mode for mode, _payload in chunks] == ["updates", "values"]
    assert graph.graph.stream_modes == [["values", "updates"]]
    assert graph.graph.context_seen["trade_date"] == "2026-11-20"
    assert graph.graph.context_seen["metrics"].ticker == "600519"
    assert len(graph.log_calls) == 1
    assert len(graph.memory_calls) == 1
    assert len(saved_logs) == 1
    assert saved_logs[0]["totals"]["call_count"] == 0
    assert final_state["run_metrics"]["duration_ms"] >= 0
    assert graph.last_run_metrics == final_state["run_metrics"]
    assert graph.signal_calls == []
    assert get_current_trade_date() == previous_date


def test_cancelled_stream_keeps_partial_values_but_never_claims_success(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state("BUY"))
    cancel_event = threading.Event()

    def on_chunk(mode, _payload):
        if mode == "values":
            cancel_event.set()

    final_state, decision = graph.stream_analysis(
        "600519",
        "2026-11-20",
        on_chunk=on_chunk,
        cancel_event=cancel_event,
    )

    assert cancel_event.is_set()
    assert decision == "HOLD"
    assert final_state["model_decision"] == "BUY"
    assert final_state["market_report"].startswith("市场报告")
    assert final_state["analysis_completed"] is False
    assert final_state["analysis_cancelled"] is True
    assert final_state["evidence_assessment"]["level"] == "blocked"
    assert final_state["decision_structured"]["decision"] == "HOLD"
    assert any("降级为 HOLD" in item for item in final_state["decision_structured"]["warnings"])
    assert "BUY" in final_state["model_decision_text"]
    assert final_state["final_trade_decision"] == BLOCKED_DECISION_TEXT
    assert "BUY" not in final_state["final_trade_decision"]
    assert "买入" not in final_state["final_trade_decision"]
    assert len(graph.log_calls) == 1
    assert len(graph.memory_calls) == 1


def test_propagate_remains_compatible_and_uses_shared_decision(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state("SELL"))

    final_state, decision = graph.propagate("600519", "2026-11-20")

    assert decision == "SELL"
    assert final_state["model_decision"] == "SELL"
    assert graph.signal_calls == []
    assert graph.last_run_metrics["ticker"] == "600519"


def test_graph_exception_is_finalized_and_reraised(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state())
    graph.graph.error = RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        graph.stream_analysis("600519", "2026-11-20")

    assert graph.curr_state["analysis_completed"] is False
    assert "provider down" in graph.curr_state["analysis_error"]
    assert graph.curr_state["evidence_assessment"]["level"] == "blocked"
    assert graph.curr_state["decision_structured"]["decision"] == "HOLD"
    assert graph.curr_state["final_trade_decision"] == BLOCKED_DECISION_TEXT
    assert "买入" not in graph.curr_state["final_trade_decision"]
    assert graph.last_run_metrics["duration_ms"] >= 0
    assert len(graph.log_calls) == 1
    assert len(graph.memory_calls) == 1


def test_finalize_sanitizes_directional_text_on_exception_and_cancel(tmp_path):
    for kwargs in (
        {"completed": False, "cancelled": False, "error": RuntimeError("boom")},
        {"completed": False, "cancelled": True, "error": None},
    ):
        graph = _bare_graph(tmp_path, _analysis_state("BUY"))
        state, decision = graph._finalize_analysis(
            _analysis_state("BUY"),
            **kwargs,
        )

        assert decision == "HOLD"
        assert state["model_decision"] == "BUY"
        assert "BUY" in state["model_decision_text"]
        assert state["final_trade_decision"] == BLOCKED_DECISION_TEXT
        assert "BUY" not in state["final_trade_decision"]
        assert "买入" not in state["final_trade_decision"]
        assert state["decision_structured"]["decision"] == "HOLD"


def test_run_analysis_restores_outer_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.save_run_log",
        lambda _metrics: None,
    )
    outer = RunMetrics(ticker="outer")
    clear_current_metrics()
    set_current_metrics(outer)
    try:
        graph = _bare_graph(tmp_path, _analysis_state())
        graph.stream_analysis("600519", "2026-11-20")
        assert get_current_metrics() is outer
    finally:
        clear_current_metrics()


def test_base_exception_is_finalized_and_reraised(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.save_run_log",
        lambda _metrics: None,
    )
    graph = _bare_graph(tmp_path, _analysis_state())
    graph.graph.error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        graph.stream_analysis("600519", "2026-11-20")

    assert graph.curr_state["analysis_completed"] is False
    assert graph.curr_state["analysis_error"].startswith("KeyboardInterrupt")
    assert graph.curr_state["evidence_assessment"]["level"] == "blocked"
    assert graph.curr_state["final_trade_decision"] == BLOCKED_DECISION_TEXT
    assert graph.last_run_metrics["duration_ms"] >= 0
    assert len(graph.log_calls) == 1
    assert len(graph.memory_calls) == 1


def test_remember_analysis_error_does_not_mask_original(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.save_run_log",
        lambda _metrics: None,
    )
    graph = _bare_graph(tmp_path, _analysis_state())
    graph.graph.error = RuntimeError("original failure")

    def boom(_state):
        raise RuntimeError("memory write failure")

    graph.remember_analysis = boom

    with pytest.raises(RuntimeError, match="original failure"):
        graph.stream_analysis("600519", "2026-11-20")

    assert graph.curr_state["analysis_completed"] is False


def test_plain_language_rewrite_runs_inside_run_metrics(tmp_path, monkeypatch):
    from tradingagents.llm_clients.base_client import observe_llm_call

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.save_run_log",
        lambda _metrics: None,
    )
    graph = _bare_graph(tmp_path, _analysis_state())
    graph.config["plain_language_rewrite"] = True

    class RewriteLLM:
        def invoke(self, prompt, **_kwargs):
            with observe_llm_call(
                provider="openai",
                model="gpt-4o-mini",
                prompt_input=prompt,
            ) as box:
                response = SimpleNamespace(
                    content="白话版：继续持有，暂不追高。",
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "total_tokens": 30,
                    },
                )
                box["response"] = response
                return response

    graph.quick_thinking_llm = RewriteLLM()

    final_state, _decision = graph.stream_analysis("600519", "2026-11-20")

    assert final_state["decision_text_plain"] == "白话版：继续持有，暂不追高。"
    assert graph.last_run_metrics["totals"]["call_count"] == 1
    assert any(
        call["model"] == "gpt-4o-mini"
        for call in graph.last_run_metrics["llm_calls"]
    )



def test_structured_json_and_rating_do_not_call_llm_classifier(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state())
    json_text = _analysis_state("OVERWEIGHT")["final_trade_decision"]
    rating_text = "## 评级（Rating）\n- UNDERWEIGHT\n\n正文提到卖出风险。"

    for text, expected in ((json_text, "OVERWEIGHT"), (rating_text, "UNDERWEIGHT")):
        decision, structured = graph.resolve_final_decision(text)
        assert decision == expected
        assert structured["decision"] == expected
        assert structured["decision_source"] in ("json", "rating_section")
    assert graph.signal_calls == []


def test_ambiguous_text_uses_one_llm_fallback_and_syncs_structure(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state(), fallback="SELL")
    text = "多空因素交织，风险收益结构尚未形成统一倾向。"

    decision, structured = graph.resolve_final_decision(text)

    assert decision == "SELL"
    assert structured["decision"] == "SELL"
    assert structured["decision_source"] == "llm_fallback"
    assert structured["source"] == "llm_fallback"
    assert len(graph.signal_calls) == 1


def test_explicit_text_wins_over_conflicting_llm_fallback(tmp_path):
    graph = _bare_graph(tmp_path, _analysis_state(), fallback="SELL")

    decision, structured = graph.resolve_final_decision("最终决策：BUY\n其余论证较为复杂。")

    assert decision == "BUY"
    assert structured["decision_source"] == "explicit_text"
    assert graph.signal_calls == []


@pytest.mark.parametrize(
    ("text", "expected", "source"),
    [
        ("## 评级（Rating）\n- 强烈买入", "BUY", "rating_section"),
        ("## 评级（Rating）\n- 超配买入", "OVERWEIGHT", "rating_section"),
        ("最终决策：特别看好，买入。", "BUY", "explicit_text"),
        ("最终决策：HOLD，不建议买入。", "HOLD", "explicit_text"),
    ],
)
def test_explicit_decision_aliases_and_negation(text, expected, source):
    parsed = parse_structured_decision(text)

    assert parsed["decision"] == expected
    assert parsed["decision_source"] == source
    assert parsed["valid"] is True


def test_compound_json_decision_matches_single_rating_without_false_conflict():
    text = (
        "## 评级（Rating）\n- 买入\n\n"
        "```json\n"
        + json.dumps({"decision": "强烈买入", "confidence": 70}, ensure_ascii=False)
        + "\n```"
    )

    parsed = parse_structured_decision(text)

    assert parsed["decision"] == "BUY"
    assert parsed["decision_source"] == "json"
    assert parsed["valid"] is True
    assert not any("冲突" in warning for warning in parsed["warnings"])


def test_json_rating_conflict_is_not_bypassed_by_structured(tmp_path):
    text = (
        "## 评级（Rating）\n- BUY\n\n"
        "```json\n"
        + json.dumps({"decision": "SELL", "confidence": 60}, ensure_ascii=False)
        + "\n```"
    )
    parsed = parse_structured_decision(text)

    assert parsed["decision"] == "HOLD"
    assert parsed["decision_source"] == "conflicted_explicit"
    assert parsed["valid"] is False
    assert any("冲突" in warning for warning in parsed["warnings"])

    graph = _bare_graph(tmp_path, _analysis_state())
    forged = {
        "decision": "BUY",
        "decision_source": "json",
        "source": "json",
        "valid": True,
        "warnings": [],
    }

    decision, structured = graph.resolve_final_decision(text, forged)

    assert decision == "HOLD"
    assert structured["decision"] == "HOLD"
    assert structured["decision_source"] == "conflicted_explicit"
    assert structured["valid"] is False
    assert any("冲突" in warning for warning in structured["warnings"])
    assert graph.signal_calls == []


@pytest.mark.parametrize(
    ("text", "expected_source"),
    [
        ("最终决策：HOLD，不建议买入。", "explicit_text"),
        ("最终决策：不建议买入。", "explicit_text"),
    ],
)
def test_negated_explicit_lines_resolve_to_hold(tmp_path, text, expected_source):
    graph = _bare_graph(tmp_path, _analysis_state(), fallback="BUY")

    decision, structured = graph.resolve_final_decision(text)

    assert decision == "HOLD"
    assert structured["decision"] == "HOLD"
    assert structured["decision_source"] == expected_source
    assert graph.signal_calls == []


@pytest.mark.parametrize(
    ("strategy_result", "expected_level"),
    [
        ({"success": True, "as_of": "2026-11-20", "price": {"close": 10.0}}, "ok"),
        ({"success": False, "as_of": "2026-11-20", "price": {"close": 10.0}}, "blocked"),
        ({"success": True, "as_of": "2026-11-21", "price": {"close": 10.0}}, "blocked"),
        ({"success": True, "as_of": "2026-11-20", "price": {"close": 0.0}}, "blocked"),
        ({"success": True, "as_of": "2026-11-20", "price": {"close": True}}, "blocked"),
    ],
)
def test_core_evidence_gate(strategy_result, expected_level):
    state = _analysis_state()
    state["strategy_result"] = strategy_result
    structured = parse_structured_decision(state["final_trade_decision"])
    state["decision_structured"] = structured

    evidence = assess_analysis_evidence(
        state,
        "BUY",
        "BUY",
        required_reports=("market",),
    )

    assert evidence["level"] == expected_level
    assert evidence["allow_directional"] is (expected_level != "blocked")
    assert all(item["status"] in ("ok", "warning", "missing") for item in evidence["items"])
    assert all("as_of" in item for item in evidence["items"])


def test_missing_reports_and_citations_are_warnings_not_blocked():
    state = _analysis_state()
    state["market_report"] = ""
    state["decision_structured"] = parse_structured_decision(state["final_trade_decision"])
    state["decision_structured"]["citations"] = []

    evidence = assess_analysis_evidence(
        state,
        "BUY",
        "BUY",
        required_reports=("market",),
    )

    assert evidence["level"] == "warning"
    assert evidence["allow_directional"] is True
    assert any(item["label"] == "市场/技术面报告" and item["status"] == "missing" for item in evidence["items"])
    assert any(item["label"] == "结构化引用" and item["status"] == "missing" for item in evidence["items"])


def test_partial_strategy_failure_is_warning():
    state = _analysis_state()
    state["strategy_result"]["signals"] = [
        {"summary": "策略计算失败：ZeroDivisionError", "triggered": False},
        {"summary": "均线策略触发", "triggered": True},
    ]

    evidence = assess_analysis_evidence(state, "BUY", "BUY")

    assert evidence["level"] == "warning"
    assert evidence["allow_directional"] is True
    assert any(item["label"] == "策略计算完整性" for item in evidence["items"])


def test_all_strategies_failed_without_total_is_blocked():
    state = _analysis_state()
    state["strategy_result"]["summary"] = {}
    state["strategy_result"]["signals"] = [
        {"summary": "策略计算失败：KeyError", "triggered": False},
        {"summary": "策略计算失败：ValueError", "triggered": False},
    ]

    evidence = assess_analysis_evidence(state, "BUY", "BUY")

    assert "total" not in state["strategy_result"]["summary"]
    assert evidence["level"] == "blocked"
    assert evidence["allow_directional"] is False
    assert any(
        item["label"] == "策略计算完整性" and item["status"] == "missing"
        for item in evidence["items"]
    )


def test_partial_strategy_failure_without_total_stays_warning():
    state = _analysis_state()
    state["strategy_result"]["summary"] = {}
    state["strategy_result"]["signals"] = [
        {"summary": "策略计算失败：KeyError", "triggered": False},
        {"summary": "均线策略触发", "triggered": True},
    ]

    evidence = assess_analysis_evidence(state, "BUY", "BUY")

    assert "total" not in state["strategy_result"]["summary"]
    assert evidence["level"] == "warning"
    assert evidence["allow_directional"] is True


def test_shared_finalization_downgrades_directional_decision(tmp_path):
    state = _analysis_state("BUY")
    state["strategy_result"]["success"] = False
    graph = _bare_graph(tmp_path, state)

    final_state, decision = graph.stream_analysis("600519", "2026-11-20")

    assert decision == "HOLD"
    assert final_state["model_decision"] == "BUY"
    assert final_state["decision_structured"]["decision"] == "HOLD"
    assert final_state["evidence_assessment"]["allow_directional"] is False
    assert final_state["evidence_assessment"]["warnings"]
    assert "BUY" in final_state["model_decision_text"]
    assert final_state["final_trade_decision"] == BLOCKED_DECISION_TEXT
    assert "BUY" not in final_state["final_trade_decision"]
    assert "买入" not in final_state["final_trade_decision"]
    assert graph.memory_calls[0]["final_trade_decision"].startswith("HOLD")


def test_plain_summary_blocked_hides_directional_actions():
    state = _analysis_state("BUY")
    state["strategy_result"]["success"] = False
    state["decision_structured"] = parse_structured_decision(state["final_trade_decision"])
    evidence = assess_analysis_evidence(state, "BUY", "HOLD")

    summary = build_plain_summary(
        ticker="600519",
        decision="BUY",
        plan={"decision": "BUY", "position_pct": 0.2, "entry": 1688.0},
        structured=state["decision_structured"],
        position_advice={"advice": {"action": "买入", "delta_qty": 100}},
        evidence=evidence,
    )

    assert summary["decision"] == "HOLD"
    assert "核心数据不足" in summary["headline"]
    assert "暂缓交易" in summary["action"]
    assert "买入" not in summary["action"]
    assert summary["action_holding"] == ""
    assert summary["numbers"] == []
    assert summary["reliability"] == evidence["summary"]


def test_prefetch_tool_thread_inherits_analysis_context():
    clear_current_metrics()
    metrics = RunMetrics(ticker="600519", trade_date="2026-11-20")
    set_current_metrics(metrics)
    previous_date = set_current_trade_date("2026-11-20")
    seen = {}

    class Tool:
        def invoke(self, _args):
            seen["trade_date"] = get_current_trade_date()
            seen["metrics"] = get_current_metrics()
            return "social data"

    try:
        _prefetch_analyst_material(
            "social",
            {"company_of_interest": "600519", "trade_date": "2026-11-20"},
            SimpleNamespace(tools_by_name={"get_social_sentiment": Tool()}),
        )
    finally:
        clear_current_metrics()
        set_current_trade_date(previous_date)

    assert seen["trade_date"] == "2026-11-20"
    assert seen["metrics"] is metrics


def _install_single_vendor(monkeypatch, impl):
    monkeypatch.setattr(interface, "get_vendor", lambda _category, _method: "akshare")
    monkeypatch.setattr(interface, "_is_a_share_only", lambda: False)
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_stock_data", {"akshare": impl})


def test_singleflight_same_key_calls_vendor_once(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    start = threading.Barrier(5)
    calls = 0
    calls_lock = threading.Lock()

    def impl(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(3)
        return "DATA"

    _install_single_vendor(monkeypatch, impl)

    def worker():
        start.wait()
        return interface.route_to_vendor("get_stock_data", "600519", "2026-11-01")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker) for _ in range(4)]
        start.wait()
        assert entered.wait(3)
        release.set()
        results = [future.result(timeout=3) for future in futures]

    assert results == ["DATA"] * 4
    assert calls == 1


def test_disabled_memo_still_routes_without_inflight_future(monkeypatch):
    calls = 0

    def impl(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return "DATA"

    _install_single_vendor(monkeypatch, impl)
    monkeypatch.setattr(interface, "_MEMO_ENABLED", False)

    assert interface.route_to_vendor("get_stock_data", "600519", "2026-11-01") == "DATA"
    assert interface.route_to_vendor("get_stock_data", "600519", "2026-11-01") == "DATA"
    assert calls == 2


def test_singleflight_different_keys_remain_concurrent(monkeypatch):
    both_entered = threading.Barrier(2)

    def impl(_ticker, start_date, *_args):
        both_entered.wait(timeout=3)
        return "DATA " + start_date

    _install_single_vendor(monkeypatch, impl)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            interface.route_to_vendor,
            "get_stock_data",
            "600519",
            "2026-11-01",
        )
        second = pool.submit(
            interface.route_to_vendor,
            "get_stock_data",
            "600519",
            "2026-11-02",
        )
        assert first.result(timeout=5) == "DATA 2026-11-01"
        assert second.result(timeout=5) == "DATA 2026-11-02"


def test_reset_route_caches_detaches_inflight_generation(monkeypatch):
    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def impl(*_args, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
            current = calls
        if current == 1:
            first_entered.set()
        else:
            second_entered.set()
        assert release.wait(3)
        return f"DATA {current}"

    _install_single_vendor(monkeypatch, impl)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            interface.route_to_vendor,
            "get_stock_data",
            "600519",
            "2026-11-01",
        )
        assert first_entered.wait(3)
        interface.reset_route_caches()
        second = pool.submit(
            interface.route_to_vendor,
            "get_stock_data",
            "600519",
            "2026-11-01",
        )
        assert second_entered.wait(3)
        release.set()
        assert first.result(timeout=3) == "DATA 1"
        assert second.result(timeout=3) == "DATA 2"
    assert calls == 2


def test_singleflight_memo_put_failure_wakes_waiters_and_retries(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    start = threading.Barrier(4)
    calls = 0
    lock = threading.Lock()

    def impl(*_args, **_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(3)
        return "DATA"

    def broken_put(*_args, **_kwargs):
        raise RuntimeError("memo put boom")

    original_claim = interface._memo_claim
    claimed = 0
    claimed_lock = threading.Lock()
    all_claimed = threading.Event()

    def counting_claim(key):
        nonlocal claimed
        role = original_claim(key)
        with claimed_lock:
            claimed += 1
            if claimed >= 3:
                all_claimed.set()
        return role

    _install_single_vendor(monkeypatch, impl)
    monkeypatch.setattr(interface, "_memo_put", broken_put)
    monkeypatch.setattr(interface, "_memo_claim", counting_claim)

    def worker():
        start.wait()
        return interface.route_to_vendor(
            "get_stock_data", "600519", "2026-11-01"
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker) for _ in range(3)]
        start.wait()
        assert entered.wait(3)
        assert all_claimed.wait(3)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert results == ["DATA"] * 3
    assert calls == 1

    # 缓存写入失败后不能留下 in-flight 记录；后续请求应重新成为 leader。
    assert interface.route_to_vendor(
        "get_stock_data", "600519", "2026-11-01"
    ) == "DATA"
    assert calls == 2
    with interface._memo_lock:
        assert not interface._memo_inflight



def test_analysis_job_handler_passes_per_job_event_and_reraises(monkeypatch):
    cancel_event = threading.Event()
    seen = {}

    def fail(request, job_id, owner, cancel_event):
        seen.update(request=request, job_id=job_id, owner=owner, cancel_event=cancel_event)
        raise RuntimeError("queue failure")

    monkeypatch.setattr(server, "run_analysis_sync", fail)
    payload = {
        "request": {"company": "600519", "date": "2026-11-20"},
        "owner": "alice",
    }

    with pytest.raises(RuntimeError, match="queue failure"):
        server._analysis_job_handler("job-1", payload, cancel_event)

    assert seen["cancel_event"] is cancel_event
    assert seen["owner"] == "alice"


def test_run_analysis_sync_marks_failure_reraises_and_keeps_external_event(monkeypatch):
    external_cancel = threading.Event()
    external_cancel.set()
    server.analysis_cancel_event.clear()
    failures = []
    messages = []
    monkeypatch.setattr(server, "mark_job_running", lambda _job_id: None)
    monkeypatch.setattr(server, "fail_job", lambda job_id, error: failures.append((job_id, error)))
    monkeypatch.setattr(server, "broadcast_threadsafe", messages.append)

    class FailingGraph:
        def __init__(self, **_kwargs):
            pass

        def stream_analysis(self, *_args, **_kwargs):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(server, "TradingAgentsGraph", FailingGraph)
    request = server.AnalyzeRequest(company="600519", date="2026-11-20")

    with pytest.raises(RuntimeError, match="model unavailable"):
        server.run_analysis_sync(
            request,
            "job-2",
            cancel_event=external_cancel,
        )

    assert failures and failures[0][0] == "job-2"
    assert any(message["type"] == "error" for message in messages)
    assert external_cancel.is_set()
    assert not server.analysis_cancel_event.is_set()
    assert server.analysis_status["running"] is False


def test_job_cancel_endpoint_uses_queue(monkeypatch):
    server.analysis_cancel_event.clear()
    cancelled = []

    class Queue:
        def cancel(self, job_id):
            cancelled.append(job_id)
            return True

    monkeypatch.setattr(server, "get_job_queue", lambda: Queue())
    monkeypatch.setattr(
        server,
        "get_job",
        lambda job_id, _include_result: {"job_id": job_id, "status": "running"},
    )

    response = asyncio.run(_call_cancel_job("job-3"))

    assert response["success"] is True
    assert cancelled == ["job-3"]
    assert not server.analysis_cancel_event.is_set()


def _call_cancel_job(job_id):
    params = inspect.signature(server.api_cancel_job).parameters
    kwargs = {}
    if "user" in params:
        kwargs["user"] = {"username": "admin", "role": "admin"}
    return server.api_cancel_job(job_id, **kwargs)


class _FakeTa:
    def __init__(self, final_state: dict):
        self.final_state = final_state
        self.rewrite_calls = 0
        self.last_run_metrics = {
            "duration_ms": 12.5,
            "totals": {"call_count": 2, "total_tokens": 90, "estimated_cost": 0.01},
            "currency": "CNY",
        }

    def stream_analysis(self, *_args, **_kwargs):
        return copy.deepcopy(self.final_state), self.final_state["decision"]

    def rewrite_in_plain_language(self, _text):
        self.rewrite_calls += 1
        return ""


def _contract_state() -> dict:
    state = _analysis_state("BUY")
    state["analysis_completed"] = True
    state["decision_structured"] = parse_structured_decision(state["final_trade_decision"])
    state["model_decision"] = "BUY"
    state["decision"] = "HOLD"
    state["decision_text_plain"] = "graph 生成的白话版：继续持有，暂不追高。"
    state["evidence_assessment"] = assess_analysis_evidence(
        state,
        "BUY",
        "HOLD",
        required_reports=("market",),
    )
    return state


def test_webui_complete_data_and_report_meta_include_reliability_contract(monkeypatch):
    state = _contract_state()
    fake_ta = _FakeTa(state)
    saved = []
    finished = []
    monkeypatch.setattr(server, "TradingAgentsGraph", lambda **_kwargs: fake_ta)
    monkeypatch.setattr(server, "mark_job_running", lambda _job_id: None)
    monkeypatch.setattr(server, "broadcast_threadsafe", lambda _message: None)
    monkeypatch.setattr(server, "save_report", lambda **kwargs: saved.append(kwargs) or "report-1")
    monkeypatch.setattr(server, "finish_job", lambda *args, **kwargs: finished.append((args, kwargs)))
    monkeypatch.setattr(
        "tradingagents.services.decision_validation.get_stored_decision",
        lambda _ticker: None,
    )
    monkeypatch.setattr(
        "tradingagents.services.portfolio.evaluate_plan_for_account",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "tradingagents.services.position_advisor.advice_for_analysis",
        lambda *_args, **_kwargs: None,
    )
    request = server.AnalyzeRequest(company="600519", date="2026-11-20")

    result = server.run_analysis_sync(request, "job-4")

    for key in (
        "evidence_assessment",
        "decision_validation",
        "decision_diff",
        "decision_structured",
        "model_decision",
        "run_metrics",
    ):
        assert key in result
    assert result["model_decision"] == "BUY"
    assert result["decision"] == "HOLD"
    assert result["run_metrics"]["totals"]["call_count"] == 2
    assert finished[0][1]["status"] == "completed"
    for key in (
        "evidence_assessment",
        "decision_validation",
        "model_decision",
        "run_metrics",
    ):
        assert key in saved[0]["meta"]
    assert fake_ta.rewrite_calls == 0


def test_runner_result_and_report_meta_include_reliability_contract(monkeypatch):
    from tradingagents.services import runner

    state = _contract_state()
    fake_ta = _FakeTa(state)
    fake_ta.propagate = lambda *_args, **_kwargs: (copy.deepcopy(state), "HOLD")
    saved = []
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.TradingAgentsGraph",
        lambda **_kwargs: fake_ta,
    )
    monkeypatch.setattr(runner, "save_report", lambda **kwargs: saved.append(kwargs) or "report-2")
    monkeypatch.setattr(
        "tradingagents.storage.audit_store.append_audit_event",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "tradingagents.services.decision_validation.get_stored_decision",
        lambda _ticker: None,
    )
    monkeypatch.setattr(
        "tradingagents.services.position_advisor.advice_for_analysis",
        lambda *_args, **_kwargs: None,
    )

    result = runner.run_analysis(
        "600519",
        "2026-11-20",
        source="scheduler",
        notify=False,
    )

    assert result["model_decision"] == "BUY"
    assert result["decision"] == "HOLD"
    assert result["run_metrics"]["currency"] == "CNY"
    assert result["decision_text_plain"] == "graph 生成的白话版：继续持有，暂不追高。"
    assert "evidence_assessment" in saved[0]["meta"]
    assert "decision_validation" in saved[0]["meta"]
    assert "model_decision" in saved[0]["meta"]
    assert "run_metrics" in saved[0]["meta"]
    assert (
        saved[0]["meta"]["decision_text_plain"]
        == "graph 生成的白话版：继续持有，暂不追高。"
    )


def _blocked_state() -> dict:
    state = _analysis_state("BUY")
    state["analysis_completed"] = True
    state["strategy_result"]["success"] = False
    state["strategy_report"] = "策略扫描失败：供应商不可用"
    state["model_decision"] = "BUY"
    state["decision"] = "HOLD"
    state["model_decision_text"] = state["final_trade_decision"]
    state["evidence_assessment"] = assess_analysis_evidence(
        state,
        "BUY",
        "HOLD",
        required_reports=("market",),
    )
    return state


def test_runner_blocked_uses_neutral_plan_and_sanitizes_notification(monkeypatch):
    from tradingagents.services import runner

    state = _blocked_state()
    fake_ta = _FakeTa(state)
    fake_ta.propagate = lambda *_args, **_kwargs: (copy.deepcopy(state), "HOLD")
    saved = []
    notifications = []
    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.TradingAgentsGraph",
        lambda **_kwargs: fake_ta,
    )
    monkeypatch.setattr(runner, "save_report", lambda **kwargs: saved.append(kwargs) or "report-3")
    monkeypatch.setattr(
        "tradingagents.storage.audit_store.append_audit_event",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "tradingagents.services.decision_validation.get_stored_decision",
        lambda _ticker: None,
    )
    monkeypatch.setattr(
        "tradingagents.services.position_advisor.advice_for_analysis",
        lambda *_args, **_kwargs: {
            "advice": {"action": "买入", "delta_qty": 100},
            "markdown": "建议买入 100 股",
        },
    )
    monkeypatch.setattr(
        runner,
        "send_notification",
        lambda title, text: notifications.append((title, text)) or {},
    )

    result = runner.run_analysis(
        "600519",
        "2026-11-20",
        source="scheduler",
        notify=True,
    )

    assert result["evidence_assessment"]["level"] == "blocked"
    assert result["decision"] == "HOLD"
    assert result["position_advice"] is None
    neutral = result["decision_plan"]
    assert neutral["decision"] == "HOLD"
    assert neutral["position_pct"] == 0.0
    assert neutral["entry"] == 0.0
    assert neutral["score"] == 0
    assert neutral["reasons"]
    assert "证据门禁未通过" in neutral["disclaimer"]
    assert result["decision_diff"]["preliminary_decision"] == "HOLD"
    assert result["decision_diff"]["final_decision"] == "HOLD"
    assert result["decision_diff"]["has_diff"] is False

    assert saved[0]["decision"] == "HOLD"
    assert saved[0]["meta"]["decision_plan"] == neutral
    assert saved[0]["decision_text"] == BLOCKED_DECISION_TEXT
    assert "BUY" not in saved[0]["decision_text"]
    assert "买入" not in saved[0]["decision_text"]

    assert result["final_state"]["model_decision_text"]
    assert "BUY" in result["final_state"]["model_decision_text"]
    assert result["final_state"]["final_trade_decision"] == BLOCKED_DECISION_TEXT

    assert notifications
    _title, text = notifications[0]
    assert "BUY" not in text
    assert "买入" not in text
    assert "暂缓交易" in text
