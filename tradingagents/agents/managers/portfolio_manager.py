# ============================================================================
# 模块：投资组合经理 (Portfolio Manager)
# 角色：多 Agent 系统中的最终决策者，综合风险分析师辩论结果，做出最终交易决策
# 核心功能：
#   1. 收集所有分析报告（市场、情绪、新闻、基本面）
#   2. 审阅交易员的投资计划
#   3. 综合风险分析师（激进/保守/中性）的辩论历史
#   4. 参考历史记忆（从类似情况中学习）
#   5. 给出最终评级（Buy/Overweight/Hold/Underweight/Sell）
# 技术栈：LangChain 的 LLM 直接调用（invoke）、记忆系统（memory）、
#         LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    投资组合经理用 LLM 来"综合"所有信息，做出最终交易决策。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。投资组合经理不使用工具，
#    而是直接用 LLM 分析已有报告和风险辩论历史，做出最终决策。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    投资组合经理只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。投资组合经理是图中的最后一个分析节点，
#    在风险辩论结束后执行，是整个系统的"终审"环节。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    投资组合经理从黑板上读取：
#      - company_of_interest（公司名）
#      - risk_debate_state（风险辩论状态：含完整历史）
#      - market_report（市场分析报告）
#      - news_report（新闻分析报告）
#      - fundamentals_report（基本面分析报告）
#      - sentiment_report（情绪/舆情分析报告）
#      - investment_plan（交易员的投资计划）
#    投资组合经理写回到黑板上：
#      - risk_debate_state（添加 judge_decision 最终裁决）
#      - final_trade_decision（最终交易决策）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_portfolio_manager(llm, memory) 是工厂函数：
#    传入 LLM 和记忆系统，返回一个节点函数。
#
# 7. 什么是记忆系统（Memory）？
#    FinancialSituationMemory 使用 BM25 算法检索相似的历史情况。
#    投资组合经理用它来检索历史上类似情况下的决策经验。
#    记忆系统允许 Agent 从过去的成功和失败中学习，
#    避免重复犯错，持续改进决策质量。
#
# 8. 投资组合经理在系统中的角色和职责
#    投资组合经理是整个系统的"最终决策者"：
#    - 它不是分析师，而是决策者
#    - 它综合所有分析师的报告和风险辩论结果
#    - 它参考历史记忆来避免重复犯错
#    - 它给出最终的投资评级和决策
#
#    与研究经理的区别：
#    - 研究经理：评估投资辩论（investment_debate），制定投资计划
#    - 投资组合经理：评估风险辩论（risk_debate），做出最终交易决策 ← 本文件
#
# 9. 评级标准（Rating Scale）
#    投资组合经理使用五级评级：
#    - Buy：强烈信心买入或加仓
#    - Overweight：看好，逐步增加敞口
#    - Hold：维持当前仓位
#    - Underweight：减少敞口，部分获利了结
#    - Sell：退出或避免入场
#
# 10. 系统中的整体流程（本节点的位置）
#     用户输入 → [分析师] → [牛熊辩论] → [研究经理] → [交易员] → [风险辩论] → [组合经理] ← 本文件（最终决策）
#
# ============================================================================

# --- 导入项目内部工具函数 ---
# build_instrument_context：构建目标公司的上下文信息字符串
from tradingagents.agents.utils.agent_utils import build_instrument_context


