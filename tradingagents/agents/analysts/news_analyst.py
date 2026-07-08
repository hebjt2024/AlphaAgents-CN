# ============================================================================
# 模块：新闻分析师 (News Analyst)
# 角色：负责分析近期新闻和宏观经济趋势，生成新闻分析报告
# 核心功能：调用 get_news 获取公司特定新闻，调用 get_global_news 获取全球宏观经济新闻
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
#    在本项目中，LLM 是 Agent 的"大脑"，负责阅读新闻、提取关键信息、撰写报告。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。它不是被动回答，而是主动决定
#    "我需要什么数据 → 调用什么工具 → 如何分析"。
#    新闻分析师 Agent 会自主决定：先搜公司新闻还是先搜全球新闻？
#    需要搜索几次？每次用什么关键词？
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    提供提示词模板、工具绑定、链式调用等能力，简化 LLM 开发。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎，把多个 Agent 连成一张图。
#    每个节点是一个函数（接收 state，返回 state 更新），
#    LangGraph 负责按定义的顺序执行节点。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    新闻分析师从黑板上读取 trade_date（日期）和 company_of_interest（公司名），
#    把分析结果 news_report 写到黑板上。
#
# 6. 什么是工厂函数（Factory Function）？
#    create_news_analyst(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层函数负责"配置"（传入 llm），内层函数负责"执行"（接收 state）。
#    这让 LangGraph 节点签名（只需 state 参数）和配置需求（需要 llm）解耦。
#
# 7. 什么是工具（Tool）？
#    工具是 Agent 可调用的 Python 函数，有名称、描述和参数。
#    LLM 通过阅读描述理解工具作用，在需要时"请求"调用。
#    新闻分析师有 2 个工具：get_news（公司新闻）和 get_global_news（全球新闻）。
#
# 8. 新闻分析师在系统中的角色和职责
#    新闻分析师关注"世界正在发生什么"：
#    - 公司层面：公司有什么重大新闻？（产品发布、财报、诉讼等）
#    - 宏观层面：全球宏观经济如何？（利率、通胀、地缘政治等）
#    这些信息帮助交易员理解新闻事件对股价的潜在影响。
#
#    与其他分析师的区别：
#    - 基本面分析师：关注财务报表数据（数字）
#    - 新闻分析师：关注新闻事件和宏观趋势（事件）← 本文件
#    - 社交媒体分析师：关注公众情绪和讨论（情绪）
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
import time
import json

# --- 导入项目内部工具函数 ---
# 这些函数被 @tool 装饰器标记为 LangChain 工具，LLM 可"看到"描述并请求调用。
#
# build_instrument_context：构建目标公司的上下文信息
# get_global_news：获取全球宏观经济新闻（参数：curr_date, look_back_days, limit）
# get_news：获取公司特定新闻或主题搜索（参数：query, start_date, end_date）
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_news,
)
# get_config：获取全局配置
from tradingagents.dataflows.config import get_config


