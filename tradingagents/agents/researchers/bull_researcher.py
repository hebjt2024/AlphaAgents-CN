# ============================================================================
# 模块：牛市研究员 (Bull Researcher)
# 角色：多 Agent 辩论系统中的"多方"，负责提出看涨论点，强调增长潜力和积极因素
# 核心功能：
#   1. 阅读所有分析报告（市场、情绪、新闻、基本面）
#   2. 针对熊市分析师的论点进行反驳
#   3. 强调增长潜力、竞争优势和正面指标
#   4. 参考历史记忆，从过去的错误中学习
#   5. 以对话式风格参与辩论，与熊市研究员形成对抗
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
#    牛市研究员用 LLM 来"阅读"分析报告、"思考"看涨论点、"辩论"反驳熊市观点。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。牛市研究员是一种特殊 Agent：
#    它不使用工具（不查数据），而是直接用 LLM 分析已有报告并生成辩论论点。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    牛市研究员只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。牛市研究员是图中的一个节点，
#    在分析师生成报告之后执行，与熊市研究员交替发言形成辩论。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    牛市研究员从黑板上读取：
#      - market_report（市场分析报告）
#      - sentiment_report（情绪分析报告）
#      - news_report（新闻分析报告）
#      - fundamentals_report（基本面分析报告）
#      - investment_debate_state（辩论状态：历史、对方最新论点等）
#    牛市研究员写回到黑板上：
#      - investment_debate_state（更新辩论历史、当前回复）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_bull_researcher(llm, memory) 是工厂函数：
#    传入 LLM 和记忆系统，返回一个节点函数。
#    外层负责"配置"（传入 llm 和 memory），内层负责"执行"（接收 state）。
#    与分析师不同，牛市研究员的工厂函数多了一个 memory 参数。
#
# 7. 什么是记忆系统（Memory）？
#    记忆系统让 Agent 能"记住"过去的经验。
#    FinancialSituationMemory 使用 BM25 算法检索相似的历史情况：
#    - 存储过去的（市场情况, 决策建议）对
#    - 检索时，把当前市场情况作为查询，找到最相似的历史记录
#    - 让 Agent 避免重复过去的错误，借鉴成功的策略
#    牛市研究员用它来检索历史上类似情况下的看涨经验。
#
# 8. 什么是辩论机制？
#    本系统使用"对抗性辩论"来提高决策质量：
#    - 牛市研究员（Bull）：提出看涨论点
#    - 熊市研究员（Bear）：提出看跌论点
#    两者交替发言，互相反驳。研究经理（Research Manager）最后裁决。
#    这种设计模拟真实投资决策中的多角度分析。
#
# 9. 什么是 llm.invoke()？
#    invoke() 是 LLM 的最基础调用方法：传入文本提示词，返回文本回复。
#    与分析师不同（分析师用 ChatPromptTemplate + bind_tools + | 管道），
#    牛市研究员直接用 llm.invoke(prompt) ——更简单直接，适合不需要工具的场景。
#
# 10. 系统中的整体流程（本节点的位置）
#     用户输入 → [4个分析师生成报告] → [牛熊研究员辩论] ← 本文件
#                                         ↓
#                                    [研究经理裁决] → [交易员] → [风险辩论] → [组合经理]
#
# ============================================================================

# AIMessage：LangChain 的 AI 回复消息类型
# 虽然导入了但实际未直接使用（llm.invoke 返回的就是 AIMessage 对象）
from langchain_core.messages import AIMessage
import time
import json