# ============================================================================
# 工厂函数：create_portfolio_manager
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建投资组合经理需要传入 LLM 实例和记忆系统。
# 工厂函数模式解决这个矛盾：
#   外层 create_portfolio_manager(llm, memory) 接收 llm 和 memory，
#   内层 portfolio_manager_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 和 memory 变量。
# ============================================================================
def create_portfolio_manager(llm, memory):
    """
    创建投资组合经理节点函数（工厂函数模式）。

    投资组合经理是整个系统的"最终决策者"：
    - 它不是分析师，而是决策者
    - 它综合所有分析师的报告和风险辩论结果
    - 它参考历史记忆来避免重复犯错
    - 它给出最终的投资评级和决策

    参数：
        llm: 大语言模型实例，驱动推理和决策
        memory: 记忆系统实例，用于检索历史决策和经验教训
            记忆系统允许 Agent 从过去的成功和失败中学习，
            避免重复犯错，持续改进决策质量。
    """

    # ========================================================================
    # 内层节点函数：portfolio_manager_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def portfolio_manager_node(state) -> dict:
        """
        投资组合经理节点：LangGraph 工作流中的标准节点函数。

        这个节点在风险辩论阶段之后执行，是整个系统的"终审"环节。

        参数：
            state: 状态字典，包含：
                - company_of_interest: 目标公司名称
                - risk_debate_state: 风险辩论状态（包含辩论历史和各方观点）
                - market_report: 市场分析报告
                - news_report: 新闻分析报告
                - fundamentals_report: 基本面分析报告
                - sentiment_report: 情绪/舆情分析报告
                - investment_plan: 交易员的投资计划

        【读取的状态字段】：
            - state["company_of_interest"]：目标公司名称
            - state["risk_debate_state"]：风险辩论状态
              - .history：完整的风险辩论历史
            - state["market_report"]：市场分析报告
            - state["news_report"]：新闻分析报告
            - state["fundamentals_report"]：基本面分析报告
            - state["sentiment_report"]：情绪/舆情分析报告
            - state["investment_plan"]：交易员的投资计划

        【写入的状态字段】：
            - "risk_debate_state"：更新后的辩论状态（含 judge_decision）
            - "final_trade_decision"：最终交易决策文本
        """

        # 构建目标公司的上下文信息
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        instrument_context = build_instrument_context(state["company_of_interest"], state.get("analysis_horizon", "medium"))

        # ====================================================================
        # 提取风险辩论的历史记录
        #
        # risk_debate_state 是一个字典，包含了整个风险辩论的状态：
        #   - history: 完整的辩论历史（所有角色的发言按顺序排列）
        #   - aggressive_history: 激进分析师的历史发言
        #   - conservative_history: 保守分析师的历史发言
        #   - neutral_history: 中性分析师的历史发言
        #   - latest_speaker: 最后发言者
        #   - count: 辩论轮数
        #
        # 投资组合经理需要读取完整的风险辩论历史来评估三方观点。
        # ====================================================================
        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]

        # 提取四份分析报告（由四个分析师 Agent 分别生成）
        market_research_report = state["market_report"]          # 市场分析师报告
        news_report = state["news_report"]                        # 新闻分析师报告
        fundamentals_report = state["fundamentals_report"]        # 基本面分析师报告
        sentiment_report = state["sentiment_report"]              # 社交媒体分析师报告

        # 提取交易员的执行方案；若自定义流程跳过了交易员节点，
        # 则回退到研究经理的投资计划，保证最终裁决仍有决策依据。
        trader_plan = (
            state.get("trader_investment_plan")
            or state.get("investment_plan", "")
        )

        # 分析师报告快照（截断超长内容，控制上下文长度）
        def _clip(text: str, limit: int = 6000) -> str:
            text = str(text or "").strip()
            return text if len(text) <= limit else text[:limit] + "..."

        analyst_reports = (
            f"### 市场分析\n{_clip(market_research_report)}\n\n"
            f"### 舆情分析\n{_clip(sentiment_report)}\n\n"
            f"### 新闻分析\n{_clip(news_report)}\n\n"
            f"### 基本面分析\n{_clip(fundamentals_report)}"
        )

        # ====================================================================
        # 记忆检索（Memory Retrieval）
        #
        # 【什么是记忆检索？】
        # 将当前所有的分析报告合并成一个"当前情况"描述字符串，
        # 然后通过 memory.get_memories() 检索历史上相似的情况。
        #
        # memory.get_memories(curr_situation, n_matches=2) 的工作方式：
        #   1. 将 curr_situation 转换为向量（embedding）
        #   2. 在历史记忆中搜索最相似的 2 条记录
        #   3. 返回这些记录，包含之前的决策建议和经验教训
        #
        # 这种设计实现了"经验学习"——Agent 不会重复犯同样的错误。
        #
        # f"..." 是 Python 的 f-string，{变量} 会被替换为实际值
        # \n\n 是两个换行符，用于分隔不同报告
        # ====================================================================
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        # 将检索到的历史记忆格式化为字符串，方便注入到 LLM 提示词中
        # enumerate(past_memories, 1) 从 1 开始编号遍历
        # rec["recommendation"] 是每条记忆中的经验教训文本
        past_memory_str = ""
        for _i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ====================================================================
        # 构建投资组合经理的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。投资组合经理用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："As the Portfolio Manager, synthesize the risk analysts'
        #          debate and deliver the final trading decision."
        #   → 角色定义：投资组合经理。
        #     核心任务：综合风险分析师的辩论，给出最终交易决策。
        #
        # {instrument_context}
        #   → 公司上下文信息，确保 LLM 知道分析的是哪家公司。
        #
        # Rating Scale（评级标准）：
        #   使用五级评级（比研究经理的三级更细）：
        #   - Buy：强烈信心买入或加仓
        #   - Overweight：看好，逐步增加敞口
        #   - Hold：维持当前仓位，不需要行动
        #   - Underweight：减少敞口，部分获利了结
        #   - Sell：退出或避免入场
        #
        #   为什么用五级而非三级？
        #   投资组合经理是最终决策者，需要更精细的评级来指导实际操作。
        #   "use exactly one" 要求 LLM 只选一个，不能含糊。
        #
        # Context（上下文）：
        #   - Trader's proposed plan：交易员的投资计划（需要评估的方案）
        #   - Lessons from past decisions：过去决策的教训（历史记忆）
        #
        # Required Output Structure（要求的输出结构）：
        #   1. Rating：评级（Buy/Overweight/Hold/Underweight/Sell 之一）
        #   2. Executive Summary：执行摘要（入场策略、仓位大小、关键风险水平、时间范围）
        #   3. Investment Thesis：投资论点（基于分析师辩论和历史反思的详细推理）
        #
        # Risk Analysts Debate History（风险分析师辩论历史）：
        #   {history}：完整的风险辩论历史，让投资组合经理能看到三方辩论
        #
        # 最后一段："Be decisive and ground every conclusion in specific
        #          evidence from the analysts."
        #   → 决策要求：果断，每个结论都要有分析师辩论中的具体证据支撑。
        #     防止 LLM 做出没有依据的决策。
        # ====================================================================
        prompt = f"""作为投资组合经理，请综合风险分析师的辩论，并给出最终交易决策。

{instrument_context}

---

**评级量表**（请仅使用以下之一）：
- **买入（Buy）**：强烈看好，建仓或加仓
- **超配（Overweight）**：前景乐观，逐步增加敞口
- **持有（Hold）**：维持当前仓位，无需操作
- **低配（Underweight）**：降低敞口，部分获利了结
- **卖出（Sell）**：退出仓位或避免入场

**背景信息：**
- 交易员提议的计划：**{trader_plan}**
- 过往决策的教训：**{past_memory_str}**

**分析师报告：**
{analyst_reports}

**要求的输出结构：**
1. **评级（Rating）**：明确给出买入 / 超配 / 持有 / 低配 / 卖出 之一。
2. **执行摘要（Executive Summary）**：涵盖入场策略、仓位规模、关键风险位和时间维度的简明行动计划。
3. **投资论点（Investment Thesis）**：基于分析师辩论和过往反思的详细推理。

---

**风险分析师辩论历史：**
{history}

---

请果断决策，并确保每一个结论都基于分析师提供的具体证据。"""

        # [英文备份] prompt (en):
        # prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.
        #
        # {instrument_context}
        #
        # ---
        #
        # **Rating Scale** (use exactly one):
        # - **Buy**: Strong conviction to enter or add to position
        # - **Overweight**: Favorable outlook, gradually increase exposure
        # - **Hold**: Maintain current position, no action needed
        # - **Underweight**: Reduce exposure, take partial profits
        # - **Sell**: Exit position or avoid entry
        #
        # **Context:**
        # - Trader's proposed plan: **{trader_plan}**
        # - Lessons from past decisions: **{past_memory_str}**
        #
        # **Required Output Structure:**
        # 1. **Rating**: State one of Buy / Overweight / Hold / Underweight / Sell.
        # 2. **Executive Summary**: A concise action plan covering entry strategy, position sizing, key risk levels, and time horizon.
        # 3. **Investment Thesis**: Detailed reasoning anchored in the analysts' debate and past reflections.
        #
        # ---
        #
        # **Risk Analysts Debate History:**
        # {history}
        #
        # ---
        #
        # Be decisive and ground every conclusion in specific evidence from the analysts."""

        # ====================================================================
        # LLM 直接调用（invoke）
        #
        # 【什么是 llm.invoke()？】
        # 与分析师 Agent 不同，投资组合经理不使用工具（tools），
        # 而是直接调用 LLM 进行推理和决策。
        #
        # llm.invoke(prompt) 的含义：
        #   将提示词发送给 LLM，LLM 进行推理并返回一个 AIMessage 对象。
        #   response.content 就是 LLM 生成的文本回复（最终决策）。
        #
        # 这种直接调用方式适用于不需要工具调用的场景，
        # 比 bind_tools 更简单直接。
        # ====================================================================
        response = llm.invoke(prompt)

        # ====================================================================
        # 更新风险辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        #
        # 保持原有的辩论历史不变，但添加投资组合经理的最终决策。
        # latest_speaker 设为 "Judge"（法官），表示这是最终的裁决。
        #
        # 注意：这里保留了所有中间状态（history、aggressive_history 等），
        # 确保整个决策过程是可追溯和可审计的。
        #
        # 【为什么要保留所有字段？】
        # 返回的字典会替换全局状态中的 risk_debate_state。
        # 如果不保留原有字段，辩论历史会丢失。
        # 所以需要把所有字段都写进新字典。
        # ====================================================================
        new_risk_debate_state = {
            "judge_decision": response.content,                                    # 最终裁决内容
            "history": risk_debate_state["history"],                               # 完整辩论历史
            "aggressive_history": risk_debate_state["aggressive_history"],         # 激进分析师历史
            "conservative_history": risk_debate_state["conservative_history"],     # 保守分析师历史
            "neutral_history": risk_debate_state["neutral_history"],               # 中性分析师历史
            "latest_speaker": "Judge",                                             # 当前发言者：法官
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],                                   # 保持辩论轮数不变
        }

        # ====================================================================
        # 返回状态更新
        #
        # 【返回的字段说明】
        # - risk_debate_state: 更新后的辩论状态（包含最终决策）
        #   → LangGraph 会用 new_risk_debate_state 替换全局的 risk_debate_state
        #
        # - final_trade_decision: 最终交易决策文本，供整个系统使用
        #   → 这是投资组合经理的最终裁决，包含评级、执行摘要和投资论点
        #   → 这是整个多 Agent 系统的最终输出
        #   → 后续会被 SignalProcessor 提取出简洁的决策词（如 "BUY"）
        # ====================================================================
        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": response.content,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_portfolio_manager 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 和 memory 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("portfolio_manager", create_portfolio_manager(llm, portfolio_manager_memory))
    # ========================================================================
    return portfolio_manager_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设分析 AAPL，风险辩论已完成多轮：
