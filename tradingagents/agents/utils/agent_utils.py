# =============================================================================
# 前置知识：阅读本文件前需要理解的核心概念
# =============================================================================
#
# 【1. LangChain 的消息类型体系】
#   LangChain 使用"消息对象"来表示对话中的每一条消息。
#   每条消息都有一个"角色"（role），表示这条消息是谁说的：
#
#   - HumanMessage（人类消息）：
#       角色：用户 / 人类
#       用途：表示用户发送给 AI 的消息
#       示例：HumanMessage(content="请分析AAPL的技术指标")
#
#   - AIMessage（AI 消息）：
#       角色：助手 / AI
#       用途：表示大语言模型（LLM）的回复
#       示例：AIMessage(content="好的，我来获取AAPL的股价数据...")
#       特殊：AIMessage 可能包含 tool_calls（工具调用请求）
#
#   - SystemMessage（系统消息）：
#       角色：系统
#       用途：定义 AI 的行为规范（如"你是一个交易助手"）
#       示例：SystemMessage(content="你是一个交易助手，负责分析金融市场。")
#       特点：通常放在对话的最前面，AI 会遵循其中的指令
#
#   - ToolMessage（工具消息）：
#       角色：工具
#       用途：表示工具函数执行后返回的结果
#       示例：ToolMessage(content="Date,Open,High,Low,Close,Volume\n2024-01-01,150,...")
#       特点：与 AIMessage 的 tool_calls 配对使用，包含工具调用的结果
#
#   消息流转示例：
#     1. 用户发消息：HumanMessage("分析AAPL")
#     2. AI 决定调用工具：AIMessage(content="", tool_calls=[get_stock_data(...)])
#     3. 工具执行返回：ToolMessage(content="Date,Open,High,Low,Close...")
#     4. AI 基于工具结果回复：AIMessage(content="基于技术分析，AAPL显示...")
#
# 【2. RemoveMessage（消息删除操作）】
#   来自 langchain_core.messages 模块。
#   RemoveMessage 是 LangGraph 的特殊"操作类型"。
#
#   它不是一个真正的"消息"，而是一个"删除指令"。
#   当 LangGraph 的节点返回包含 RemoveMessage 的 messages 列表时，
#   LangGraph 的 add_messages reducer 会识别这些 RemoveMessage，
#   并从消息列表中删除对应的旧消息。
#
#   工作原理：
#   - 每条消息都有一个唯一的 id 属性（如 "msg_001"）
#   - RemoveMessage(id="msg_001") 告诉 LangGraph："删除 id 为 msg_001 的消息"
#   - LangGraph 的 add_messages reducer 看到 RemoveMessage 后，
#     会在消息列表中查找匹配 id 的消息并删除它
#
#   为什么需要删除消息？
#   - 随着对话越来越长，消息列表会越来越长，消耗大量 token
#   - 有些中间过程的消息（如工具调用和结果）在后续阶段不再需要
#   - 通过 RemoveMessage 可以清理不再需要的消息，控制上下文长度
#
# 【3. 闭包（Closure）和工厂函数（Factory Function）】
#
#   【闭包是什么】
#   在 Python 中，函数内部可以定义另一个函数。
#   内部函数可以引用外部函数的变量——这就是"闭包"。
#   即使外部函数执行完毕并返回后，内部函数仍然可以访问那些变量。
#
#   示例：
#       def outer(x):          # 外部函数
#           def inner(y):      # 内部函数（闭包）
#               return x + y   # 引用了外部函数的 x 变量
#           return inner        # 返回内部函数
#
#       add_5 = outer(5)       # 创建一个"加5"的函数
#       result = add_5(3)      # 结果是 8（5 + 3）
#
#   在这个例子中，即使 outer(5) 执行完毕后返回了，
#   inner 函数仍然记得 x 的值是 5。
#   这就是闭包的"记忆"能力。
#
#   【工厂函数是什么】
#   工厂函数是一种设计模式：一个函数的返回值是"另一个函数"。
#   就像工厂生产产品一样——你给工厂原材料（参数），工厂返回一个产品（函数）。
#
#   在本文件中：
#       def create_msg_delete():           # 工厂函数
#           def delete_messages(state):    # 被生产的函数
#               ...使用 state...
#               return {...}
#           return delete_messages          # 返回产品
#
#   调用方式：
#       delete_func = create_msg_delete()  # 工厂生产产品
#       result = delete_func(state)        # 使用产品
#
#   为什么要用工厂函数？
#   因为 LangGraph 要求节点函数的签名是 func(state) -> dict。
#   工厂函数让我们可以在"创建时"做一些初始化工作，
#   然后返回一个符合 LangGraph 接口要求的函数。
#
# 【4. 列表推导式（List Comprehension）】
#   Python 的简写语法，用一行代码创建列表。
#
#   传统写法：
#       squares = []
#       for i in range(5):
#           squares.append(i * i)
#       # 结果：[0, 1, 4, 9, 16]
#
#   列表推导式写法：
#       squares = [i * i for i in range(5)]
#       # 结果：[0, 1, 4, 9, 16]
#
#   语法：[表达式 for 变量 in 可迭代对象]
#   含义：对可迭代对象中的每个元素，计算表达式的值，收集成列表。
#
#   在本文件中：
#       removal_operations = [RemoveMessage(id=m.id) for m in messages]
#   含义：对 messages 列表中的每条消息 m，创建一个 RemoveMessage(id=m.id)，
#   收集成一个新的列表。等价于：
#       removal_operations = []
#       for m in messages:
#           removal_operations.append(RemoveMessage(id=m.id))
#
# 【5. 列表拼接（+ 运算符）】
#   Python 中，两个列表可以用 + 连接成一个新列表：
#       [1, 2, 3] + [4, 5]    # 结果：[1, 2, 3, 4, 5]
#
#   在本文件中：
#       removal_operations + [placeholder]
#   含义：将删除操作列表和占位消息列表连接成一个列表。
#
# 【6. f-string（格式化字符串）】
#   Python 3.6+ 引入的字符串格式化语法。
#   在字符串前加 f 前缀，用 {} 包围变量名，变量会被替换为它的值。
#
#   示例：
#       name = "AAPL"
#       text = f"The instrument to analyze is `{name}`."
#       # 结果："The instrument to analyze is `AAPL`."
#
#   在本文件中，build_instrument_context 函数使用 f-string 来
#   将股票代码嵌入到描述文本中。
#
# 【7. 本文件在系统中的位置】
#   本文件提供两个核心功能：
#   1. build_instrument_context(ticker)：构建公司上下文说明文本
#      - 被所有分析师节点调用，用于在系统提示词中注入正确的股票代码
#   2. create_msg_delete()：创建消息删除函数
#      - 被 LangGraph 调用，用于在不同分析阶段之间清理旧消息
#        防止上下文过长导致 LLM 超出 token 限制
# =============================================================================


