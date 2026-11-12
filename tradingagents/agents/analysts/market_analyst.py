# =============================================================================
# 前置知识：阅读本文件前需要理解的核心概念
# =============================================================================
#
# 【1. ChatPromptTemplate（聊天提示词模板）】
#   来自 langchain_core.prompts 模块。
#   ChatPromptTemplate 是 LangChain 提供的"结构化聊天提示词构建器"。
#
#   【与 Python f-string 的对比】
#   Python 原生的 f-string 也能做字符串替换：
#       name = "Alice"
#       text = f"Hello, {name}!"   # 结果："Hello, Alice!"
#   但 f-string 有两个重要限制：
#     (a) f-string 在执行时立即替换所有变量，无法"先填一部分，稍后填另一部分"
#     (b) f-string 只能生成纯文本，无法区分"系统消息"和"用户消息"等角色
#
#   ChatPromptTemplate 的优势：
#     (a) 支持"消息类型"（message type）：可以指定某段文字是 "system" 角色
#         （系统提示，定义 AI 的行为）还是 "human" 角色（用户输入）还是
#         "ai" 角色（AI 回复）。大语言模型对不同角色的消息处理方式不同。
#     (b) 支持"后续填充"：模板可以先填一部分变量，剩下的稍后再填。
#         这就是 partial() 方法的功能。
#     (c) 支持"消息占位符"：可以在模板中预留位置，运行时插入完整的历史消息列表。
#
#   示例：
#       from langchain_core.prompts import ChatPromptTemplate
#
#       # 创建模板，包含两个消息
#       prompt = ChatPromptTemplate.from_messages([
#           ("system", "你是一个{role}助手。"),       # system 角色消息
#           ("human", "请帮我{task}。"),              # human 角色消息
#       ])
#       # 先填 role 变量
#       prompt = prompt.partial(role="翻译")
#       # 稍后填 task 变量并执行
#       result = prompt.invoke({"task": "翻译这句话"})
#
# 【2. MessagesPlaceholder（消息占位符）】
#   来自 langchain_core.prompts 模块。
#   MessagesPlaceholder 是 ChatPromptTemplate 中的特殊组件。
#   它的作用：在模板中"预留一个位置"，运行时用一组消息对象（如对话历史）
#   来填充这个位置。
#
#   为什么需要它？
#   对话历史是一个"消息列表"（如 [HumanMessage(...), AIMessage(...), ...]），
#   不是简单的字符串。MessagesPlaceholder 可以把这些消息对象"按原样"插入到
#   模板中正确的位置，保持消息的角色信息（哪条是用户说的、哪条是 AI 说的）。
#
#   示例：
#       from langchain_core.prompts import MessagesPlaceholder
#
#       prompt = ChatPromptTemplate.from_messages([
#           ("system", "你是交易助手。"),
#           MessagesPlaceholder(variable_name="messages"),  # 历史消息插入位置
#       ])
#       # 执行时，messages 变量的值是一个消息列表
#       result = prompt.invoke({"messages": [HumanMessage(content="分析AAPL")]})
#
# 【3. partial() 方法（分步填表）】
#   partial 是"部分填充"的意思。
#   ChatPromptTemplate.partial(变量名=值) 的作用：
#   把模板中的某个变量"提前填好"，返回一个新的模板（原模板不变）。
#   剩下的变量可以稍后再填。
#
#   类比：就像填写一份表格
#   - 表格有 5 个空位：姓名、年龄、日期、部门、职位
#   - 你现在只知道姓名和部门，可以先填这两个
#   - 这就是 partial(name="Alice", department="技术部")
#   - 剩下的年龄、日期、职位等表格送到下一站时再填
#
#   为什么要分步填？
#   因为有些变量在"创建模板时"就知道（如系统提示词、工具名称列表），
#   有些变量要到"执行时"才知道（如用户输入的日期、公司信息）。
#   partial 让你在"知道的时候先填"，避免到处传递这些已知值。
#
# 【4. | 管道操作符（LangChain LCEL 表达式语言）】
#   | 是 Python 的"按位或"运算符，但 LangChain 重新定义了它的含义。
#   在 LangChain 中，| 是"LCEL"（LangChain Expression Language）的管道操作符。
#   它的含义：把"左边"的输出，作为"右边"的输入。
#
#   类比：Unix/Linux 的管道
#   在 Linux 中：cat file.txt | grep "error" | sort
#   - cat 的输出 → 作为 grep 的输入
#   - grep 的输出 → 作为 sort 的输入
#
#   在 LangChain 中：
#       chain = prompt | llm | output_parser
#   - prompt 的输出（格式化后的消息）→ 作为 llm 的输入
#   - llm 的输出（AI 回复）→ 作为 output_parser 的输入
#
#   技术原理：LangChain 的组件类实现了 __or__ 和 __ror__ 魔术方法，
#   当你写 a | b 时，Python 实际上调用 a.__or__(b)，返回一个 RunnableSequence
#   （可执行序列），把 a 和 b 链接在一起。
#
# 【5. bind_tools() 方法（工具绑定）】
#   bind_tools 是 LangChain 中 LLM 对象的方法。
#   语法：llm.bind_tools(tools)
#   作用：把"工具信息"附加到 LLM 上，返回一个新的 LLM 对象。
#
#   什么是"绑定工具"？
#   当你调用 llm.bind_tools(tools) 时，LangChain 会：
#   (a) 读取每个工具的元数据：
#       - name: 工具名称（如 "get_stock_data"）
#       - description: 工具描述（来自函数的 docstring）
#       - args_schema: 参数模式（来自函数签名和 Annotated 注解）
#   (b) 把这些信息转换为 LLM 能理解的格式（如 OpenAI 的 Function Calling schema）
#   (c) 在之后每次调用这个 LLM 时，工具信息会随请求一起发送给 LLM
#   (d) LLM 就能"看到"有哪些工具可用，并在需要时决定调用某个工具
#
#   运行时行为：
#   - LLM 收到用户消息后，如果判断需要调用工具来获取数据，
#     它不会直接回复文本，而是返回一个"工具调用请求"（tool_call）
#   - tool_call 包含：工具名称、参数值
#   - LangGraph 的 ToolNode 会执行这个工具调用，把结果返回给 LLM
#   - LLM 拿到工具结果后，再生成最终回复
#
# 【6. invoke() 方法（执行操作）】
#   invoke 是 LangChain 所有"可执行对象"（Runnable）的统一方法。
#   语法：result = obj.invoke(input)
#   含义：把 input 作为输入，执行这个对象，返回输出结果。
#
#   不同对象的 invoke 行为不同：
#   - prompt.invoke({"messages": [...]}): 把变量填入模板，返回格式化后的消息
#   - llm.invoke(messages): 把消息发给 LLM API，返回 AI 的回复
#   - chain.invoke(input): 按顺序执行链中的每一步，返回最终结果
#
#   为什么用统一的 invoke 而不是各自的方法名？
#   因为 LCEL 的设计理念是"所有组件都是 Runnable"，
#   都支持 invoke、batch、stream 等统一接口，
#   这样就可以用 | 管道把任何组件连接在一起。
#
# 【7. 工厂函数模式（Factory Function Pattern）】
#   工厂函数是一种编程模式：一个函数的返回值是"另一个函数"。
#
#   在本文件中：
#       def create_market_analyst(llm):        # 这是工厂函数
#           def market_analyst_node(state):     # 这是被生产的函数
#               ...使用 llm...
#               return {...}
#           return market_analyst_node           # 返回内部函数
#
#   调用方式：
#       analyst = create_market_analyst(llm)     # 创建分析师
#       result = analyst(state)                  # 使用分析师
#
#   为什么要用工厂函数？
#   因为 LangGraph 的节点函数签名要求是 func(state) -> dict，
#   但我们的节点函数还需要访问 llm 参数。
#   工厂函数让我们在"创建时"传入 llm，在"使用时"只传 state。
#   这样既满足了 LangGraph 的接口要求，又能灵活配置不同的 LLM。
#
# 【8. 闭包（Closure）】
#   闭包是指：内部函数可以引用外部函数的变量。
#   在本文件中，market_analyst_node 是 create_market_analyst 的内部函数。
#   它可以引用外部函数的 llm 参数。
#   即使 create_market_analyst 执行完毕后，llm 变量仍然存活在闭包中，
#   market_analyst_node 可以继续使用它。
#
#   类比：工厂生产一台机器时，把某个零件（llm）安装进去。
#   之后机器被搬到别处使用时，零件仍然在机器内部。
#
# 【9. tool_calls（工具调用请求）】
#   当 LLM 被绑定了工具后，它的回复可能包含两种内容：
#   - content: 文本回复（当 LLM 认为不需要调用工具时）
#   - tool_calls: 工具调用请求列表（当 LLM 认为需要调用工具获取数据时）
#
#   tool_calls 是一个列表，每个元素包含：
#   - name: 要调用的工具名称（如 "get_stock_data"）
#   - args: 工具参数（如 {"symbol": "AAPL", "start_date": "2024-01-01", ...}）
#   - id: 唯一标识符，用于关联工具调用和返回结果
#
#   在本文件中，我们检查 result.tool_calls 是否为空：
#   - 如果为空（len == 0）：LLM 没有请求工具调用，说明它已经完成分析，
#     result.content 就是最终的市场分析报告。
#   - 如果不为空：LLM 请求了工具调用，说明它还需要获取数据。
#     此时 result.content 可能为空，报告需要等到工具调用完成后再生成。
#
# 【10. 本文件在系统中的位置】
#   本文件实现"市场分析师"智能体，它是 TradingAgents 系统中四位研究员之一。
#   市场分析师负责：
#   - 从 12 个技术指标中选择最多 8 个最相关的指标
#   - 调用 get_stock_data 获取股价历史数据
#   - 调用 get_indicators 计算选定的技术指标
#   - 生成详细的市场趋势分析报告
#   报告会被存入 AgentState 的 market_report 字段，
#   供后续的投资辩论和交易决策使用。
# =============================================================================