# ============================================================================
# 工厂函数：create_news_analyst
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 的节点函数签名要求只接收 state 一个参数。
# 但创建新闻分析师需要传入 LLM 实例（决定用哪个模型）。
# 工厂函数模式解决了这个矛盾：
#   外层 create_news_analyst(llm) 接收 llm，
#   内层 news_analyst_node(state) 只接收 state，
#   内层通过"闭包"机制捕获外层的 llm 变量。
#
# 类比：配置一台打印机（传入纸张类型），之后只需要发送文档（state）就能打印。
#   create_news_analyst = 打印机配置过程，llm = 纸张类型，
#   news_analyst_node = 配置好的打印机，state = 要打印的文档。
# ============================================================================
def create_news_analyst(llm):
    """
    创建新闻分析师节点函数（工厂函数模式）。

    新闻分析师的核心职责：
    1. 分析过去一周的新闻和趋势
    2. 涵盖公司特定新闻和全球宏观经济新闻两个层面
    3. 生成包含具体、可操作见解的综合报告
    4. 报告末尾附 Markdown 表格整理关键信息

    参数：
        llm: 大语言模型实例，驱动 Agent 的推理和决策
    """

    # ========================================================================
    # 内层节点函数：news_analyst_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def news_analyst_node(state):
        """
        新闻分析师节点：LangGraph 工作流中的标准节点函数。

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
            - "news_report"：新闻分析报告文本，供后续节点使用
        """
        # 从状态中提取当前交易日期
        # 例如 current_date = "2024-01-15"
        current_date = state["trade_date"]

        # 构建目标公司的上下文信息字符串
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        # 这个信息会被注入到 LLM 提示词中，让 Agent 知道在分析哪家公司
        instrument_context = build_instrument_context(state["company_of_interest"])

        # ====================================================================
        # 定义工具列表
        #
        # 在 LangChain 中，工具是 Agent 可以调用的函数。
        # LLM 会根据分析需求自主决定调用哪个工具。
        #
        # 为什么新闻分析师需要这 2 个工具？
        #
        # get_news(query, start_date, end_date)：
        #   用于搜索公司特定新闻或特定主题的新闻。
        #   query 参数可以传公司名称、关键词等。
        #   例如 LLM 可能调用 get_news("AAPL earnings", "2024-01-08", "2024-01-15")
        #
        # get_global_news(curr_date, look_back_days, limit)：
        #   用于获取全球宏观经济新闻，不限于特定公司。
        #   look_back_days 控制回溯天数，limit 控制返回条数。
        #   例如 LLM 可能调用 get_global_news("2024-01-15", 7, 10)
        #
        # LLM 会根据分析需求自动决定调用顺序和参数。
        # 例如，它可能先调用 get_news 获取公司新闻，
        # 再调用 get_global_news 获取宏观经济背景。
        # ====================================================================
        tools = [
            get_news,
            get_global_news,
        ]

        # ====================================================================
        # 系统提示词（System Prompt）
        #
        # 逐段解析这段提示词为什么这样设计：
        #
        # 第 1 段："You are a news researcher tasked with analyzing recent
        #          news and trends over the past week."
        #   → 角色定义：新闻研究员，分析"过去一周"的新闻和趋势。
        #     限定一周范围，确保信息时效性。
        #
        # 第 2 段："Please write a comprehensive report of the current state
        #          of the world that is relevant for trading and macroeconomics."
        #   → 任务定义：写一份"与交易和宏观经济相关的世界现状"报告。
        #     关键词"trading and macroeconomics"限定关注范围——不是所有新闻，
        #     只关注影响交易和宏观经济的事件。
        #
        # 第 3 段："Use the available tools: get_news(query, start_date, end_date)
        #          for company-specific or targeted news searches, and
        #          get_global_news(curr_date, look_back_days, limit) for broader
        #          macroeconomic news."
        #   → 工具引导：明确告诉 LLM 两个工具各自的用途和参数格式。
        #     写出参数名（如 query, start_date）帮助 LLM 正确调用。
        #
        # 第 4 段："Provide specific, actionable insights with supporting
        #          evidence to help traders make informed decisions."
        #   → 输出要求：可操作的见解 + 证据支撑。
        #     防止 LLM 只罗列新闻标题而不做分析。
        #     交易员需要的是"这条新闻意味着什么"而非原始新闻列表。
        #
        # 第 5 段："Make sure to append a Markdown table at the end of the
        #          report to organize key points in the report, organized
        #          and easy to read."
        #   → 格式要求：末尾附 Markdown 表格，便于快速浏览关键信息。
        #     结构化输出也方便后续 Agent 提取关键数据。
        # ====================================================================
        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for company-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
        )

        # ====================================================================
        # 构建 ChatPromptTemplate（聊天提示词模板）
        #
        # 【什么是 ChatPromptTemplate？】
        # ChatPromptTemplate 是 LangChain 的"聊天提示词模板"，
        # 把文本组织成有角色区分的消息序列发送给 LLM。
        # 消息角色包括：system（系统/角色定义）、human（用户）、ai（AI回复）、tool（工具结果）。
        #
        # 【from_messages() 的结构】
        # 接收一个列表，每个元素是一条消息：
        #   - ("system", "内容")：系统消息，定义 AI 的角色和行为（最高优先级）
        #   - MessagesPlaceholder(...)：占位符，运行时插入历史消息列表
        #
        # 模板中的 {变量名} 是占位符，通过 .partial() 方法填充。
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
        #     这是多 Agent 系统的"终止条件"。
        #
        # "You have access to the following tools: {tool_names}."
        #   → {tool_names} 会被 partial() 填充为 "get_news, get_global_news"
        #
        # "{system_message}"
        #   → 插入上面的角色专属提示词。
        #
        # "For your reference, the current date is {current_date}. {instrument_context}"
        #   → 提供时间上下文和公司信息。
        #
        # 【FINAL TRANSACTION PROPOSAL 机制】
        #   这是整个多 Agent 系统的"停止信号"。
        #   当任何一个 Agent 在回复中输出 "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**"
        #   时，整个团队就知道分析已经完成，可以停止进一步的讨论了。
        #   在分析师阶段一般不会触发这个信号（分析师只写报告不做交易决策），
        #   但保留这个机制是为了和后续的交易员节点兼容。
        # ====================================================================
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                # ==================================================================
                # MessagesPlaceholder：运行时插入历史消息
                #
                # 【什么是 MessagesPlaceholder？】
                # 它是一个"动态占位符"，在定义模板时先占一个位置，
                # 运行时（调用 invoke 时）把实际的消息列表填进去。
                #
                # 【为什么需要它？】
                # 在多 Agent 系统中，消息历史会不断增长：
                #   用户消息 → Agent 回复 → 工具调用 → 工具结果 → Agent 回复 → ...
                # MessagesPlaceholder 让模板自动展开所有历史消息，
                # 不需要手动拼接。后续 Agent 看到完整对话历史才能理解上下文。
                #
                # variable_name="messages"：
                #   告诉模板"运行时从输入中找名为 'messages' 的变量来填充"。
                #   后续 chain.invoke(state["messages"]) 时，state["messages"]
                #   是一个消息对象列表，会被自动展开填入这个位置。
                # ==================================================================
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # ====================================================================
        # .partial() 方法：预先填充部分变量
        #
        # 【什么是 partial()？】
        # partial() 可以"提前填好"模板中的 {变量} 占位符。
        # 每次调用返回一个新模板对象（原模板不变），已填的变量不再需要传入。
        #
        # 【为什么要分步填充？】
        # 因为变量在不同时间点确定：
        #   - system_message, tool_names：创建时就知道 → 现在填
        #   - current_date, instrument_context：运行时才知道 → 在节点函数内填
        #   - messages：运行时才知道 → 留给 invoke() 填充
        #
        # 这里 4 次调用 partial()，分别填入 4 个变量。
        # ====================================================================
        # 填入角色专属提示词
        prompt = prompt.partial(system_message=system_message)
        # 将工具名称列表拼接成逗号分隔的字符串，如 "get_news, get_global_news"
        # [tool.name for tool in tools] 是列表推导式，提取每个工具的 name 属性
        # ", ".join(...) 把列表元素用逗号+空格拼接成字符串
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
        # bind_tools(tools) 把工具信息（名称、描述、参数类型）转换为
        # LLM 能理解的格式（JSON Schema），附加到每次 LLM 请求中。
        # 这样 LLM 就"知道"有哪些工具可用、怎么调用。
        # 当 LLM 需要数据时，在回复中生成 tool_calls（工具调用请求），
        # LangChain 框架自动执行对应函数。
        #
        # 注意：bind_tools 不会立即调用工具，只是"告知 LLM 工具的存在"。
        #
        # 执行流程：
        # 1. 用户消息 → prompt 模板格式化 → 生成完整提示词
        # 2. 完整提示词 → llm.bind_tools(tools) → LLM 推理并可能请求工具调用
        # 3. LLM 返回 AIMessage，包含 content（文本）和/或 tool_calls（工具调用请求）
        #
        # 注意：这里只执行了一次 LLM 调用。如果 LLM 返回了 tool_calls，
        # 实际的工具执行和后续循环由 LangGraph 的工作流引擎负责处理。
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
        #   [{"name": "get_news", "args": {"query": "AAPL", "start_date": "..."}, "id": "..."}]
        # 如果不为空 → LLM 想调用工具，LangGraph 路由到 ToolNode 执行
        # 如果为空 → LLM 已完成分析，生成了最终报告
        # ====================================================================
        result = chain.invoke(state["messages"])

        report = ""

        # ====================================================================
        # 判断 LLM 是否完成了分析（没有请求进一步的工具调用）
        #
        # 如果 tool_calls 为空（len == 0）：LLM 已有足够信息，生成最终报告
        # 如果 tool_calls 不为空：LLM 需要先获取数据，报告暂时留空
        #   （LangGraph 会路由到 ToolNode 执行工具，再回到本节点）
        #
        # 为什么在 tool_calls 非空时 report 留空？
        # 因为此时 LLM 还在"请求工具"阶段，尚未写出最终报告。
        # report 只有在 LLM 不再需要工具时才有意义。
        # ====================================================================
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
        #     期望接收消息列表（支持追加多条）。
        #     这条消息成为后续 Agent 的"历史消息"上下文。
        #
        # - "news_report": report
        #   → 新闻分析报告文本。如果 LLM 还在请求工具阶段，report 为空字符串。
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
            "news_report": report,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_news_analyst 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("news_analyst", create_news_analyst(llm))
    # ========================================================================
    return news_analyst_node


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
#       "news_report": "",          # 初始为空
#       ...其他字段...
#   }
#
# 步骤 2：LangGraph 调用 news_analyst_node(state)
#   → current_date = "2024-01-15"
#   → instrument_context = "The instrument to analyze is `AAPL`..."
#   → 构建 prompt，填充所有变量
#   → chain.invoke(state["messages"])
#
# 步骤 3：LLM 第一次回复（请求工具——搜公司新闻）
#   result.tool_calls = [{"name": "get_news", "args": {"query": "AAPL", "start_date": "2024-01-08", "end_date": "2024-01-15"}}]
#   → 返回 {"messages": [result], "news_report": ""}
#   → LangGraph 检测到 tool_calls，路由到 ToolNode
#
# 步骤 4：ToolNode 执行 get_news(...)
#   → 返回 {"messages": [ToolMessage("Apple 相关新闻列表...")]}
#   → LangGraph 再次调用 news_analyst_node（带上了工具结果）
#
# 步骤 5：LLM 第二次回复（请求工具——搜全球新闻）
#   result.tool_calls = [{"name": "get_global_news", "args": {"curr_date": "2024-01-15", "look_back_days": 7, "limit": 10}}]
#   → 重复步骤 3-4
#
# 步骤 6：LLM 最终回复（不再请求工具，生成报告）
#   result.tool_calls = []
#   result.content = "## 新闻分析报告\n\n公司层面：Apple 发布了...\n宏观层面：美联储...\n\n| 类别 | 关键事件 | 影响 |\n|---|---|---|"
#   → 返回 {"messages": [result], "news_report": "## 新闻分析报告..."}
#
# 步骤 7：LangGraph 将 news_report 合并到全局状态
#   state["news_report"] = "## 新闻分析报告..."
#   → 进入下一个节点（如研究员辩论或组合经理）
#
# ============================================================================
