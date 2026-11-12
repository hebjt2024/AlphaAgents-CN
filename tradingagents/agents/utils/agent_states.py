# =============================================================================
# 前置知识：阅读本文件前需要理解的核心概念
# =============================================================================
#
# 【1. Python 的类型注解（Type Annotation）】
#   Python 是一门"动态类型"语言，这意味着你不需要提前声明变量的类型。
#   但 Python 3.5+ 引入了"类型注解"功能，允许你可选地为变量标注类型。
#   例如：
#       name: str = "Alice"        # 标注 name 是字符串类型
#       age: int = 30              # 标注 age 是整数类型
#   注意：类型注解是"可选的"且"不影响运行时行为"。
#   即使你标注了类型，Python 运行时也不会强制检查。
#   类型注解主要用于：
#     - IDE（如 PyCharm、VS Code）的智能提示和自动补全
#     - 静态类型检查工具（如 mypy）的代码检查
#     - 让其他开发者更容易理解代码意图
#
# 【2. TypedDict（类型化字典）】
#   来自 typing_extensions 模块（也可从 typing 导入，Python 3.8+）。
#   普通的 Python 字典 dict 可以包含任意键值对，没有类型约束：
#       d = {"name": "Alice", "age": 30, "scores": [90, 80]}  # 键和值的类型任意
#   TypedDict 允许你定义一个"字典的模板"，规定每个键的名字和对应值的类型：
#       class Person(TypedDict):
#           name: str
#           age: int
#   然后你就可以像创建普通字典一样使用它：
#       p: Person = {"name": "Alice", "age": 30}
#   【极其重要的理解】：TypedDict 在运行时其实就是普通的 dict！
#   它不会创建新的数据结构，也不做任何运行时检查。
#   类型注解只用于 IDE 提示和静态检查器（如 mypy）。
#   换句话说，以下两行代码在运行时完全等价：
#       state: AgentState = {"company_of_interest": "AAPL", ...}
#       state = {"company_of_interest": "AAPL", ...}
#   TypedDict 的价值在于"文档化"和"类型安全提示"，而非运行时行为。
#
# 【3. Annotated（类型注解包装器）】
#   来自 typing 模块。
#   语法：Annotated[原始类型, "元数据1", "元数据2", ...]
#   Annotated 的作用是给一个类型"贴标签"（添加元数据）。
#   例如：
#       name: Annotated[str, "用户的全名"]
#   这行代码的含义是："name 是字符串类型，并且它的描述是'用户的全名'"。
#   【关键理解】：Annotated 在运行时不改变类型本身。
#   `Annotated[str, "描述"]` 在运行时仍然就是 `str`。
#   元数据字符串"描述"可以被框架（如 LangGraph、LangChain）在运行时读取，
#   也可以被 IDE 和静态检查器读取，用于提供更多信息。
#   在本项目中，Annotated 的元数据字符串用于向 LangGraph 描述字段的含义。
#
# 【4. MessagesState（消息状态基类）】
#   来自 langgraph.graph 模块。
#   MessagesState 是 LangGraph 预定义的一个 TypedDict 子类。
#   它的源码大致等价于：
#       from typing import Annotated
#       from typing_extensions import TypedDict
#       from langgraph.graph.message import add_messages
#
#       class MessagesState(TypedDict):
#           messages: Annotated[list, add_messages]
#   其中 `messages` 字段存储一个消息列表（如 [HumanMessage(...), AIMessage(...)]）。
#   `add_messages` 是一个"reducer"（归约函数），它的作用是：
#   - 当 LangGraph 节点返回新的 messages 时，不是"覆盖"旧列表，
#     而是"追加"到旧列表末尾。
#   - 例如：旧消息列表有 3 条，节点返回 2 条新消息，
#     合并后列表有 5 条消息（3 + 2），而不是只有 2 条。
#   这种"追加而非覆盖"的行为对于对话历史管理至关重要。
#   我们的 AgentState 继承 MessagesState，因此自动拥有 messages 字段。
#
# 【5. StateGraph（状态图）】
#   来自 langgraph.graph 模块。
#   StateGraph 是 LangGraph 的核心类，用于将多智能体工作流建模为"有向图"。
#   - 图中的"节点"（node）代表处理步骤（如"市场分析"、"风险评估"）
#   - 图中的"边"（edge）代表数据流向（如"市场分析完成后→投资辩论"）
#   - 状态（State）在节点间传递，状态类型定义了"什么数据在流转"
#   本文件定义的就是这个"状态类型"，它决定了整个工作流中可以传递哪些数据。
#
# 【6. 继承（Inheritance）】
#   Python 的面向对象特性。一个类可以"继承"另一个类，自动获得其所有属性和方法。
#   语法：class 子类(父类):
#   在本文件中：class AgentState(MessagesState):
#   这表示 AgentState 继承了 MessagesState，自动拥有 messages 字段，
#   同时还可以添加自己的字段（如 company_of_interest、trade_date 等）。
#   对于 TypedDict 来说，继承意味着"在父类的字段基础上，添加更多字段"。
#
# 【7. 本文件在系统中的位置】
#   本文件定义了 TradingAgents 多智能体系统中所有智能体共享的"状态数据结构"。
#   可以把状态理解为一个"大黑板"，所有智能体都在上面读写信息：
#   - 市场分析师写上 market_report
#   - 情绪分析师写上 sentiment_report
#   - 交易员读取所有报告，写上 trader_investment_plan
#   - 风险分析师读取计划，写上 final_trade_decision
#   这个"大黑板"的结构（有哪些字段、每个字段什么类型）就是本文件定义的。
# =============================================================================


