# ============================================================================
# 模块：基本面分析师 (Fundamentals Analyst)
# 角色：负责分析公司的基本面数据，包括财务报表、公司概况、财务历史等
# 核心功能：调用 get_fundamentals、get_balance_sheet、get_cashflow、
#           get_income_statement 等工具获取公司财务数据，生成基本面分析报告
# 技术栈：LangChain 的 ChatPromptTemplate（提示词模板）、
#         LLM 工具绑定（bind_tools）实现 Agent 工具调用
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是一种经过海量文本训练的人工智能模型，能理解自然语言并生成回复。
#    在本项目中，LLM 充当"智能体的大脑"，负责推理、分析数据和做决策。
#    常见的 LLM 包括 OpenAI 的 GPT 系列、Google 的 Gemini、Anthropic 的 Claude。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。它不是简单的一问一答，而是能"自主决定"
#    何时调用工具、调用哪个工具、如何综合数据来完成任务。
#    在本文件中，基本面分析师就是一个 Agent：它先决定调用哪个财务数据工具，
#    拿到数据后进行分析，最终生成一份基本面报告。
#
# 3. 什么是 LangChain？
#    LangChain 是一个 Python 框架，用于快速构建 LLM 应用。
#    它提供了"提示词模板""工具绑定""链式调用"等功能，
#    让开发者不用手写底层 API 调用，专注于业务逻辑。
#
# 4. 什么是 LangGraph？
#    LangGraph 是 LangChain 生态中的"工作流编排引擎"。
#    它把多个 Agent 连成一张"图"（Graph），定义数据如何从一个节点
#    流向下一个节点。每个节点是一个函数，接收状态（state），
#    返回状态更新。LangGraph 负责调度这些节点的执行顺序。
#
# 5. 什么是状态（state）？
#    状态是一个 Python 字典（dict），在所有节点之间共享。
#    可以理解为"公共黑板"：每个 Agent 从黑板上读取自己需要的信息，
#    把分析结果写到黑板上，供后面的 Agent 使用。
#    例如：基本面分析师读取 trade_date（交易日期）和 company_of_interest（公司名），
#    写入 fundamentals_report（基本面报告）。
#
# 6. 什么是工厂函数（Factory Function）？
#    工厂函数是一种设计模式：一个函数"生产"另一个函数。
#    在本文件中，create_fundamentals_analyst(llm) 就是一个工厂函数：
#    你传入一个 LLM 实例，它返回一个"配置好的"节点函数 fundamentals_analyst_node。
#    这样做的好处是：LLM 只在创建时传入一次，后续每次执行节点时都能复用。
#
# 7. 什么是工具（Tool）？
#    工具是 Agent 可以调用的 Python 函数。每个工具有名称、描述和参数。
#    LLM 通过阅读工具的描述来"理解"工具的作用，在需要时"请求"调用它。
#    LangChain 框架负责实际执行工具函数，把结果返回给 LLM。
#
# 8. 多 Agent 系统中的分析师角色
#    本项目模拟了一个投资研究团队，包含多个分析师（市场、新闻、社交媒体、基本面）。
#    每个分析师关注不同维度，独立生成报告。这些报告汇总后供研究员辩论和决策使用。
#    基本面分析师专注公司财务健康：收入、利润、资产、负债、现金流等。
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

# --- 导入 LangChain 核心模块 ---
# ChatPromptTemplate：用于构建结构化的聊天提示词模板，支持 system 和 human 消息的角色区分
# MessagesPlaceholder：在提示词中预留一个位置，运行时动态插入历史消息列表
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json

# --- 导入项目内部工具函数 ---
# 这些函数都被 @tool 装饰器标记为 LangChain 工具，LLM 可以"看到"它们的描述并请求调用。
#
# build_instrument_context：构建当前分析目标的上下文信息（如公司名称、股票代码等）
# get_balance_sheet：获取公司资产负债表数据（资产=负债+股东权益）
# get_cashflow：获取公司现金流量表数据（经营/投资/筹资现金流）
# get_fundamentals：获取公司综合基本面数据（如公司简介、关键财务指标等）
# get_income_statement：获取公司利润表数据（收入、成本、利润）
# get_insider_transactions：获取公司内部人交易数据（高管买卖股票记录）
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
)
# get_config：获取全局配置（如 API 密钥、模型参数等）
from tradingagents.dataflows.config import get_config


