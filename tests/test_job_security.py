"""任务隔离、授权与证据门禁回归测试。

覆盖：
* WebSocket 任务消息按 owner 隔离（管理员/单用户可见全部）；
* run_analysis_sync 的每条任务消息都带顶层与 data 内 job_id；
* jobs REST API 的 owner / admin / 越权行为；
* /api/status 与 /api/stop 的 owner 校验；
* blocked 时 fail-closed 的中性计划；
* start_analysis 持久化完整 pipeline 配置；
* server 不再二次调用 LLM 白话改写。
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from webui.backend import server

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


class _AIMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = []
        self.name = "Market Analyst"


class _FakeTa:
    """最小 TradingAgentsGraph 替身。"""

    def __init__(self, state: dict, decision: str = "BUY"):
        self.state = state
        self.decision = decision
        self.rewrite_calls: list[str] = []
        self.last_run_metrics = {
            "duration_ms": 1.0,
            "totals": {"call_count": 1, "total_tokens": 10, "estimated_cost": 0.0},
            "currency": "CNY",
        }

    def stream_analysis(self, *_args, on_chunk=None, **_kwargs):
        if on_chunk is not None:
            on_chunk(
                "updates",
                {
                    "Market Analyst": {
                        "messages": [_AIMessage("节点消息")],
                        "market_report": "市场报告正文",
                    }
                },
            )
            on_chunk("values", copy.deepcopy(self.state))
        return copy.deepcopy(self.state), self.decision

    def rewrite_in_plain_language(self, text):
        self.rewrite_calls.append(text)
        return "LLM 改写不应被调用"


class _FakeQueue:
    def __init__(self):
        self.submitted: dict = {}
        self.cancelled: list[str] = []

    def active_count(self):
        return 0

    def pending_count(self):
        return 0

    def submit(self, **kwargs):
        self.submitted = kwargs
        return "job-pipeline"

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return True


def _state(*, evidence=None, plain_text=None, completed=True) -> dict:
    state = {
        "final_trade_decision": "BUY 600519，目标价 2000，止损 1500",
        "market_report": "市场报告正文",
        "strategy_report": "策略扫描成功",
        "strategy_result": {
            "success": True,
            "as_of": "2026-11-20",
            "price": {"close": 1688.0},
            "summary": {"consensus": "偏多", "net_score": 18.0, "triggered": 3},
            "signals": [],
        },
        "analysis_completed": completed,
        "decision": "BUY",
        "model_decision": "BUY",
    }
    if evidence is not None:
        state["evidence_assessment"] = evidence
    if plain_text is not None:
        state["decision_text_plain"] = plain_text
    return state


def _patch_run_analysis(monkeypatch, fake_ta):
    saved: list[dict] = []
    finished: list[dict] = []
    messages: list[dict] = []

    monkeypatch.setattr(server, "TradingAgentsGraph", lambda **_kwargs: fake_ta)
    monkeypatch.setattr(server, "mark_job_running", lambda _job_id: None)
    monkeypatch.setattr(server, "broadcast_threadsafe", messages.append)
    monkeypatch.setattr(
        server, "save_report", lambda **kwargs: saved.append(kwargs) or "report-1"
    )
    monkeypatch.setattr(
        server, "finish_job", lambda *args, **kwargs: finished.append(kwargs)
    )
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
        lambda *_args, **_kwargs: {"markdown": "持仓建议"},
    )
    return saved, finished, messages


# ---------------------------------------------------------------------------
# WebSocket 隔离
# ---------------------------------------------------------------------------


def test_websocket_broadcast_filters_by_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    alice = _FakeWS()
    bob = _FakeWS()
    admin = _FakeWS()
    monkeypatch.setattr(server, "active_connections", {alice, bob, admin})
    monkeypatch.setattr(
        server,
        "websocket_users",
        {
            alice: {"username": "alice", "role": "viewer"},
            bob: {"username": "bob", "role": "viewer"},
            admin: {"username": "root", "role": "admin"},
        },
    )

    message = {
        "type": "progress",
        "job_id": "job-1",
        "data": {"job_id": "job-1", "company": "600519", "message": "进度"},
        "_ws_owner": "alice",
    }
    asyncio.run(server._broadcast_async(message))

    assert len(alice.sent) == 1
    assert bob.sent == []
    assert len(admin.sent) == 1
    # 内部 owner 标记不能泄露给客户端
    assert "_ws_owner" not in alice.sent[0]
    assert alice.sent[0]["job_id"] == "job-1"


def test_websocket_initial_status_hides_other_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(
        server,
        "websocket_users",
        {
            "alice-ws": {"username": "alice", "role": "viewer"},
        },
    )
    # 当前任务属于 bob
    monkeypatch.setitem(server.analysis_status, "owner", "bob")
    monkeypatch.setitem(server.analysis_status, "running", True)
    monkeypatch.setitem(server.analysis_status, "company", "600519")
    monkeypatch.setitem(server.analysis_status, "job_id", "job-bob")

    assert server._ws_can_receive("alice-ws", "bob") is False
    assert server._ws_can_receive("alice-ws", "alice") is True
    assert server._ws_can_receive("alice-ws", None) is True


# ---------------------------------------------------------------------------
# 所有任务消息都带 job_id；不再二次改写
# ---------------------------------------------------------------------------


def test_all_job_messages_carry_job_id_and_no_rewrite(monkeypatch):
    state = _state(
        evidence={
            "level": "ok",
            "allow_directional": True,
            "summary": "核心证据通过",
            "items": [],
            "warnings": [],
        },
        plain_text="graph 写入的白话版本",
    )
    fake_ta = _FakeTa(state, decision="BUY")
    saved, finished, messages = _patch_run_analysis(monkeypatch, fake_ta)

    request = server.AnalyzeRequest(company="600519", date="2026-11-20")
    result = server.run_analysis_sync(request, "job-42", owner="alice")

    assert fake_ta.rewrite_calls == []
    assert result["decision_text_plain"] == "graph 写入的白话版本"

    expected = {
        "analysis_start",
        "progress",
        "node_change",
        "report_ready",
        "message",
        "analysis_complete",
        "status",
    }
    seen = {message["type"] for message in messages}
    assert expected <= seen
    for message in messages:
        assert message["job_id"] == "job-42", message["type"]
        assert message["data"]["job_id"] == "job-42", message["type"]
        assert message["_ws_owner"] == "alice"
    assert saved and finished[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# blocked：fail-closed 中性计划
# ---------------------------------------------------------------------------


def test_blocked_state_uses_neutral_plan_and_hitl_text(monkeypatch):
    # 缺失 evidence_assessment 且分析未完成 → server 重新评估后必须 blocked
    state = _state(completed=False)
    fake_ta = _FakeTa(state, decision="BUY")
    saved, finished, _messages = _patch_run_analysis(monkeypatch, fake_ta)

    request = server.AnalyzeRequest(company="600519", date="2026-11-20")
    result = server.run_analysis_sync(request, "job-blocked", owner="alice")

    evidence = result["evidence_assessment"]
    assert evidence["level"] == "blocked"
    assert evidence["allow_directional"] is False

    assert result["decision"] == "HOLD"
    plan = result["decision_plan"]
    assert plan["decision"] == "HOLD"
    assert plan["direction"] == "中性"
    assert plan["score"] == 0
    assert plan["consensus"] == "中性"
    assert plan["stop_loss"] == 0.0
    assert plan["take_profit"] == 0.0

    diff = result["decision_diff"]
    assert diff["preliminary_decision"] == "HOLD"
    assert diff["final_decision"] == "HOLD"
    assert diff["changed_fields"] == []

    assert result["position_advice"] is None
    assert "position_advice" not in result["reports"]
    assert "BUY" not in result["decision_text"]
    assert "核心数据不足" in result["decision_text_plain"]
    assert "BUY" not in result["reports"]["final_trade_decision"]

    assert fake_ta.rewrite_calls == []
    assert saved[0]["decision"] == "HOLD"
    assert saved[0]["meta"]["decision_plan"]["score"] == 0
    assert finished[0]["status"] == "completed"


def test_evidence_assessment_exception_fails_closed(monkeypatch):
    state = _state()
    state.pop("strategy_result", None)
    fake_ta = _FakeTa(state, decision="BUY")
    _saved, _finished, _messages = _patch_run_analysis(monkeypatch, fake_ta)

    def boom(*_args, **_kwargs):
        raise RuntimeError("assess failed")

    monkeypatch.setattr(
        "tradingagents.services.decision_validation.assess_analysis_evidence", boom
    )

    request = server.AnalyzeRequest(company="600519", date="2026-11-20")
    result = server.run_analysis_sync(request, "job-boom")

    assert result["evidence_assessment"]["level"] == "blocked"
    assert result["decision"] == "HOLD"
    assert result["decision_plan"]["score"] == 0


# ---------------------------------------------------------------------------
# jobs API 授权
# ---------------------------------------------------------------------------


def _job(job_id: str, owner: str, status: str = "queued", result=None) -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "error": "",
        "result": result,
        "meta": {"payload": {"owner": owner}},
    }


def test_list_jobs_filters_by_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    calls: list = []
    monkeypatch.setattr(
        server,
        "list_jobs",
        lambda limit, offset, owner: calls.append(owner) or [],
    )

    asyncio.run(
        server.api_list_jobs(
            limit=10, offset=0, user={"username": "alice", "role": "viewer"}
        )
    )
    asyncio.run(
        server.api_list_jobs(
            limit=10, offset=0, user={"username": "root", "role": "admin"}
        )
    )
    assert calls == ["alice", None]


@pytest.mark.parametrize(
    "endpoint",
    ["api_get_job", "api_get_job_result"],
)
def test_get_job_endpoints_enforce_owner(monkeypatch, endpoint):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(
        server,
        "get_job",
        lambda job_id, include_result: _job(
            "job-bob", "bob", status="completed", result={"decision": "BUY"}
        ),
    )

    func = getattr(server, endpoint)
    denied = asyncio.run(
        func("job-bob", user={"username": "alice", "role": "viewer"})
    )
    assert denied["success"] is False
    assert denied["code"] == "not_found"
    assert "job" not in denied

    allowed = asyncio.run(
        func("job-bob", user={"username": "bob", "role": "viewer"})
    )
    assert allowed["success"] is True

    admin = asyncio.run(
        func("job-bob", user={"username": "root", "role": "admin"})
    )
    assert admin["success"] is True


def test_cancel_job_enforces_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(
        server,
        "get_job",
        lambda job_id, include_result: _job("job-bob", "bob", status="running"),
    )
    queue = _FakeQueue()
    monkeypatch.setattr(server, "get_job_queue", lambda: queue)

    denied = asyncio.run(
        server.api_cancel_job(
            "job-bob", user={"username": "alice", "role": "viewer"}
        )
    )
    assert denied["success"] is False
    assert denied["code"] == "not_found"
    assert queue.cancelled == []

    allowed = asyncio.run(
        server.api_cancel_job(
            "job-bob", user={"username": "bob", "role": "viewer"}
        )
    )
    assert allowed["success"] is True
    assert queue.cancelled == ["job-bob"]


def test_delete_job_enforces_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(
        server,
        "get_job",
        lambda job_id, include_result: _job("job-bob", "bob", status="completed"),
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        server, "delete_job", lambda job_id: deleted.append(job_id) or True
    )

    denied = asyncio.run(
        server.api_delete_job(
            "job-bob", user={"username": "alice", "role": "viewer"}
        )
    )
    assert denied["success"] is False
    assert denied["code"] == "not_found"
    assert deleted == []

    allowed = asyncio.run(
        server.api_delete_job(
            "job-bob", user={"username": "bob", "role": "viewer"}
        )
    )
    assert allowed["success"] is True
    assert deleted == ["job-bob"]


# ---------------------------------------------------------------------------
# status / stop owner
# ---------------------------------------------------------------------------


def test_status_and_stop_enforce_owner(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(server, "get_job_queue", lambda: None)
    monkeypatch.setitem(server.analysis_status, "running", True)
    monkeypatch.setitem(server.analysis_status, "owner", "bob")
    monkeypatch.setitem(server.analysis_status, "company", "600519")
    monkeypatch.setitem(server.analysis_status, "job_id", "job-bob")

    alice_status = asyncio.run(
        server.get_status(user={"username": "alice", "role": "viewer"})
    )
    assert alice_status["running"] is False
    assert alice_status["company"] == ""
    assert alice_status["job_id"] == ""

    bob_status = asyncio.run(
        server.get_status(user={"username": "bob", "role": "viewer"})
    )
    assert bob_status["running"] is True
    assert bob_status["company"] == "600519"
    assert bob_status["job_id"] == "job-bob"

    alice_stop = asyncio.run(
        server.stop_analysis(user={"username": "alice", "role": "viewer"})
    )
    assert alice_stop["success"] is False

    queue = _FakeQueue()
    monkeypatch.setattr(server, "get_job_queue", lambda: queue)
    bob_stop = asyncio.run(
        server.stop_analysis(user={"username": "bob", "role": "viewer"})
    )
    assert bob_stop["success"] is True
    assert queue.cancelled == ["job-bob"]


# ---------------------------------------------------------------------------
# start_analysis 持久化完整 pipeline 配置
# ---------------------------------------------------------------------------


def test_start_analysis_persists_pipeline_meta(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=False)
    )
    queue = _FakeQueue()
    monkeypatch.setattr(server, "ensure_job_queue", lambda **_kwargs: queue)
    monkeypatch.setattr(
        server,
        "validate_analysis_date",
        lambda company, date: (True, "", date),
    )
    monkeypatch.setattr(
        "tradingagents.llm_clients.auth.ensure_provider_credentials",
        lambda *_args, **_kwargs: None,
    )

    request = server.AnalyzeRequest(
        company="600519",
        date="2026-11-20",
        pipeline_preset="standard",
        analysts=["market", "news"],
        strategies=["bull_trend"],
        llm_provider="anthropic",
    )
    response = asyncio.run(
        server.start_analysis(
            request,
            _user={"username": "local", "role": "admin"},
            _risk=None,
        )
    )

    assert response["success"] is True
    meta = queue.submitted["meta"]
    assert meta["pipeline_preset"] == "standard"
    assert meta["analysts"] == ["market", "news"]
    assert meta["strategies"] == ["bull_trend"]
    assert meta["llm_provider"] == "anthropic"
    assert isinstance(meta["pipeline"], dict)
    assert meta["pipeline"]["analysts"] == ["market", "news"]
    assert "max_debate_rounds" in meta["pipeline"]


def test_queue_full_does_not_leak_another_owners_job_id(monkeypatch):
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(multi_user=True)
    )
    monkeypatch.setattr(
        server,
        "validate_analysis_date",
        lambda company, date: (True, "", date),
    )
    queue = _FakeQueue()
    queue.active_count = lambda: server.MAX_PENDING_ANALYSES
    monkeypatch.setattr(server, "ensure_job_queue", lambda **_kwargs: queue)
    monkeypatch.setitem(server.analysis_status, "job_id", "job-alice")
    monkeypatch.setitem(server.analysis_status, "owner", "alice")

    request = server.AnalyzeRequest(company="600519", date="2026-11-20")
    bob_response = asyncio.run(
        server.start_analysis(
            request,
            _user={"username": "bob", "role": "viewer"},
            _risk=None,
        )
    )
    admin_response = asyncio.run(
        server.start_analysis(
            request,
            _user={"username": "root", "role": "admin"},
            _risk=None,
        )
    )

    assert bob_response["success"] is False
    assert bob_response["job_id"] == ""
    assert admin_response["job_id"] == "job-alice"