# =============================================================================
# 模块名称：agent_states.py
# 模块功能：定义 TradingAgents 多智能体系统中所有智能体的状态数据结构
#
# 本文件定义了三个 TypedDict 类：
# 1. InvestDebateState —— 多头 vs 空头 的二方辩论状态
# 2. RiskDebateState   —— 激进 / 保守 / 中立 的三方辩论状态
# 3. AgentState        —— 主状态，贯穿整个交易分析流程，继承自 MessagesState
#
# 这些类只是"类型定义"，不包含任何可执行逻辑。
# 它们的作用是：告诉 LangGraph 框架"状态字典里有哪些键、每个键存什么类型的值"。
# =============================================================================


# ---------------------------------------------------------------------------
# 导入区：每个 import 语句的作用和原因
# ---------------------------------------------------------------------------

# 【为什么需要 Annotated】
#   Annotated 用于给类型添加元数据标签。
#   在本文件中，每个状态字段都写成 Annotated[类型, "描述"] 的形式，
#   这样 LangGraph 框架可以读取描述信息，IDE 也能显示字段含义。
#
# 【为什么需要 Sequence】
#   Sequence 是一个抽象的"有序集合"类型（list、tuple 都是 Sequence）。
#   本文件中虽然在最终定义里没有直接使用 Sequence，
#   但它是 typing 模块的标准导入，常用于类型注解中标注"有序集合"。
from typing import Annotated

# 【为什么需要 ToolNode】
#   ToolNode 是 LangGraph 预编译的"工具节点"。
#   在 LangGraph 工作流中，当 LLM 请求调用工具时，
#   ToolNode 负责实际执行工具函数并将结果返回给 LLM。
#   虽然本文件不直接使用它，但它是状态流程图的组成部分。
# 【为什么需要 END、StateGraph、START、MessagesState】
#   - END: 图节点终止标记，表示工作流到此结束（相当于"终点站"）
#   - StateGraph: 状态图类，用于构建智能体工作流（相当于"路线图"）
#   - START: 图节点起始标记，表示工作流从这里开始（相当于"始发站"）
#   - MessagesState: 消息状态基类，内置 messages 字段，
#     使用 add_messages reducer 实现"追加而非覆盖"的消息管理
#   本文件中的 AgentState 继承自 MessagesState，因此需要导入它。
from langgraph.graph import MessagesState

