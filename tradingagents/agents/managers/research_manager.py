# ============================================================================
# 模块：研究经理 (Research Manager)
# 角色：多 Agent 系统中的辩论主持人和决策者，负责评估牛熊辩论并制定投资计划
# 核心功能：
#   1. 收集所有分析报告（市场、情绪、新闻、基本面）
#   2. 审阅牛市分析师和熊市分析师的辩论历史
#   3. 批判性地评估双方论点，做出买入/卖出/持有的决策
#   4. 制定详细的投资计划供交易员执行
#   5. 参考历史记忆，从过去的错误中学习
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
#    研究经理用 LLM 来"阅读"牛熊辩论、"评估"双方论点、"制定"投资计划。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。研究经理不使用工具，
#    而是直接用 LLM 分析已有报告和辩论历史，做出裁决。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    研究经理只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。研究经理是图中的一个节点，
#    在牛熊研究员辩论结束后执行，负责裁决和制定投资计划。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    研究经理从黑板上读取：
#      - company_of_interest（公司名）
#      - investment_debate_state（辩论状态：完整历史等）
#      - market_report（市场分析报告）
#      - sentiment_report（情绪分析报告）
#      - news_report（新闻分析报告）
#      - fundamentals_report（基本面分析报告）
#    研究经理写回到黑板上：
#      - investment_debate_state（添加 judge_decision 裁决）
#      - investment_plan（投资计划，供交易员使用）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_research_manager(llm, memory) 是工厂函数：
#    传入 LLM 和记忆系统，返回一个节点函数。
#
# 7. 什么是记忆系统（Memory）？
#    FinancialSituationMemory 使用 BM25 算法检索相似的历史情况。
#    研究经理用它来检索历史上类似情况下的裁决经验，避免重复犯错。
#
# 8. 研究经理在系统中的角色和职责
#    研究经理是辩论的"裁判"：
#    - 阅读牛熊双方的完整辩论历史
#    - 评估哪方论点更有说服力
#    - 做出明确的决策（买入/卖出/持有）
#    - 制定详细的投资计划
#
#    研究经理与投资组合经理的区别：
#    - 研究经理：评估投资辩论（investment_debate），制定投资计划
#    - 投资组合经理：评估风险辩论（risk_debate），做出最终交易决策
#
# 9. 系统中的整体流程（本节点的位置）
#    用户输入 → [4个分析师生成报告] → [牛熊研究员辩论] → [研究经理裁决] ← 本文件
#                                                              ↓
#                                              [交易员] → [风险辩论] → [组合经理最终决策]
#
# ============================================================================


# --- 导入项目内部工具函数 ---
# build_instrument_context：构建目标公司的上下文信息字符串
from tradingagents.agents.utils.agent_utils import build_instrument_context


