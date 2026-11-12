"""P2 评估/学习闭环测试。

覆盖：
    - 中文检索的区分度（BM25 不再全部得 0 分）
    - 记忆的持久化往返（SQLite）
    - 记忆写入后 get_memories() 非空
    - 规则化（非 LLM）记忆写入器在分析结束后产出条目
    - past_memory_str 真的被注入提示词
    - evaluate_decision 在合成行情上算对前瞻收益
    - 对齐表的命中率汇总
    - 有效样本量不足时的标记

全部离线：不联网、不调用 LLM、不新增依赖。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.graph.reflection import MEMORY_ROLES, RuleBasedReflector
from tradingagents.services import evaluation
from tradingagents.services.evaluation import (
    build_evaluation_table,
    effective_sample_size,
    evaluate_decision,
    evaluate_reports,
)
from tradingagents.storage.memory_store import (
    clear_memories,
    count_memories,
    list_memories,
    load_all_memories,
    save_memory,
)


def _frame(closes, start="2024-01-02", freq="B") -> pd.DataFrame:
    """构造一段合成日线行情（仅用于测试，不联网）。"""
    dates = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [float(c) for c in closes],
            "High": [float(c) * 1.01 for c in closes],
            "Low": [float(c) * 0.99 for c in closes],
            "Close": [float(c) for c in closes],
            "Volume": [1000] * len(closes),
        }
    )


def _state() -> dict:
    """构造一份最小的分析最终状态。"""
    return {
        "company_of_interest": "600519",
        "trade_date": "2024-01-02",
        "market_report": "科技股波动加剧，成交量放大，RSI 处于超买区间。",
        "sentiment_report": "社交媒体情绪偏谨慎，讨论集中在估值消化。",
        "news_report": "公司公告季度营收略低于预期，机构下调评级。",
        "fundamentals_report": "市盈率处于历史 80% 分位，现金流稳定。",
        "investment_debate_state": {
            "bull_history": "Bull: 科技股波动虽大，但长期成长逻辑未变。",
            "bear_history": "Bear: 估值过高叠加机构减持，短期承压。",
            "judge_decision": "研究经理：谨慎看多，建议分批建仓。",
            "history": "Bull: ...\nBear: ...",
            "count": 2,
        },
        "risk_debate_state": {
            "aggressive_history": "激进：加杠杆买入。",
            "conservative_history": "保守：等待回调。",
            "neutral_history": "中性：分批建仓，控制仓位。",
            "judge_decision": "组合经理：买入，仓位 50%。",
            "history": "激进 ... 保守 ... 中性 ...",
            "count": 3,
        },
        "trader_investment_plan": "交易方案：在 1680 元附近买入 100 股，止损 1620。",
        "final_trade_decision": "最终决策：买入 600519，仓位 50%。",
    }


class ChineseRetrievalTests(unittest.TestCase):
    """中文检索必须有区分度。"""

    def setUp(self):
        self.memory = FinancialSituationMemory("test_memory")
        self.memory.add_situations(
            [
                ("科技股波动加剧，机构持续减持高成长板块", "降低成长股敞口"),
                ("消费板块需求疲软，量价背离明显", "关注防御性板块与现金流"),
            ]
        )

    def test_chinese_query_ranks_matching_document_first(self):
        hits = self.memory.get_memories("科技股波动加剧", n_matches=2)
        self.assertEqual(len(hits), 2)
        self.assertIn("科技股波动加剧", hits[0]["matched_situation"])
        self.assertNotIn("消费板块", hits[0]["matched_situation"])

    def test_chinese_top_score_is_positive(self):
        hits = self.memory.get_memories("科技股波动加剧，机构减持", n_matches=2)
        self.assertGreater(hits[0]["similarity_score"], 0.0)
        # 不匹配的文档得分必须是 0，而不是被归一化成"看起来像匹配"
        self.assertEqual(hits[1]["similarity_score"], 0.0)

    def test_unrelated_query_does_not_produce_fake_similarity(self):
        hits = self.memory.get_memories("完全无关的内容", n_matches=2)
        self.assertTrue(all(hit["similarity_score"] == 0.0 for hit in hits))

    def test_english_behaviour_preserved(self):
        memory = FinancialSituationMemory("en")
        memory.add_situations(
            [
                ("Tech sector showing high volatility", "Reduce tech exposure"),
                ("Consumer staples demand weakening", "Favor defensive sectors"),
            ]
        )
        hits = memory.get_memories("tech sector volatility", n_matches=2)
        self.assertIn("Tech sector", hits[0]["matched_situation"])
        self.assertGreater(hits[0]["similarity_score"], 0.0)

    def test_mixed_chinese_and_latin_tokens(self):
        memory = FinancialSituationMemory("mixed")
        memory.add_situations(
            [
                ("RSI 超买，MACD 死叉，趋势转弱", "降低仓位"),
                ("营收增速提升，毛利率改善", "继续持有"),
            ]
        )
        hits = memory.get_memories("RSI超买死叉", n_matches=1)
        self.assertIn("RSI", hits[0]["matched_situation"])
        self.assertGreater(hits[0]["similarity_score"], 0.0)


class MemoryApiTests(unittest.TestCase):
    def test_get_memories_non_empty_after_add(self):
        memory = FinancialSituationMemory("m")
        self.assertEqual(memory.get_memories("任意查询"), [])
        memory.add_situations([("科技股波动", "减仓"), ("消费疲软", "防御")])
        hits = memory.get_memories("科技股波动", n_matches=2)
        self.assertTrue(hits)
        self.assertIn("matched_situation", hits[0])
        self.assertIn("recommendation", hits[0])
        self.assertIn("similarity_score", hits[0])

    def test_clear_and_reset(self):
        memory = FinancialSituationMemory("m")
        memory.add_situations([("科技股波动", "减仓")])
        memory.clear()
        self.assertEqual(len(memory), 0)
        self.assertEqual(memory.get_memories("科技股波动"), [])
        memory.add_situations([("消费疲软", "防御")])
        memory.reset()
        self.assertEqual(len(memory), 0)

    def test_to_dict_from_dict_round_trip(self):
        memory = FinancialSituationMemory("m")
        memory.add_situations([("科技股波动", "减仓"), ("消费疲软", "防御")])
        restored = FinancialSituationMemory.from_dict(memory.to_dict())
        self.assertEqual(restored.documents, memory.documents)
        self.assertEqual(restored.recommendations, memory.recommendations)
        self.assertTrue(restored.get_memories("科技股波动"))


class MemoryStoreTests(unittest.TestCase):
    """记忆的持久化（SQLite）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "test.db")},
        )
        env.start()
        self.addCleanup(env.stop)

    def test_save_and_list(self):
        memory_id = save_memory(
            role="bull",
            situation="科技股波动加剧",
            recommendation="降低成长股敞口",
            ticker="600519",
            trade_date="2024-01-02",
        )
        self.assertGreater(memory_id, 0)
        items = list_memories(role="bull")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["situation"], "科技股波动加剧")
        self.assertEqual(items[0]["recommendation"], "降低成长股敞口")
        self.assertEqual(items[0]["ticker"], "600519")
        self.assertEqual(count_memories(role="bull"), 1)
        self.assertEqual(count_memories(role="bear"), 0)

    def test_memory_survives_save_load_round_trip(self):
        memory = FinancialSituationMemory("bull")
        memory.add_situations(
            [("科技股波动加剧，机构减持", "降低成长股敞口")]
        )
        save_memory(
            role="bull",
            situation=memory.documents[0],
            recommendation=memory.recommendations[0],
        )

        # 新实例：从库里恢复后应能检索到内容
        restored = FinancialSituationMemory("bull")
        rows = list_memories(role="bull")
        restored.add_situations(
            [(row["situation"], row["recommendation"]) for row in rows]
        )
        hits = restored.get_memories("科技股波动加剧", n_matches=1)
        self.assertTrue(hits)
        self.assertGreater(hits[0]["similarity_score"], 0.0)
        self.assertIn("降低成长股敞口", hits[0]["recommendation"])

    def test_load_all_memories_keyed_by_role(self):
        for role in ("bull", "bear", "trader"):
            save_memory(role=role, situation=f"{role} 情景", recommendation=f"{role} 建议")
        loaded = load_all_memories()
        for role in ("bull", "bear", "trader"):
            self.assertIn(role, loaded)
            self.assertEqual(len(loaded[role]), 1)

    def test_clear_memories(self):
        save_memory(role="bull", situation="a", recommendation="b")
        save_memory(role="bear", situation="c", recommendation="d")
        self.assertEqual(clear_memories(role="bull"), 1)
        self.assertEqual(count_memories(), 1)
        self.assertEqual(clear_memories(), 1)
        self.assertEqual(count_memories(), 0)