# 【为什么需要 datetime 模块】
#   交易系统大量涉及日期时间处理（如"获取最近30天的股价数据"）。
#   - date: 表示一个日期（年-月-日），如 date(2024, 6, 15)
#   - timedelta: 表示一段时间差，如 timedelta(days=30) 表示30天
#   - datetime: 表示日期+时间的组合，如 datetime(2024, 6, 15, 10, 30)
#   这些类型虽然在本文件的类型定义中没有直接出现，
#   但它们是交易系统基础设施的一部分，可能在后续扩展中使用。
# 【为什么需要 TypedDict】
#   TypedDict 是本文件的核心工具。
#   它允许我们定义"有固定键和值类型的字典"。
#   没有它，状态字典就只是普通的 dict，无法享受类型提示和字段自动补全。
#
# 【为什么需要 Optional】
#   Optional[X] 表示"可以是 X 类型，也可以是 None"。
#   在状态字段中，有些字段在初始状态下可能还没有值（None），
#   Optional 提供了一种方式来标注这种"可选"状态。
from typing_extensions import TypedDict

# 【为什么需要 ChatOpenAI】
#   ChatOpenAI 是 LangChain 封装的 OpenAI 聊天模型接口。
#   虽然在此文件中未直接使用（本文件只定义数据结构，不含逻辑），
#   但它可能被其他模块通过 `from agent_states import *` 间接引用。
#   这是一个"再导出"（re-export）模式，方便其他文件从统一位置导入。
# 【为什么需要 `from tradingagents.agents import *`】
#   这行代码从项目的 agents 包中导入所有公开符号（__all__ 列表中的内容）。
#   这是一个"再导出"模式，让本文件可以访问 agents 包中定义的公共接口。
#   通配符 * 导入会导入 __all__ 列表中定义的所有内容。
#   如果 __all__ 未定义，则导入所有不以 _ 开头的名称。
from tradingagents.agents import *