# ============================================================================
# 工厂函数：create_fundamentals_analyst
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# 在 LangGraph 工作流中，每个节点是一个"只接收 state 参数"的函数。
# 但创建一个基本面分析师时，需要传入 LLM 实例（决定用哪个模型）。
# 如果直接写一个接收 (state, llm) 两个参数的函数，LangGraph 无法识别。
#
# 解决方案：工厂函数模式
#   外层函数 create_fundamentals_analyst(llm) 接收 llm 参数，
#   内层函数 fundamentals_analyst_node(state) 只接收 state 参数。
#   内层函数"捕获"了外层的 llm 变量（闭包），所以能使用它。
#
# 这种"先配置、后使用"的模式在编程中非常常见：
#   类比：你去面包店说"我要一个能烤全麦面包的烤箱"，店员给你一个
#   已经设好温度的烤箱。之后你只需要把面团放进去（state），就能得到面包。
#   create_fundamentals_analyst = 店员，llm = 温度设置，
#   fundamentals_analyst_node = 你拿到的烤箱，state = 面团。
# ============================================================================
def create_fundamentals_analyst(llm):
    """
    创建基本面分析师节点函数。

    参数：
        llm: 大语言模型实例（如 ChatOpenAI），是 LangChain 中的基础聊天模型。
             Agent 的所有推理和决策都由这个 LLM 驱动。

    返回值：
        fundamentals_analyst_node: 一个符合 LangGraph 节点签名的函数，
            接收 state（状态字典），返回部分更新的状态字典。
            这是 LangGraph 工作流中的标准节点格式。

    关于 LangGraph 节点（Node）：
        LangGraph 中的每个节点都是一个函数，它接收当前状态（state），
        处理后返回状态更新。这里的 state 是一个字典，包含了整个工作流中
        共享的所有数据，如消息历史、分析报告、日期信息等。
    """

    # ========================================================================
    # 内层节点函数：fundamentals_analyst_node
    # ========================================================================
    # 这个函数就是最终注册到 LangGraph 图中的节点。
    # 每次 LangGraph 执行到这个节点时，就会调用这个函数。
    # ========================================================================
    def fundamentals_analyst_node(state):
        """
        基本面分析师的核心执行逻辑。

        参数：
            state: LangGraph 的状态字典，包含：
                - trade_date: 当前交易日期
                - company_of_interest: 目标公司名称
                - messages: 消息历史列表（LangChain 的 Message 对象列表）

        【读取的状态字段】：
            - state["trade_date"]：当前交易日期
            - state["company_of_interest"]：目标公司名称
            - state["messages"]：历史消息列表

        【写入的状态字段】：
            - "messages"：将 LLM 回复追加到消息历史
            - "fundamentals_report"：基本面分析报告文本
        """
        # 从状态中提取当前交易日期，用于工具调用时的时间上下文
        # 例如 current_date = "2024-01-15"
        current_date = state["trade_date"]

        # 构建目标公司的上下文信息字符串（如 "公司名称: Apple Inc. 股票代码: AAPL"）
        # 这个信息会被注入到 LLM 的提示词中，让 Agent 知道它在分析哪家公司
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        instrument_context = build_instrument_context(state["company_of_interest"])

        # ====================================================================
        # 定义工具列表（Tools）
        #
        # 在 LangChain 中，工具（Tool）是 Agent 可以调用的函数。
        # 当 LLM 判断需要获取数据时，它会生成一个 tool_call 请求，
        # LangChain 框架会自动执行对应的函数并将结果返回给 LLM。
        #
        # 这里的工具都是 Python 函数，它们会调用外部 API 或数据库来获取真实数据。
        #
        # 为什么基本面分析师需要这 4 个工具？
        #   - get_fundamentals：获取公司概况（行业、市值、PE 比率等关键指标）
        #   - get_balance_sheet：获取资产=负债+权益的快照（公司"拥有什么、欠什么"）
        #   - get_cashflow：获取现金的流入流出（公司"钱从哪来、花到哪去"）
        #   - get_income_statement：获取收入与利润明细（公司"赚了多少、花了多少"）
        # LLM 会自主决定先调用哪个工具，可能先看概况再看详细报表。
        # ====================================================================
        tools = [
            get_fundamentals,       # 获取公司综合基本面数据
            get_balance_sheet,      # 获取资产负债表
            get_cashflow,           # 获取现金流量表
            get_income_statement,   # 获取利润表
        ]

        # ====================================================================
        # 系统提示词（System Prompt）
        #
        # 系统提示词定义了 Agent 的角色、任务和行为规范。
        # 它是 LLM 最重要的"指令"，决定了 Agent 会如何思考和行动。
        #
        # 逐段解析这段提示词为什么这样设计：
        #
        # 第 1 段："You are a researcher tasked with analyzing fundamental
        #          information over the past week about a company."
        #   → 角色定义：告诉 LLM 它是"研究员"，分析"过去一周"的基本面信息。
        #     限定时间范围避免 LLM 纠结于太久远的数据。
        #
        # 第 2 段："Please write a comprehensive report of the company's
        #          fundamental information such as financial documents,
        #          company profile, basic company financials, and company
        #          financial history to gain a full view of the company's
        #          fundamental information to inform traders."
        #   → 任务定义：写一份全面报告，涵盖财务文件、公司概况、基本财务、
        #     财务历史。目的是"为交易员提供完整视角"——说明受众是交易员，
        #     影响报告的写作风格（专业、数据驱动）。
        #
        # 第 3 段："Make sure to include as much detail as possible."
        #   → 质量要求：鼓励详细，避免 LLM 敷衍了事只写几句话。
        #
        # 第 4 段："Provide specific, actionable insights with supporting
        #          evidence to help traders make informed decisions."
        #   → 输出要求："可操作的见解 + 支撑证据"。这防止 LLM 只罗列数据
        #     而不做分析。交易员需要的是"这些数据意味着什么"而非原始数据。
        #
        # 第 5 段："Make sure to append a Markdown table at the end of the
        #          report to organize key points in the report, organized
        #          and easy to read."
        #   → 格式要求：末尾附 Markdown 表格。表格便于人类快速浏览关键信息，
        #     也方便后续 Agent 解析。这是"结构化输出"的常见做法。
        #
        # 第 6 段："Use the available tools: `get_fundamentals` for comprehensive
        #          company analysis, `get_balance_sheet`, `get_cashflow`, and
        #          `get_income_statement` for specific financial statements."
        #   → 工具引导：明确告诉 LLM 每个工具的用途，帮助它做正确的工具选择。
        #     虽然工具本身有描述，但在提示词中再次强调可以提高调用准确率。
        # ====================================================================
        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements.",
        )

        # ====================================================================
        # 构建 ChatPromptTemplate（聊天提示词模板）
        #
        # 【什么是 ChatPromptTemplate？】
        # ChatPromptTemplate 是 LangChain 提供的"聊天提示词模板"。
        # 它把多段文本组织成"有角色区分"的消息序列，发送给 LLM。
        # 在聊天模型中，消息分为几种角色：
        #   - system：系统消息，定义 AI 的角色和行为规范（最高优先级）
        #   - human/user：用户消息，提出问题或提供数据
        #   - ai/assistant：AI 的回复
        #   - tool：工具执行返回的结果
        #
        # 【from_messages() 的结构】
        # from_messages() 接收一个列表，列表中每个元素是一条消息。
        # 每条消息是一个元组 (role, content) 或一个特殊占位符对象。
        #
        # 本模板包含两条消息：
        #   1. ("system", "...")  — 系统消息，包含协作提示和角色定义
        #   2. MessagesPlaceholder(...) — 占位符，运行时插入历史消息
        #
        # 模板中的 {变量名} 是占位符，会在后续通过 .partial() 方法填充实际值。
        #
        # 【协作提示词的含义】—— 逐行解析：
        #
        # "You are a helpful AI assistant, collaborating with other assistants."
        #   → 告诉 LLM：你是团队的一员，要和其他 Agent 协作。
        #     这为多 Agent 系统奠定基调。
        #
        # "Use the provided tools to progress towards answering the question."
        #   → 鼓励使用工具推进任务，而不是凭空回答。
        #
        # "If you are unable to fully answer, that's OK; another assistant with
        #  different tools will help where you left off."
        #   → 容错机制：如果做不完也没关系，其他 Agent 会接力。
        #     这让 Agent 不会"卡住"，而是尽力推进。
        #
        # "Execute what you can to make progress."
        #   → 行动导向：先做能做的，不要等。
        #
        # "If you or any other assistant has the FINAL TRANSACTION PROPOSAL:
        #  **BUY/HOLD/SELL** or deliverable, prefix your response with
        #  FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
        #   → 停止信号机制：当某个 Agent 输出这个前缀时，整个团队知道分析完成了。
        #     这是多 Agent 系统中常见的"终止条件"设计。
        #
        # "You have access to the following tools: {tool_names}."
        #   → 列出可用工具名称，{tool_names} 会被 partial() 填充。
        #
        # "{system_message}"
        #   → 插入上面定义的角色专属提示词。
        #
        # "For your reference, the current date is {current_date}.
        #  {instrument_context}"
        #   → 提供时间上下文和公司信息，帮助 LLM 理解"现在分析什么、什么时候"。
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
                # MessagesPlaceholder 是一个"动态占位符"。
                # 在定义模板时，你不知道运行时会有多少条历史消息，
                # 所以先用 MessagesPlaceholder 占一个位置，
                # 运行时（调用 invoke 时）会把实际的消息列表填进去。
                #
                # 【为什么需要它？】
                # 在多 Agent 系统中，消息历史会不断增长。
                # 第一个 Agent 调用工具后，工具结果会成为消息历史的一部分，
                # 下一个 Agent 需要看到之前的完整对话才能理解上下文。
                # MessagesPlaceholder 让这一切自动发生——
                # 你只需传入 state["messages"]，模板会自动展开。
                #
                # variable_name="messages" 的含义：
                #   告诉模板"运行时从输入数据中找名为 'messages' 的变量来填充"。
                #   后续 chain.invoke(state["messages"]) 时，state["messages"]
                #   是一个消息对象列表，会被填入这个位置。
                # ==================================================================
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # ====================================================================
        # .partial() 方法：预先填充部分变量
        #
        # 【什么是 partial()？】
        # partial() 是 LangChain 提示词模板的核心功能之一。
        # 模板中有很多 {变量} 占位符，partial() 可以"提前填好"其中一部分。
        # 每次调用返回一个新的模板对象（原模板不变），已填的变量不再需要传入。
        #
        # 【为什么要分步填充？】
        # 因为不同的变量在不同时间点才能确定：
        #   - system_message：创建节点时就确定了 → 现在填
        #   - tool_names：创建节点时就确定了 → 现在填
        #   - current_date：运行时才知道 → 现在填（因为在节点函数内部）
        #   - instrument_context：运行时才知道 → 现在填
        #   - messages：运行时才知道 → 留给 invoke() 时填充
        #
        # 通过多次 partial()，逐步把变量填入，最终生成一个完整的提示词。
        # ====================================================================
        # 填入角色专属提示词
        prompt = prompt.partial(system_message=system_message)
        # 将工具名称列表拼接成逗号分隔的字符串，如 "get_fundamentals, get_balance_sheet"
        # 这样做是为了让 LLM 知道有哪些工具可用
        # [tool.name for tool in tools] 是列表推导式，提取每个工具的名称属性
        # ", ".join(...) 把列表拼成逗号分隔的字符串
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        # 填入当前交易日期
        prompt = prompt.partial(current_date=current_date)
        # 填入公司上下文信息
        prompt = prompt.partial(instrument_context=instrument_context)

        # ====================================================================
        # 构建 LangChain 链（Chain）：prompt | llm.bind_tools(tools)
        #
        # 【什么是 LangChain 链（Chain）和 LCEL？】
        # LCEL = LangChain Expression Language，LangChain 的链式表达式语言。
        # 它用 Python 的管道操作符 | 把多个组件串联起来，
        # 形成一条"数据处理流水线"。
        #
        # 【| 管道操作符的工作方式（Unix 管道类比）】
        # 在 Unix/Linux 中，管道符 | 把前一个命令的输出作为后一个命令的输入：
        #   cat file.txt | grep "error" | sort
        # 在 LangChain 中也是一样：
        #   prompt | llm.bind_tools(tools)
        #   数据流向：输入 → prompt（格式化） → llm.bind_tools（LLM推理） → 输出
        #
        # 具体流程：
        #   1. 输入（消息列表）传入 prompt 模板
        #   2. prompt 模板把 {变量} 替换为实际值，生成完整的消息序列
        #   3. 完整的消息序列传入 llm.bind_tools(tools)
        #   4. LLM 接收消息，进行推理，可能请求调用工具
        #   5. 返回 AIMessage 结果
        #
        # 【bind_tools() 的作用】
        # bind_tools(tools) 将工具信息"附加"到 LLM 上。
        # 它把每个 Python 函数的名称、描述、参数类型转换为 LLM 能理解的格式
        # （通常是 JSON Schema），放在每次 LLM 请求中。
        # 这样 LLM 就"知道"有哪些工具可用、每个工具做什么、怎么传参数。
        # 当 LLM 认为需要数据时，它会在回复中生成 tool_calls（工具调用请求），
        # LangChain 框架（或 LangGraph 的 ToolNode）会自动执行对应函数。
        #
        # 注意：bind_tools 不会立即调用工具，只是"告诉 LLM 工具的存在"。
        # 实际调用发生在 LLM 返回 tool_calls 之后。
        # ====================================================================
        chain = prompt | llm.bind_tools(tools)

        # ====================================================================
        # invoke()：执行链并获取结果
        #
        # 【什么是 invoke()？】
        # invoke() 是 LangChain 链的执行方法。传入输入数据，返回输出结果。
        # 这里传入 state["messages"]（历史消息列表），得到 LLM 的回复。
        #
        # 执行过程：
        #   1. state["messages"] 传入 prompt 模板
        #   2. prompt 模板填充 {messages} 占位符（通过 MessagesPlaceholder）
        #      和其他已 partial 的变量，生成完整的消息序列
        #   3. 完整消息序列发送给 LLM（附带工具定义）
        #   4. LLM 推理后返回一个 AIMessage 对象
        #
        # 【result 是什么？】
        # result 是一个 AIMessage 对象，包含两个重要属性：
        #   - result.content：LLM 生成的文本回复（字符串）
        #   - result.tool_calls：LLM 请求调用的工具列表（列表）
        #
        # 【result.tool_calls 的含义】
        # tool_calls 是一个列表，每个元素是一个工具调用请求：
        #   [{"name": "get_balance_sheet", "args": {"ticker": "AAPL"}, "id": "..."}]
        # 如果 tool_calls 不为空，说明 LLM 想调用工具获取数据，
        # LangGraph 的工作流会把控制权转给 ToolNode 执行工具，
        # 然后把工具结果返回给这个 Agent，再执行一次。
        # 如果 tool_calls 为空，说明 LLM 已经有足够信息，生成了最终报告。
        # ====================================================================
        result = chain.invoke(state["messages"])

        report = ""

        # ====================================================================
        # 判断 LLM 是否只是生成了文本回复（没有请求工具调用）
        #
        # 这里的逻辑：
        # - 如果 tool_calls 为空（len == 0）：LLM 完成了分析，直接输出报告内容
        # - 如果 tool_calls 不为空：LLM 需要先获取数据，报告暂时留空
        #   （后续 LangGraph 的条件边会把控制权转给 ToolNode，
        #    工具执行后再次回到这个节点，直到 LLM 不再请求工具为止）
        #
        # 为什么在 tool_calls 非空时不写 report？
        # 因为此时 LLM 还没写出最终报告，它只是在"请求工具"阶段。
        # report 只有在 LLM 不再需要工具、直接输出分析文本时才有意义。
        # ====================================================================
        if len(result.tool_calls) == 0:
            report = result.content

        # ====================================================================
        # 返回状态更新字典
        #
        # 【返回值的作用】
        # 在 LangGraph 中，节点函数返回的字典会被"合并"到全局状态（state）中。
        # 这就是"状态更新"机制：你返回什么字段，对应字段就会被更新。
        #
        # 【返回的字段说明】
        # - "messages": [result]
        #   → 把 LLM 的回复作为一条新消息，追加到全局的 messages 列表中。
        #     包装成列表 [result] 是因为 LangGraph 的 messages reducer
        #     期望接收一个消息列表（支持追加多条消息）。
        #     这条消息会成为后续 Agent 的"历史消息"上下文。
        #
        # - "fundamentals_report": report
        #   → 基本面分析报告文本。如果 LLM 还在请求工具阶段，report 为空字符串，
        #     不会覆盖之前的内容（因为空字符串在 LangGraph 中默认会覆盖）。
        #     当 LLM 最终完成分析时，report 包含完整的报告文本，
        #     供后续的研究员（bull/bear）和经理节点读取使用。
        #
        # 【状态更新后的数据流】
        # 返回后，LangGraph 会检查 result.tool_calls：
        #   - 如果有 tool_calls → 路由到 ToolNode 执行工具 → 回到本节点
        #   - 如果没有 tool_calls → 进入下一个节点（其他分析师或辩论节点）
        # ====================================================================
        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 这种设计模式称为"工厂函数"——create_fundamentals_analyst 是工厂，
    # 它接收 llm 参数，返回一个配置好的节点函数 fundamentals_analyst_node。
    #
    # 内层函数 fundamentals_analyst_node "捕获"了外层的 llm 变量，
    # 这种机制称为"闭包"（Closure）。即使外层函数已经返回，
    # 内层函数仍然可以使用 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("fundamentals_analyst", create_fundamentals_analyst(llm))
    # 即先用工厂函数创建节点，再注册到图中。
    # ========================================================================
    return fundamentals_analyst_node


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
#       "fundamentals_report": "",      # 初始为空
#       ...其他字段...
#   }
#
# 步骤 2：LangGraph 调用 fundamentals_analyst_node(state)
#   → current_date = "2024-01-15"
#   → instrument_context = "The instrument to analyze is `AAPL`..."
#   → 构建 prompt，填充所有变量
#   → chain.invoke(state["messages"])
#
# 步骤 3：LLM 第一次回复（请求工具）
#   result.tool_calls = [{"name": "get_fundamentals", "args": {"ticker": "AAPL"}}]
#   result.content = ""（没有文本输出）
#   → 返回 {"messages": [result], "fundamentals_report": ""}
#   → LangGraph 检测到 tool_calls，路由到 ToolNode
#
# 步骤 4：ToolNode 执行 get_fundamentals("AAPL")
#   → 返回 {"messages": [ToolMessage("Apple Inc. 概况数据...")]}
#   → LangGraph 再次调用 fundamentals_analyst_node（带上了工具结果）
#
# 步骤 5：LLM 第二次回复（继续请求工具）
#   result.tool_calls = [{"name": "get_balance_sheet", "args": {"ticker": "AAPL"}}]
#   → 重复步骤 3-4
#
# 步骤 6：LLM 最终回复（不再请求工具，生成报告）
#   result.tool_calls = []
#   result.content = "## AAPL 基本面分析报告\n\nApple Inc. 的财务状况...\n\n| 指标 | 值 |\n|---|---|"
#   → 返回 {"messages": [result], "fundamentals_report": "## AAPL 基本面分析报告..."}
#
# 步骤 7：LangGraph 将 fundamentals_report 合并到全局状态
#   state["fundamentals_report"] = "## AAPL 基本面分析报告..."
#   → 进入下一个节点（如新闻分析师或辩论节点）
#
# ============================================================================
