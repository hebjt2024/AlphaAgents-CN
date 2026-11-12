# ============================================================================
# 模块：社交媒体分析师 (Social Media Analyst)
# 角色：负责分析社交媒体帖子、公司新闻和公众情绪，生成情绪/舆情分析报告
# 核心功能：调用 get_news 工具搜索社交媒体讨论和公司新闻，
#           分析公众对公司每日情绪变化，生成综合舆情报告
# 技术栈：LangChain 的 ChatPromptTemplate、LLM 工具绑定（bind_tools）、
#         LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    在本项目中，LLM 充当"智能体的大脑"，负责分析社交媒体上的情绪和讨论。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。它能自主决定何时调用工具、如何分析数据。
#    社交媒体分析师 Agent 会自主决定搜索什么关键词、分析哪些维度的情绪。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架，提供提示词模板、工具绑定等能力。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎，把多个 Agent 连成一张图。
#    每个节点是一个函数（接收 state，返回 state 更新）。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    社交媒体分析师读取 trade_date 和 company_of_interest，
#    把分析结果 sentiment_report 写到黑板上。
#
# 6. 什么是工厂函数（Factory Function）？
#    create_social_media_analyst(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层负责"配置"（传入 llm），内层负责"执行"（接收 state）。
#
# 7. 什么是工具（Tool）？
#    工具是 Agent 可调用的 Python 函数，有名称、描述和参数。
#    LLM 通过阅读描述理解工具作用，在需要时"请求"调用。
#    社交媒体分析师只有 1 个工具：get_news，但通过不同 query 实现多维搜索。
#
# 8. 社交媒体分析师在系统中的角色和职责
#    社交媒体分析师关注"人们在说什么、感受如何"：
#    - 社交媒体帖子：Reddit、Twitter 上的讨论
#    - 情绪数据：公众对公司的每日情绪变化（正面/负面/中性）
#    - 近期公司新闻：从社交媒体角度解读的公司新闻
#    这些信息帮助交易员理解"市场情绪"——有时情绪比基本面更能影响短期股价。
#
#    与其他分析师的区别：
#    - 基本面分析师：关注财务报表数据（数字）
#    - 新闻分析师：关注新闻事件和宏观趋势（事件）
#    - 社交媒体分析师：关注公众情绪和社交媒体讨论（情绪）← 本文件
#    - 市场分析师：关注价格走势和技术指标（图表）
#
# 9. 系统中的整体流程（本节点的位置）
#    用户输入（公司 + 日期）
#        |
#        v
#    [4个分析师并行]  ← 本文件是其中之一
#      市场分析师 / 新闻分析师 / 社交媒体分析师 / 基本面分析师
#        |
#        v
#    [牛熊研究员辩论] → [研究经理裁决] → [交易员] → [风险辩论] → [组合经理最终决策]
#
# ============================================================================

# ChatPromptTemplate：构建结构化聊天提示词模板，支持多角色消息
# MessagesPlaceholder：在提示词中预留位置，运行时动态插入历史消息列表

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# --- 导入项目内部工具函数 ---
# build_instrument_context：构建目标公司的上下文信息字符串
# get_news：获取新闻/社交媒体数据的工具函数（参数：query, start_date, end_date）
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_news,
    search_web,
)

# get_config：获取全局配置


