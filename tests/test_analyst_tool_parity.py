"""分析师工具清单与 ToolNode 必须完全一致。

历史缺陷：分析师节点 bind_tools 列出了 11 个工具，但 ToolNode 只注册了 4 个。
LLM 一旦调用未注册的工具，ToolNode 直接抛错，报告只剩开头一句"我先去取数据"，
下游只能得出"数据不足、无法判断"。这里用测试锁死两者的一致性。
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from tradingagents.agents.analysts.fundamentals_analyst import (
    FUNDAMENTALS_ANALYST_TOOLS,
)
from tradingagents.agents.analysts.market_analyst import MARKET_ANALYST_TOOLS
from tradingagents.agents.analysts.news_analyst import NEWS_ANALYST_TOOLS
from tradingagents.agents.analysts.social_media_analyst import (
    SOCIAL_ANALYST_TOOLS,
)
from tradingagents.graph.setup import _execute_tool_calls
from tradingagents.graph.trading_graph import TradingAgentsGraph

EXPECTED = {
    "market": MARKET_ANALYST_TOOLS,
    "social": SOCIAL_ANALYST_TOOLS,
    "news": NEWS_ANALYST_TOOLS,
    "fundamentals": FUNDAMENTALS_ANALYST_TOOLS,
}


@tool
def _echo(text: str) -> str:
    """返回输入文本。"""
    return text


def test_tool_nodes_cover_every_analyst_tool():
    tool_nodes = TradingAgentsGraph._create_tool_nodes(None)
    assert set(tool_nodes) == set(EXPECTED)
    for analyst_type, tools in EXPECTED.items():
        registered = set(tool_nodes[analyst_type].tools_by_name)
        declared = {tool.name for tool in tools}
        assert declared <= registered, (
            f"{analyst_type} 分析师会调用 {sorted(declared - registered)}，"
            "但 ToolNode 未注册这些工具"
        )


def test_execute_tool_calls_does_not_require_graph_runtime():
    """并行分析师在图层之外手动执行工具，不能依赖图注入的 runtime config。"""
    node = ToolNode([_echo])
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "_echo", "args": {"text": "hello"}, "id": "call-1"}
        ],
    )
    results = _execute_tool_calls(node, message)
    assert len(results) == 1
    assert results[0].content == "hello"
    assert results[0].tool_call_id == "call-1"


def test_execute_tool_calls_returns_error_message_for_unknown_tool():
    node = ToolNode([_echo])
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "not_registered", "args": {}, "id": "call-2"}
        ],
    )
    results = _execute_tool_calls(node, message)
    assert len(results) == 1
    assert "不存在" in results[0].content