# =============================================================================
# 投资辩论状态（InvestDebateState）
# =============================================================================
# 【何时使用此状态】
#   在研究员团队辩论阶段使用。当四位研究员（市场、情绪、新闻、基本面）
#   各自完成报告后，系统会启动"多头（bull）vs 空头（bear）"辩论：
#   - 多头研究员：论证"应该买入"的理由
#   - 空头研究员：论证"应该卖出或不买"的理由
#   双方轮流发言，最终由法官智能体做出裁决。
#
# 【运行时数据流示例】
#   在运行时，InvestDebateState 的实际值类似这样（它就是一个普通 dict）：
#
#   {
#       "bull_history": "Based on strong earnings growth and bullish MACD...",
#       "bear_history": "However, RSI indicates overbought conditions at 78...",
#       "history": "Bull: Based on strong earnings...\nBear: However, RSI...",
#       "current_response": "However, RSI indicates overbought conditions at 78...",
#       "judge_decision": "",
#       "count": 2
#   }
#
#   注意：这就是一个普通的 Python 字典，TypedDict 的类型注解
#   在运行时不起任何约束作用，只是方便开发者和 IDE 理解字段含义。
# =============================================================================
class InvestDebateState(TypedDict):
    """
    投资辩论状态类 - 记录多头与空头研究员的辩论过程

    这个 TypedDict 定义了一个结构化字典，用于在 LangGraph 节点间传递辩论数据。
    每个字段使用 Annotated 包装，元数据字符串描述该字段的含义。

    【调用时机】
        在研究员团队完成各自报告后，系统进入投资辩论阶段时使用。
        每一轮辩论（多头发言→空头回应）都会更新此状态。

    【输入来源】
        由 LangGraph 框架在进入辩论阶段时初始化，各字段的初始值通常为空字符串和 0。

    【输出去向】
        辩论结束后，judge_decision 字段的值会被传递到 AgentState 的
        investment_plan 字段，供后续的交易员（Trader）智能体使用。
    """

    # bull_history: 多头研究员的对话历史（字符串类型）
    # ----------------------------------------------------------------
    # 这个字段累积记录多头研究员的所有发言内容。
    # 每轮多头发言后，新内容会被追加到这个字符串末尾。
    # Annotated[str, "Bullish Conversation history"] 的含义：
    #   - str: 字段值的类型是字符串
    #   - "Bullish Conversation history": 元数据描述，告诉框架和开发者
    #     这个字段存储的是"多头方的对话历史"
    # 运行时，Annotated 不改变 str 类型的任何行为，它就是个普通字符串。
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]  # Bullish Conversation history

    # bear_history: 空头研究员的对话历史（字符串类型）
    # ----------------------------------------------------------------
    # 与 bull_history 对称，累积记录空头研究员的所有发言内容。
    # 多头看涨（bull = 牛市），空头看跌（bear = 熊市）。
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]  # Bullish Conversation history

    # history: 完整的对话历史（字符串类型）
    # ----------------------------------------------------------------
    # 记录所有参与者的发言，按时间顺序拼接。
    # 与 bull_history / bear_history 不同，这里记录的是"完整对话流"，
    # 包含多头和空头的交替发言，方便法官智能体阅读完整辩论过程。
    history: Annotated[str, "Conversation history"]  # Conversation history

    # current_response: 最新的发言内容（字符串类型）
    # ----------------------------------------------------------------
    # 存储最近一次发言的完整内容。
    # 作用：下一个发言者需要参考"对方刚才说了什么"来组织自己的反驳。
    # 这就像辩论赛中"对方辩友刚才说……，但我认为……"的引用机制。
    current_response: Annotated[str, "Latest response"]  # Last response

    # judge_decision: 法官的最终裁决（字符串类型）
    # ----------------------------------------------------------------
    # 辩论达到指定轮次后，法官智能体阅读完整辩论记录，
    # 做出最终的投资建议（如"BUY"、"HOLD"、"SELL"或更详细的方案）。
    # 在辩论进行中，此字段为空字符串。
    judge_decision: Annotated[str, "Final judge decision"]  # Last response

    # count: 当前对话轮次计数（整数类型）
    # ----------------------------------------------------------------
    # 记录当前已经进行了多少轮辩论（一轮 = 多头发言 + 空头回应）。
    # 作用：LangGraph 的条件边会检查 count 是否达到预设的最大轮次（如 5 轮）。
    # 如果没达到，继续辩论（count + 1）；如果达到了，跳转到法官裁决节点。
    # Annotated[int, ...] 表示这个字段的值是整数，元数据描述为"当前对话长度"。
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