class RuleBasedReflectionTests(unittest.TestCase):
    """规则化记忆写入（零 LLM 调用）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "test.db")},
        )
        env.start()
        self.addCleanup(env.stop)

    def test_build_entries_covers_all_roles(self):
        writer = RuleBasedReflector()
        entries = writer.build_entries(_state())
        self.assertEqual(set(entries), set(MEMORY_ROLES))
        for situation, recommendation in entries.values():
            self.assertIn("科技股波动加剧", situation)
            self.assertTrue(recommendation)

    def test_write_produces_entries_without_llm(self):
        writer = RuleBasedReflector()
        memories = {role: FinancialSituationMemory(role) for role in MEMORY_ROLES}
        written = writer.write(_state(), memories, ticker="600519", trade_date="2024-01-02")
        self.assertEqual(set(written), set(MEMORY_ROLES))
        for memory in memories.values():
            self.assertEqual(len(memory), 1)
            self.assertTrue(memory.get_memories("科技股波动加剧", n_matches=1))

    def test_write_persists_to_store(self):
        writer = RuleBasedReflector()
        memories = {role: FinancialSituationMemory(role) for role in MEMORY_ROLES}
        writer.write(_state(), memories)
        self.assertEqual(count_memories(), len(MEMORY_ROLES))
        rows = list_memories(role="portfolio_manager")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "600519")

    def test_no_llm_invoked(self):
        """规则化路径完全不需要 LLM。"""

        writer = RuleBasedReflector()
        # 写入器本身不持有任何模型句柄
        self.assertFalse(hasattr(writer, "llm"))
        self.assertFalse(hasattr(writer, "quick_thinking_llm"))
        memories = {role: FinancialSituationMemory(role) for role in MEMORY_ROLES}
        written = writer.write(_state(), memories)
        self.assertEqual(len(written), len(MEMORY_ROLES))


class PromptPlumbingTests(unittest.TestCase):
    """past_memory_str 必须真的进入提示词。"""

    def test_bull_researcher_prompt_contains_memory(self):
        from tradingagents.agents.researchers.bull_researcher import (
            create_bull_researcher,
        )

        captured: dict[str, str] = {}

        class FakeLLM:
            def invoke(self, prompt):
                captured["prompt"] = str(prompt)
                message = type("Msg", (), {"content": "Bull: 测试论点"})()
                return message

        memory = FinancialSituationMemory("bull")
        memory.add_situations(
            [
                (
                    "科技股波动加剧，机构持续减持",
                    "历史经验：科技股波动加剧时应降低成长股敞口",
                )
            ]
        )

        node = create_bull_researcher(FakeLLM(), memory)
        node(
            {
                "messages": [],
                "investment_debate_state": {
                    "history": "",
                    "bull_history": "",
                    "current_response": "",
                    "count": 0,
                },
                "market_report": "科技股波动加剧",
                "sentiment_report": "情绪谨慎",
                "news_report": "机构下调评级",
                "fundamentals_report": "估值偏高",
            }
        )
        prompt = captured["prompt"]
        self.assertIn("历史经验：科技股波动加剧时应降低成长股敞口", prompt)
        # 提示词里确实存在"注入历史经验"的槽位（而不是被丢弃）
        self.assertIn("类似情境的反思与经验教训", prompt)

    def test_portfolio_manager_prompt_contains_memory(self):
        from tradingagents.agents.managers.portfolio_manager import (
            create_portfolio_manager,
        )

        captured: dict[str, str] = {}

        class FakeLLM:
            def invoke(self, prompt):
                captured["prompt"] = str(prompt)
                message = type("Msg", (), {"content": "最终决策：买入"})()
                return message

        memory = FinancialSituationMemory("pm")
        memory.add_situations(
            [("科技股波动加剧", "历史经验：组合经理应控制仓位在 50%")]
        )

        node = create_portfolio_manager(FakeLLM(), memory)
        node(
            {
                "messages": [],
                "company_of_interest": "600519",
                "trade_date": "2024-01-02",
                "market_report": "科技股波动加剧",
                "sentiment_report": "情绪谨慎",
                "news_report": "机构下调评级",
                "fundamentals_report": "估值偏高",
                "risk_debate_state": {
                    "history": "",
                    "judge_decision": "",
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "count": 0,
                },
                "trader_investment_plan": "买入 100 股",
            }
        )
        self.assertIn("历史经验：组合经理应控制仓位在 50%", captured["prompt"])


class EvaluateDecisionTests(unittest.TestCase):
    """evaluate_decision 在合成行情上的行为。"""

    def test_forward_return_computed_correctly(self):
        # 10 个交易日的等比上涨行情：每个交易日 +1%
        closes = [100 * (1.01**index) for index in range(30)]
        frame = _frame(closes)

        row = evaluate_decision(
            ticker="600519",
            decision="买入",
            entry_date="2024-01-02",
            horizons=(5, 10),
            frame=frame,
        )
        self.assertEqual(row["normalized_decision"], "BUY")
        self.assertEqual(row["direction"], "long")
        self.assertAlmostEqual(row["entry_price"], 100.0, places=6)

        five = row["horizons"]["5"]
        self.assertTrue(five["available"])
        self.assertAlmostEqual(five["forward_return"], 1.01**5 - 1, places=5)
        self.assertTrue(five["correct"])

        ten = row["horizons"]["10"]
        self.assertAlmostEqual(ten["forward_return"], 1.01**10 - 1, places=5)

    def test_sell_decision_correct_when_price_falls(self):
        closes = [100 * (0.99**index) for index in range(30)]
        frame = _frame(closes)
        row = evaluate_decision(
            ticker="600519",
            decision="卖出",
            entry_date="2024-01-02",
            horizons=(5,),
            frame=frame,
        )
        self.assertEqual(row["direction"], "short")
        self.assertTrue(row["horizons"]["5"]["correct"])

    def test_buy_decision_wrong_when_price_falls(self):
        closes = [100 * (0.99**index) for index in range(30)]
        frame = _frame(closes)
        row = evaluate_decision(
            ticker="600519",
            decision="买入",
            entry_date="2024-01-02",
            horizons=(5,),
            frame=frame,
        )
        self.assertFalse(row["horizons"]["5"]["correct"])
        self.assertFalse(row["correct"])

    def test_hold_correct_within_band(self):
        closes = [100 * (1.001**index) for index in range(30)]
        frame = _frame(closes)
        row = evaluate_decision(
            ticker="600519",
            decision="持有",
            entry_date="2024-01-02",
            horizons=(5,),
            frame=frame,
        )
        self.assertEqual(row["direction"], "flat")
        self.assertTrue(row["horizons"]["5"]["correct"])

    def test_price_loader_injection(self):
        """行情读取函数可以注入（测试不联网）。"""
        frame = _frame([100.0 + index for index in range(30)])
        calls: list[tuple] = []

        def loader(ticker, start, end):
            calls.append((ticker, start, end))
            return frame

        row = evaluate_decision(
            ticker="600519",
            decision="BUY",
            entry_date="2024-01-02",
            horizons=(5,),
            price_loader=loader,
        )
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "600519")
        self.assertTrue(row["horizons"]["5"]["available"])
        self.assertGreater(row["horizons"]["5"]["forward_return"], 0)

    def test_missing_future_data_marked_unavailable(self):
        frame = _frame([100.0] * 8)
        row = evaluate_decision(
            ticker="600519",
            decision="BUY",
            entry_date="2024-01-02",
            horizons=(5, 20),
            frame=frame,
        )
        self.assertTrue(row["horizons"]["5"]["available"])
        self.assertFalse(row["horizons"]["20"]["available"])
        self.assertIsNone(row["horizons"]["20"]["forward_return"])


class EvaluationTableTests(unittest.TestCase):
    """对齐表汇总。"""

    def _rows(self):
        up = _frame([100 * (1.01**index) for index in range(40)])
        down = _frame([100 * (0.99**index) for index in range(40)])
        loader_calls: list[str] = []

        def loader(ticker, start, end):
            loader_calls.append(ticker)
            return up if ticker == "UP" else down

        rows = []
        rows.append(
            evaluate_decision(
                "UP", "买入", "2024-01-02", horizons=(5, 10, 20), price_loader=loader
            )
        )
        rows.append(
            evaluate_decision(
                "DOWN", "买入", "2024-01-02", horizons=(5, 10, 20), price_loader=loader
            )
        )
        return rows

    def test_hit_rate_aggregation(self):
        rows = self._rows()
        table = build_evaluation_table(rows)
        self.assertEqual(table["total"], 2)
        self.assertEqual(table["by_decision"]["BUY"]["count"], 2)
        self.assertEqual(table["by_decision"]["BUY"]["hits"], 1)
        self.assertAlmostEqual(table["by_decision"]["BUY"]["hit_rate"], 0.5, places=4)
        self.assertEqual(table["by_ticker"]["UP"]["hits"], 1)
        self.assertEqual(table["by_ticker"]["DOWN"]["hits"], 0)
        self.assertAlmostEqual(table["overall"]["hit_rate"], 0.5, places=4)

    def test_by_horizon_breakdown(self):
        table = build_evaluation_table(self._rows())
        self.assertEqual(set(table["by_horizon"]), {"5", "10", "20"})
        for horizon in ("5", "10", "20"):
            self.assertEqual(table["by_horizon"][horizon]["count"], 2)

    def test_sample_insufficient_flag(self):
        """样本太小时必须打上 sample_insufficient 标记。"""
        table = build_evaluation_table(self._rows())
        self.assertEqual(table["min_effective_sample"], 8)
        self.assertTrue(table["sample_insufficient"])
        self.assertTrue(table["by_horizon"]["5"]["sample_insufficient"])

    def test_effective_sample_size_counts_non_overlapping(self):
        """同一天的多个决策窗口互相重叠，有效样本量应当更小。"""
        up = _frame([100 * (1.01**index) for index in range(60)])
        rows = []
        for day in ("2024-01-02", "2024-01-03", "2024-01-04"):
            rows.append(
                evaluate_decision(
                    "600519", "买入", day, horizons=(20,), frame=up
                )
            )
        # 三个窗口互相重叠 → 有效样本量最多 1
        self.assertEqual(effective_sample_size(rows, 20), 1)
        self.assertEqual(len(rows), 3)

        table = build_evaluation_table(rows)
        self.assertEqual(table["raw_count"], 3)
        self.assertEqual(table["effective_sample_size"], 1)
        self.assertTrue(table["sample_insufficient"])

    def test_effective_sample_size_grows_with_spacing(self):
        up = _frame([100 * (1.01**index) for index in range(200)], freq="B")
        rows = []
        for day in ("2024-01-02", "2024-03-01", "2024-05-01"):
            rows.append(
                evaluate_decision("600519", "买入", day, horizons=(20,), frame=up)
            )
        self.assertEqual(effective_sample_size(rows, 20), 3)

    def test_evaluate_reports_reads_rows_and_builds_table(self):
        reports = [
            {"ticker": "UP", "trade_date": "2024-01-02", "decision": "买入"},
            {"ticker": "DOWN", "trade_date": "2024-01-02", "decision": "买入"},
        ]
        up = _frame([100 * (1.01**index) for index in range(40)])
        down = _frame([100 * (0.99**index) for index in range(40)])

        def loader(ticker, start, end):
            return up if ticker == "UP" else down

        result = evaluate_reports(reports=reports, price_loader=loader)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["summary"]["total"], 2)
        self.assertAlmostEqual(
            result["summary"]["by_decision"]["BUY"]["hit_rate"], 0.5, places=4
        )

    def test_outcomes_written_back_to_memory(self):
        """评估结果必须能回写进记忆，让"决策 → 结果"闭环成立。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(tmp.name, "test.db")},
        )
        env.start()
        self.addCleanup(env.stop)

        reports = [
            {
                "ticker": "600519",
                "trade_date": "2024-01-02",
                "decision": "买入",
                "reports": {"market_report": "科技股波动加剧，成交量放大"},
            }
        ]
        up = _frame([100 * (1.01**index) for index in range(40)])

        def loader(ticker, start, end):
            return up

        result = evaluate_reports(
            reports=reports, price_loader=loader, write_memory=True
        )
        self.assertEqual(result["summary"]["memory_written"], 2)

        rows = list_memories(role="portfolio_manager")
        self.assertEqual(len(rows), 1)
        self.assertIn("决策：BUY", rows[0]["recommendation"])
        self.assertIn("5日收益", rows[0]["recommendation"])
        # 记忆的键与分析师检索用的键一致（报告摘要），才可能被命中
        self.assertIn("科技股波动加剧", rows[0]["situation"])


