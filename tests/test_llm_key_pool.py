"""同模型多 Key：解析、轮询、失败切换。"""

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate

from tradingagents.llm_clients.key_pool import (
    KeyPool,
    RotatingChatModel,
    is_transient_llm_error,
    resolve_api_keys,
)


class _FakeClient:
    def __init__(self, key: str, responses: dict[str, object]):
        self.api_key = key
        self.responses = responses
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        response = self.responses[self.api_key]
        if isinstance(response, Exception):
            raise response
        return response


class _FakeBase:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.model = "fake-model"

    def model_copy(self, update):
        return _FakeClient(update["api_key"], self.responses)


class _RateLimitError(Exception):
    pass


def test_resolve_api_keys_parses_multiple_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEYS", "key-a,key-b;\nkey-c")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEYS", raising=False)
    keys = resolve_api_keys(
        "anthropic", "https://api.minimax.cn/anthropic"
    )
    assert keys == ["key-a", "key-b", "key-c"]


def test_key_pool_round_robin_and_cooldown():
    pool = KeyPool(["k1", "k2"], cooldown_seconds=60)
    first = pool.acquire()
    second = pool.acquire()
    assert {first[1], second[1]} == {"k1", "k2"}
    pool.mark_failure(first[0])
    # 冷却中的 Key 不应被优先选中
    third = pool.acquire()
    assert third[1] == second[1]


def test_rotating_model_switches_on_empty_content():
    base = _FakeBase(
        {
            "k1": AIMessage(content=""),
            "k2": AIMessage(content="有效报告"),
        }
    )
    llm = RotatingChatModel(base, KeyPool(["k1", "k2"], cooldown_seconds=30))
    result = llm.invoke("分析 000878")
    assert result.content == "有效报告"


def test_rotating_model_switches_on_transient_error():
    base = _FakeBase(
        {
            "k1": _RateLimitError("429 rate limit"),
            "k2": AIMessage(content="ok"),
        }
    )
    llm = RotatingChatModel(base, KeyPool(["k1", "k2"]))
    assert llm.invoke("hi").content == "ok"


def test_transient_error_detection():
    assert is_transient_llm_error(_RateLimitError("429 rate limit"))
    assert not is_transient_llm_error(ValueError("invalid request"))


def test_rotating_model_bind_tools_works_in_lcel_pipeline():
    base = _FakeBase({"k1": AIMessage(content="pipeline ok")})
    llm = RotatingChatModel(base, KeyPool(["k1"]))
    chain = ChatPromptTemplate.from_messages([("user", "{text}")]) | llm.bind_tools(
        []
    )
    assert chain.invoke({"text": "hello"}).content == "pipeline ok"
