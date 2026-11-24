# ============================================================================
# 模块：熊市研究员 (Bear Researcher)
# 角色：多 Agent 辩论系统中的"空方"，负责提出看跌论点，强调风险和负面因素
# 核心功能：
#   1. 阅读所有分析报告（市场、情绪、新闻、基本面）
#   2. 针对牛市分析师的论点进行反驳
#   3. 强调风险、挑战和负面指标
#   4. 参考历史记忆，从过去的错误中学习
#   5. 以对话式风格参与辩论
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
#    熊市研究员用 LLM 来"阅读"分析报告、"思考"看跌论点、"辩论"反驳牛市观点。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。熊市研究员是一种特殊 Agent：
#    它不使用工具（不查数据），而是直接用 LLM 分析已有报告并生成辩论论点。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    熊市研究员只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。熊市研究员是图中的一个节点，
#    在牛市研究员发言后执行，形成交替辩论。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    熊市研究员从黑板上读取：
#      - market_report（市场分析报告）
#      - sentiment_report（情绪分析报告）
#      - news_report（新闻分析报告）
#      - fundamentals_report（基本面分析报告）
#      - investment_debate_state（辩论状态：历史、牛市最新论点等）
#    熊市研究员写回到黑板上：
#      - investment_debate_state（更新辩论历史、当前回复）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_bear_researcher(llm, memory) 是工厂函数：
#    传入 LLM 和记忆系统，返回一个节点函数。
#    外层负责"配置"（传入 llm 和 memory），内层负责"执行"（接收 state）。
#
# 7. 什么是记忆系统（Memory）？
#    FinancialSituationMemory 使用 BM25 算法检索相似的历史情况。
#    熊市研究员用它来检索历史上类似情况下的看跌经验，避免重复犯错。
#
# 8. 什么是辩论机制？为什么需要"空方"？
#    本系统使用"对抗性辩论"来提高决策质量：
#    - 牛市研究员（Bull）：提出看涨论点
#    - 熊市研究员（Bear）：提出看跌论点 ← 本文件
#    两者交替发言，互相反驳。研究经理最后裁决。
#    这种"辩论机制"是多 Agent 系统中常见的对抗性设计模式，
#    通过让不同立场的 Agent 互相辩论，可以：
#    1. 减少单一视角的偏见
#    2. 发现被忽视的风险
#    3. 提高决策的稳健性
#
# 9. 什么是 llm.invoke()？
#    invoke() 是 LLM 的最基础调用方法：传入文本提示词，返回文本回复。
#    熊市研究员直接用 llm.invoke(prompt)，不需要 ChatPromptTemplate 或工具。
#
# 10. 系统中的整体流程（本节点的位置）
#     用户输入 → [4个分析师生成报告] → [牛熊研究员辩论] ← 本文件
#                                         ↓
#                                    [研究经理裁决] → [交易员] → [风险辩论] → [组合经理]
#
# ============================================================================

# AIMessage：LangChain 的 AI 回复消息类型
# 虽然这里导入了但实际未直接使用（LLM.invoke 返回的就是 AIMessage）