class GraphWiringTests(unittest.TestCase):
    """端到端：分析 → 规则化写入 → 落库 → 新实例恢复 → 提示词可检索。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(
            os.environ,
            {"TA_DB_PATH": os.path.join(self.tmp.name, "test.db")},
        )
        env.start()
        self.addCleanup(env.stop)

    def _build_graph(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        class FakeLLM:
            def invoke(self, prompt, *args, **kwargs):
                message = type("Msg", (), {"content": "测试输出", "tool_calls": []})()
                return message

            def bind_tools(self, tools, **kwargs):
                return self

        class FakeClient:
            def get_llm(self):
                return FakeLLM()

        config = {
            "llm_provider": "openai",
            "deep_think_llm": "fake",
            "quick_think_llm": "fake",
            "backend_url": None,
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "project_dir": self.tmp.name,
            "pipeline": {
                "analysts": ["market"],
                "enable_debate": True,
                "enable_trader": True,
                "enable_risk": True,
            },
        }
        with patch(
            "tradingagents.graph.trading_graph.create_llm_client",
            return_value=FakeClient(),
        ):
            return TradingAgentsGraph(config=config)

    def test_memory_survives_across_graph_instances(self):
        graph = self._build_graph()
        # 初始：库里没有记忆
        self.assertEqual(len(graph.bull_memory), 0)

        # 分析完成后写入记忆
        written = graph.remember_analysis(_state())
        self.assertEqual(set(written), set(MEMORY_ROLES))
        self.assertEqual(len(graph.bull_memory), 1)
        self.assertEqual(count_memories(), len(MEMORY_ROLES))

        # 新实例（模拟下一次分析）必须能恢复记忆
        graph2 = self._build_graph()
        self.assertEqual(len(graph2.bull_memory), 1)
        hits = graph2.bull_memory.get_memories("科技股波动加剧", n_matches=1)
        self.assertTrue(hits)
        self.assertGreater(hits[0]["similarity_score"], 0.0)

    def test_loaded_memory_reaches_prompt(self):
        """恢复的记忆必须真的能进入提示词（past_memory_str 非空）。"""
        from tradingagents.agents.researchers.bear_researcher import (
            create_bear_researcher,
        )

        graph = self._build_graph()
        graph.remember_analysis(_state())
        graph2 = self._build_graph()

        captured: dict[str, str] = {}

        class FakeLLM:
            def invoke(self, prompt):
                captured["prompt"] = str(prompt)
                return type("Msg", (), {"content": "Bear: 测试"})()

        node = create_bear_researcher(FakeLLM(), graph2.bear_memory)
        node(
            {
                "messages": [],
                "investment_debate_state": {
                    "history": "",
                    "bear_history": "",
                    "current_response": "",
                    "count": 0,
                },
                "market_report": "科技股波动加剧，成交量放大。",
                "sentiment_report": "情绪谨慎。",
                "news_report": "机构下调评级。",
                "fundamentals_report": "估值偏高。",
            }
        )
        prompt = captured["prompt"]
        self.assertIn("看空论点", prompt)
        self.assertIn("最终决策：买入 600519", prompt)


class NormalizationTests(unittest.TestCase):
    def test_normalize_decision(self):
        self.assertEqual(evaluation.normalize_decision("买入"), "BUY")
        self.assertEqual(evaluation.normalize_decision("增持"), "BUY")
        self.assertEqual(evaluation.normalize_decision("超配"), "OVERWEIGHT")
        self.assertEqual(evaluation.normalize_decision("卖出"), "SELL")
        self.assertEqual(evaluation.normalize_decision("低配"), "UNDERWEIGHT")
        self.assertEqual(evaluation.normalize_decision("持有"), "HOLD")
        self.assertEqual(evaluation.normalize_decision("BUY"), "BUY")
        self.assertEqual(evaluation.normalize_decision("看不懂"), "UNKNOWN")

    def test_direction_mapping(self):
        self.assertEqual(evaluation.decision_direction("BUY"), "long")
        self.assertEqual(evaluation.decision_direction("SELL"), "short")
        self.assertEqual(evaluation.decision_direction("HOLD"), "flat")
        self.assertEqual(evaluation.decision_direction("UNKNOWN"), "unknown")


if __name__ == "__main__":
    unittest.main()