# =============================================================================
# 模块名称：agent_utils.py
# 模块功能：智能体工具函数模块，提供辅助函数供智能体工作流使用
#
# 本文件包含两个函数：
# 1. build_instrument_context(ticker) —— 构建公司上下文说明文本
# 2. create_msg_delete() —— 工厂函数，创建消息删除函数
# =============================================================================


# ---------------------------------------------------------------------------
# 导入区：每个 import 语句的作用和原因
# ---------------------------------------------------------------------------

# 【HumanMessage】
#   为什么需要：表示"人类发送的消息"。
#   解决什么问题：在 LangChain 中，对话消息不是普通字符串，
#   而是带有"角色"信息的消息对象。HumanMessage 表示用户角色的消息。
#   在本文件中，它被用作"占位消息"——删除所有旧消息后，
#   添加一条 HumanMessage("Continue") 以满足某些 LLM 的最低消息要求。
#
# 【RemoveMessage】
#   为什么需要：表示"删除消息的操作"。
#   解决什么问题：随着对话越来越长，消息列表会消耗大量 token。
#   RemoveMessage 通过消息 id 删除指定的旧消息，控制上下文长度。
#   它是 LangGraph 的特殊操作类型——不是真正的消息，
#   而是一个"删除指令"，LangGraph 的 reducer 会识别并执行它。
from langchain_core.messages import HumanMessage, RemoveMessage

# --- 导入项目内部工具函数 ---
# 以下导入的函数都被 @tool 装饰器标记为 LangChain 工具。
# 这些工具可以从其他模块导入后，直接被 LLM 通过 bind_tools 调用。
# 本文件导入它们是为了"再导出"（re-export）——
# 让其他文件可以从 agent_utils 统一导入所有工具。