# ============================================================================
# 工厂函数：create_bear_researcher
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建熊市研究员需要传入 LLM 实例和记忆系统。
# 工厂函数模式解决这个矛盾：
#   外层 create_bear_researcher(llm, memory) 接收 llm 和 memory，
#   内层 bear_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 和 memory 变量。
#
# 与牛市研究员的结构完全对称，只是角色立场相反。
# ============================================================================
def create_bear_researcher(llm, memory):
    """
    创建熊市研究员节点函数（工厂函数模式）。

    熊市研究员扮演"空方"角色，在辩论中与牛市研究员对立：
    - 寻找和强调负面因素
    - 反驳牛市分析师的乐观论点
    - 提供风险视角，帮助系统做出更全面的决策

    这种"辩论机制"是多 Agent 系统中常见的对抗性设计模式，
    通过让不同立场的 Agent 互相辩论，可以：
    1. 减少单一视角的偏见
    2. 发现被忽视的风险
    3. 提高决策的稳健性

    参数：
        llm: 大语言模型实例，驱动推理和辩论
        memory: 记忆系统实例，用于检索历史辩论经验
    """

    # ========================================================================
    # 内层节点函数：bear_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def bear_node(state) -> dict:
        """
        熊市研究员节点：LangGraph 工作流中的标准节点函数。

        每次调用时，熊市研究员会：
        1. 阅读所有分析报告
        2. 查看辩论历史和牛市分析师的最新论点
        3. 生成反驳论点
        4. 更新辩论状态

        参数：
            state: 状态字典，包含分析报告、辩论状态等

        【读取的状态字段】：
            - state["investment_debate_state"]：投资辩论状态
              - .history：完整辩论历史
              - .bear_history：熊市分析师自己的发言历史
              - .current_response：牛市分析师的最新论点（需要反驳）
            - state["market_report"]：市场分析报告
            - state["sentiment_report"]：情绪分析报告
            - state["news_report"]：新闻分析报告
            - state["fundamentals_report"]：基本面分析报告

        【写入的状态字段】：
            - "investment_debate_state"：更新后的辩论状态
              - history：追加了熊市新论点
              - bear_history：追加了熊市新论点
              - current_response：更新为熊市最新论点
              - count：辩论轮数 +1
        """
        # 获取投资辩论的当前状态
        # investment_debate_state 是一个嵌套字典，记录辩论的所有信息
        investment_debate_state = state["investment_debate_state"]

        # 提取辩论历史
        # history：完整的辩论历史（包括牛市和熊市的所有发言）
        # bear_history：只包含熊市分析师的发言历史（用于追踪自己的论点）
        # 使用 .get("key", "") 提供默认值，防止状态为空时报错
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        # current_response：牛市分析师的最新论点（熊市需要反驳的对象）
        # 熊市研究员会直接回应这个论点，形成"对话式"辩论
        current_response = investment_debate_state.get("current_response", "")

        # 提取四份分析报告（由四个分析师 Agent 分别生成）
        # 这些报告是熊市研究员的"论据来源"
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        # ====================================================================
        # 记忆检索（Memory Retrieval）
        #
        # 【什么是记忆检索？】
        # 从历史中检索类似情况下的辩论经验，帮助熊市研究员：
        # 1. 避免重复之前无效的论点
        # 2. 借鉴之前成功的反驳策略
        # 3. 持续改进辩论质量
        #
        # memory.get_memories(curr_situation, n_matches=2) 的工作方式：
        #   1. 将 curr_situation 分词
        #   2. 使用 BM25 算法计算与所有历史记录的相似度
        #   3. 返回最相似的 2 条记录
        #   4. 每条记录包含 "recommendation" 字段（过去的经验教训）
        #
        # f"..." 是 Python 的 f-string（格式化字符串），{变量} 会被替换为实际值
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
        # 构建熊市分析师的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。熊市研究员用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："You are a Bear Analyst making the case against investing
        #          in the stock. Your goal is to present a well-reasoned
        #          argument emphasizing risks, challenges, and negative
        #          indicators."
        #   → 角色定义：看跌分析师，反对投资这只股票。
        #     三个关注点：风险、挑战、负面指标。
        #     关键词"well-reasoned"要求论点有逻辑推理。
        #
        # 第 2 段："Leverage the provided research and data to highlight
        #          potential downsides and counter bullish arguments
        #          effectively."
        #   → 行动指令：利用研究报告和数据，突出潜在下行风险，有效反驳牛市论点。
        #
        # Key points to focus on（关键关注点）：
        # - Risks and Challenges：风险和挑战（市场饱和、财务不稳定、宏观经济威胁）
        # - Competitive Weaknesses：竞争劣势（市场地位弱、创新下降、竞争对手威胁）
        # - Negative Indicators：负面指标（财务数据、市场趋势、负面新闻）
        # - Bull Counterpoints：牛市反驳（用数据批判牛市的乐观假设）
        # - Engagement：辩论风格（对话式、直接回应、有效辩论而非简单罗列事实）
        #
        # 与牛市分析师的提示词形成对称结构：
        #   - 熊市关注"风险和挑战" ↔ 牛市关注"增长和优势"
        #   - 熊市关注"竞争劣势" ↔ 牛市关注"竞争优势"
        #   - 熊市关注"负面指标" ↔ 牛市关注"正面指标"
        #
        # 关键变量（通过 f-string 的 {变量} 注入）：
        #   {current_response}：牛市分析师的最新论点（需要反驳的目标）
        #   {past_memory_str}：历史经验教训
        # ====================================================================
        prompt = f"""你是一名熊市分析师，负责论证不应投资该股票。你的目标是提出逻辑严密的论点，强调风险、挑战以及负面指标。请利用提供的研究和数据，突出潜在的不利因素并有效反驳看多论点。

重点关注以下要点：

- 风险与挑战（Risks and Challenges）：突出市场饱和、财务不稳定或宏观经济威胁等可能拖累该股表现的因素。
- 竞争劣势（Competitive Weaknesses）：强调较弱的市场地位、创新乏力或来自竞争对手的威胁等薄弱环节。
- 负面指标（Negative Indicators）：以财务数据、市场趋势或近期不利新闻作为支撑你立场的证据。
- 多头反驳（Bull Counterpoints）：用具体数据和严密逻辑批判性地分析多头论点，揭示其弱点或过度乐观的假设。
- 互动性（Engagement）：以对话式风格呈现你的论点，直接针对多头分析师的观点进行有效辩论，而非简单罗列事实。

可用资源：

市场研究报告：{market_research_report}
社交媒体情绪报告：{sentiment_report}
最新国际时事新闻：{news_report}
公司基本面报告：{fundamentals_report}
辩论对话历史：{history}
上次多头论点：{current_response}
类似情境的反思与经验教训：{past_memory_str}
请利用这些信息，给出有说服力的空头论点，驳斥多头的论断，并展开一场展现投资该股票的风险与弱点的激烈辩论。你还必须回应过往反思，从过去所犯的错误和教训中学习。
"""

        # [英文备份] prompt (en):
        # prompt = f"""You are a Bear Analyst making the case against investing in the stock. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.
        #
        # Key points to focus on:
        #
        # - Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
        # - Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
        # - Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
        # - Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
        # - Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.
        #
        # Resources available:
        #
        # Market research report: {market_research_report}
        # Social media sentiment report: {sentiment_report}
        # Latest world affairs news: {news_report}
        # Company fundamentals report: {fundamentals_report}
        # Conversation history of the debate: {history}
        # Last bull argument: {current_response}
        # Reflections from similar situations and lessons learned: {past_memory_str}
        # Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the stock. You must also address reflections and learn from lessons and mistakes you made in the past.
        # """

        # ====================================================================
        # LLM 直接调用，生成熊市论点
        #
        # 【什么是 llm.invoke()？】
        # 与分析师不同，熊市研究员不使用 ChatPromptTemplate 和 bind_tools，
        # 而是直接调用 llm.invoke(prompt)。
        #
        # llm.invoke(prompt) 的含义：
        #   将提示词字符串发送给 LLM，LLM 进行推理并返回一个 AIMessage 对象。
        #   response.content 就是 LLM 生成的文本回复（熊市论点）。
        #
        # 为什么不用 bind_tools？
        #   熊市研究员不需要调用工具获取数据——所有数据已在提示词中。
        #   它的任务是"分析+辩论"，不需要工具，直接调用更简单高效。
        # ====================================================================
        from tradingagents.llm_clients import invoke_stream

        response = invoke_stream(llm, prompt)

        # 为论点添加发言者标签，方便在辩论历史中区分不同角色
        # 这样辩论历史中可以清楚区分谁说了什么：
        #   "Bull Analyst: ..."（牛市发言）
        #   "Bear Analyst: ..."（熊市发言）
        argument = f"Bear Analyst: {response.content}"

        # ====================================================================
        # 更新辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 熊市研究员返回 {"investment_debate_state": new_investment_debate_state}
        # → LangGraph 会用 new_investment_debate_state 替换全局的 investment_debate_state
        #
        # 【状态更新逻辑】
        # 关键操作：
        # 1. 将新论点追加到完整辩论历史（history）和熊市历史（bear_history）
        # 2. 将新论点设为 current_response（牛市分析师下一轮会反驳这个）
        # 3. count + 1，记录辩论轮数
        #
        # 这种状态更新模式确保了辩论的连续性——每个 Agent 都能看到
        # 完整的辩论历史和对方的论点。
        #
        # 【为什么要保留所有字段？】
        # new_investment_debate_state 是一个完整的字典，包含所有字段。
        # 如果只返回修改的字段，LangGraph 的状态合并可能导致其他字段丢失。
        # 所以需要把所有字段都写进新字典（即使没修改的字段也要原样保留）。
        #
        # 与牛市研究员的对称性：
        # 牛市更新 bull_history + current_response，保留 bear_history
        # 熊市更新 bear_history + current_response，保留 bull_history
        # ====================================================================
        new_investment_debate_state = {
            "history": history + "\n" + argument,                                   # 追加到完整辩论历史
            "bear_history": bear_history + "\n" + argument,                         # 追加到熊市历史
            "bull_history": investment_debate_state.get("bull_history", ""),        # 保持牛市历史不变
            "current_response": argument,                                           # 更新当前回复
            "count": investment_debate_state["count"] + 1,                          # 辩论轮数 +1
        }

        # 返回状态更新，只更新 investment_debate_state
        return {"investment_debate_state": new_investment_debate_state}

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_bear_researcher 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 和 memory 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("bear_researcher", create_bear_researcher(llm, bear_memory))
    # ========================================================================
    return bear_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设分析 AAPL，牛市已发表第一轮论点：