# =============================================================================
# 风险管理辩论状态（RiskDebateState）
# =============================================================================
# 【何时使用此状态】
#   在风险管理团队辩论阶段使用。当交易员制定投资计划后，
#   三位风险分析师（激进型、保守型、中立型）会对计划进行三方辩论：
#   - 激进型（aggressive）：倾向于承担更高风险以获取更高回报
#   - 保守型（conservative）：优先保护本金，厌恶风险
#   - 中立型（neutral）：在风险和回报之间寻找平衡点
#   三方轮流发言，最终由风险法官做出最终交易决策。
#
# 【与 InvestDebateState 的区别】
#   InvestDebateState 是"二方辩论"（多头 vs 空头），
#   RiskDebateState 是"三方辩论"（激进 vs 保守 vs 中立）。
#   因此 RiskDebateState 需要更多字段来追踪三方各自的发言历史。
#
# 【运行时数据流示例】
#   在运行时，RiskDebateState 的实际值类似这样：
#
#   {
#       "aggressive_history": "The current plan is too conservative. Given the...",
#       "conservative_history": "I disagree. The market volatility index (VIX)...",
#       "neutral_history": "Both sides have valid points. We should consider...",
#       "history": "Aggressive: The current plan...\nConservative: I disagree...\nNeutral: Both sides...",
#       "latest_speaker": "neutral",
#       "current_aggressive_response": "The current plan is too conservative...",
#       "current_conservative_response": "I disagree. The VIX...",
#       "current_neutral_response": "Both sides have valid points...",
#       "judge_decision": "",
#       "count": 3
#   }
# =============================================================================
class RiskDebateState(TypedDict):
    """
    风险管理辩论状态类 - 记录三位风险分析师的辩论过程

    与 InvestDebateState 的二方辩论不同，这里涉及三方辩论，
    需要额外的字段来追踪每个参与者的发言历史和当前发言。

    【调用时机】
        在交易员生成投资计划后，进入风险管理辩论阶段时使用。

    【输入来源】
        由 LangGraph 框架在进入风险辩论阶段时初始化。

    【输出去向】
        辩论结束后，judge_decision 的值传递到 AgentState 的
        final_trade_decision 字段，作为整个系统的最终输出。
    """

    # aggressive_history: 激进型分析师的对话历史
    # ----------------------------------------------------------------
    # 累积记录激进型分析师的所有发言。激进型倾向于"高风险高回报"策略。
    aggressive_history: Annotated[
        str, "Aggressive Agent's Conversation history"
    ]  # Conversation history

    # conservative_history: 保守型分析师的对话历史
    # ----------------------------------------------------------------
    # 累积记录保守型分析师的所有发言。保守型倾向于"安全第一，保本为主"。
    conservative_history: Annotated[
        str, "Conservative Agent's Conversation history"
    ]  # Conversation history

    # neutral_history: 中立型分析师的对话历史
    # ----------------------------------------------------------------
    # 累积记录中立型分析师的所有发言。中立型在风险和回报间寻求平衡。
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]  # Conversation history

    # history: 完整的对话历史
    # ----------------------------------------------------------------
    # 按时间顺序记录所有三方的发言，供法官智能体阅读。
    history: Annotated[str, "Conversation history"]  # Conversation history

    # latest_speaker: 最后发言的分析师标识（字符串）
    # ----------------------------------------------------------------
    # 【关键字段】记录"谁最后发了言"，用于实现三方轮流发言的机制。
    # 可能的值：'aggressive'、'conservative'、'neutral'
    # 工作原理：LangGraph 的条件边会读取此字段，
    #   如果 latest_speaker == 'aggressive'，则下一个发言者是 conservative；
    #   如果 latest_speaker == 'conservative'，则下一个发言者是 neutral；
    #   如果 latest_speaker == 'neutral'，则下一个发言者是 aggressive。
    # 这样就实现了 A→B→C→A→B→C... 的循环发言顺序。
    latest_speaker: Annotated[str, "Analyst that spoke last"]

    # current_aggressive_response: 激进型分析师的最新发言
    # ----------------------------------------------------------------
    # 存储激进型分析师最近一次的完整发言内容。
    # 其他分析师在发言时会参考这个字段来组织反驳。
    current_aggressive_response: Annotated[
        str, "Latest response by the aggressive analyst"
    ]  # Last response

    # current_conservative_response: 保守型分析师的最新发言
    # ----------------------------------------------------------------
    # 存储保守型分析师最近一次的完整发言内容。
    current_conservative_response: Annotated[
        str, "Latest response by the conservative analyst"
    ]  # Last response

    # current_neutral_response: 中立型分析师的最新发言
    # ----------------------------------------------------------------
    # 存储中立型分析师最近一次的完整发言内容。
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]  # Last response

    # judge_decision: 法官的最终裁决
    # ----------------------------------------------------------------
    # 三方辩论结束后，风险法官阅读完整辩论记录，做出最终交易决策。
    # 这是整个 TradingAgents 系统的最终输出。
    judge_decision: Annotated[str, "Judge's decision"]

    # count: 当前对话轮次计数
    # ----------------------------------------------------------------
    # 与 InvestDebateState 中的 count 作用相同，用于控制辩论轮次上限。
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


