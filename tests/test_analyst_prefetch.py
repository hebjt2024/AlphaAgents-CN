"""social/news 核心数据预取：规格、格式化与全失败回退。"""

from tradingagents.graph.setup import (
    _prefetch_analyst_material,
    _prefetch_specs,
)


class _FakeTool:
    def __init__(self, output):
        self.output = output

    def invoke(self, args):
        return self.output


class _FakeToolNode:
    def __init__(self, names, output="data"):
        self.tools_by_name = {name: _FakeTool(f"{name}:{output}") for name in names}


def test_social_specs_include_core_tools(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    names = [name for name, _ in _prefetch_specs("social", "600737", "2026-11-17")]
    assert names == ["get_social_sentiment", "get_news"]


def test_news_specs_include_core_tools(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    names = [name for name, _ in _prefetch_specs("news", "600737", "2026-11-17")]
    assert names == [
        "get_news",
        "get_announcements",
        "get_global_news",
        "get_macro_overview",
        "get_northbound_flow",
    ]


def test_prefetch_material_contains_tool_data(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    node = _FakeToolNode(["get_social_sentiment", "get_news"], output="ok")
    text = _prefetch_analyst_material(
        "social",
        {"company_of_interest": "600737", "trade_date": "2026-11-17"},
        node,
    )
    assert "系统预取的核心数据" in text
    assert "工具 get_social_sentiment" in text
    assert "get_news:ok" in text


def test_prefetch_all_failed_returns_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    class _BrokenTool:
        def invoke(self, args):
            raise RuntimeError("boom")

    class _BrokenNode:
        tools_by_name = {
            "get_social_sentiment": _BrokenTool(),
            "get_news": _BrokenTool(),
        }

    text = _prefetch_analyst_material(
        "social",
        {"company_of_interest": "600737", "trade_date": "2026-11-17"},
        _BrokenNode(),
    )
    assert text == ""