# 【get_stock_data】
#   为什么需要：获取股票价格数据（OHLCV 格式）。
#   解决什么问题：市场分析师需要历史股价数据来计算技术指标。
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)

# 【get_indicators】
#   为什么需要：获取技术指标数据（如 RSI、MACD 等）。
#   解决什么问题：市场分析师需要计算好的技术指标来分析市场趋势。
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)

# 【基本面数据工具函数集合】
# get_fundamentals: 获取综合基本面数据（如 P/E、ROE 等估值指标）
# get_balance_sheet: 获取资产负债表（公司的资产、负债和股东权益）
# get_cashflow: 获取现金流量表（公司的现金流入和流出）
# get_income_statement: 获取利润表（公司的收入、成本和利润）
#   为什么需要：基本面研究员需要财务数据来评估公司的财务健康状况。
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)

# 【新闻数据工具函数集合】
# get_news: 获取公司相关新闻（如财报发布、产品动态等）
# get_insider_transactions: 获取内部人交易数据（高管买卖股票的记录）
# get_global_news: 获取全球宏观新闻（如经济政策、地缘政治事件等）
#   为什么需要：新闻研究员需要新闻和事件信息来分析市场影响因素。
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)


# =============================================================================
# build_instrument_context() - 构建交易工具上下文说明
# =============================================================================
# 【调用时机】
#   在每个分析师节点函数执行时调用。
#   通常在构建系统提示词之前调用，将返回值注入到提示词模板中。
#
# 【输入】
#   ticker (str): 股票代码字符串，可能包含交易所后缀。
#   例如："AAPL"（苹果）、"SHOP.TO"（Shopify多伦多）、"HSBA.L"（汇丰伦敦）
#
# 【输出】
#   str: 一段描述文本，告诉 LLM 应该使用哪个精确的股票代码。
#   例如：
#   "The instrument to analyze is `AAPL`. Use this exact ticker in every
#    tool call, report, and recommendation, preserving any exchange suffix
#    (e.g. `.TO`, `.L`, `.HK`, `.T`)."
#
# 【输出被谁使用】
#   返回值被注入到分析师节点的 ChatPromptTemplate 中，
#   作为 instrument_context 模板变量的值。
#   LLM 会看到这段文字，从而在调用工具时使用正确的股票代码。
#
# 【运行时数据流示例】
#   输入：ticker = "SHOP.TO"
#   输出："The instrument to analyze is `SHOP.TO`. Use this exact ticker
#          in every tool call, report, and recommendation, preserving any
#          exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
#   LLM 看到这段文字后，在调用 get_stock_data 时会传入 symbol="SHOP.TO"，
#   而不是错误的 "SHOP"（丢失了交易所后缀会导致数据查询失败）。
# =============================================================================
def build_instrument_context(ticker: str) -> str:
    """
    构建交易标的上下文说明文本

    这个函数生成一段描述文本，告诉智能体应该使用哪个精确的股票代码。
    某些交易所的股票代码包含后缀（如 .TO 表示多伦多交易所、.L 表示伦敦交易所），
    此函数确保智能体保留这些后缀，避免因代码不完整导致数据查询失败。

    参数：
        ticker (str): 股票代码，可能包含交易所后缀，如 "AAPL"、"SHOP.TO"、"HSBA.L"

    返回：
        str: 一段描述文本，包含精确的股票代码和调用指令

    使用场景：
        在每次工具调用前，将此文本注入到智能体的提示词中，
        确保所有工具调用使用正确的股票代码格式。
    """
    # 使用 f-string（格式化字符串）将 ticker 变量的值嵌入到文本中。
    # f"..." 中的 {ticker} 会被替换为 ticker 参数的实际值。
    # 例如，如果 ticker = "AAPL"，则 {ticker} 被替换为 "AAPL"。
    #
    # 返回的文本包含三部分（三行字符串拼接成一个长字符串）：
    # 1. "The instrument to analyze is `{ticker}`."
    #    告诉 LLM 要分析的股票代码是什么。
    #    注意：{ticker} 两边的反引号 ` 是 Markdown 语法，表示"代码"格式。
    #
    # 2. "Use this exact ticker in every tool call, report, and recommendation, "
    #    告诉 LLM 在所有地方（工具调用、报告、建议）都使用这个精确的代码。
    #
    # 3. "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    #    特别强调要保留交易所后缀。后缀的例子：
    #    - .TO: 多伦多证券交易所（Toronto Stock Exchange）
    #    - .L: 伦敦证券交易所（London Stock Exchange）
    #    - .HK: 香港交易所（Hong Kong Stock Exchange）
    #    - .T: 东京证券交易所（Tokyo Stock Exchange）
    #    如果 LLM 丢失了后缀（如把 "SHOP.TO" 写成 "SHOP"），
    #    数据供应商可能返回错误的或空的数据。
    #
    # Python 中相邻的字符串字面量会自动拼接：
    #   "Hello " "World"  等价于  "Hello World"
    # 所以这三行字符串会被自动拼接成一个完整的长字符串。
    return (
        f"The instrument to analyze is `{ticker}`. "  # 指定要分析的股票代码
        "Use this exact ticker in every tool call, report, and recommendation, "  # 在所有工具调用中使用
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."  # 保留交易所后缀
    )