# ============================================================================
# 工厂函数：create_research_manager
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建研究经理需要传入 LLM 实例和记忆系统。
# 工厂函数模式解决这个矛盾：
#   外层 create_research_manager(llm, memory) 接收 llm 和 memory，
#   内层 research_manager_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 和 memory 变量。
# ============================================================================
def create_research_manager(llm, memory):
    """
    创建研究经理节点函数（工厂函数模式）。

    研究经理与投资组合经理的区别：
    - 研究经理：评估牛熊辩论（investment debate），制定投资计划
    - 投资组合经理：评估风险辩论（risk debate），做出最终交易决策

    工作流程：
    1. 四个分析师生成研究报告 →
    2. 牛熊研究员辩论（多轮） →
    3. 研究经理评估辩论，制定投资计划 →  ← 本文件
    4. 交易员根据计划制定具体方案 →
    5. 风险分析师辩论风险 →
    6. 投资组合经理做最终决策

    参数：
        llm: 大语言模型实例，驱动推理和决策
        memory: 记忆系统实例，用于检索历史决策和经验教训
    """

    # ========================================================================
    # 内层节点函数：research_manager_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def research_manager_node(state) -> dict:
        """
        研究经理节点：LangGraph 工作流中的标准节点函数。

        这个节点在牛熊辩论阶段之后执行，负责：
        1. 评估辩论质量
        2. 做出明确的买入/卖出/持有决策
        3. 制定详细的投资计划

        参数：
            state: 状态字典，包含：
                - company_of_interest: 目标公司名称
                - investment_debate_state: 投资辩论状态
                - market_report: 市场分析报告
                - sentiment_report: 情绪分析报告
                - news_report: 新闻分析报告
                - fundamentals_report: 基本面分析报告

        【读取的状态字段】：
            - state["company_of_interest"]：目标公司名称
            - state["investment_debate_state"]：投资辩论状态（含完整历史）
            - state["market_report"]：市场分析报告
            - state["sentiment_report"]：情绪分析报告
            - state["news_report"]：新闻分析报告
            - state["fundamentals_report"]：基本面分析报告

        【写入的状态字段】：
            - "investment_debate_state"：更新后的辩论状态（含 judge_decision）
            - "investment_plan"：投资计划，供交易员节点使用
        """
        # 构建目标公司的上下文信息
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        instrument_context = build_instrument_context(state["company_of_interest"], state.get("analysis_horizon", "medium"))

        # ====================================================================
        # 提取投资辩论的历史记录
        #
        # investment_debate_state 包含：
        #   - history: 完整的辩论历史（牛市和熊市分析师的发言交替排列）
        #   - bear_history: 熊市分析师的所有发言
        #   - bull_history: 牛市分析师的所有发言
        #   - current_response: 最近一轮的发言
        #   - count: 辩论轮数
        #
        # 研究经理需要读取完整的辩论历史来评估双方论点。
        # ====================================================================

        # 获取完整的投资辩论状态（必须先赋值，才能使用）
        investment_debate_state = state["investment_debate_state"]

        # 提取辩论历史，使用 .get("history", "") 提供默认值，防止状态为空时报错
        history = investment_debate_state.get("history", "")

        # 提取四份分析报告（由四个分析师 Agent 分别生成）
        # 虽然辩论历史中可能已包含报告信息，但研究经理也需要直接阅读原始报告
        # 以便独立验证辩论双方的论点是否与报告数据一致
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        # ====================================================================
        # 记忆检索（Memory Retrieval）
        #
        # 【什么是记忆检索？】
        # 将所有分析报告合并成"当前情况"，检索历史相似情况。
        # 这样研究经理在做决策时可以参考过去的经验教训。
        #
        # memory.get_memories(curr_situation, n_matches=2) 的工作方式：
        #   1. 将 curr_situation 分词
        #   2. 使用 BM25 算法计算与所有历史记录的相似度
        #   3. 返回最相似的 2 条记录
        #   4. 每条记录包含 "recommendation" 字段（过去的经验教训）
        #
        # f"..." 是 Python 的 f-string，{变量} 会被替换为实际值
        # \n\n 是两个换行符，用于分隔不同报告
        # ====================================================================
        curr_situation = f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        # 格式化为字符串
        # enumerate(past_memories, 1) 从 1 开始编号遍历
        # rec["recommendation"] 是每条记忆中的经验教训文本
        past_memory_str = ""
        for _i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ====================================================================
        # 构建研究经理的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。研究经理用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："As the portfolio manager and debate facilitator, your role
        #          is to critically evaluate this round of debate and make a
        #          definitive decision: align with the bear analyst, the bull
        #          analyst, or choose Hold only if it is strongly justified
        #          based on the arguments presented."
        #   → 角色定义：组合经理和辩论主持人。
        #     核心任务：批判性评估辩论，做出明确决策。
        #     关键约束：只能选牛市、熊市或持有，持有需要强有力的理由。
        #
        # 第 2 段："Summarize the key points from both sides concisely, focusing
        #          on the most compelling evidence or reasoning."
        #   → 任务 1：简洁总结双方关键论点，聚焦最有说服力的证据。
        #
        # 第 3 段："Your recommendation—Buy, Sell, or Hold—must be clear and
        #          actionable."
        #   → 任务 2：明确的推荐（买入/卖出/持有），必须清晰可执行。
        #
        # 第 4 段："Avoid defaulting to Hold simply because both sides have
        #          valid points; commit to a stance grounded in the debate's
        #          strongest arguments."
        #   → 关键约束：禁止"和稀泥"！不能因为双方都有道理就默认选择持有。
        #     必须基于辩论中最强的论点做出承诺。这个设计防止 LLM 做出
        #     "安全但无用"的决策。
        #
        # 第 5 段："Additionally, develop a detailed investment plan for the
        #          trader."
        #   → 任务 3：为交易员制定详细的投资计划。
        #
        # Required output structure（要求的输出结构）：
        # - Your Recommendation：明确推荐（最有说服力的论点支撑）
        # - Rationale：理由（为什么这些论点导致这个结论）
        # - Strategic Actions：战略行动（执行推荐的具体步骤）
        #
        # 第 6 段："Take into account your past mistakes on similar situations.
        #          Use these insights to refine your decision-making and ensure
        #          you are learning and improving."
        #   → 学习机制：参考过去的错误，用这些洞察改进决策。
        #
        # 第 7 段："Present your analysis conversationally, as if speaking
        #          naturally, without special formatting."
        #   → 输出风格：对话式，自然表达，不需要特殊格式。
        #
        # 注入的变量：
        #   {past_memory_str}：过去反思的经验教训
        #   {instrument_context}：公司上下文信息
        #   {history}：完整的辩论历史
        # ====================================================================
        prompt = f"""作为投资组合经理兼辩论主持人，你的职责是批判性地评估本轮辩论，并做出明确的决策：支持熊方分析师、支持牛方分析师，或者仅在论证充分时才选择持有。

请简洁地总结双方的关键论点，聚焦于最有说服力的证据或推理。你的推荐——买入、卖出或持有——必须清晰且可操作。不要仅仅因为双方都有合理之处就默认选择持有；要基于辩论中最有力的论点表明立场。

此外，为交易员制定一份详细的投资计划。该计划应包含：

你的推荐（Your Recommendation）：由最具说服力的论点支撑的明确立场。
理由（Rationale）：解释为何这些论点会导致你的结论。
战略行动（Strategic Actions）：实施该建议的具体步骤。
请考虑你在类似情境下犯过的错误。利用这些经验来优化你的决策过程，确保你在持续学习和改进。请以对话式的口吻呈现你的分析，就像自然交谈一样，不要使用特殊格式。

以下是你过往对错误的反思：
"{past_memory_str}"

{instrument_context}

以下为辩论内容：
辩论历史：
{history}"""

        # [英文备份] prompt (en):
        # prompt = f"""As the portfolio manager and debate facilitator, your role is to critically evaluate this round of debate and make a definitive decision: align with the bear analyst, the bull analyst, or choose Hold only if it is strongly justified based on the arguments presented.
        #
        # Summarize the key points from both sides concisely, focusing on the most compelling evidence or reasoning. Your recommendation—Buy, Sell, or Hold—must be clear and actionable. Avoid defaulting to Hold simply because both sides have valid points; commit to a stance grounded in the debate's strongest arguments.
        #
        # Additionally, develop a detailed investment plan for the trader. This should include:
        #
        # Your Recommendation: A decisive stance supported by the most convincing arguments.
        # Rationale: An explanation of why these arguments lead to your conclusion.
        # Strategic Actions: Concrete steps for implementing the recommendation.
        # Take into account your past mistakes on similar situations. Use these insights to refine your decision-making and ensure you are learning and improving. Present your analysis conversationally, as if speaking naturally, without special formatting.
        #
        # Here are your past reflections on mistakes:
        # "{past_memory_str}"
        #
        # {instrument_context}
        #
        # Here is the debate:
        # Debate History:
        # {history}"""

        # ====================================================================
        # LLM 直接调用
        #
        # 【什么是 llm.invoke()？】
        # 研究经理不使用工具，直接进行推理和决策。
        # llm.invoke(prompt) 将提示词发送给 LLM 并返回推理结果。
        # response.content 是 LLM 生成的文本回复（裁决+投资计划）。
        #
        # 为什么不用 bind_tools？
        #   研究经理不需要调用工具——所有数据（报告+辩论历史）已在提示词中。
        #   它的任务是"评估+决策"，不需要工具，直接调用更简单。
        # ====================================================================
        from tradingagents.llm_clients import invoke_stream

        response = invoke_stream(llm, prompt)

        # ====================================================================
        # 更新投资辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        #
        # 将研究经理的决策记录到辩论状态中：
        #   - judge_decision: 最终的裁决（研究经理的决策）
        #     response.content 包含了推荐、理由和战略行动
        #   - current_response: 当前回复（供后续节点使用）
        #   - 保留原有的辩论历史（history、bear_history、bull_history 不变）
        #   - count 保持不变（裁决不增加辩论轮数）
        #
        # 【为什么要保留所有字段？】
        # 返回的字典会替换全局状态中的 investment_debate_state。
        # 如果不保留原有字段，辩论历史会丢失。
        # 所以需要把所有字段都写进新字典。
        # ====================================================================
        new_investment_debate_state = {
            "judge_decision": response.content,                                    # 研究经理的决策
            "history": investment_debate_state.get("history", ""),                 # 保留辩论历史
            "bear_history": investment_debate_state.get("bear_history", ""),       # 保留熊市分析师历史
            "bull_history": investment_debate_state.get("bull_history", ""),       # 保留牛市分析师历史
            "current_response": response.content,                                  # 当前回复
            "count": investment_debate_state["count"],                             # 保持辩论轮数
        }

        # ====================================================================
        # 返回状态更新
        #
        # 【返回的字段说明】
        # - investment_debate_state: 更新后的辩论状态（包含裁决）
        #   → LangGraph 会用 new_investment_debate_state 替换全局的 investment_debate_state
        #
        # - investment_plan: 投资计划，供交易员节点使用
        #   → 这是研究经理生成的投资计划文本，包含推荐、理由和战略行动
        #   → 交易员会基于这个计划制定具体的交易方案
        # ====================================================================
        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": response.content,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_research_manager 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 和 memory 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("research_manager", create_research_manager(llm, invest_judge_memory))
    # ========================================================================
    return research_manager_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设分析 AAPL，牛熊研究员已完成多轮辩论：