#
# 步骤 1：牛市发言后的状态
#   state = {
#       "market_report": "AAPL 技术指标显示上升趋势...",
#       "sentiment_report": "社交媒体情绪偏正面...",
#       "news_report": "Apple 发布新产品...",
#       "fundamentals_report": "营收增长 15%...",
#       "investment_debate_state": {
#           "history": "Bull Analyst: Apple 的营收增长 15%...",
#           "bull_history": "Bull Analyst: Apple 的营收增长 15%...",
#           "bear_history": "",
#           "current_response": "Bull Analyst: Apple 的营收增长 15%...",
#           "count": 1,
#       }
#   }
#
# 步骤 2：LangGraph 调用 bear_node(state)
#   → 提取 4 份报告
#   → 记忆检索：memory.get_memories(curr_situation) → 检索到 2 条历史经验
#   → 构建 prompt（注入报告、辩论历史、牛市最新论点、历史记忆）
#   → llm.invoke(prompt) → 生成熊市反驳论点
#
# 步骤 3：LLM 返回熊市论点
#   response.content = "虽然营收增长 15%，但利润率在下降，竞争加剧..."
#   argument = "Bear Analyst: 虽然营收增长 15%，但利润率在下降..."
#
# 步骤 4：更新辩论状态并返回
#   new_investment_debate_state = {
#       "history": "Bull Analyst: Apple 的营收增长 15%...\nBear Analyst: 虽然营收增长 15%...",
#       "bull_history": "Bull Analyst: Apple 的营收增长 15%...",
#       "bear_history": "Bear Analyst: 虽然营收增长 15%...",
#       "current_response": "Bear Analyst: 虽然营收增长 15%...",
#       "count": 2,
#   }
#   → 返回 {"investment_debate_state": new_investment_debate_state}
#
# 步骤 5：LangGraph 检查辩论轮数
#   → 如果 count < max_debate_rounds → 再次回到牛市研究员（反驳熊市）
#   → 如果 count >= max_debate_rounds → 路由到研究经理裁决
#
# 步骤 6：研究经理读取完整辩论历史，做出买入/卖出/持有裁决
#
# ============================================================================