# =============================================================================
# 主智能体状态（AgentState）
# =============================================================================
# 【何时使用此状态】
#   AgentState 是整个 TradingAgents 系统的"主状态"。
#   从用户输入目标公司开始，到最终输出交易决策，整个流程中所有节点
#   共享这一个状态对象。
#
# 【继承关系】
#   TypedDict → MessagesState → AgentState
#
#   AgentState 继承自 MessagesState，因此自动拥有 messages 字段。
#   messages 字段使用 add_messages reducer，这意味着：
#   - 每个节点返回的消息会"追加"到 messages 列表，而非"覆盖"
#   - 这样整个对话历史就被完整保留下来
#
# 【系统工作流程（按字段使用顺序）】
#   阶段1 - 输入：
#     用户设置 company_of_interest（如 "AAPL"）和 trade_date（如 "2024-06-15"）
#
#   阶段2 - 研究阶段（四位研究员并行工作）：
#     市场分析师   → 写入 market_report
#     情绪分析师   → 写入 sentiment_report
#     新闻研究员   → 写入 news_report
#     基本面研究员 → 写入 fundamentals_report
#
#   阶段3 - 投资辩论阶段：
#     多头 vs 空头辩论 → 写入 investment_debate_state（InvestDebateState 类型）
#     法官裁决 → 写入 investment_plan
#
#   阶段4 - 交易阶段：
#     交易员读取所有报告和投资计划 → 写入 trader_investment_plan
#
#   阶段5 - 风险管理阶段：
#     三方风险辩论 → 写入 risk_debate_state（RiskDebateState 类型）
#     风险法官裁决 → 写入 final_trade_decision（系统最终输出）
#
#   全程：
#     messages 字段（继承自 MessagesState）记录所有智能体的对话消息
#     sender 字段追踪每条消息来自哪个智能体
#
# 【运行时数据流示例】
#   在运行时，AgentState 的实际值类似这样（一个大字典）：
#
#   {
#       "messages": [
#           HumanMessage(content="Analyze AAPL for 2024-06-15"),
#           AIMessage(content="I'll start by getting stock data..."),
#           ToolMessage(content="Date,Open,High,Low,Close,Volume..."),
#           AIMessage(content="Based on the technical analysis..."),
#           ...
#       ],
#       "company_of_interest": "AAPL",
#       "trade_date": "2024-06-15",
#       "sender": "market_analyst",
#       "market_report": "Based on the technical analysis, AAPL shows...",
#       "sentiment_report": "",
#       "news_report": "",
#       "fundamentals_report": "",
#       "investment_debate_state": {
#           "bull_history": "", "bear_history": "", ...
#       },
#       "investment_plan": "",
#       "trader_investment_plan": "",
#       "risk_debate_state": {
#           "aggressive_history": "", ...
#       },
#       "final_trade_decision": ""
#   }
#
#   注意：这就是一个普通的 Python 字典！TypedDict 和 Annotated
#   在运行时不改变任何行为。它们只是让 IDE 和开发者更容易理解
#   "这个字典应该有哪些键、每个键存什么类型的值"。
# =============================================================================
class AgentState(MessagesState):
    """
    主智能体状态类 - 整个交易系统的核心数据容器

    继承自 MessagesState，因此自动拥有 messages 字段（存储对话消息列表）。
    在此之上添加了交易分析全流程所需的所有字段。

    继承关系：
    TypedDict -> MessagesState -> AgentState

    【messages 字段（继承自 MessagesState）】
        messages: Annotated[list, add_messages]
        - 类型是 list（列表），存储 LangChain 消息对象
        - add_messages 是 reducer 函数：新消息"追加"而非"覆盖"
        - 每个节点返回的 messages 会自动追加到已有列表

    【调用时机】
        从系统启动到结束，全程使用。

    【输入来源】
        用户初始化时设置 company_of_interest 和 trade_date，
        其他字段由各智能体节点在运行过程中逐步填充。

    【输出去向】
        final_trade_decision 字段是系统的最终输出，即交易决策。
    """

    # company_of_interest: 目标公司标识（字符串类型）
    # ----------------------------------------------------------------
    # 存储用户指定的目标公司股票代码。
    # 例如："AAPL"（苹果）、"TSLA"（特斯拉）、"SHOP.TO"（Shopify多伦多）
    # 这个字段在系统启动时由用户设置，后续所有智能体都会读取它。
    company_of_interest: Annotated[str, "Company that we are interested in trading"]

    # trade_date: 交易日期（字符串类型）
    # ----------------------------------------------------------------
    # 格式为 "yyyy-mm-dd"（如 "2024-06-15"）。
    # 指定分析所基于的交易日期。所有数据查询都以这个日期为基准。
    # 例如：获取该日期前 6 个月的股价数据用于技术分析。
    trade_date: Annotated[str, "What date we are trading at"]

    # analysis_horizon: 分析视角（short/medium/long）
    # 会注入到所有分析师的 instrument_context 中，影响报告的时间维度。
    analysis_horizon: Annotated[str, "Investment horizon: short/medium/long"]

    # sender: 消息发送者标识（字符串类型）
    # ----------------------------------------------------------------
    # 追踪当前消息来自哪个智能体（如 "market_analyst"、"trader"）。
    # 作用：
    #   1. 路由：LangGraph 根据 sender 决定下一步走哪条边
    #   2. 日志：方便调试时追踪消息来源
    sender: Annotated[str, "Agent that sent this message"]

    # ========== 研究阶段（Research Step）==========
    # 以下四个字段分别由四位研究员在研究阶段生成。
    # 研究阶段通常并行执行，四位研究员同时工作。

    # market_report: 市场分析报告（字符串类型）
    # ----------------------------------------------------------------
    # 由市场分析师（Market Analyst）智能体生成。
    # 内容包括：技术指标分析（SMA、EMA、MACD、RSI、布林带等）、
    # 趋势判断、支撑/阻力位等。
    # 生成时机：市场分析师节点执行完成后。
    # 使用者：投资辩论阶段的多头/空头研究员、交易员。
    market_report: Annotated[str, "Report from the Market Analyst"]

    # strategy_report: 本次启用策略的扫描结果（字符串类型）
    # 由独立的策略扫描节点生成，供市场分析师参考
    strategy_report: Annotated[str, "Report from the Strategy Scanner"]

    # strategy_result: 策略扫描的结构化结果（dict）
    # 用于生成结构化决策卡（评分/止损/目标价/仓位）
    strategy_result: Annotated[dict, "Structured result from the Strategy Scanner"]

    # sentiment_report: 社交媒体情绪分析报告（字符串类型）
    # ----------------------------------------------------------------
    # 由社交媒体分析师（Social Media Analyst）智能体生成。
    # 内容包括：Twitter/Reddit 等平台上的投资者情绪分析。
    # 生成时机：社交媒体分析师节点执行完成后。
    # 使用者：投资辩论阶段的多头/空头研究员、交易员。
    sentiment_report: Annotated[
        str, "Report from the Social Media Analyst"
    ]

    # news_report: 新闻分析报告（字符串类型）
    # ----------------------------------------------------------------
    # 由新闻研究员（News Researcher）智能体生成。
    # 内容涵盖：当前全球事件、公司相关新闻、行业动态等。
    # 生成时机：新闻研究员节点执行完成后。
    # 使用者：投资辩论阶段的多头/空头研究员、交易员。
    news_report: Annotated[
        str, "Report from the News Researcher of current world affairs"
    ]

    # fundamentals_report: 基本面分析报告（字符串类型）
    # ----------------------------------------------------------------
    # 由基本面研究员（Fundamentals Researcher）智能体生成。
    # 内容包括：财务报表分析（资产负债表、利润表、现金流量表）、
    # 估值指标（P/E、P/B、ROE 等）。
    # 生成时机：基本面研究员节点执行完成后。
    # 使用者：投资辩论阶段的多头/空头研究员、交易员。
    fundamentals_report: Annotated[str, "Report from the Fundamentals Researcher"]

    # ========== 研究员团队辩论阶段（Researcher Team Discussion Step）==========

    # investment_debate_state: 投资辩论状态
    # ----------------------------------------------------------------
    # 类型为 InvestDebateState（上面定义的 TypedDict）。
    # 这是一个"嵌套的 TypedDict"——状态中的状态。
    # 在运行时，它的值是一个普通的字典，包含 bull_history、bear_history 等键。
    # 记录多头/空头辩论的完整过程。
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]

    # investment_plan: 投资计划（字符串）
    # ----------------------------------------------------------------
    # 由研究员团队综合多头/空头辩论后生成的投资方案。
    # 法官智能体阅读辩论记录后做出裁决，裁决结果写入此字段。
    # 使用者：交易员（Trader）智能体根据此计划制定具体执行方案。
    investment_plan: Annotated[str, "Plan generated by the Analyst"]

    # trader_investment_plan: 交易员投资计划（字符串）
    # ----------------------------------------------------------------
    # 由交易员（Trader）智能体根据研究员计划生成的具体执行方案。
    # 交易员会考虑仓位大小、入场/出场时机、止损设置等实际交易细节。
    # 使用者：风险分析师团队会评估此计划的风险。
    trader_investment_plan: Annotated[str, "Plan generated by the Trader"]

    # ========== 风险管理团队辩论阶段（Risk Management Team Discussion Step）==========

    # risk_debate_state: 风险辩论状态
    # ----------------------------------------------------------------
    # 类型为 RiskDebateState（上面定义的 TypedDict）。
    # 记录三位风险分析师（激进/保守/中立）的三方辩论过程。
    # 同样是"嵌套的 TypedDict"——运行时就是一个普通字典。
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]

    # final_trade_decision: 最终交易决策（字符串）
    # ----------------------------------------------------------------
    # 由风险分析师团队综合三方辩论后做出的最终决策。
    # 风险法官阅读完整辩论记录后做出裁决，裁决结果写入此字段。
    # 这是整个 TradingAgents 系统的最终输出。
    # 可能的值例如："FINAL TRANSACTION PROPOSAL: **BUY**" 或
    #   "FINAL TRANSACTION PROPOSAL: **SELL**" 或
    #   "FINAL TRANSACTION PROPOSAL: **HOLD**"
    final_trade_decision: Annotated[str, "Final decision made by the Risk Analysts"]


