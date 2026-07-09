# =============================================================================
# TradingAgents/graph/conditional_logic.py
# =============================================================================
# 模块：ConditionalLogic - 图的条件路由逻辑
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件涉及的核心概念，如果你完全没有 Python 和 LangGraph 经验，请先阅读以下说明。
#
# --- 概念 1：什么是条件边（Conditional Edge）？ ---
# 在 LangGraph 中，有两种边：
#   a. 普通边（add_edge）：确定性的。A → B 表示执行完 A 后必定执行 B。
#      就像一条直路，没有分叉。
#   b. 条件边（add_conditional_edges）：动态的。A → 根据条件函数返回值 → B 或 C。
#      就像一个十字路口，根据信号灯决定走哪条路。
#
# 条件边是 LangGraph 实现"智能决策"和"循环"的关键机制。
# 没有条件边，图只能是一条直线（A→B→C→D→...）。
# 有了条件边，图可以实现：
#   - 分支：A → B 或 A → C（根据条件选择路径）
#   - 循环：A → B → A → B → ...（重复执行直到满足条件）
#   - 提前终止：A → B → C（正常）或 A → END（提前结束）
#
# --- 概念 2：条件边的路由机制 ---
# 条件边的定义方式：
#   workflow.add_conditional_edges(
#       "源节点",           # 从哪个节点出发
#       条件函数,           # 一个 Python 函数：接收 state，返回一个字符串
#       路由映射,           # 告诉 LangGraph 返回值对应哪个目标节点
#   )
#
# 执行流程：
#   1. 源节点执行完毕
#   2. LangGraph 调用条件函数(state)，得到一个字符串返回值
#   3. 在路由映射中查找这个返回值对应的目标节点名
#   4. 将流程路由到该目标节点
#
# 路由映射的两种形式：
#   a. 列表形式：["tools_market", "Msg Clear Market"]
#      条件函数返回的字符串直接作为目标节点名。
#      即：返回 "tools_market" → 去名为 "tools_market" 的节点
#
#   b. 字典形式：{"key1": "Node1", "key2": "Node2"}
#      条件函数返回的字符串作为 key，查找字典中对应的 value（即目标节点名）。
#      即：返回 "key1" → 去名为 "Node1" 的节点
#      好处：返回值和节点名可以不同，更灵活。
#
# 通俗比喻：条件边就像火车站的道岔——
#   信号灯（条件函数返回值）决定列车走哪条轨道（目标节点）。
#
# --- 概念 3：tool_calls 是什么？ ---
# 当 LLM（大语言模型）需要获取数据时，它不会直接调用函数，而是输出一个 tool_calls 结构。
# tool_calls 是 LLM 回复中的一个特殊字段，格式类似：
#   tool_calls = [{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "call_123"}]
#
# 这相当于 LLM 说："请帮我调用 get_stock_data 工具，参数是 ticker=AAPL"
# LangGraph 的 ToolNode 会检测到这个结构，自动执行对应的工具函数。
#
# 为什么要这样设计？
#   LLM 本身只能输出文本，不能直接执行代码或访问网络。
#   通过 tool_calls 机制，LLM 可以"请求"调用外部工具，
#   由 LangGraph 框架来实际执行工具调用，再把结果返回给 LLM。
#
# --- 概念 4：本文件中两类条件逻辑 ---
#
# 第一类：分析阶段条件（should_continue_market/social/news/fundamentals）
#   用于控制分析师和工具节点之间的循环。
#   判断逻辑：检查最后一条消息是否包含 tool_calls
#     - 有 tool_calls → 返回工具节点名（让 ToolNode 执行工具调用，获取数据）
#     - 无 tool_calls → 返回消息清除节点名（分析已完成，进入下一个阶段）
#   这实现了"分析师反复调用工具获取数据"的循环：
#     分析师 → 有 tool_calls → 工具 → 回到分析师 → 有 tool_calls → 工具 → ...
#     → 无 tool_calls → 消息清除 → 下一个分析师
#
# 第二类：辩论阶段条件（should_continue_debate/risk_analysis）
#   用于控制辩论的轮数和发言顺序。
#   判断逻辑：检查辩论计数器是否达到上限
#     - 达到上限 → 返回裁判节点名（结束辩论，进入评判）
#     - 未达到上限 → 根据当前发言者决定下一个发言者
#   这实现了"牛熊交替辩论"和"三方循环辩论"：
#     牛 → 熊 → 牛 → 熊 → ... → 达到上限 → 研究经理
#     激进 → 保守 → 中性 → 激进 → ... → 达到上限 → 投资组合经理
#
# --- 概念 5：RecursionLimit 与辩论轮数控制的关系 ---
# LangGraph 有两种防止无限循环的机制：
#   1. RecursionLimit（在 propagation.py 中设置，默认 100 步）：
#      每经过一个节点算一步，步数超过限制时抛出 RecursionError。
#      这是全局的"兜底"安全机制。
#   2. 辩论轮数控制（在本文件中实现）：
#      通过 count 计数器精确控制辩论轮数。
#      max_debate_rounds 控制牛熊辩论轮数。
#      max_risk_discuss_rounds 控制三方辩论轮数。
#      这是局部的"精确"控制机制。
# 两者配合使用：正常情况下由轮数控制结束辩论，递归限制作为最后的兜底。
#
# --- 概念 6：Python 特有语法说明 ---
#
# a. state: AgentState（类型提示参数）
#    方法定义中的 state: AgentState 表示参数 state 的类型是 AgentState。
#    类型提示不影响运行，只是给人和 IDE 看的"说明书"。
#
# b. -> str（返回类型提示）
#    -> str 表示方法返回一个字符串。
#
# c. state["messages"]（字典取值）
#    从字典 state 中取键为 "messages" 的值。
#    state["messages"] 是一个消息列表。
#
# d. state["messages"][-1]（负索引）
#    [-1] 表示取列表的最后一个元素。
#    [-2] 表示倒数第二个，[-3] 表示倒数第三个，依此类推。
#    最后一条消息就是最新的消息（LLM 刚生成的回复）。
#
# e. if last_message.tool_calls:（真值判断）
#    tool_calls 是消息对象的一个属性。
#    如果 LLM 请求了工具调用，tool_calls 是一个非空列表（真值）。
#    如果 LLM 没有请求工具调用，tool_calls 是空列表 [] 或 None（假值）。
#    所以 if last_message.tool_calls: 等价于"如果 LLM 请求了工具调用"。
#
# f. .startswith("Bull")（字符串前缀判断）
#    "Bull: AAPL has strong growth...".startswith("Bull") → True
#    "Bear: P/E ratio is too high...".startswith("Bull") → False
#    用于判断当前发言者是牛方还是熊方。
#
# g. >= 比较运算符
#    count >= 2 * max_debate_rounds
#    当 count 达到 2 * max_debate_rounds 时，辩论结束。
#    乘以 2 是因为每轮辩论有 2 个代理发言（牛方 + 熊方）。
#
# =============================================================================
#
# 【文件说明】
# 这个模块定义了 LangGraph 图中"条件边"的判断逻辑。
# 条件边是 LangGraph 实现动态路由的关键机制——它根据当前状态
# 决定下一个要执行的节点是哪一个。
#
# 【条件函数的命名规范】
# 所有条件函数以 "should_continue_" 开头，返回目标节点名称。
# 这种命名风格清晰地表达了"是否应该继续"的语义。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
# AgentState：TypedDict，包含图执行中所有共享的状态数据
# AgentState 是一个带类型提示的字典，定义了以下字段：
#   messages, company_of_interest, trade_date, investment_debate_state,
#   risk_debate_state, market_report, sentiment_report, news_report,
#   fundamentals_report, trader_investment_plan, investment_plan, final_trade_decision
from tradingagents.agents.utils.agent_states import AgentState