# =============================================================================
# create_msg_delete() - 创建消息删除函数（工厂函数模式）
# =============================================================================
# 【调用时机】
#   在系统初始化阶段调用一次：delete_func = create_msg_delete()
#   然后将返回的 delete_func 注册到 LangGraph 图中。
#   当工作流需要在两个分析阶段之间"清理"对话历史时，LangGraph 调用 delete_func(state)。
#
# 【输入】
#   无参数（create_msg_delete 本身不接受参数）。
#   返回的 delete_messages 函数接受一个参数：
#     state (dict): LangGraph 状态字典，必须包含 "messages" 键
#
# 【输出】
#   create_msg_delete 返回一个函数 delete_messages。
#   delete_messages 返回一个字典：
#   {
#       "messages": [
#           RemoveMessage(id="msg_001"),  # 删除第1条消息
#           RemoveMessage(id="msg_002"),  # 删除第2条消息
#           ...
#           HumanMessage(content="Continue")  # 占位消息
#       ]
#   }
#
# 【输出被谁使用】
#   返回的字典被 LangGraph 框架接收。
#   LangGraph 的 add_messages reducer 会处理这个列表：
#   - 遇到 RemoveMessage：从消息列表中删除对应 id 的消息
#   - 遇到 HumanMessage：追加到消息列表末尾
#   最终效果：旧消息全部被删除，只留下一条 "Continue" 占位消息。
#
# 【为什么需要这个函数】
#   - 在智能体工作流中，不同阶段之间需要"清空"对话历史，避免上下文过长
#   - 直接清空会导致某些 LLM 提供商（如 Anthropic Claude）报错，
#     因为它们要求消息列表中至少有一条消息
#   - 添加 "Continue" 占位消息可以满足这一要求
#
# 【运行时数据流示例】
#   假设调用前 state["messages"] 有 3 条消息：
#   [
#       HumanMessage(id="msg_001", content="分析AAPL"),
#       AIMessage(id="msg_002", content="好的，我来获取数据..."),
#       ToolMessage(id="msg_003", content="Date,Open,High,Low,Close...")
#   ]
#
#   delete_messages(state) 执行后返回：
#   {
#       "messages": [
#           RemoveMessage(id="msg_001"),   # 删除 HumanMessage
#           RemoveMessage(id="msg_002"),   # 删除 AIMessage
#           RemoveMessage(id="msg_003"),   # 删除 ToolMessage
#           HumanMessage(content="Continue")  # 新的占位消息
#       ]
#   }
#
#   LangGraph 处理后，state["messages"] 变为：
#   [
#       HumanMessage(content="Continue")   # 只剩一条占位消息
#   ]
# =============================================================================
#
# ---------------------------------------------------------------------------
# 以下是可视化的流程图：消息删除的完整流程时序图
# ---------------------------------------------------------------------------
# ```mermaid
# sequenceDiagram
#     participant Node as delete_messages节点
#     participant State as 共享状态messages
#     participant Reducer as add_messages reducer
#     Node->>State: 读取当前messages列表
#     Node->>Node: 为每条消息创建RemoveMessage(id=m.id)
#     Node->>Node: 添加占位消息HumanMessage("Continue")
#     Node->>Reducer: 返回 {messages: removal_operations + [placeholder]}
#     Reducer->>Reducer: 识别RemoveMessage，按id删除对应消息
#     Reducer->>State: 追加占位消息
#     Note over State: 最终messages = [HumanMessage("Continue")]
# ```
# ---------------------------------------------------------------------------
def create_msg_delete():
    """
    创建消息删除函数（工厂函数 / 闭包模式）

    返回值是一个函数 delete_messages，该函数可以：
    1. 删除状态中的所有历史消息（通过 RemoveMessage）
    2. 添加一条占位消息 "Continue"（用于 Anthropic Claude 兼容性）

    为什么需要这个函数？
    - 在智能体工作流中，有时需要"清空"对话历史，避免上下文过长
    - 直接清空会导致某些 LLM 提供商（如 Anthropic）报错，因为它们要求
      消息列表中至少有一条消息
    - 添加 "Continue" 占位消息可以满足这一要求

    设计模式：工厂函数（Factory Function）
    - create_msg_delete 是"工厂"，负责生产函数
    - 返回的 delete_messages 是"产品"，供 LangGraph 节点使用
    - 这种模式的优势是可以在工厂函数中添加配置参数（虽然当前版本没有）

    返回：
        function: 一个接受 state 字典并返回更新字典的函数

    使用示例：
        delete_func = create_msg_delete()  # 创建删除函数
        new_state = delete_func(current_state)  # 调用删除函数
    """

    # =====================================================================
    # delete_messages(state) —— 实际的 LangGraph 节点函数（闭包）
    # =====================================================================
    # 【什么是闭包】
    #   delete_messages 是 create_msg_delete 的内部函数。
    #   虽然当前版本没有引用外部变量，但这个结构允许将来扩展：
    #   如果需要在 delete_messages 中使用外部配置，
    #   可以在 create_msg_delete 中定义变量，delete_messages 通过闭包访问。
    #
    #   闭包的核心特性：内部函数"记住"了它被创建时的环境。
    #   即使外部函数已经返回，内部函数仍然可以访问外部函数的变量。
    #
    #   类比：工厂生产一台机器时，把配置参数"烧录"到机器的芯片中。
    #   机器被搬到别处使用时，配置参数仍然在芯片里。
    #
    # 【调用时机】
    #   当 LangGraph 工作流需要在两个分析阶段之间清理对话历史时调用。
    #   例如：市场分析完成后、进入投资辩论之前，清空旧消息。
    #
    # 【输入】
    #   state (dict): LangGraph 状态字典
    #   必须包含 "messages" 键，其值是一个消息对象列表。
    #   每个消息对象都有一个 .id 属性（唯一标识符）。
    #
    # 【输出】
    #   dict: 包含 "messages" 键的字典，值是一个混合列表：
    #   - 前面是若干 RemoveMessage 对象（删除旧消息的指令）
    #   - 最后是一个 HumanMessage 对象（占位消息）
    #   LangGraph 的 reducer 会自动处理这些操作。
    # =====================================================================
    def delete_messages(state):
        """
        删除所有消息并添加占位消息

        这是 create_msg_delete 工厂函数返回的内部函数（闭包）。
        它接收 LangGraph 的状态字典，清除其中的消息历史。

        参数：
            state (dict): LangGraph 状态字典，必须包含 "messages" 键

        返回：
            dict: 包含 "messages" 键的更新字典，其中：
                  - 包含所有旧消息的 RemoveMessage 操作
                  - 加上一条新的 HumanMessage("Continue") 占位消息

        工作原理：
            1. 从状态中取出所有消息
            2. 为每条消息创建一个 RemoveMessage 操作（按消息 id 删除）
            3. 添加一条 HumanMessage("Continue") 作为占位
            4. LangGraph 的 reducer 会自动处理 RemoveMessage 和消息合并
        """
        # 从状态字典中取出 messages 列表。
        #
        # state["messages"] 是一个消息对象列表，例如：
        # [
        #     HumanMessage(id="msg_001", content="分析AAPL"),
        #     AIMessage(id="msg_002", content="好的，我来获取数据..."),
        #     ToolMessage(id="msg_003", content="Date,Open,High,Low,Close..."),
        #     AIMessage(id="msg_004", content="基于技术分析...")
        # ]
        #
        # 每个消息对象都有一个 .id 属性，这是消息的唯一标识符。
        # LangGraph 在添加消息时会自动分配 id。
        messages = state["messages"]

        # 为每条消息创建一个 RemoveMessage 操作。
        #
        # 【列表推导式语法解释】
        # [RemoveMessage(id=m.id) for m in messages]
        #
        # 这行代码等价于以下传统写法：
        #   removal_operations = []             # 创建空列表
        #   for m in messages:                  # 遍历每条消息
        #       removal = RemoveMessage(id=m.id)  # 创建删除操作
        #       removal_operations.append(removal)  # 添加到列表
        #
        # 列表推导式 [表达式 for 变量 in 列表] 的含义：
        #   对 messages 列表中的每个元素 m，
        #   计算 RemoveMessage(id=m.id) 的值，
        #   收集成一个新的列表。
        #
        # 【RemoveMessage 的工作原理】
        # RemoveMessage(id=m.id) 创建一个"删除指令"：
        # "请删除 id 为 m.id 的那条消息"
        #
        # 这不是一个真正的消息，而是一个操作指令。
        # 当 LangGraph 的 add_messages reducer 看到这个指令时，
        # 它会在消息列表中查找 id 匹配的消息并删除它。
        #
        # 运行时，removal_operations 的值类似这样：
        # [
        #     RemoveMessage(id="msg_001"),
        #     RemoveMessage(id="msg_002"),
        #     RemoveMessage(id="msg_003"),
        #     RemoveMessage(id="msg_004")
        # ]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # 创建一条占位消息，内容为 "Continue"。
        #
        # HumanMessage 表示这是一条"人类消息"（用户角色的消息）。
        # 但这里它不来自真正的用户，而是用作系统占位。
        #
        # 为什么需要占位消息？
        # 某些 LLM 提供商（如 Anthropic Claude）要求消息列表不能为空。
        # 如果直接删除所有消息而不添加占位消息，下一次调用 LLM 时会报错：
        #   "messages list cannot be empty"
        # 添加一条 "Continue" 占位消息可以满足这一要求。
        #
        # "Continue" 这个词的含义是告诉 LLM "继续之前的工作"，
        # 但由于旧消息已被删除，LLM 会认为这是一个新的对话开始。
        # 不过这不是问题，因为相关数据已经存储在 state 的其他字段中
        # （如 market_report、sentiment_report 等），不依赖消息历史。
        #
        # 运行时，placeholder 的值类似这样：
        # HumanMessage(content="Continue")
        placeholder = HumanMessage(content="Continue")

        # 返回状态更新字典。
        #
        # "messages" 键对应的值是一个列表，使用 + 运算符拼接两部分：
        #
        # 1. removal_operations（删除操作列表）：
        #    [RemoveMessage(id="msg_001"), RemoveMessage(id="msg_002"), ...]
        #    这些指令告诉 LangGraph 删除对应的旧消息。
        #
        # 2. [placeholder]（占位消息列表）：
        #    [HumanMessage(content="Continue")]
        #    这是一条新的占位消息，会被追加到消息列表末尾。
        #
        # 列表拼接：[A, B] + [C] = [A, B, C]
        # 所以 removal_operations + [placeholder] 的结果是：
        # [
        #     RemoveMessage(id="msg_001"),      # 删除第1条消息
        #     RemoveMessage(id="msg_002"),      # 删除第2条消息
        #     RemoveMessage(id="msg_003"),      # 删除第3条消息
        #     RemoveMessage(id="msg_004"),      # 删除第4条消息
        #     HumanMessage(content="Continue")   # 新的占位消息
        # ]
        #
        # LangGraph 会按顺序处理这个列表：
        # 1. 先执行所有 RemoveMessage，删除对应的旧消息
        # 2. 再追加 HumanMessage("Continue") 到消息列表
        # 最终 state["messages"] 只包含一条消息：HumanMessage(content="Continue")
        return {"messages": removal_operations + [placeholder]}

    # 工厂函数返回内部函数 delete_messages。
    #
    # 此时 delete_messages 作为闭包被返回。
    # 调用者获得的是一个"可以接受 state 参数的函数"，
    # 这个函数可以被 LangGraph 注册为节点函数。
    #
    # 使用方式：
    #   delete_func = create_msg_delete()  # 调用工厂，获得产品
    #   result = delete_func(state)        # 使用产品
    #   # result 是 {"messages": [RemoveMessage(...), ..., HumanMessage("Continue")]}
    return delete_messages