#
# 步骤 1：辩论结束后的状态
#   state = {
#       "company_of_interest": "AAPL",
#       "market_report": "AAPL 技术指标显示上升趋势...",
#       "sentiment_report": "社交媒体情绪偏正面...",
#       "news_report": "Apple 发布新产品...",
#       "fundamentals_report": "营收增长 15%...",
#       "investment_debate_state": {
#           "history": "Bull Analyst: ...\nBear Analyst: ...\nBull Analyst: ...\nBear Analyst: ...",
#           "bull_history": "Bull Analyst: ...\nBull Analyst: ...",
#           "bear_history": "Bear Analyst: ...\nBear Analyst: ...",
#           "current_response": "Bear Analyst: ...",
#           "count": 4,  # 已达到最大辩论轮数
#       }
#   }
#
# 步骤 2：LangGraph 调用 research_manager_node(state)
#   → 构建 instrument_context
#   → 提取辩论历史 history
#   → 记忆检索：memory.get_memories(curr_situation) → 检索到 2 条历史经验
#   → 构建 prompt（注入辩论历史、历史记忆、公司信息）
#   → llm.invoke(prompt) → 生成裁决和投资计划
#
# 步骤 3：LLM 返回裁决
#   response.content = "经过评估，牛市论点更有说服力。推荐：Buy。
#                      理由：营收增长 15%... 战略行动：建议在支撑位买入..."
#
# 步骤 4：更新状态并返回
#   new_investment_debate_state = {
#       "judge_decision": "经过评估，牛市论点更有说服力。推荐：Buy...",
#       "history": "Bull Analyst: ...\nBear Analyst: ...",
#       ...保留原有字段...
#       "count": 4,  # 不变
#   }
#   → 返回 {
#       "investment_debate_state": new_investment_debate_state,
#       "investment_plan": "经过评估，牛市论点更有说服力。推荐：Buy..."
#   }
#
# 步骤 5：LangGraph 将 investment_plan 合并到全局状态
#   state["investment_plan"] = "经过评估，牛市论点更有说服力。推荐：Buy..."
#   → 进入交易员节点
#
# ============================================================================
