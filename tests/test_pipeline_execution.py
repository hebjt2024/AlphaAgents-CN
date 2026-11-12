"""用假 LLM 执行精简/标准图，验证自定义编排可真正跑通。"""

import unittest
from unittest.mock import patch

import pandas as pd
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_capital_flow,
    get_cashflow,
    get_dragon_tiger,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_news,
    get_northbound_flow,
    get_stock_data,
)
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.pipeline_presets import build_pipeline_config
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup


class FakeChatModel(BaseChatModel):
    """最小假模型：不联网，返回固定文本，支持 bind_tools。"""

    prefix: str = "测试输出"

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = self.prefix
        # 让组合经理输出一个可识别的决策
        joined = "\n".join(str(getattr(m, "content", "")) for m in messages)
        if "投资组合经理" in joined:
            text = "**买入（Buy）**\n\n测试最终决策：建议买入。"
        elif "交易助手" in joined:
            text = "测试交易方案：分批买入。"
        elif "研究经理" in joined or "辩论" in joined:
            text = "测试研究计划：建议买入。"
        message = AIMessage(content=text)
        return ChatResult(generations=[ChatGeneration(message=message)])


class FakeMemory:
    def get_memories(self, situation, n_matches=2):
        return []

    def add_situations(self, situations):
        return None


def _tool_nodes():
    return {
        "market": ToolNode(
            [get_stock_data, get_indicators, get_capital_flow, get_dragon_tiger]
        ),
        "social": ToolNode([get_news]),
        "news": ToolNode(
            [
                get_news,
                get_global_news,
                get_insider_transactions,
                get_northbound_flow,
            ]
        ),
        "fundamentals": ToolNode(
            [
                get_fundamentals,
                get_balance_sheet,
                get_cashflow,
                get_income_statement,
            ]
        ),
    }


def _setup(pipeline):
    memory = FakeMemory()
    return GraphSetup(
        FakeChatModel(),
        FakeChatModel(),
        _tool_nodes(),
        memory,
        memory,
        memory,
        memory,
        memory,
        ConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1),
        pipeline=pipeline,
    )


def _initial_state():
    state = Propagator().create_initial_state("600519", "2024-01-02", "short")
    state["messages"] = [HumanMessage(content="600519")]
    return state


def _price_frame():
    closes = [10 + i * 0.04 for i in range(120)]
    return pd.DataFrame(
        {
            "Date": pd.date_range("2023-01-01", periods=120, freq="D"),
            "Open": [c * 0.995 for c in closes],
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1000] * 120,
        }
    )


class PipelineExecutionTests(unittest.TestCase):
    def test_lite_pipeline_executes(self):
        cfg = build_pipeline_config(preset="lite")
        graph = _setup(cfg).setup_graph(cfg["analysts"])
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=_price_frame(),
        ):
            final = graph.invoke(_initial_state(), {"recursion_limit": 50})
        self.assertTrue(final.get("strategy_report"))
        self.assertTrue(final.get("market_report"))
        self.assertTrue(final.get("final_trade_decision"))
        # 精简流程不包含交易员/多空辩论/风险辩论
        self.assertFalse(final.get("trader_investment_plan"))
        self.assertFalse(
            final.get("investment_debate_state", {}).get("history")
        )
        self.assertFalse(final.get("risk_debate_state", {}).get("history"))

    def test_standard_pipeline_executes(self):
        cfg = build_pipeline_config(preset="standard")
        graph = _setup(cfg).setup_graph(cfg["analysts"])
        with patch(
            "tradingagents.services.backtest._load_prices",
            return_value=_price_frame(),
        ):
            final = graph.invoke(_initial_state(), {"recursion_limit": 100})
        self.assertTrue(final.get("strategy_report"))
        self.assertTrue(final.get("market_report"))
        self.assertTrue(final.get("investment_plan"))
        self.assertTrue(final.get("trader_investment_plan"))
        self.assertTrue(final.get("risk_debate_state", {}).get("history"))
        self.assertTrue(final.get("final_trade_decision"))


if __name__ == "__main__":
    unittest.main()
