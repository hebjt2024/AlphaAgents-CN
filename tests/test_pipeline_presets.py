"""自定义图编排：预设合并、状态初始化与图编译测试。"""

import os
import unittest
from unittest.mock import patch

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


def _compile(pipeline: dict, analysts: list[str]):
    setup = GraphSetup(
        object(),
        object(),
        _tool_nodes(),
        object(),
        object(),
        object(),
        object(),
        object(),
        ConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1),
        pipeline=pipeline,
    )
    return setup.setup_graph(analysts)


class PresetTests(unittest.TestCase):
    def test_presets(self):
        lite = build_pipeline_config(preset="lite")
        self.assertEqual(lite["analysts"], ["market", "fundamentals"])
        self.assertFalse(lite["enable_debate"])
        self.assertFalse(lite["enable_trader"])
        self.assertFalse(lite["enable_risk"])

        deep = build_pipeline_config(preset="deep")
        self.assertEqual(deep["max_debate_rounds"], 2)
        self.assertEqual(deep["max_risk_discuss_rounds"], 2)

    def test_custom_overrides(self):
        cfg = build_pipeline_config(
            analysts=["market", "news", "unknown"],
            enable_debate=False,
            enable_trader=True,
            enable_risk=True,
            preset="custom",
        )
        self.assertEqual(cfg["analysts"], ["market", "news"])
        self.assertFalse(cfg["enable_debate"])
        self.assertTrue(cfg["enable_risk"])

    def test_risk_requires_trader(self):
        cfg = build_pipeline_config(
            enable_trader=False, enable_risk=True, preset="custom"
        )
        self.assertFalse(cfg["enable_risk"])

    def test_preset_strategy_keys(self):
        from tradingagents.strategies import DEFAULT_STRATEGY_KEYS

        core = list(DEFAULT_STRATEGY_KEYS)
        self.assertEqual(
            build_pipeline_config(preset="lite")["strategy_keys"], core
        )
        self.assertEqual(
            build_pipeline_config(preset="standard")["strategy_keys"], core
        )
        self.assertEqual(
            build_pipeline_config(preset="standard-fast")["strategy_keys"], core
        )
        deep = build_pipeline_config(preset="deep")["strategy_keys"]
        self.assertGreater(len(deep), len(core))
        self.assertTrue(set(core) <= set(deep))

    def test_standard_fast_uses_quick_research_manager(self):
        self.assertEqual(
            build_pipeline_config(preset="standard-fast")["research_think"],
            "quick",
        )
        self.assertEqual(
            build_pipeline_config(preset="standard")["research_think"],
            "deep",
        )

    def test_standard_fast_uses_parallel_risk(self):
        self.assertTrue(
            build_pipeline_config(preset="standard-fast")["parallel_risk"]
        )
        self.assertFalse(
            build_pipeline_config(preset="standard")["parallel_risk"]
        )
        self.assertFalse(
            build_pipeline_config(preset="deep")["parallel_risk"]
        )


class GraphCompileTests(unittest.TestCase):
    def test_lite_pipeline_compiles_without_optional_nodes(self):
        cfg = build_pipeline_config(preset="lite")
        graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        self.assertIn("Portfolio Manager", nodes)
        self.assertIn("Strategy Scanner", nodes)
        self.assertTrue(
            "Parallel Analysts" in nodes or "Market Analyst" in nodes
        )
        self.assertNotIn("Bull Researcher", nodes)
        self.assertNotIn("Trader", nodes)
        self.assertNotIn("Aggressive Analyst", nodes)

    def test_standard_pipeline_compiles_with_all_nodes(self):
        cfg = build_pipeline_config(preset="standard")
        graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        for name in (
            "Strategy Scanner",
            "Bull Researcher",
            "Bear Researcher",
            "Research Manager",
            "Trader",
            "Aggressive Analyst",
            "Conservative Analyst",
            "Neutral Analyst",
            "Portfolio Manager",
        ):
            self.assertIn(name, nodes)

    def test_standard_fast_compiles_with_parallel_risk(self):
        cfg = build_pipeline_config(preset="standard-fast")
        graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        self.assertIn("Trader", nodes)
        self.assertIn("Parallel Risk", nodes)
        self.assertIn("Portfolio Manager", nodes)
        self.assertNotIn("Aggressive Analyst", nodes)
        self.assertNotIn("Conservative Analyst", nodes)
        self.assertNotIn("Neutral Analyst", nodes)

    def test_parallel_analysts_enabled(self):
        cfg = build_pipeline_config(preset="standard")
        with patch.dict(
            os.environ, {"TRADINGAGENTS_PARALLEL_ANALYSTS": "1"}, clear=False
        ):
            graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        self.assertIn("Parallel Analysts", nodes)
        self.assertNotIn("Market Analyst", nodes)

    def test_parallel_analysts_can_fall_back_to_serial(self):
        cfg = build_pipeline_config(preset="standard")
        with patch.dict(
            os.environ, {"TRADINGAGENTS_PARALLEL_ANALYSTS": "0"}, clear=False
        ):
            graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        self.assertIn("Market Analyst", nodes)
        self.assertNotIn("Parallel Analysts", nodes)

    def test_custom_pipeline_without_debate_and_risk(self):
        cfg = build_pipeline_config(
            analysts=["market", "social", "news", "fundamentals"],
            enable_debate=False,
            enable_trader=True,
            enable_risk=False,
            preset="custom",
        )
        graph = _compile(cfg, cfg["analysts"])
        nodes = set(graph.get_graph().nodes)
        self.assertNotIn("Bull Researcher", nodes)
        self.assertIn("Strategy Scanner", nodes)
        self.assertIn("Trader", nodes)
        self.assertNotIn("Aggressive Analyst", nodes)
        self.assertIn("Portfolio Manager", nodes)


class InitialStateTests(unittest.TestCase):
    def test_decision_fields_are_initialized(self):
        state = Propagator().create_initial_state(
            "600519", "2024-01-02", "short"
        )
        for key in (
            "investment_plan",
            "trader_investment_plan",
            "final_trade_decision",
        ):
            self.assertIn(key, state)
        self.assertEqual(state["analysis_horizon"], "short")


if __name__ == "__main__":
    unittest.main()