# ============================================================================
# 模块：市场分析师 (Market Analyst)
# 角色：负责分析技术指标和市场数据，选择最相关的技术指标并生成市场分析报告
# 核心功能：调用 get_stock_data 获取股票历史数据，调用 get_indicators 计算技术指标
#           （如 SMA、EMA、MACD、RSI、布林带、ATR、VWMA 等），
#           从预定义的指标列表中选择最多 8 个互补指标进行分析
# 技术栈：LangChain 的 ChatPromptTemplate、LLM 工具绑定（bind_tools）、
#         LangGraph 节点函数模式
# ============================================================================


# ---------------------------------------------------------------------------
# 导入区：每个 import 语句的作用和原因
# ---------------------------------------------------------------------------

# 【ChatPromptTemplate】
#   为什么需要：用于构建结构化的聊天提示词模板。
#   解决什么问题：让 LLM 知道"你是谁、你应该做什么、你能用什么工具"。
#   与 f-string 相比的优势：
#   (a) 支持消息类型（system / human / ai），不同角色消息对 LLM 行为影响不同
#   (b) 支持 partial() 分步填表，先填已知变量，稍后填运行时变量
#   (c) 支持 MessagesPlaceholder，可以在运行时插入完整的历史消息列表
#
# 【MessagesPlaceholder】
#   为什么需要：在提示词模板中预留一个"历史消息插入位置"。
#   解决什么问题：LLM 需要看到之前的对话历史才能理解上下文，
#   MessagesPlaceholder 把整个消息列表"按原样"插入模板，保留每条消息的角色信息。
# 【json 模块】
#   为什么需要：可能用于解析或生成 JSON 格式的数据。
#   解决什么问题：LLM 返回的工具调用参数通常是 JSON 格式，
#   需要解析后才能传给工具函数。

