"""P3 测试覆盖补齐：CLI、图编排、Agent 工具、调度器。

背景：项目此前 203 个测试几乎没有覆盖到以下关键路径：

- ``cli/main.py``（2863 行）只有 1 个函数被测过，且存在一个真实的
  星号导入覆盖 bug（本地同名定义覆盖了 ``cli/utils.py`` 里带规范化的
  实现）；
- ``tradingagents/graph/`` 的图编排与条件路由；
- ``tradingagents/agents/utils/`` 的工具层；
- ``tradingagents/scheduler.py`` 的调度判定逻辑。

本文件补齐这些路径的单元测试（全部离线，不触网、不调用 LLM）。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# 隔离测试数据库，避免污染开发环境
_TMP_DB = Path(tempfile.mkdtemp(prefix="alphaagents-p3-")) / "test.db"
os.environ.setdefault("TA_DB_PATH", str(_TMP_DB))


class CliImportHygieneTests(unittest.TestCase):
    """CLI 模块的导入卫生：星号导入不得悄悄覆盖关键函数。"""

    def test_cli_main_does_not_shadow_utils_helpers(self):
        import cli.main as main_module
        import cli.utils as utils

        self.assertIs(
            main_module.get_ticker,
            utils.get_ticker,
            "cli.main 覆盖了 cli.utils.get_ticker —— "
            "CLI 会因此跳过代码归一化",
        )
        self.assertIs(main_module.get_analysis_date, utils.get_analysis_date)

    def test_normalize_ticker_symbol_is_used_by_cli(self):
        from cli.utils import normalize_ticker_symbol

        self.assertEqual(normalize_ticker_symbol("  600519 "), "600519")
        self.assertEqual(normalize_ticker_symbol("  nvda "), "NVDA")


class RootEntryPointTests(unittest.TestCase):
    """根目录 main.py 不得在**导入时**触发一次付费分析。"""

    def test_importing_main_has_no_side_effects(self):
        import ast

        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        # 模块顶层不得出现对 propagate() 的调用（那会在导入时触发付费分析）
        def _calls_propagate(node) -> bool:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    if isinstance(func, ast.Attribute) and func.attr == "propagate":
                        return True
            return False

        module_level_propagate = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Expr) and _calls_propagate(node)
        ]
        self.assertEqual(
            module_level_propagate,
            [],
            "main.py 在模块顶层调用了 propagate()，导入即触发一次付费分析",
        )

        self.assertTrue(
            any(
                isinstance(node, ast.FunctionDef) and node.name == "main"
                for node in tree.body
            ),
            "main.py 应提供 main() 入口函数",
        )
        self.assertIn(
            "__main__",
            source,
            "main.py 缺少 if __name__ == '__main__' 守卫",
        )

    def test_main_module_imports_without_triggering_analysis(self):
        import importlib

        module = importlib.import_module("main")
        self.assertTrue(callable(module.main))


class PipelinePresetTests(unittest.TestCase):
    """流水线预设：阶段开关必须真的影响图结构。"""

    def test_presets_expose_expected_shape(self):
        from tradingagents.graph.pipeline_presets import (
            ALL_ANALYSTS,
            get_preset,
        )

        for name in ("lite", "standard", "deep"):
            preset = get_preset(name)
            self.assertIn("analysts", preset)
            self.assertIn("enable_debate", preset)
            self.assertIn("enable_trader", preset)
            self.assertIn("enable_risk", preset)

        self.assertTrue(ALL_ANALYSTS)

    def test_risk_requires_trader(self):
        from tradingagents.graph.pipeline_presets import get_preset

        preset = get_preset("custom")
        # 开启风控就必须有交易员环节，否则风控辩论没有输入
        if preset.get("enable_risk") and not preset.get("enable_trader"):
            self.fail("风控环节不能脱离交易员独立开启")

    def test_lite_preset_is_cheapest(self):
        from tradingagents.graph.pipeline_presets import get_preset

        lite = get_preset("lite")
        standard = get_preset("standard")
        self.assertLessEqual(
            len(lite.get("analysts") or []),
            len(standard.get("analysts") or []),
        )


class ConditionalLogicTests(unittest.TestCase):
    """条件路由：辩论计数决定何时收敛。"""

    def test_debate_stops_after_configured_rounds(self):
        from tradingagents.graph.conditional_logic import ConditionalLogic

        logic = ConditionalLogic()
        for max_rounds in (1, 2):
            state = {
                "investment_debate_state": {
                    "count": 2 * max_rounds,
                    "current_response": "Bull: done",
                }
            }
            self.assertEqual(logic.should_continue_debate(state), "Research Manager")

    def test_debate_continues_below_threshold(self):
        from tradingagents.graph.conditional_logic import ConditionalLogic

        logic = ConditionalLogic()
        state = {
            "investment_debate_state": {
                "count": 0,
                "current_response": "Bull: still talking",
            }
        }
        self.assertNotEqual(logic.should_continue_debate(state), "Research Manager")

    def test_risk_debate_stops_after_configured_rounds(self):
        from tradingagents.graph.conditional_logic import ConditionalLogic

        logic = ConditionalLogic()
        for max_rounds in (1, 2):
            state = {
                "risk_debate_state": {
                    "count": 3 * max_rounds,
                    "latest_speaker": "Neutral: done",
                }
            }
            self.assertEqual(
                logic.should_continue_risk_analysis(state), "Portfolio Manager"
            )


class AgentToolTests(unittest.TestCase):
    """工具层：参数解析与异常处理不能把脏数据送给 LLM。"""

    def test_indicator_tool_splits_comma_separated_list(self):
        from tradingagents.agents.utils.technical_indicators_tools import (
            get_indicators,
        )

        with patch(
            "tradingagents.agents.utils.technical_indicators_tools.route_to_vendor",
            return_value="ok",
        ) as route:
            get_indicators.func(symbol="600519", indicator="rsi, macd", curr_date="2026-01-01")
            # 逗号分隔必须被拆成多次调用，而不是把 "rsi, macd" 当成一个指标
            self.assertGreaterEqual(route.call_count, 2)

    def test_strategy_tool_is_exposed(self):
        from tradingagents.agents.utils import strategy_tools

        self.assertTrue(hasattr(strategy_tools, "get_strategy_signals"))

    def test_capital_flow_tool_handles_missing_data(self):
        """资金流数据不可用时，工具必须返回明确说明而不是抛异常。"""
        from tradingagents.agents.utils import capital_flow_tools

        tool = capital_flow_tools.get_capital_flow
        with patch(
            "tradingagents.agents.utils.capital_flow_tools.route_to_vendor",
            side_effect=RuntimeError("No available vendor for 'get_capital_flow'"),
        ):
            result = tool.func(ticker="600519", curr_date="2026-01-01")
            self.assertIsInstance(result, str)
            self.assertTrue(result)

    def test_search_tool_without_api_key_returns_guidance(self):
        from tradingagents.agents.utils import search_tools

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            result = search_tools.search_web.func(query="贵州茅台")
            self.assertIsInstance(result, str)


class SchedulerLogicTests(unittest.TestCase):
    """调度判定：交易日、补跑与失败重试。"""

    def test_scheduler_module_exposes_public_api(self):
        from tradingagents import scheduler

        self.assertTrue(hasattr(scheduler, "start_scheduler"))
        self.assertTrue(hasattr(scheduler, "stop_scheduler"))
        self.assertTrue(hasattr(scheduler, "run_scheduler_once") or hasattr(
            scheduler, "main"
        ), "调度器应提供一次性运行入口，以便用 cron/systemd 外部调度")

    def test_scheduler_state_is_persisted(self):
        """当日批次状态必须落库，重启后不能重复跑。"""
        from tradingagents.storage import scheduled_store

        scheduled_store.set_state("p3-test-key", "2026-11-12")
        self.assertEqual(
            scheduled_store.get_state("p3-test-key"), "2026-11-12"
        )
        scheduled_store.set_state("p3-test-key", "")
        self.assertEqual(scheduled_store.get_state("p3-test-key"), "")


class ReportIdempotencyTests(unittest.TestCase):
    """报告去重：同一 (代码, 日期) 不应产生重复行。"""

    def test_duplicate_detection_helper(self):
        from tradingagents.storage.job_store import find_duplicate_report

        self.assertIsNotNone(find_duplicate_report)
        # 没有写入任何报告时不应报错
        result = find_duplicate_report("__不存在__", "2026-11-12")
        self.assertIn(result, (None, False, 0))


if __name__ == "__main__":
    unittest.main()