# ============================================================================
# 工厂函数：create_bull_researcher
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建牛市研究员需要传入 LLM 实例和记忆系统。
# 工厂函数模式解决这个矛盾：
#   外层 create_bull_researcher(llm, memory) 接收 llm 和 memory，
#   内层 bull_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 和 memory 变量。
#
# 与分析师的工厂函数对比：
#   分析师：create_xxx(llm) → 只传 llm
#   研究员：create_xxx(llm, memory) → 多传一个 memory
#   辩论员：create_xxx(llm) → 只传 llm（不需要记忆）
# ============================================================================
def create_bull_researcher(llm, memory):
    """
    创建牛市研究员节点函数（工厂函数模式）。

    牛市研究员扮演"多方"角色，在辩论中与熊市研究员对立：
    - 寻找和强调正面因素
    - 反驳熊市分析师的悲观论点
    - 提供乐观视角，平衡风险分析

    牛市研究员与熊市研究员的关系：
    这两个 Agent 是辩论系统中的"对手"，它们交替发言，
    形成多轮辩论。研究经理（Research Manager）最终评估辩论
    并做出决策。这种设计模拟了真实投资决策中的多角度分析。

    参数：
        llm: 大语言模型实例，驱动推理和辩论
        memory: 记忆系统实例，用于检索历史辩论经验
    """

    # ========================================================================
    # 内层节点函数：bull_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def bull_node(state) -> dict:
        """
        牛市研究员节点：LangGraph 工作流中的标准节点函数。

        每次调用时，牛市研究员会：
        1. 阅读所有分析报告
        2. 查看辩论历史和熊市分析师的最新论点
        3. 生成反驳论点
        4. 更新辩论状态

        参数：
            state: 状态字典，包含分析报告、辩论状态等

        【读取的状态字段】：
            - state["investment_debate_state"]：投资辩论状态
              - .history：完整辩论历史
              - .bull_history：牛市分析师自己的发言历史
              - .current_response：熊市分析师的最新论点（需要反驳）
            - state["market_report"]：市场分析报告
            - state["sentiment_report"]：情绪分析报告
            - state["news_report"]：新闻分析报告
            - state["fundamentals_report"]：基本面分析报告

        【写入的状态字段】：
            - "investment_debate_state"：更新后的辩论状态
              - history：追加了牛市新论点
              - bull_history：追加了牛市新论点
              - current_response：更新为牛市最新论点
              - count：辩论轮数 +1
        """
        # 获取投资辩论的当前状态
        # investment_debate_state 是一个嵌套字典，记录辩论的所有信息
        investment_debate_state = state["investment_debate_state"]

        # 提取辩论历史
        # history：完整的辩论历史（包括牛市和熊市的所有发言交替排列）
        # bull_history：只包含牛市分析师自己的发言历史（用于追踪自己的论点）
        # 使用 .get("key", "") 提供默认值，防止状态为空时报错
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

        # current_response：熊市分析师的最新论点（牛市需要反驳的对象）
        # 牛市研究员会直接回应这个论点，形成"对话式"辩论
        current_response = investment_debate_state.get("current_response", "")

        # 提取四份分析报告（由四个分析师 Agent 分别生成）
        # 这些报告是牛市研究员的"论据来源"
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        # ====================================================================
        # 记忆检索（Memory Retrieval）
        #
        # 【什么是记忆检索？】
        # 将当前的所有分析报告合并成一个"当前情况"描述字符串，
        # 然后通过 memory.get_memories() 检索历史上类似的情况。
        #
        # memory.get_memories(curr_situation, n_matches=2) 的工作方式：
        #   1. 将 curr_situation 分词
        #   2. 使用 BM25 算法计算与所有历史记录的相似度
        #   3. 返回最相似的 2 条记录
        #   4. 每条记录包含 "recommendation" 字段（过去的经验教训）
        #
        # 记忆检索帮助牛市研究员：
        # 1. 学习过去成功的论点策略
        # 2. 避免之前无效的论证方式
        # 3. 持续改进辩论质量
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
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        # ====================================================================
        # 构建牛市分析师的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。牛市研究员用 f-string 直接构建，
        # 不使用 ChatPromptTemplate（因为不需要工具绑定和消息历史）。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："You are a Bull Analyst advocating for investing in the
        #          stock. Your task is to build a strong, evidence-based case
        #          emphasizing growth potential, competitive advantages, and
        #          positive market indicators."
        #   → 角色定义：看涨分析师，为投资这只股票辩护。
        #     三个关注点：增长潜力、竞争优势、正面指标。
        #     关键词"evidence-based"要求论点有数据支撑。
        #
        # 第 2 段："Leverage the provided research and data to address concerns
        #          and counter bearish arguments effectively."
        #   → 行动指令：利用研究报告和数据，回应担忧，有效反驳熊市论点。
        #
        # Key points to focus on（关键关注点）：
        # - Growth Potential：增长潜力（市场机会、收入预测、可扩展性）
        # - Competitive Advantages：竞争优势（独特产品、强大品牌、市场主导地位）
        # - Positive Indicators：正面指标（财务健康、行业趋势、正面新闻）
        # - Bear Counterpoints：熊市反驳（用数据批判熊市的悲观假设）
        # - Engagement：辩论风格（对话式、直接回应、有效辩论）
        #
        # 与熊市分析师的提示词形成对称结构：
        #   - 熊市关注"风险和挑战" ↔ 牛市关注"增长和优势"
        #   - 熊市关注"竞争劣势" ↔ 牛市关注"竞争优势"
        #   - 熊市关注"负面指标" ↔ 牛市关注"正面指标"
        #
        # Resources available（可用资源）：
        # 通过 f-string 注入四份分析报告、辩论历史、熊市最新论点、历史记忆
        #
        # 关键变量（通过 f-string 的 {变量} 注入）：
        #   {market_research_report}：市场分析报告
        #   {sentiment_report}：情绪分析报告
        #   {news_report}：新闻分析报告
        #   {fundamentals_report}：基本面分析报告
        #   {history}：完整辩论历史（让 LLM 看到之前的辩论）
        #   {current_response}：熊市分析师的最新论点（需要反驳的目标）
        #   {past_memory_str}：历史经验教训
        # ====================================================================
        prompt = f"""You are a Bull Analyst advocating for investing in the stock. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
Company fundamentals report: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Reflections from similar situations and lessons learned: {past_memory_str}
Use this information to deliver a compelling bull argument, refute the bear's concerns, and engage in a dynamic debate that demonstrates the strengths of the bull position. You must also address reflections and learn from lessons and mistakes you made in the past.
"""

        # ====================================================================
        # LLM 直接调用（invoke）
        #
        # 【什么是 llm.invoke()？】
        # 与分析师不同，牛市研究员不使用 ChatPromptTemplate 和 bind_tools，
        # 而是直接调用 llm.invoke(prompt)。
        #
        # llm.invoke(prompt) 的含义：
        #   将提示词字符串发送给 LLM，LLM 进行推理并返回一个 AIMessage 对象。
        #   response.content 就是 LLM 生成的文本回复（牛市论点）。
        #
        # 为什么不用 bind_tools？
        #   牛市研究员不需要调用工具获取数据——所有数据已在提示词中。
        #   它的任务是"分析+辩论"，不需要工具，直接调用更简单高效。
        #
        # 这种直接调用方式适用于不需要工具调用的场景，
        # 比 ChatPromptTemplate + | 管道 + bind_tools 更简单直接。
        # ====================================================================
        response = llm.invoke(prompt)

        # 为论点添加发言者标签，便于在辩论历史中追踪
        # 这样辩论历史中可以清楚区分谁说了什么：
        #   "Bull Analyst: ..."（牛市发言）
        #   "Bear Analyst: ..."（熊市发言）
        argument = f"Bull Analyst: {response.content}"

        # ====================================================================
        # 更新辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 牛市研究员返回 {"investment_debate_state": new_investment_debate_state}
        # → LangGraph 会用 new_investment_debate_state 替换全局的 investment_debate_state
        #
        # 【状态更新逻辑】
        # 与熊市研究员的状态更新逻辑对称：
        # 1. 将新论点追加到完整辩论历史（history）和牛市历史（bull_history）
        # 2. 将新论点设为 current_response（熊市下一轮会反驳这个）
        # 3. 保持熊市历史不变（bear_history，不能改对手的记录）
        # 4. count + 1（记录辩论轮数）
        #
        # 【为什么要保留所有字段？】
        # new_investment_debate_state 是一个完整的字典，包含所有字段。
        # 如果只返回修改的字段，LangGraph 的状态合并可能导致其他字段丢失。
        # 所以需要把所有字段都写进新字典（即使没修改的字段也要原样保留）。
        # ====================================================================
        new_investment_debate_state = {
            "history": history + "\n" + argument,                                   # 追加到完整辩论历史
            "bull_history": bull_history + "\n" + argument,                         # 追加到牛市历史
            "bear_history": investment_debate_state.get("bear_history", ""),        # 保持熊市历史不变
            "current_response": argument,                                           # 更新当前回复
            "count": investment_debate_state["count"] + 1,                          # 辩论轮数 +1
        }

        # 返回状态更新，只更新 investment_debate_state
        return {"investment_debate_state": new_investment_debate_state}

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_bull_researcher 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 和 memory 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("bull_researcher", create_bull_researcher(llm, bull_memory))
    # ========================================================================
    return bull_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设分析 AAPL，分析师已生成 4 份报告：