# =============================================================================
# 状态字段读写矩阵（参见图解指南.md 第四章 4.2 节）
# =============================================================================
# 以下矩阵展示每个节点对 AgentState 状态字段的读（R）和写（W）操作。
# 这有助于理解数据在整个工作流中的流转关系。
#
# R = 读取    W = 写入    RW = 读写
#
# | 节点                   | messages | company_of_interest | market_report | sentiment_report | news_report | fundamentals_report | investment_debate_state | investment_plan | trader_investment_plan | risk_debate_state | final_trade_decision |
# |------------------------|----------|---------------------|---------------|------------------|-------------|---------------------|------------------------|-----------------|------------------------|-------------------|----------------------|
# | Market Analyst          | RW       | R                   | W             |                  |             |                     |                        |                 |                        |                   |                      |
# | Social Analyst          | RW       | R                   |               | W                |             |                     |                        |                 |                        |                   |                      |
# | News Analyst            | RW       | R                   |               |                  | W           |                     |                        |                 |                        |                   |                      |
# | Fundamentals Analyst    | RW       | R                   |               |                  |             | W                   |                        |                 |                        |                   |                      |
# | Bull Researcher         |          | R                   | R             | R                | R           | R                   | W                      |                 |                        |                   |                      |
# | Bear Researcher         |          | R                   | R             | R                | R           | R                   | W                      |                 |                        |                   |                      |
# | Research Manager        |          | R                   | R             | R                | R           | R                   | W                      | W               |                        |                   |                      |
# | Trader                  |          | R                   | R             | R                | R           | R                   | R                      | R               | W                      |                   |                      |
# | Aggressive Analyst      |          | R                   |               |                  |             |                     | R                      |                 | R                      | W                 |                      |
# | Conservative Analyst    |          | R                   |               |                  |             |                     | R                      |                 | R                      | W                 |                      |
# | Neutral Analyst         |          | R                   |               |                  |             |                     | R                      |                 | R                      | W                 |                      |
# | Portfolio Manager       |          | R                   | R             | R                | R           | R                   | R                      | R               | R                      | W                 | W                    |
# =============================================================================