#
# 步骤 1：风险辩论结束后的状态
#   state = {
#       "company_of_interest": "AAPL",
#       "market_report": "AAPL 技术指标显示上升趋势...",
#       "news_report": "Apple 发布新产品...",
#       "fundamentals_report": "营收增长 15%...",
#       "sentiment_report": "社交媒体情绪偏正面...",
#       "investment_plan": "推荐：Buy。理由：... 战略行动：...",
#       "risk_debate_state": {
#           "history": "Aggressive Analyst: ...\nConservative Analyst: ...\nNeutral Analyst: ...",
#           "aggressive_history": "Aggressive Analyst: ...",
#           "conservative_history": "Conservative Analyst: ...",
#           "neutral_history": "Neutral Analyst: ...",
#           "latest_speaker": "Neutral",
#           "count": 6,  # 已达到最大风险辩论轮数
#       }
#   }
#
# 步骤 2：LangGraph 调用 portfolio_manager_node(state)
#   → 构建 instrument_context
#   → 提取风险辩论历史 history
#   → 提取交易员投资计划 investment_plan
#   → 记忆检索：memory.get_memories(curr_situation) → 检索到 2 条历史经验
#   → 构建 prompt（注入评级标准、交易员计划、历史教训、风险辩论历史）
#   → llm.invoke(prompt) → 生成最终决策
#
# 步骤 3：LLM 返回最终决策
#   response.content = "Rating: Buy\nExecutive Summary: 建议在 $170 附近建仓...\n
#                       Investment Thesis: 基于激进分析师的增长论点和基本面数据..."
#
# 步骤 4：更新状态并返回
#   new_risk_debate_state = {
#       "judge_decision": "Rating: Buy...",
#       "history": "Aggressive Analyst: ...",
#       ...保留所有原有字段...
#       "latest_speaker": "Judge",
#       "count": 6,
#   }
#   → 返回 {
#       "risk_debate_state": new_risk_debate_state,
#       "final_trade_decision": "Rating: Buy\nExecutive Summary: ..."
#   }
#
# 步骤 5：LangGraph 将 final_trade_decision 合并到全局状态
#   state["final_trade_decision"] = "Rating: Buy\nExecutive Summary: ..."
#   → 这是整个多 Agent 系统的最终输出
#   → 后续 SignalProcessor 会从这段文本中提取出简洁的决策词（如 "BUY"）
#
# ============================================================================