# 【time 模块】
#   为什么需要：可能用于时间戳记录或性能测量。
#   解决什么问题：在调试或日志中记录操作耗时。

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# --- 导入项目内部工具函数 ---
# 【build_instrument_context】
#   为什么需要：构建目标公司的上下文信息字符串。
#   解决什么问题：某些交易所的股票代码包含后缀（如 .TO、.L），
#   需要确保 LLM 在调用工具时使用正确的、完整的股票代码。
#
# 【get_indicators】
#   为什么需要：根据指标名称列表计算技术指标（如 "close_50_sma"、"rsi"）。
#   解决什么问题：LLM 不需要自己计算 SMA、MACD 等复杂公式，
#   只需调用这个工具就能获取计算好的指标数据。
#   这是一个被 @tool 装饰器标记的 LangChain 工具函数。
#
# 【get_stock_data】
#   为什么需要：获取股票的历史价格数据（OHLCV 格式，通常返回 CSV 字符串）。
#   解决什么问题：技术指标的计算需要原始价格数据作为输入，
#   这个工具从数据供应商获取历史股价。
#   这是一个被 @tool 装饰器标记的 LangChain 工具函数。
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_capital_flow,
    get_dragon_tiger,
    get_event_calendar,
    get_indicators,
    get_lockup_expiry,
    get_stock_data,
    get_valuation_percentile,
)

# 【get_config】
#   为什么需要：获取全局配置（如数据供应商选择、API 密钥等）。
#   解决什么问题：系统支持多个数据供应商（如 Yahoo Finance、Alpha Vantage），
#   通过配置来切换供应商，而不需要修改代码。