#
# 步骤 1：LangGraph 进入辩论阶段，初始状态
#   state = {
#       "market_report": "AAPL 技术指标显示上升趋势...",
#       "sentiment_report": "社交媒体情绪偏正面...",
#       "news_report": "Apple 发布新产品...",
#       "fundamentals_report": "营收增长 15%...",
#       "investment_debate_state": {
#           "history": "",           # 初始为空
#           "bull_history": "",       # 初始为空
#           "bear_history": "",      # 初始为空
#           "current_response": "",  # 初始为空（牛市先发言，没有需要反驳的）
#           "count": 0,              # 初始为 0
#       }
#   }
#
# 步骤 2：LangGraph 调用 bull_node(state)（牛市先发言）
#   → 提取 4 份报告
#   → 记忆检索：memory.get_memories(curr_situation) → 检索到 2 条历史经验
#   → 构建 prompt（注入报告、辩论历史、历史记忆）
#   → llm.invoke(prompt) → 生成牛市论点
#
# 步骤 3：LLM 返回牛市论点
#   response.content = "Apple 的营收增长 15%，远超行业平均..."
#   argument = "Bull Analyst: Apple 的营收增长 15%，远超行业平均..."
#
# 步骤 4：更新辩论状态并返回
#   new_investment_debate_state = {
#       "history": "Bull Analyst: Apple 的营收增长 15%...",
#       "bull_history": "Bull Analyst: Apple 的营收增长 15%...",
#       "bear_history": "",
#       "current_response": "Bull Analyst: Apple 的营收增长 15%...",
#       "count": 1,
#   }
#   → 返回 {"investment_debate_state": new_investment_debate_state}
#
# 步骤 5：LangGraph 路由到熊市研究员
#   → 熊市研究员读取 current_response（牛市论点）作为反驳对象
#   → 熊市发言后，轮数 = 2
#   → 如果未达到最大辩论轮数，再次回到牛市研究员
#   → 重复步骤 2-4，牛市反驳熊市的最新论点
#
# 步骤 6：达到最大辩论轮数后，LangGraph 路由到研究经理
#   → 研究经理读取完整辩论历史，做出裁决
#
# ============================================================================