# =============================================================================
# 类：ConditionalLogic
# =============================================================================
class ConditionalLogic:
    """
    处理图流控制的条件逻辑。

    【关键概念】
    每个条件函数都接收当前状态（AgentState），返回一个字符串。
    这个字符串在路由映射（route_map）中查找对应的目标节点。

    详见上方"前置知识-概念 2：条件边的路由机制"

    【配置参数】
    - max_debate_rounds：投资辩论（牛/熊方）的最大轮数
    - max_risk_discuss_rounds：风险分析辩论（三方）的最大轮数

    【运行时数据流示例（ConditionalLogic 的使用）】

    条件函数在图中被这样使用（在 setup.py 中）：

        workflow.add_conditional_edges(
            "Market Analyst",                              # 源节点
            conditional_logic.should_continue_market,       # 条件函数
            ["tools_market", "Msg Clear Market"],           # 路由映射
        )

    当 Market Analyst 节点执行完毕后，LangGraph 调用：
        conditional_logic.should_continue_market(state)

    state = {
        "messages": [HumanMessage("AAPL"), AIMessage(tool_calls=[...])],
        ...
    }

    条件函数检查 state["messages"][-1]（最后一条消息）：
        - 如果有 tool_calls → 返回 "tools_market" → 路由到工具节点
        - 如果没有 tool_calls → 返回 "Msg Clear Market" → 路由到消息清除节点
    """

    # =====================================================================
    # 以下是可视化的流程图（Mermaid）—— 三张决策树
    # 对应图解指南 3.1.2（分析师工具循环）、3.2.2（牛熊辩论轮转）、3.3.2（三方辩论轮转）
    # =====================================================================

    # 以下是可视化的流程图：分析师工具循环决策树（对应图解指南 3.1.2 节）
    """
```mermaid
flowchart TD
    A["分析师节点执行完毕<br/>LLM 输出 AIMessage"]
    B{"检查 last_message.tool_calls<br/>是否非空？"}
    C["返回 'tools_xxx'<br/>路由到 ToolNode"]
    D["返回 'Msg Clear xxx'<br/>路由到消息清除节点"]
    E["ToolNode 执行工具函数<br/>返回 ToolMessage"]
    F["回到分析师节点<br/>LLM 看到工具数据"]
    G["消息清除节点<br/>清除历史消息"]
    H["进入下一个分析师<br/>或进入辩论阶段"]

    A --> B
    B -->|"有 tool_calls（LLM 想要数据）"| C
    B -->|"无 tool_calls（分析完成）"| D
    C --> E
    E --> F
    F --> A
    D --> G
    G --> H

    style B fill:#F39C12,color:#fff
    style C fill:#E74C3C,color:#fff
    style D fill:#27AE60,color:#fff
```
以上是可视化的流程图：分析师工具循环决策树。
    """

    # 以下是可视化的流程图：牛熊辩论轮转决策树（对应图解指南 3.2.2 节）
    """
```mermaid
flowchart TD
    A["研究员节点执行完毕"]
    B{"count >= 2 × max_debate_rounds?<br/>已达辩论轮数上限？"}
    C["返回 'Research Manager'<br/>结束辩论，进入裁决"]
    D{"current_response<br/>以 'Bull' 开头？"}
    E["返回 'Bear Researcher'<br/>让空头回应"]
    F["返回 'Bull Researcher'<br/>让多头回应"]

    A --> B
    B -->|"是——辩论结束"| C
    B -->|"否——继续辩论"| D
    D -->|"是——多头刚发言"| E
    D -->|"否——空头刚发言"| F

    style B fill:#E74C3C,color:#fff
    style D fill:#3498DB,color:#fff
```
以上是可视化的流程图：牛熊辩论轮转决策树。
    """

    # 以下是可视化的流程图：三方辩论轮转决策树（对应图解指南 3.3.2 节）
    """
```mermaid
flowchart TD
    A["风险分析师节点执行完毕"]
    B{"count >= 3 × max_risk_discuss_rounds?<br/>已达讨论轮数上限？"}
    C["返回 'Portfolio Manager'<br/>结束讨论，最终决策"]
    D{"latest_speaker<br/>以 'Aggressive' 开头？"}
    E["返回 'Conservative Analyst'<br/>保守方回应"]
    F{"latest_speaker<br/>以 'Conservative' 开头？"}
    G["返回 'Neutral Analyst'<br/>中立方回应"]
    H["返回 'Aggressive Analyst'<br/>激进方发言（新一轮循环）"]

    A --> B
    B -->|"是"| C
    B -->|"否"| D
    D -->|"是"| E
    D -->|"否"| F
    F -->|"是"| G
    F -->|"否（Neutral或空）"| H

    style B fill:#E74C3C,color:#fff
    style D fill:#3498DB,color:#fff
    style F fill:#9B59B6,color:#fff
```
以上是可视化的流程图：三方辩论轮转决策树。
    """

    # -------------------------------------------------------------------------
    # 构造函数
    # -------------------------------------------------------------------------
    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """
        初始化条件逻辑。

        参数:
            max_debate_rounds (int): 最大辩论轮数。
                1 轮 = 牛方发言 + 熊方回应 = 2 次节点执行。
                因此 count 达到 2 * max_debate_rounds 时结束辩论。
                例如 max_debate_rounds=1，则最多 2 次发言（牛 1 次 + 熊 1 次）。
                例如 max_debate_rounds=2，则最多 4 次发言（牛 2 次 + 熊 2 次）。
            max_risk_discuss_rounds (int): 最大风险讨论轮数。
                1 轮 = 激进 + 保守 + 中性 = 3 次节点执行。
                因此 count 达到 3 * max_risk_discuss_rounds 时结束讨论。
                例如 max_risk_discuss_rounds=1，则最多 3 次发言。
                例如 max_risk_discuss_rounds=2，则最多 6 次发言。

        运行时数据流示例：
            conditional_logic = ConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1)
            # self.max_debate_rounds = 1
            # self.max_risk_discuss_rounds = 1

            # 后续在条件函数中使用：
            # should_continue_debate 中检查：count >= 2 * 1 = 2 → 结束辩论
            # should_continue_risk_analysis 中检查：count >= 3 * 1 = 3 → 结束讨论
        """
        # 保存辩论轮数限制到实例属性
        # 这些值后续在 should_continue_debate 和 should_continue_risk_analysis 中使用
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    # =========================================================================
    # 分析阶段条件函数
    # =========================================================================
    # 这些函数判断分析师是否已完成分析，还是需要继续调用工具获取数据。
    #
    # 【判断逻辑】
    # 1. 检查消息列表中的最后一条消息
    # 2. 如果最后一条消息包含 tool_calls（LLM 请求调用工具）：
    #    -> 返回工具节点名，让工具节点执行工具调用
    # 3. 如果最后一条消息不包含 tool_calls（LLM 已完成分析）：
    #    -> 返回消息清除节点名，进入下一个阶段
    #
    # 【tool_calls 是什么？】
    # 当 LLM 需要获取数据时，它不会直接调用函数，而是输出一个 tool_calls 结构。
    # LangGraph 的 ToolNode 会检测到这个结构，自动执行对应的工具函数。
    # 详见上方"前置知识-概念 3：tool_calls 是什么？"
    #
    # 【运行时数据流示例（分析师与工具的循环）】
    #
    # 第 1 次执行 Market Analyst：
    #   state["messages"] = [HumanMessage("AAPL")]
    #   → LLM 看到 "AAPL"，想要获取股价数据
    #   → LLM 输出 AIMessage(content="Let me check the data...",
    #                         tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}}])
    #   → should_continue_market 检查 tool_calls → 非空 → 返回 "tools_market"
    #
    # 执行 tools_market（ToolNode）：
    #   → ToolNode 调用 get_stock_data(ticker="AAPL")
    #   → 返回 ToolMessage(content="Date,Open,High,Low,Close,Volume\n2024-01-15,185.5,...")
    #   → 流程回到 Market Analyst
    #
    # 第 2 次执行 Market Analyst：
    #   state["messages"] = [HumanMessage, AIMessage(tool_calls=[...]), ToolMessage("...")]
    #   → LLM 看到 ToolMessage 中的股价数据，想要获取技术指标
    #   → LLM 输出 AIMessage(tool_calls=[{"name": "get_indicators", "args": {"ticker": "AAPL"}}])
    #   → should_continue_market 检查 tool_calls → 非空 → 返回 "tools_market"
    #
    # 执行 tools_market（ToolNode）：
    #   → ToolNode 调用 get_indicators(ticker="AAPL")
    #   → 返回 ToolMessage(content="RSI: 72.3, MACD: bullish crossover, ...")
    #   → 流程回到 Market Analyst
    #
    # 第 3 次执行 Market Analyst：
    #   state["messages"] = [HumanMessage, AIMessage, ToolMessage, AIMessage, ToolMessage]
    #   → LLM 有了足够的数据，不再请求工具调用
    #   → LLM 输出 AIMessage(content="Based on the data, RSI at 72.3 indicates overbought...",
    #                         tool_calls=[])  # 注意：tool_calls 为空
    #   → should_continue_market 检查 tool_calls → 为空 → 返回 "Msg Clear Market"
    #   → 流程进入消息清除节点，准备下一个分析师
    # =========================================================================

    def should_continue_market(self, state: AgentState):
        """
        判断市场分析是否应该继续。

        【流程】
        1. 市场分析师调用 LLM 进行分析
        2. 如果 LLM 需要数据（如股价、技术指标），它会输出 tool_calls
        3. 此时条件函数返回 "tools_market"，路由到工具节点
        4. 工具节点获取数据后，返回给市场分析师继续分析
        5. 如果 LLM 不需要更多数据（分析完成），返回 "Msg Clear Market"

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "tools_market"（继续获取数据）或 "Msg Clear Market"（分析完成）

        运行时数据流示例：
            # 假设当前状态：
            state = {
                "messages": [
                    HumanMessage("AAPL"),
                    AIMessage(content="Let me check the stock data...",
                              tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}}]),
                ],
                "company_of_interest": "AAPL",
                ...
            }

            # 调用条件函数：
            result = should_continue_market(state)

            # 内部执行：
            # messages = state["messages"]  # 取出消息列表
            # last_message = messages[-1]   # 取最后一条消息（AIMessage）
            # last_message.tool_calls = [{"name": "get_stock_data", ...}]  # 非空
            # → 返回 "tools_market"

            # LangGraph 根据返回值 "tools_market" 路由到 tools_market 节点

            # ----

            # 另一种情况（分析完成时）：
            state = {
                "messages": [
                    HumanMessage("AAPL"),
                    AIMessage(tool_calls=[...]),  # 之前请求过工具
                    ToolMessage("RSI: 72.3, MACD: ..."),  # 工具返回的数据
                    AIMessage(content="Based on the data, RSI at 72.3...",
                              tool_calls=[]),  # 注意：tool_calls 为空
                ],
                ...
            }

            result = should_continue_market(state)
            # last_message = messages[-1]  # 最后一条 AIMessage
            # last_message.tool_calls = []  # 空列表（假值）
            # → 返回 "Msg Clear Market"
            # → LangGraph 路由到消息清除节点
        """
        # 获取消息列表
        # state["messages"] 是一个包含所有对话消息的列表
        # 消息类型包括：HumanMessage, AIMessage, ToolMessage 等
        messages = state["messages"]
        # 获取最后一条消息（最新消息）
        # Python 语法说明：[-1] 表示取列表的最后一个元素
        # 最后一条消息是 LLM 最新的回复（AIMessage）
        last_message = messages[-1]

        # 检查最后一条消息是否包含工具调用请求
        # tool_calls 是 LLM 输出的一个属性，表示 LLM 想调用哪些工具
        # Python 语法说明：if last_message.tool_calls:
        # 空列表 [] 和 None 是"假值"，非空列表是"真值"
        # 所以 if last_message.tool_calls: 等价于"如果 LLM 请求了工具调用"
        if last_message.tool_calls:
            # 有工具调用：路由到工具节点，执行工具调用
            return "tools_market"
        # 没有工具调用：分析完成，路由到消息清除节点
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """
        判断社交媒体分析是否应该继续。

        逻辑与 should_continue_market 相同，但针对社交媒体分析师。
        当社交媒体分析师的 LLM 请求调用工具（如 get_news）时，
        返回 "tools_social" 让 ToolNode 执行工具调用。
        当分析完成时，返回 "Msg Clear Social" 进入消息清除。

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "tools_social" 或 "Msg Clear Social"

        运行时数据流示例：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="Let me check social media sentiment...",
                              tool_calls=[{"name": "get_news", "args": {"ticker": "AAPL"}}]),
                ],
                ...
            }
            result = should_continue_social(state)
            # → 返回 "tools_social"（因为 tool_calls 非空）

            # 分析完成时：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="Social sentiment is 78% positive...",
                              tool_calls=[]),
                ],
                ...
            }
            result = should_continue_social(state)
            # → 返回 "Msg Clear Social"（因为 tool_calls 为空）
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_social"
        return "Msg Clear Social"

    def should_continue_news(self, state: AgentState):
        """
        判断新闻分析是否应该继续。

        逻辑与 should_continue_market 相同，但针对新闻分析师。
        当新闻分析师的 LLM 请求调用工具（如 get_news, get_global_news,
        get_insider_transactions）时，返回 "tools_news" 让 ToolNode 执行工具调用。
        当分析完成时，返回 "Msg Clear News" 进入消息清除。

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "tools_news" 或 "Msg Clear News"

        运行时数据流示例：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="Let me check insider transactions...",
                              tool_calls=[{"name": "get_insider_transactions", "args": {"ticker": "AAPL"}}]),
                ],
                ...
            }
            result = should_continue_news(state)
            # → 返回 "tools_news"（因为 tool_calls 非空）

            # 分析完成时：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="CEO Tim Cook sold 50,000 shares...",
                              tool_calls=[]),
                ],
                ...
            }
            result = should_continue_news(state)
            # → 返回 "Msg Clear News"（因为 tool_calls 为空）
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_news"
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """
        判断基本面分析是否应该继续。

        逻辑与 should_continue_market 相同，但针对基本面分析师。
        当基本面分析师的 LLM 请求调用工具（如 get_fundamentals,
        get_balance_sheet, get_cashflow, get_income_statement）时，
        返回 "tools_fundamentals" 让 ToolNode 执行工具调用。
        当分析完成时，返回 "Msg Clear Fundamentals" 进入消息清除。

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "tools_fundamentals" 或 "Msg Clear Fundamentals"

        运行时数据流示例：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="Let me check the balance sheet...",
                              tool_calls=[{"name": "get_balance_sheet", "args": {"ticker": "AAPL"}}]),
                ],
                ...
            }
            result = should_continue_fundamentals(state)
            # → 返回 "tools_fundamentals"（因为 tool_calls 非空）

            # 分析完成时：
            state = {
                "messages": [
                    ...,
                    AIMessage(content="Total assets: $352B, P/E ratio: 28.5...",
                              tool_calls=[]),
                ],
                ...
            }
            result = should_continue_fundamentals(state)
            # → 返回 "Msg Clear Fundamentals"（因为 tool_calls 为空）
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_fundamentals"
        return "Msg Clear Fundamentals"

    # =========================================================================
    # 辩论阶段条件函数
    # =========================================================================

    def should_continue_debate(self, state: AgentState) -> str:
        """
        判断投资辩论（牛/熊方）是否应该继续。

        【辩论机制】
        牛市研究员和熊市研究员交替发言，形成辩论。
        每个研究员根据对方的论点进行回应。

        发言顺序：Bull → Bear → Bull → Bear → ... （交替）

        【终止条件】
        当 count >= 2 * max_debate_rounds 时结束辩论。
        - 2 倍是因为每轮辩论有 2 个代理发言（牛方 + 熊方）
        - 例如 max_debate_rounds=1，则 count 达到 2 时结束（牛 1 次 + 熊 1 次）
        - 例如 max_debate_rounds=2，则 count 达到 4 时结束（牛 2 次 + 熊 2 次）

        【路由逻辑】
        1. 如果辩论轮数达到上限 -> "Research Manager"（结束辩论，进入研究经理）
        2. 如果当前发言以 "Bull" 开头 -> "Bear Researcher"（让熊方回应）
        3. 否则（当前发言以 "Bear" 开头）-> "Bull Researcher"（让牛方回应）

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "Research Manager"（结束辩论）, "Bear Researcher" 或 "Bull Researcher"

        运行时数据流示例（完整辩论过程，假设 max_debate_rounds=1）：

            # 初始状态：count=0, current_response=""

            # 第 1 步：Bull Researcher 执行
            # LLM 生成看多分析：
            #   "Bull: AAPL shows strong revenue growth of 15% YoY..."
            # 更新状态：
            #   investment_debate_state["bull_history"] = "Bull: AAPL shows..."
            #   investment_debate_state["current_response"] = "Bull: AAPL shows..."
            #   investment_debate_state["count"] = 1

            # 条件函数检查：
            state["investment_debate_state"]["count"] = 1
            # 1 >= 2*1 = 2? 否，继续辩论
            state["investment_debate_state"]["current_response"] = "Bull: AAPL shows..."
            # .startswith("Bull")? 是 → 返回 "Bear Researcher"
            # → 路由到 Bear Researcher

            # 第 2 步：Bear Researcher 执行
            # LLM 生成看空分析（针对牛方论点回应）：
            #   "Bear: But P/E ratio of 28.5 is above industry average..."
            # 更新状态：
            #   investment_debate_state["bear_history"] = "Bear: But P/E..."
            #   investment_debate_state["current_response"] = "Bear: But P/E..."
            #   investment_debate_state["count"] = 2

            # 条件函数检查：
            state["investment_debate_state"]["count"] = 2
            # 2 >= 2*1 = 2? 是，辩论结束
            # → 返回 "Research Manager"
            # → 路由到 Research Manager（研究经理综合辩论结果）

            # 第 3 步：Research Manager 执行
            # LLM 综合牛熊论点，做出投资判断
        """
        # 检查辩论轮数是否达到上限
        # count >= 2 * max_debate_rounds 意味着已经进行了足够的辩论轮数
        # Python 语法说明：state["investment_debate_state"]["count"]
        #   这是嵌套字典取值：先从 state 取 "investment_debate_state"（一个字典），
        #   再从中取 "count"（一个整数）。
        #   例如 state["investment_debate_state"]["count"] = 2
        #   2 >= 2 * 1 = 2 → True → 结束辩论
        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            # 达到上限，结束辩论，路由到研究经理
            return "Research Manager"

        # 检查当前发言者，决定下一个发言者
        # current_response 以 "Bull" 或 "Bear" 开头，标识发言者
        # Python 语法说明：.startswith("Bull") 判断字符串是否以 "Bull" 开头
        #   "Bull: AAPL has strong growth...".startswith("Bull") → True
        #   "Bear: P/E ratio is too high...".startswith("Bull") → False
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            # 牛方刚发言完毕 -> 让熊方回应
            return "Bear Researcher"
        # 熊方刚发言完毕 -> 让牛方回应
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """
        判断风险分析辩论（三方）是否应该继续。

        【三方辩论机制】
        激进、保守、中性三个分析师轮流发言，讨论风险。
        发言顺序：激进 -> 保守 -> 中性 -> 激进 -> ...（循环）

        【终止条件】
        当 count >= 3 * max_risk_discuss_rounds 时结束讨论。
        - 3 倍是因为每轮有 3 个代理发言
        - 例如 max_risk_discuss_rounds=1，则 count 达到 3 时结束
        - 例如 max_risk_discuss_rounds=2，则 count 达到 6 时结束

        【路由逻辑】
        1. 如果讨论轮数达到上限 -> "Portfolio Manager"（结束讨论，最终决策）
        2. 如果最新发言者以 "Aggressive" 开头 -> "Conservative Analyst"（保守方回应）
        3. 如果最新发言者以 "Conservative" 开头 -> "Neutral Analyst"（中性方回应）
        4. 否则 -> "Aggressive Analyst"（激进方发言，完成一轮循环）

        参数:
            state (AgentState): 当前图状态

        返回:
            str: "Portfolio Manager"（结束讨论）, "Conservative Analyst",
                 "Neutral Analyst" 或 "Aggressive Analyst"

        运行时数据流示例（完整三方辩论过程，假设 max_risk_discuss_rounds=1）：

            # 初始状态：count=0, latest_speaker=""

            # 第 1 步：Aggressive Analyst 执行
            # LLM 生成激进风险分析：
            #   "Aggressive: Given strong fundamentals, leverage up and buy 200 more shares..."
            # 更新状态：
            #   risk_debate_state["aggressive_history"] = "Aggressive: ..."
            #   risk_debate_state["latest_speaker"] = "Aggressive"
            #   risk_debate_state["count"] = 1

            # 条件函数检查：
            state["risk_debate_state"]["count"] = 1
            # 1 >= 3*1 = 3? 否，继续讨论
            state["risk_debate_state"]["latest_speaker"] = "Aggressive"
            # .startswith("Aggressive")? 是 → 返回 "Conservative Analyst"
            # → 路由到 Conservative Analyst

            # 第 2 步：Conservative Analyst 执行
            # LLM 生成保守风险分析：
            #   "Conservative: Reduce position by 30%, set stop loss at $175..."
            # 更新状态：
            #   risk_debate_state["conservative_history"] = "Conservative: ..."
            #   risk_debate_state["latest_speaker"] = "Conservative"
            #   risk_debate_state["count"] = 2

            # 条件函数检查：
            state["risk_debate_state"]["count"] = 2
            # 2 >= 3? 否，继续讨论
            state["risk_debate_state"]["latest_speaker"] = "Conservative"
            # .startswith("Aggressive")? 否
            # .startswith("Conservative")? 是 → 返回 "Neutral Analyst"
            # → 路由到 Neutral Analyst

            # 第 3 步：Neutral Analyst 执行
            # LLM 生成中性风险分析：
            #   "Neutral: Maintain current position, monitor for changes..."
            # 更新状态：
            #   risk_debate_state["neutral_history"] = "Neutral: ..."
            #   risk_debate_state["latest_speaker"] = "Neutral"
            #   risk_debate_state["count"] = 3

            # 条件函数检查：
            state["risk_debate_state"]["count"] = 3
            # 3 >= 3*1 = 3? 是，讨论结束
            # → 返回 "Portfolio Manager"
            # → 路由到 Portfolio Manager（投资组合经理综合三方观点，做出最终决策）

            # 第 4 步：Portfolio Manager 执行
            # LLM 综合三方辩论，做出最终交易决策
            #   "Based on balanced risk perspectives, BUY with 60% position size..."
        """
        # 检查讨论轮数是否达到上限
        # count >= 3 * max_risk_discuss_rounds 意味着已经进行了足够的讨论轮数
        # Python 语法说明：state["risk_debate_state"]["count"]
        #   嵌套字典取值：先取 risk_debate_state，再取 count
        #   例如 state["risk_debate_state"]["count"] = 3
        #   3 >= 3 * 1 = 3 → True → 结束讨论
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            # 达到上限，结束讨论，路由到投资组合经理
            return "Portfolio Manager"

        # 根据最新发言者决定下一个发言者
        # latest_speaker 以 "Aggressive"/"Conservative"/"Neutral" 开头，标识发言者
        # Python 语法说明：.startswith("Aggressive") 判断字符串是否以 "Aggressive" 开头
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            # 激进方刚发言 -> 保守方回应
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            # 保守方刚发言 -> 中性方回应
            return "Neutral Analyst"
        # 中性方刚发言（或初始状态 latest_speaker 为空）-> 激进方发言，开始新一轮
        # 注意：当 latest_speaker 为空字符串 "" 时，既不以 "Aggressive" 也不以 "Conservative" 开头，
        # 所以会走到这里返回 "Aggressive Analyst"（这是辩论的起点）
        return "Aggressive Analyst"