# ============================================================================
# 工厂函数：create_social_media_analyst
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建社交媒体分析师需要传入 LLM 实例。
# 工厂函数模式解决这个矛盾：
#   外层 create_social_media_analyst(llm) 接收 llm，
#   内层 social_media_analyst_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 变量。
# ============================================================================
def create_social_media_analyst(llm):
    """
    创建社交媒体分析师节点函数（工厂函数模式）。

    社交媒体分析师的核心职责：
    1. 分析社交媒体上关于目标公司的帖子和讨论
    2. 分析公众对公司每日情绪变化（正面/负面/中性）
    3. 分析公司近期新闻
    4. 生成综合舆情分析报告，为交易员提供决策参考

    与其他分析师的对比：
    - 基本面分析师：关注财务报表和公司基本面
    - 市场分析师：关注技术指标和价格走势
    - 新闻分析师：关注全球宏观经济和公司新闻
    - 社交媒体分析师：关注公众情绪和社交媒体讨论（舆情分析）

    参数：
        llm: 大语言模型实例，驱动 Agent 的推理和决策
    """

    # ========================================================================
    # 内层节点函数：social_media_analyst_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def social_media_analyst_node(state):
        """
        社交媒体分析师节点：LangGraph 工作流中的标准节点函数。

        参数：
            state: 状态字典，包含：
                - trade_date: 当前交易日期
                - company_of_interest: 目标公司名称
                - messages: 历史消息列表

        【读取的状态字段】：
            - state["trade_date"]：当前交易日期
            - state["company_of_interest"]：目标公司名称
            - state["messages"]：历史消息列表

        【写入的状态字段】：
            - "messages"：将 LLM 回复追加到消息历史
            - "sentiment_report"：情绪/舆情分析报告，供后续节点使用
        """
        # 从状态中提取当前交易日期
        current_date = state["trade_date"]

        # 构建目标公司的上下文信息字符串
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        instrument_context = build_instrument_context(state["company_of_interest"], state.get("analysis_horizon", "medium"))

        # ====================================================================
        # 定义工具列表
        #
        # 为什么社交媒体分析师只有一个工具 get_news？
        #
        # get_news(ticker, start_date, end_date) 是一个通用的新闻搜索工具，
        # 通过不同的 query 参数可以实现多种搜索：
        # - 搜索社交媒体讨论：query = "AAPL stock reddit twitter"
        # - 搜索公司新闻：query = "Apple Inc news"
        # - 搜索情绪分析相关数据：query = "AAPL sentiment analysis"
        #
        # 虽然只有一个工具，但 LLM 会根据分析需求，用不同的 query 参数
        # 多次调用 get_news，从而覆盖社交媒体、新闻和情绪等多个维度。
        #
        # 这体现了 Agent 的"自主性"——LLM 决定搜索什么、搜几次、用什么关键词。
        # ====================================================================
        tools = [
            get_news,
            # 配置 TAVILY_API_KEY 后，可联网补充最新舆情/事件
            search_web,
        ]

        # ====================================================================
        # 系统提示词（System Prompt）
        #
        # 逐段解析这段提示词为什么这样设计：
        #
        # 第 1 段："You are a social media and company specific news
        #          researcher/analyst tasked with analyzing social media posts,
        #          recent company news, and public sentiment for a specific
        #          company over the past week."
        #   → 角色定义：社交媒体和公司新闻研究员/分析师。
        #     三个分析维度：社交媒体帖子、近期公司新闻、公众情绪。
        #     时间范围：过去一周。
        #
        # 第 2 段："You will be given a company's name your objective is
        #          to write a comprehensive long report detailing your analysis,
        #          insights, and implications for traders and investors on this
        #          company's current state after looking at social media and what
        #          people are saying about that company, analyzing sentiment data
        #          of what people feel each day about the company, and looking at
        #          recent company news."
        #   → 任务定义：写一份"详尽的"长报告，包含分析、见解和对交易员的影响。
        #     三个信息来源：
        #     1) 社交媒体上人们在说什么
        #     2) 每日情绪数据（人们每天对公司的感受）
        #     3) 近期公司新闻
        #     关键词"comprehensive long"鼓励详细，避免敷衍。
        #
        # 第 3 段："Use the get_news(ticker, start_date, end_date) tool to
        #          search for company-specific news and social media discussions."
        #   → 工具引导：告诉 LLM 用 get_news 搜索，并给出参数名。
        #
        # 第 4 段："Try to look at all sources possible from social media
        #          to sentiment to news."
        #   → 全面性要求：鼓励 LLM 查看所有可能的来源。
        #     这促使 LLM 多次调用 get_news，用不同 query 覆盖不同维度。
        #
        # 第 5 段："Provide specific, actionable insights with supporting
        #          evidence to help traders make informed decisions."
        #   → 输出要求：可操作的见解 + 证据支撑。
        #
        # 第 6 段："Make sure to append a Markdown table at the end of the
        #          report to organize key points in the report, organized
        #          and easy to read."
        #   → 格式要求：末尾附 Markdown 表格。
        # ====================================================================
        system_message = (
            "你是一名社交媒体与公司专属新闻研究员/分析师，负责分析某家特定公司过去一周的"
            "社交媒体帖子、近期公司新闻以及公众情绪。你会获得公司名称，你的目标是撰写一份全面的长篇报告，"
            "详细描述你在查看社交媒体上关于该公司的舆论、分析公众每日对该公司的情绪数据、"
            "并审视近期公司新闻之后，得出的分析、洞察以及对交易员和投资者关于该公司当前状况的启示。"
            "请使用 get_news(ticker, start_date, end_date) 工具搜索公司专属新闻和社交媒体讨论（ticker 传入股票代码，例如 600519）。"
            "如需补充最新舆情或事件，可调用 search_web 进行联网搜索。"
            "尽可能涵盖从社交媒体到情绪数据再到新闻的所有来源。"
            "请提供有具体支撑证据、可操作的洞察，帮助交易员做出明智的决策。"
            + """ 请务必在报告末尾附带一个 Markdown 表格，将报告中的关键点整理归纳，便于阅读。"""
        )
        # [英文备份] system_message (en):
        # system_message = (
        #     "You are a social media and company specific news researcher/analyst tasked with analyzing social media posts, recent company news, and public sentiment for a specific company over the past week. You will be given a company's name your objective is to write a comprehensive long report detailing your analysis, insights, and implications for traders and investors on this company's current state after looking at social media and what people are saying about that company, analyzing sentiment data of what people feel each day about the company, and looking at recent company news. Use the get_news(ticker, start_date, end_date) tool to search for company-specific news and social media discussions. Try to look at all sources possible from social media to sentiment to news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
        #     + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
        # )

        # ====================================================================
        # 构建 ChatPromptTemplate（聊天提示词模板）
        #
        # 【什么是 ChatPromptTemplate？】
        # LangChain 的"聊天提示词模板"，把文本组织成有角色区分的消息序列。
        # 消息角色：system（角色定义）、human（用户）、ai（AI回复）、tool（工具结果）。
        #
        # 【from_messages() 的结构】
        # 接收一个列表，每个元素是一条消息：
        #   - ("system", "内容")：系统消息，定义 AI 的角色和行为（最高优先级）
        #   - MessagesPlaceholder(...)：占位符，运行时插入历史消息列表
        #
        # 模板中的 {变量名} 是占位符，通过 .partial() 方法填充。
        #
        # 模板中的关键变量：
        #   {tool_names}：当前 Agent 可用的工具名称列表（这里是 "get_news"）
        #   {system_message}：上面定义的角色提示词
        #   {current_date}：当前日期，帮助 LLM 理解时间上下文
        #   {instrument_context}：公司信息上下文
        #
        # 【协作提示词逐行解析】
        #
        # "You are a helpful AI assistant, collaborating with other assistants."
        #   → 多 Agent 协作基调：你是团队一员。
        #
        # "Use the provided tools to progress towards answering the question."
        #   → 鼓励用工具推进任务。
        #
        # "If you are unable to fully answer, that's OK; another assistant
        #  with different tools will help where you left off."
        #   → 容错机制：做不完没关系，其他 Agent 会接力。
        #
        # "Execute what you can to make progress."
        #   → 行动导向：先做能做的。
        #
        # "If you or any other assistant has the FINAL TRANSACTION PROPOSAL:
        #  **BUY/HOLD/SELL** or deliverable, prefix your response with
        #  FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
        #   → 停止信号机制：输出这个前缀 = 分析完成，团队停止。
        #
        # "You have access to the following tools: {tool_names}."
        #   → {tool_names} 会被 partial() 填充为 "get_news"
        #
        # "{system_message}"
        #   → 插入上面的角色专属提示词。
        #
        # "For your reference, the current date is {current_date}. {instrument_context}"
        #   → 提供时间上下文和公司信息。
        #
        # 【FINAL TRANSACTION PROPOSAL 机制】
        #   多 Agent 系统的"停止信号"。分析师阶段一般不会触发（只写报告不做决策），
        #   但保留此机制为后续交易员节点兼容。
        # ====================================================================
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是一个乐于协作的 AI 助手，与其他助手协同工作。"
                    " 请使用提供的工具逐步推进以回答当前问题。"
                    " 如果你无法完整回答也没关系，另一位拥有不同工具的助手会接着你未完成的部分继续。"
                    " 尽你所能推进任务进度。"
                    " 如果你或任何其他助手得出了最终的交易提案：**买入/持有/卖出** 或可交付成果，"
                    " 请在回复开头加上前缀 FINAL TRANSACTION PROPOSAL: **买入/持有/卖出**，以便团队知道可以停止。"
                    " 你可以使用以下工具：{tool_names}。\n{system_message}"
                    "供你参考，当前日期为 {current_date}。{instrument_context}",
                ),
        # [英文备份] 协作提示词 (en):
        # (
        #     "system",
        #     "You are a helpful AI assistant, collaborating with other assistants."
        #     " Use the provided tools to progress towards answering the question."
        #     " If you are unable to fully answer, that's OK; another assistant with different tools"
        #     " will help where you left off. Execute what you can to make progress."
        #     " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
        #     " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
        #     " You have access to the following tools: {tool_names}.\n{system_message}"
        #     "For your reference, the current date is {current_date}. {instrument_context}",
        # ),
                # ==================================================================
                # MessagesPlaceholder：运行时插入历史消息
                #
                # 【什么是 MessagesPlaceholder？】
                # 它是一个"动态占位符"，定义模板时先占位置，
                # 运行时（调用 invoke 时）把实际的消息列表填进去。
                #
                # 【为什么需要它？】
                # 在多 Agent 系统中，消息历史不断增长：
                #   用户消息 → Agent 回复 → 工具调用 → 工具结果 → ...
                # MessagesPlaceholder 让模板自动展开所有历史消息，
                # 后续 Agent 看到完整对话历史才能理解上下文。
                #
                # variable_name="messages"：
                #   告诉模板"运行时从输入中找名为 'messages' 的变量来填充"。
                #   后续 chain.invoke(state["messages"]) 时，state["messages"]
                #   是消息对象列表，会被自动展开填入此位置。
                # ==================================================================
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # ====================================================================
        # .partial() 方法：预先填充部分变量
        #
        # 【什么是 partial()？】
        # partial() 可以"提前填好"模板中的 {变量} 占位符。
        # 每次调用返回一个新模板对象，已填的变量不再需要传入。
        #
        # 【为什么要分步填充？】
        # 变量在不同时间点确定：
        #   - system_message, tool_names：创建时就知道 → 现在填
        #   - current_date, instrument_context：运行时才知道 → 在节点函数内填
        #   - messages：运行时才知道 → 留给 invoke() 填充
        #
        # partial() 是 LangChain 中模板变量的"延迟绑定"机制。
        # ====================================================================
        # 填入角色专属提示词
        prompt = prompt.partial(system_message=system_message)
        # 将工具名称列表拼接成逗号分隔的字符串，如 "get_news"
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        # 填入当前交易日期
        prompt = prompt.partial(current_date=current_date)
        # 填入公司上下文信息
        prompt = prompt.partial(instrument_context=instrument_context)

        # ====================================================================
        # 构建并执行 LangChain 链
        #
        # 【什么是 | 管道操作符（LCEL）？】
        # LCEL = LangChain Expression Language，LangChain 链式表达式语言。
        # 用 Python 的 | 操作符把组件串联成"数据处理流水线"：
        #   prompt | llm.bind_tools(tools)
        #   数据流向：输入 → prompt（格式化） → llm.bind_tools（LLM推理+工具） → 输出
        #
        # 类比 Unix 管道：cat file | grep "error" | sort
        #   前一个命令的输出 = 后一个命令的输入
        #
        # 【bind_tools() 的作用】
        # bind_tools(tools) 将 Python 函数转换为 LLM 可理解的工具定义。
        # 工具定义包括：函数名、描述、参数类型和说明。
        # LLM 根据这些定义判断何时需要调用哪个工具。
        # 当 LLM 需要数据时，在回复中生成 tool_calls（工具调用请求），
        # LangChain 框架自动执行对应函数。
        #
        # 注意：bind_tools 不会立即调用工具，只是"告知 LLM 工具的存在"。
        # ====================================================================
        chain = prompt | llm.bind_tools(tools)

        # ====================================================================
        # invoke()：执行链并获取结果
        #
        # 【什么是 invoke()？】
        # invoke() 是 LangChain 链的执行方法。
        # 传入 state["messages"]（历史消息列表），得到 LLM 的回复。
        #
        # 执行过程：
        #   1. state["messages"] → prompt 模板 → 填充 {messages} 占位符
        #   2. 完整消息序列 → LLM（附带工具定义）
        #   3. LLM 推理 → 返回 AIMessage 对象
        #
        # 【result 是什么？】
        # result 是 AIMessage 对象，包含：
        #   - result.content：LLM 的文本回复（字符串）
        #   - result.tool_calls：LLM 请求调用的工具列表（列表）
        #
        # 【result.tool_calls 的含义】
        # tool_calls 是一个列表，每个元素是一个工具调用请求：
        #   [{"name": "get_news", "args": {"query": "AAPL reddit", ...}, "id": "..."}]
        # 如果不为空 → LLM 想调用工具，LangGraph 路由到 ToolNode 执行
        # 如果为空 → LLM 已完成分析，生成了最终报告
        # ====================================================================
        result = chain.invoke(state["messages"])

        report = ""

        # 如果 LLM 没有请求工具调用，说明它已经完成了分析，提取报告内容
        #
        # 如果 tool_calls 为空（len == 0）：LLM 已有足够信息，生成最终报告
        # 如果 tool_calls 不为空：LLM 需要先获取数据，报告暂时留空
        #   （LangGraph 会路由到 ToolNode 执行工具，再回到本节点）
        if len(result.tool_calls) == 0:
            report = result.content

        # ====================================================================
        # 返回状态更新
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 你返回什么字段，对应字段就会被更新。
        #
        # 【返回的字段说明】
        # - "messages": [result]
        #   → 把 LLM 回复作为新消息追加到全局 messages 列表。
        #     包装成列表 [result] 是因为 LangGraph 的 messages reducer
        #     期望接收消息列表。这条消息成为后续 Agent 的"历史消息"上下文。
        #
        # - "sentiment_report": report
        #   → 情绪/舆情分析报告文本。如果 LLM 还在请求工具阶段，report 为空字符串。
        #     当 LLM 最终完成分析时，report 包含完整报告文本，
        #     供后续的研究员（bull/bear）和经理节点读取使用。
        #
        # 【状态更新后的数据流】
        # 返回后，LangGraph 检查 result.tool_calls：
        #   - 有 tool_calls → 路由到 ToolNode 执行 → 回到本节点
        #   - 无 tool_calls → 进入下一个节点
        # ====================================================================
        return {
            "messages": [result],
            "sentiment_report": report,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_social_media_analyst 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("social_media_analyst", create_social_media_analyst(llm))
    # ========================================================================
    return social_media_analyst_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设用户输入：公司 = "AAPL"，日期 = "2024-01-15"
#
# 步骤 1：LangGraph 初始化状态
#   state = {
#       "messages": [HumanMessage("Analyze AAPL on 2024-01-15")],
#       "company_of_interest": "AAPL",
#       "trade_date": "2024-01-15",
#       "sentiment_report": "",      # 初始为空
#       ...其他字段...
#   }
#
# 步骤 2：LangGraph 调用 social_media_analyst_node(state)
#   → current_date = "2024-01-15"
#   → instrument_context = "The instrument to analyze is `AAPL`..."
#   → 构建 prompt，填充所有变量
#   → chain.invoke(state["messages"])
#
# 步骤 3：LLM 第一次回复（请求工具——搜社交媒体讨论）
#   result.tool_calls = [{"name": "get_news", "args": {"query": "AAPL stock reddit twitter", "start_date": "2024-01-08", "end_date": "2024-01-15"}}]
#   → 返回 {"messages": [result], "sentiment_report": ""}
#   → LangGraph 检测到 tool_calls，路由到 ToolNode
#
# 步骤 4：ToolNode 执行 get_news(...)
#   → 返回 {"messages": [ToolMessage("Reddit/Twitter 上的讨论...")]}
#   → LangGraph 再次调用 social_media_analyst_node（带上了工具结果）
#
# 步骤 5：LLM 第二次回复（请求工具——搜情绪数据）
#   result.tool_calls = [{"name": "get_news", "args": {"query": "AAPL sentiment analysis", "start_date": "2024-01-08", "end_date": "2024-01-15"}}]
#   → 重复步骤 3-4
#
# 步骤 6：LLM 最终回复（不再请求工具，生成报告）
#   result.tool_calls = []
#   result.content = "## AAPL 社交媒体与情绪分析报告\n\nReddit 讨论热度...\n情绪分析：整体偏正面...\n\n| 日期 | 情绪 | 关键话题 |\n|---|---|---|"
#   → 返回 {"messages": [result], "sentiment_report": "## AAPL 社交媒体与情绪分析报告..."}
#
# 步骤 7：LangGraph 将 sentiment_report 合并到全局状态
#   state["sentiment_report"] = "## AAPL 社交媒体与情绪分析报告..."
#   → 进入下一个节点
#
# ============================================================================