# =============================================================================
# create_market_analyst(llm) —— 工厂函数，创建市场分析师节点
# =============================================================================
# 【什么是工厂函数模式】
#   工厂函数是指"一个函数返回另一个函数"的设计模式。
#   create_market_analyst(llm) 接收一个 LLM 实例，
#   返回一个 market_analyst_node(state) 函数。
#   返回的函数"闭包"了 llm 参数——即使 create_market_analyst 执行完毕，
#   market_analyst_node 仍然可以访问 llm 变量。
#
# 【为什么用工厂函数】
#   LangGraph 要求节点函数的签名是 func(state) -> dict。
#   但我们的节点函数需要访问 llm 实例。
#   如果不用工厂函数，就需要把 llm 放到 state 中传递，污染状态结构。
#   工厂函数让我们在"创建时"绑定 llm，在"使用时"只传 state，
#   既满足 LangGraph 接口要求，又能灵活配置不同的 LLM。
#
# 【调用时机】
#   在系统初始化阶段调用一次：analyst_node = create_market_analyst(llm)
#   然后将返回的 analyst_node 注册到 LangGraph 图中。
#   每当工作流执行到"市场分析"节点时，LangGraph 会调用 analyst_node(state)。
#
# 【输入】
#   llm: 大语言模型实例（如 ChatOpenAI 的实例），驱动 Agent 的推理和决策。
#   这个 llm 实例会被"闭包"到返回的函数中。
#
# 【输出】
#   返回值是一个函数 market_analyst_node(state)，
#   这个函数符合 LangGraph 节点函数的标准签名。
# =============================================================================
def create_market_analyst(llm):
    """
    创建市场分析师节点函数（工厂函数模式）。

    市场分析师的核心职责：
    1. 从预定义的 12 个技术指标中选择最多 8 个最相关的指标
    2. 先调用 get_stock_data 获取原始股价数据
    3. 再调用 get_indicators 计算选定的技术指标
    4. 生成详细的市场趋势分析报告，末尾附 Markdown 表格

    参数：
        llm: 大语言模型实例，驱动 Agent 的推理和决策

    返回：
        market_analyst_node 函数，可作为 LangGraph 节点使用
    """

    # =========================================================================
    # market_analyst_node(state) —— 实际的 LangGraph 节点函数
    # =========================================================================
    # 【什么是闭包】
    #   market_analyst_node 是 create_market_analyst 的内部函数。
    #   它引用了外部函数的 llm 变量——这就是闭包。
    #   即使 create_market_analyst 执行完毕后返回了，
    #   llm 变量仍然存活在闭包中，market_analyst_node 可以继续使用它。
    #
    #   类比：工厂生产一台机器时把电池（llm）装入机器内部。
    #   机器被搬到别处使用时，电池还在里面，不需要再从外部接电源。
    #
    # 【调用时机】
    #   当 LangGraph 工作流执行到"市场分析"节点时被调用。
    #   通常是研究阶段的第一个步骤。
    #
    # 【输入】
    #   state: AgentState 字典（在运行时就是一个普通的 Python dict），包含：
    #     - state["trade_date"]: 交易日期字符串，如 "2024-06-15"
    #     - state["company_of_interest"]: 目标公司代码，如 "AAPL"
    #     - state["messages"]: 对话消息列表（来自 MessagesState 继承）
    #
    # 【输出】
    #   返回一个字典，包含两个键：
    #     - "messages": [result] —— 将 LLM 的回复消息追加到状态的消息列表
    #       （因为 messages 字段使用 add_messages reducer，会自动追加而非覆盖）
    #     - "market_report": report —— 市场分析报告文本，存入状态供后续使用
    #
    # 【输出被谁使用】
    #   - "messages" 中的 result 会被 LangGraph 自动追加到 state["messages"]
    #   - "market_report" 会被存入 state["market_report"]，
    #     后续被投资辩论阶段的多头/空头研究员和交易员读取
    # =========================================================================
    def market_analyst_node(state):
        """
        市场分析师节点：LangGraph 工作流中的标准节点函数。

        参数：
            state: 状态字典，包含 trade_date、company_of_interest、messages 等
        """

        # =====================================================================
        # 第一步：从状态中提取基本参数
        # =====================================================================

        # 从 state 字典中取出 trade_date（交易日期）。
        # state["trade_date"] 是一个字符串，如 "2024-06-15"。
        # 这个日期用于告诉 LLM "我们分析的是哪一天的市场数据"。
        current_date = state["trade_date"]

        # 调用 build_instrument_context 构建公司上下文信息字符串。
        # state["company_of_interest"] 是目标公司代码，如 "AAPL"。
        # build_instrument_context 会返回类似这样的字符串：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker in
        #    every tool call, report, and recommendation, preserving any
        #    exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
        # 这段文字会被注入到系统提示词中，确保 LLM 使用正确的股票代码。
        instrument_context = build_instrument_context(state["company_of_interest"], state.get("analysis_horizon", "medium"))

        # ====================================================================
        # 第二步：定义工具列表
        #
        # tools 是一个列表，包含两个工具函数对象。
        # 这两个函数都被 @tool 装饰器标记为 LangChain 工具。
        #
        # 为什么只有两个工具？
        #   市场分析师只需要两个核心操作：
        #   1. get_stock_data：获取原始股价数据（必须先调用）
        #   2. get_indicators：计算技术指标（依赖原始数据）
        #
        # 调用顺序的隐含约束：
        #   get_indicators 需要 get_stock_data 的输出作为输入，
        #   但这个顺序不是代码中硬编码的 if-else 逻辑，
        #   而是通过系统提示词告诉 LLM "先调用 get_stock_data，再调用 get_indicators"，
        #   由 LLM 自主决策执行顺序。
        #
        # LangChain 的 bind_tools 机制如何工作：
        #   当 LLM 被绑定这些工具后，它在推理过程中可以"决定"调用某个工具。
        #   例如，LLM 可能会先生成一个 tool_call 请求 get_stock_data，
        #   拿到数据后再生成另一个 tool_call 请求 get_indicators。
        #   这个决策过程完全由 LLM 自主完成，不需要人工编写 if-else 逻辑。
        # ====================================================================
        tools = [
            get_stock_data,
            get_indicators,
            # A 股资金面（主力资金流向 / 龙虎榜）。非 A 股标的调用会返回
            # 无数据提示，由 LLM 自行忽略，不影响原有分析流程。
            get_capital_flow,
            get_dragon_tiger,
            # A 股择时辅助：
            #   - 估值历史分位：技术形态之外，判断当前价位的安全边际
            #   - 限售解禁 / 事件日历：识别未来可能带来抛压的时间窗口
            get_valuation_percentile,
            get_lockup_expiry,
            get_event_calendar,
        ]

        # ====================================================================
        # 第三步：构建系统提示词（System Prompt）
        #
        # 系统提示词是发送给 LLM 的"行为规范说明书"。
        # 它告诉 LLM：
        #   - 你的角色是什么（交易助手）
        #   - 你的任务是什么（选择并分析技术指标）
        #   - 你有哪些工具可用（12 个技术指标的详细说明）
        #   - 你应该遵循什么原则（避免冗余、先获取数据再计算指标）
        #
        # 以下是系统提示词的逐段翻译和解释：
        # ====================================================================

        # --- 系统提示词原文翻译和解释 ---
        #
        # 【第1段：角色定义和任务说明】
        # "You are a trading assistant tasked with analyzing financial markets.
        #  Your role is to select the most relevant indicators for a given market
        #  condition or trading strategy from the following list. The goal is to
        #  choose up to 8 indicators that provide complementary insights without
        #  redundancy."
        #
        # 翻译："你是一个交易助手，负责分析金融市场。你的角色是从以下列表中
        #        为给定的市场条件或交易策略选择最相关的指标。目标是选择最多 8 个
        #        提供互补见解且不冗余的指标。"
        # 设计意图：限定 LLM 最多选 8 个指标，避免信息过载和冗余分析。
        #
        # 【第2段：移动平均线指标】
        # "Moving Averages:"
        # 翻译："移动平均线类指标："
        #
        # "- close_50_sma: 50 SMA: A medium-term trend indicator."
        # 翻译：50日简单移动平均线，中期趋势指标。
        # "Usage: Identify trend direction and serve as dynamic support/resistance."
        # 用途：识别趋势方向，作为动态支撑/阻力位。
        # "Tips: It lags price; combine with faster indicators for timely signals."
        # 提示：它滞后于价格；应与更快的指标组合使用以获得及时信号。
        #
        # "- close_200_sma: 200 SMA: A long-term trend benchmark."
        # 翻译：200日简单移动平均线，长期趋势基准。
        # "Usage: Confirm overall market trend and identify golden/death cross setups."
        # 用途：确认整体市场趋势，识别金叉/死叉形态。
        # "Tips: It reacts slowly; best for strategic trend confirmation."
        # 提示：反应缓慢，最适合战略性趋势确认而非频繁交易入场。
        #
        # "- close_10_ema: 10 EMA: A responsive short-term average."
        # 翻译：10日指数移动平均线，响应迅速的短期均线。
        # "Usage: Capture quick shifts in momentum and potential entry points."
        # 用途：捕捉动量的快速变化和潜在入场点。
        # "Tips: Prone to noise in choppy markets; use alongside longer averages."
        # 提示：在震荡市场中容易受噪音影响；应与较长周期均线配合使用。
        #
        # 【第3段：MACD 相关指标】
        # "MACD Related:"
        # 翻译："MACD（指数平滑异同移动平均线）相关指标："
        #
        # "- macd: MACD: Computes momentum via differences of EMAs."
        # 翻译：MACD 线，通过 EMA 差值计算动量。
        # "Usage: Look for crossovers and divergence as signals of trend changes."
        # 用途：观察交叉和背离作为趋势变化的信号。
        #
        # "- macds: MACD Signal: An EMA smoothing of the MACD line."
        # 翻译：MACD 信号线，是 MACD 线的 EMA 平滑。
        # "Usage: Use crossovers with the MACD line to trigger trades."
        # 用途：与 MACD 线的交叉作为交易触发信号。
        #
        # "- macdh: MACD Histogram: Shows the gap between MACD and signal."
        # 翻译：MACD 柱状图，显示 MACD 线和信号线之间的差距。
        # "Usage: Visualize momentum strength and spot divergence early."
        # 用途：可视化动量强度，及早发现背离。
        #
        # 【第4段：动量指标】
        # "Momentum Indicators:"
        # 翻译："动量指标："
        #
        # "- rsi: RSI: Measures momentum to flag overbought/oversold conditions."
        # 翻译：RSI（相对强弱指数），衡量动量以标记超买/超卖状态。
        # "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals."
        # 用途：使用 70/30 阈值，观察背离以发出反转信号。
        # "Tips: In strong trends, RSI may remain extreme; cross-check with trend."
        # 提示：在强趋势中 RSI 可能持续极端值；需与趋势分析交叉验证。
        #
        # 【第5段：波动率指标】
        # "Volatility Indicators:"
        # 翻译："波动率指标："
        #
        # "- boll: Bollinger Middle: A 20 SMA serving as basis for Bollinger Bands."
        # 翻译：布林带中轨，20日 SMA，作为布林带的基础。
        # "- boll_ub: Bollinger Upper Band: 2 std dev above middle."
        # 翻译：布林带上轨，通常在中轨上方 2 个标准差。
        # "- boll_lb: Bollinger Lower Band: 2 std dev below middle."
        # 翻译：布林带下轨，通常在中轨下方 2 个标准差。
        # "- atr: ATR: Averages true range to measure volatility."
        # 翻译：ATR（平均真实波幅），衡量波动性。
        # "Usage: Set stop-loss levels and adjust position sizes."
        # 用途：设置止损水平和调整仓位大小。
        #
        # 【第6段：成交量指标】
        # "Volume-Based Indicators:"
        # 翻译："成交量类指标："
        #
        # "- vwma: VWMA: A moving average weighted by volume."
        # 翻译：VWMA（成交量加权移动平均线），按成交量加权的移动平均。
        #
        # 【第7段：选择原则和操作说明】
        # "Select indicators that provide diverse and complementary information."
        # 翻译：选择提供多样化和互补信息的指标。
        # "Avoid redundancy (e.g., do not select both rsi and stochrsi)."
        # 翻译：避免冗余（例如不要同时选择 RSI 和 StochRSI）。
        # "When you tool call, please use the exact name of the indicators."
        # 翻译：工具调用时请使用上述提供的确切指标名称。
        # "Please make sure to call get_stock_data first to retrieve the CSV."
        # 翻译：请确保先调用 get_stock_data 获取 CSV 数据。
        # "Then use get_indicators with the specific indicator names."
        # 翻译：然后使用 get_indicators 配合具体的指标名称。
        # "Write a very detailed and nuanced report of the trends you observe."
        # 翻译：撰写非常详细和细致的趋势分析报告。
        #
        # 【第8段：追加 Markdown 表格要求】
        # （以下是用 + 拼接的第二段字符串）
        # "Make sure to append a Markdown table at the end of the report
        #  to organize key points in the report, organized and easy to read."
        # 翻译：确保在报告末尾追加一个 Markdown 表格，
        #        将报告中的关键点整理成有组织、易读的格式。
        #
        # 设计意图：
        # 1. 逐个列出 12 个指标的名称、用途和技巧，让 LLM 充分理解每个指标
        # 2. 要求选择最多 8 个互补指标，避免信息过载和冗余
        # 3. 强调操作顺序：先获取原始数据，再计算指标
        # 4. 要求详细报告 + Markdown 表格，提高报告的可读性和结构化程度
        # ====================================================================
        system_message = (
            """你是一名交易助手，负责分析金融市场。你的角色是从下列指标列表中，为给定的市场状况或交易策略挑选**最相关的指标**。"
            "目标是选出最多 **8 个**能够提供互补视角且不冗余的指标。各分类及对应指标如下：

移动平均线（Moving Averages）：
- close_50_sma: 50 SMA（50日简单移动平均）：中期趋势指标。用途：判断趋势方向并作为动态支撑/阻力位。提示：该指标滞后于价格，建议结合更快的指标以获得及时信号。
- close_200_sma: 200 SMA（200日简单移动平均）：长期趋势基准。用途：确认整体市场趋势并识别金叉/死叉形态。提示：反应较慢，最适合用于战略层面的趋势确认，而非频繁交易入场。
- close_10_ema: 10 EMA（10日指数移动平均）：灵敏的短期均线。用途：捕捉动量的快速变化和潜在入场点。提示：在震荡市中易受噪声干扰，建议结合更长周期均线过滤虚假信号。

MACD 相关：
- macd: MACD（指数平滑异同移动平均）：通过 EMA 差值计算动量。用途：寻找交叉和背离作为趋势变化的信号。提示：在低波动或横盘市场中需结合其他指标确认。
- macds: MACD Signal（MACD 信号线）：MACD 线的 EMA 平滑。用途：与 MACD 线交叉以触发交易。提示：应作为更大策略的一部分，避免误报。
- macdh: MACD Histogram（MACD 柱状图）：显示 MACD 线与其信号线之间的差值。用途：可视化动量强度并及早发现背离。提示：可能波动剧烈，在快速变动的市场中需配合额外过滤。

动量指标：
- rsi: RSI（相对强弱指标）：衡量动量以标记超买/超卖状态。用途：应用 70/30 阈值并关注背离以判断反转。提示：在强趋势中 RSI 可能持续极端，务必结合趋势分析交叉验证。

波动率指标：
- boll: Bollinger Middle（布林带中轨）：20 SMA，作为布林带的基础。用途：作为价格运动的动态基准。提示：结合上轨和下轨可有效识别突破或反转。
- boll_ub: Bollinger Upper Band（布林带上轨）：通常位于中轨上方 2 个标准差。用途：提示潜在超买状态和突破区域。提示：需结合其他工具确认信号，强势趋势中价格可能沿上轨运行。
- boll_lb: Bollinger Lower Band（布林带下轨）：通常位于中轨下方 2 个标准差。用途：指示潜在超卖状态。提示：需额外分析以避免虚假反转信号。
- atr: ATR（平均真实波幅）：通过平均真实波幅衡量波动率。用途：根据当前市场波动率设置止损位并调整仓位大小。提示：它是滞后性指标，应作为更广泛风险管理策略的一部分。

成交量指标：
- vwma: VWMA（成交量加权移动平均）：以成交量加权的移动平均。用途：通过整合价格行为与成交量数据来确认趋势。提示：注意成交量异常激增导致的偏差结果，建议结合其他成交量分析。

- 请选择能提供多样化且互补信息的指标，避免冗余（例如不要同时选择 rsi 和 stochrsi）。同时简要说明它们为何适合当前的市场环境。调用工具时，请使用上面提供的指标确切名称，因为它们是已定义的参数，否则调用会失败。请务必先调用 get_stock_data 获取生成指标所需的 CSV 数据，然后使用 get_indicators 并传入具体的指标名称。撰写一份极为详尽、细致的趋势观察报告，提供有具体支撑证据、可操作的洞察，帮助交易员做出明智的决策。"""
            + """ 对于 A 股标的，如需要判断资金面强弱，请调用 get_capital_flow 获取主力资金流向；若个股近期出现异动，可调用 get_dragon_tiger 查看龙虎榜资金博弈。本次策略扫描结果已随上下文提供，请结合其中的策略信号（多头趋势、均线金叉、放量突破、缩量回踩、箱体、缠论/波浪等）作为技术面证据。
另请结合以下 A 股择时工具：`get_valuation_percentile` 判断当前价位在历史估值中的分位（技术形态再好看，估值处于 90% 分位也要提示回归风险）；`get_lockup_expiry` 与 `get_event_calendar` 查看未来 30 天是否存在解禁、财报披露、除权除息等可能引发波动的事件。若临近大规模解禁，必须在报告中提示抛压风险。
请务必在报告末尾附带一个 Markdown 表格，将报告中的关键点整理归纳，便于阅读。"""
        )
        # [英文备份] system_message (en):
        # system_message = (
        #     """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:
        # ...
        # Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
        #     + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
        # )

        # ====================================================================
        # 第四步：构建 ChatPromptTemplate
        #
        # ChatPromptTemplate.from_messages([...]) 接收一个消息列表，
        # 创建一个聊天提示词模板。
        #
        # 模板结构（两条消息）：
        #
        # 【消息1：system 角色】
        #   ("system", "You are a helpful AI assistant, collaborating...")
        #
        #   这是"系统消息"——告诉 LLM 它的角色和行为规范。
        #   内容包含：
        #   - 协作提示：你是一个与其他助手协作的 AI 助手
        #   - 工具使用提示：使用提供的工具来推进回答
        #   - 协作规则：如果你无法完全回答，没关系，其他助手会接手
        #   - 完成标记：如果有最终交易建议（BUY/HOLD/SELL），请加上前缀
        #   - 工具列表：你有以下工具可用 {tool_names}
        #   - 系统提示词：{system_message}（上面定义的技术指标说明）
        #   - 日期信息：当前日期是 {current_date}
        #   - 公司信息：{instrument_context}
        #
        #   其中的 {tool_names}、{system_message}、{current_date}、{instrument_context}
        #   是"模板变量"（用花括号 {} 包围），稍后通过 partial() 填充。
        #
        # 【消息2：MessagesPlaceholder（消息占位符）】
        #   MessagesPlaceholder(variable_name="messages")
        #
        #   这是一个"占位符"——在运行时，用 state["messages"] 中的
        #   完整历史消息列表来填充这个位置。
        #   例如，如果 state["messages"] 是：
        #     [HumanMessage(content="分析AAPL"), AIMessage(content="好的，我来获取数据")]
        #   那么这些消息会被原样插入到模板的这个位置。
        #
        # 为什么用 ChatPromptTemplate 而不是 f-string？
        # 1. f-string 无法区分消息角色（system vs human vs ai）
        # 2. f-string 无法插入"消息对象列表"（MessagesPlaceholder）
        # 3. ChatPromptTemplate 支持 partial() 分步填表
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
                    "供你参考，当前日期为 {current_date}。{instrument_context}"
                    "\n\n本次策略扫描结果：\n{strategy_report}",
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
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # ====================================================================
        # 第五步：使用 partial() 分步填充模板变量
        #
        # partial(变量名=值) 的作用：把模板中的某个变量"提前填好"，
        # 返回一个新的模板（原模板不变），剩下的变量稍后在 invoke 时填。
        #
        # 类比"分步填表"：
        #   模板有 4 个空位：system_message, tool_names, current_date, instrument_context
        #   这 4 个空位在创建模板时就已经知道值了，所以现在就填。
        #   模板还有一个空位：messages（MessagesPlaceholder）
        #   这个空位要到运行时才知道值（来自 state["messages"]），所以不填。
        #
        # 为什么要在创建时就填？
        # 因为 system_message、tool_names、current_date、instrument_context
        # 这些值在节点函数执行时就确定了，不需要等到 invoke 时再传。
        # 提前填好可以简化 invoke 的调用代码。
        # ====================================================================

        # 填充 system_message 变量：填入上面定义的技术指标说明文本
        prompt = prompt.partial(system_message=system_message)

        # 填充 tool_names 变量：将工具名称列表用逗号连接成字符串
        #
        # 这行代码包含两个 Python 操作：
        # 1. 列表推导式 [tool.name for tool in tools]
        #    - 遍历 tools 列表中的每个 tool 对象
        #    - 取每个 tool 的 .name 属性（工具名称字符串）
        #    - 生成一个新的列表，如 ["get_stock_data", "get_indicators"]
        #    列表推导式是 Python 的简写语法，等价于：
        #      names = []
        #      for tool in tools:
        #          names.append(tool.name)
        #
        # 2. ", ".join(names)
        #    - 将列表中的字符串用 ", " 连接成一个字符串
        #    - 例如：["get_stock_data", "get_indicators"] → "get_stock_data, get_indicators"
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))

        # 填充 current_date 变量：填入从 state 中提取的交易日期
        prompt = prompt.partial(current_date=current_date)

        # 填充 instrument_context 变量：填入公司上下文信息
        prompt = prompt.partial(instrument_context=instrument_context)
        # 填入策略扫描结果（由 Strategy Scanner 节点提前生成）
        prompt = prompt.partial(
            strategy_report=state.get("strategy_report", "")
        )

        # ====================================================================
        # 第六步：构建并执行 LangChain 链（LCEL 表达式）
        #
        # chain = prompt | llm.bind_tools(tools)
        #
        # 这行代码使用 LangChain 的 LCEL（LangChain Expression Language）
        # 管道操作符 | 将两个组件连接在一起。
        #
        # 【| 管道操作符的含义】
        #   | 是 LangChain 的 LCEL 语法，表示"将左边的输出作为右边的输入"。
        #   类似于 Unix/Linux 的管道：
        #     cat file.txt | grep "error"     # cat 输出 → grep 输入
        #   在 LangChain 中：
        #     prompt | llm.bind_tools(tools)  # prompt 输出 → llm 输入
        #
        # 【执行流程】
        #   1. prompt（ChatPromptTemplate）：
        #      接收输入（state["messages"]），将模板变量填充后，
        #      输出格式化的消息列表。
        #
        #   2. | 管道：将 prompt 的输出（消息列表）传给下一步。
        #
        #   3. llm.bind_tools(tools)：
        #      bind_tools 返回一个新的 LLM 对象，该对象"知道"有哪些工具可用。
        #      当消息列表传给它时，它会：
        #      (a) 把消息和工具信息一起发送给 LLM API（如 OpenAI API）
        #      (b) LLM 根据消息内容和工具信息，决定是回复文本还是请求调用工具
        #      (c) 返回一个 AIMessage 对象，包含 content（文本回复）和/或 tool_calls（工具调用请求）
        #
        # 【bind_tools 的工作原理】
        #   bind_tools 会读取每个工具的元数据：
        #   - name: 工具名称（如 "get_stock_data"）
        #   - description: 工具描述（来自函数的 docstring）
        #   - args_schema: 参数模式（来自函数签名和 Annotated 注解）
        #   然后把这些信息转换为 LLM API 能理解的格式（如 OpenAI Function Calling schema）。
        #   之后每次调用这个 LLM 时，工具信息会随请求一起发送。
        #   LLM 就能"看到"有哪些工具可用，并在需要时决定调用某个工具。
        #
        # 【运行时数据流示例】
        #   假设 state["messages"] = [HumanMessage(content="分析AAPL的技术指标")]
        #
        #   步骤1: prompt 接收 [HumanMessage(...)]，
        #          填充模板变量后输出：
        #          [
        #            SystemMessage(content="You are a helpful AI assistant...
        #                          You have access to the following tools: get_stock_data,
        #                          get_indicators. [系统提示词...]
        #                          the current date is 2024-06-15.
        #                          The instrument to analyze is `AAPL`..."),
        #            HumanMessage(content="分析AAPL的技术指标")
        #          ]
        #
        #   步骤2: 这些消息被传给 llm.bind_tools(tools)，
        #          LLM API 收到消息和工具信息后返回：
        #          AIMessage(
        #            content="",
        #            tool_calls=[{
        #              "name": "get_stock_data",
        #              "args": {"symbol": "AAPL", "start_date": "2024-01-01",
        #                       "end_date": "2024-06-15"},
        #              "id": "call_abc123"
        #            }]
        #          )
        #          （LLM 决定先调用 get_stock_data 获取数据）
        # ====================================================================
        chain = prompt | llm.bind_tools(tools)

        # ====================================================================
        # 第七步：执行链
        #
        # chain.invoke(state["messages"])
        #
        # invoke() 是 LangChain 所有 Runnable 对象的统一"执行"方法。
        # 参数是输入数据，返回值是输出结果。
        #
        # 这里传入 state["messages"]（对话历史消息列表），
        # 链会按顺序执行：
        #   1. prompt 接收消息列表，填充模板，输出格式化消息
        #   2. llm.bind_tools(tools) 接收格式化消息，调用 LLM API，返回 AI 回复
        #
        # result 是一个 AIMessage 对象，可能包含：
        #   - result.content: 文本内容（如果 LLM 直接回复了文字）
        #   - result.tool_calls: 工具调用请求列表（如果 LLM 决定调用工具）
        #
        # 注意：这里只调用一次 invoke。
        # 如果 LLM 请求了工具调用，实际的工具执行由 LangGraph 的 ToolNode 负责，
        # 不在这个函数内完成。这个函数只负责"启动"分析过程。
        # ====================================================================
        result = chain.invoke(state["messages"])

        # 初始化报告字符串为空
        # 这个变量将存储最终的市场分析报告
        report = ""

        # ====================================================================
        # 第八步：检查 LLM 是否已完成分析
        #
        # result.tool_calls 是 LLM 返回的工具调用请求列表。
        #
        # 如果 len(result.tool_calls) == 0：
        #   LLM 没有请求任何工具调用，说明它已经完成了分析，
        #   result.content 就是最终的市场分析报告文本。
        #   此时把报告内容提取出来。
        #
        # 如果 len(result.tool_calls) > 0：
        #   LLM 请求了工具调用（如 get_stock_data），说明它还需要获取数据。
        #   此时 result.content 可能为空，报告需要等到工具执行完毕、
        #   LLM 再次被调用后才能生成。
        #   在这种情况下，report 保持为空字符串。
        #
        # 注意：这个设计意味着，如果 LLM 在本次调用中请求了工具，
        # market_report 会暂时为空。LangGraph 会在 ToolNode 执行完工具后，
        # 再次调用这个节点（或相关的处理逻辑），直到 LLM 不再请求工具为止。
        # ====================================================================
        if len(result.tool_calls) == 0:
            report = result.content

        # ====================================================================
        # 第九步：返回状态更新
        #
        # 返回一个字典，LangGraph 会用这个字典来更新全局状态。
        #
        # "messages": [result]
        #   将 LLM 的回复（AIMessage 对象）放入 messages 列表。
        #   由于 AgentState 的 messages 字段使用 add_messages reducer，
        #   这个新消息会"追加"到现有的消息列表末尾，而非覆盖。
        #   这样整个对话历史就被完整保留下来。
        #
        # "market_report": report
        #   将市场分析报告文本存入 market_report 字段。
        #   如果 LLM 还在调用工具阶段，report 为空字符串；
        #   如果 LLM 已完成分析，report 包含完整的市场分析报告。
        #
        # 这个返回值会被 LangGraph 自动合并到全局状态中：
        #   state["messages"] += [result]          # 追加消息
        #   state["market_report"] = report        # 更新报告
        # ====================================================================
        return {
            "messages": [result],
            "market_report": report,
        }

    # 工厂函数返回内部函数 market_analyst_node
    # 此时 market_analyst_node 闭包了 llm 变量，
    # 可以在后续被 LangGraph 作为节点函数调用。
    return market_analyst_node
