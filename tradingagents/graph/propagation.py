# =============================================================================
# TradingAgents/graph/propagation.py
# =============================================================================
# 模块：Propagator - 图的初始状态创建与参数配置
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件涉及的核心概念，如果你完全没有 Python 和 LangGraph 经验，请先阅读以下说明。
#
# --- 概念 1：什么是"状态"（State）？ ---
# 在 LangGraph 中，状态是图中所有节点共享的数据字典。
# 你可以把它想象成一条流水线上的"产品"——产品从第一个工位开始，
# 每经过一个工位就被加工一次（添加新数据或修改现有数据），最后成为成品。
#
# 状态就是一个普通的 Python 字典，例如：
#   state = {
#       "messages": [HumanMessage("AAPL"), AIMessage("分析...")],
#       "company_of_interest": "AAPL",
#       "trade_date": "2024-01-15",
#       "market_report": "RSI: 72.3...",
#       ...
#   }
# 每个节点都能读取这个字典中的数据，也能添加或修改其中的字段。
#
# --- 概念 2：什么是"初始状态"（Initial State）？ ---
# 在图开始执行之前，需要准备一个"初始状态"——即流水线上第一个产品。
# 初始状态包含了图执行所需的最基本数据：
#   - 要分析哪只股票？（company_of_interest）
#   - 分析哪个交易日？（trade_date）
#   - 初始消息（messages，用来触发第一个分析师开始工作）
#   - 空的报告字段（等分析师填充）
#   - 空的辩论状态（等研究员填充）
#
# --- 概念 3：什么是"传播"（Propagation）？ ---
# "传播"指的是状态在图中的流动。
# 本类负责准备初始状态，然后由 LangGraph 引擎自动将状态"传播"到各个节点：
#   初始状态 → 节点1（加工）→ 节点2（加工）→ ... → 最终状态
# 每经过一个节点，状态都会被更新（添加新字段或修改现有字段）。
#
# --- 概念 4：RecursionLimit（递归限制） ---
# LangGraph 图中可能存在循环（如：分析师→工具→分析师→工具→...）。
# 如果不限制循环次数，图可能陷入无限循环，永远跑不完。
# RecursionLimit 是安全机制：图执行的"步数"（每经过一个节点算一步）超过此限制时，
# LangGraph 抛出 RecursionError，强制停止执行。
# 这就像给流水线设了一个"最大加工次数"，防止卡在循环里出不来。
#
# 为什么需要递归限制？
#   - 分析师和工具之间有循环（分析师→工具→分析师→...），如果 LLM 一直请求工具，会无限循环。
#   - 牛熊辩论有循环（牛→熊→牛→...），通过 max_debate_rounds 控制，但递归限制是额外的安全保障。
#   - 风险辩论也有循环（激进→保守→中性→激进→...）。
#   - 递归限制是"兜底"安全机制——即使辩论轮数控制出了 bug，也不会无限循环。
#
# --- 概念 5：stream_mode 参数 ---
# stream_mode 控制 stream() 方法返回的数据格式：
#   - "values"：返回每个节点执行后的完整状态（快照）。
#     例如：节点1 执行后返回 {"messages": [...], "market_report": "...", ...}
#          节点2 执行后返回 {"messages": [...], "market_report": "...", "sentiment_report": "...", ...}
#   - "updates"：只返回增量更新（只返回被修改的字段）。
#     例如：节点2 执行后返回 {"sentiment_report": "..."}（只有新增/修改的字段）
# 本系统使用 "values" 模式，因为需要完整状态来做日志记录和反思。
#
# --- 概念 6：AgentState 结构 ---
# AgentState 是一个 TypedDict（带类型提示的字典），定义了图中所有节点共享的状态结构。
# 它包含以下字段：
#   - messages: 消息列表（LLM 对话历史），包括 HumanMessage、AIMessage、ToolMessage 等
#   - company_of_interest: 目标公司/股票代码（如 "AAPL"）
#   - trade_date: 交易日期（如 "2024-01-15"）
#   - investment_debate_state: 投资辩论子状态（嵌套的 InvestDebateState）
#   - risk_debate_state: 风险辩论子状态（嵌套的 RiskDebateState）
#   - market_report: 市场分析报告（由市场分析师填充）
#   - fundamentals_report: 基本面分析报告（由基本面分析师填充）
#   - sentiment_report: 情绪分析报告（由社交媒体分析师填充）
#   - news_report: 新闻分析报告（由新闻分析师填充）
#   - trader_investment_plan: 交易员投资计划
#   - investment_plan: 最终投资计划
#   - final_trade_decision: 最终交易决策
#
# --- 概念 7：InvestDebateState 结构 ---
# InvestDebateState 是投资辩论的子状态，跟踪牛/熊方辩论进度：
#   - bull_history: 牛市方发言历史
#   - bear_history: 熊市方发言历史
#   - history: 完整辩论历史
#   - current_response: 当前最新发言（以 "Bull" 或 "Bear" 开头，用于条件函数判断下一个发言者）
#   - judge_decision: 裁判（研究经理）的决策
#   - count: 辩论轮数计数器（每次发言 +1，达到 2*max_debate_rounds 时结束）
#
# --- 概念 8：RiskDebateState 结构 ---
# RiskDebateState 是风险辩论的子状态，跟踪激进/保守/中性方辩论进度：
#   - aggressive_history: 激进方发言历史
#   - conservative_history: 保守方发言历史
#   - neutral_history: 中性方发言历史
#   - history: 完整辩论历史
#   - latest_speaker: 最新发言者（以 "Aggressive"/"Conservative"/"Neutral" 开头）
#   - current_aggressive_response: 激进方当前发言
#   - current_conservative_response: 保守方当前发言
#   - current_neutral_response: 中性方当前发言
#   - judge_decision: 裁判（投资组合经理）的决策
#   - count: 辩论轮数计数器（每次发言 +1，达到 3*max_risk_discuss_rounds 时结束）
#
# --- 概念 9：Python 特有语法说明 ---
#
# a. TypedDict（带类型提示的字典）
#    TypedDict 是 Python typing 模块提供的工具，它让字典像结构体一样有明确的字段定义。
#    InvestDebateState({...}) 虽然看起来像构造函数调用，但实际上 TypedDict 本质就是字典，
#    这行代码相当于创建一个普通的字典，只是 IDE 会根据 TypedDict 的定义做类型检查。
#
# b. -> Dict[str, Any]（返回类型提示）
#    方法定义中的 -> 表示返回值类型。
#    -> Dict[str, Any] 表示返回一个键为字符串、值为任意类型的字典。
#    类型提示不影响运行，只是给人和 IDE 看的"说明书"。
#
# c. str(trade_date)（类型转换）
#    把任意值转为字符串。例如 str(2024-01-15) → "2024-01-15"。
#    确保 trade_date 始终是字符串类型，避免类型不一致的问题。
#
# d. ("human", company_name)（元组/Tuple）
#    元组是 Python 的一种不可变序列，用圆括号 () 表示。
#    ("human", "AAPL") 是一个包含两个元素的元组。
#    在 LangChain 中，消息可以用元组格式表示：("角色", "内容")。
#
# =============================================================================
#
# 【文件说明】
# 这个模块负责图的"传播"——即准备图执行所需的一切：
# 1. 创建初始状态（initial state）：定义图中所有节点共享的数据结构
# 2. 提供图执行参数（graph args）：控制执行行为（如递归限制）
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
from typing import Any  # 类型提示

# Dict：字典类型
# Any：任意类型
# List：列表类型
# Optional：可选类型（可以是该类型，也可以是 None）
# 导入代理状态数据类
# AgentState, InvestDebateState, RiskDebateState 都是 TypedDict（带类型提示的字典）
from tradingagents.agents.utils.agent_states import (
    InvestDebateState,  # 投资辩论子状态类型
    # 详见上方"前置知识-概念 7：InvestDebateState 结构"
    RiskDebateState,  # 风险辩论子状态类型
    # 详见上方"前置知识-概念 8：RiskDebateState 结构"
)


# =============================================================================
# 类：Propagator
# =============================================================================
class Propagator:
    """
    处理状态的初始化和图的传播。

    【职责】
    1. 创建初始状态字典：为图执行准备起始数据
    2. 提供图执行参数：如递归限制（防止无限循环）

    【为什么叫"传播器"？】
    "传播"（propagation）指的是状态在图中的流动。
    这个类负责准备初始状态，然后由 LangGraph 引擎自动将其"传播"到各个节点。

    【运行时数据流示例（Propagator 的使用）】
        propagator = Propagator(max_recur_limit=100)

        # 创建初始状态
        init_state = propagator.create_initial_state("AAPL", "2024-01-15")
        # init_state = {
        #     "messages": [("human", "AAPL")],
        #     "company_of_interest": "AAPL",
        #     "trade_date": "2024-01-15",
        #     "investment_debate_state": {"bull_history": "", "count": 0, ...},
        #     "risk_debate_state": {"aggressive_history": "", "count": 0, ...},
        #     "market_report": "",
        #     "sentiment_report": "",
        #     "news_report": "",
        #     "fundamentals_report": "",
        # }

        # 获取执行参数
        args = propagator.get_graph_args()
        # args = {
        #     "stream_mode": "values",
        #     "config": {"recursion_limit": 100},
        # }

        # 执行图
        final_state = graph.invoke(init_state, **args)
    """

    # -------------------------------------------------------------------------
    # 构造函数
    # -------------------------------------------------------------------------
    def __init__(self, max_recur_limit=100):
        """
        初始化传播器。

        参数:
            max_recur_limit (int): 最大递归限制。
                这是 LangGraph 的安全机制：
                如果图执行超过此限制（如无限循环），LangGraph 会抛出 RecursionError。
                默认值 100 足够处理正常的分析流程（分析 -> 辩论 -> 决策）。

        详见上方"前置知识-概念 4：RecursionLimit"

        运行时数据流示例：
            propagator = Propagator()  # 使用默认值 100
            # 或
            propagator = Propagator(max_recur_limit=200)  # 自定义限制

            # max_recur_limit 后续在 get_graph_args 中被包装为：
            # config = {"recursion_limit": 100}
            # 传给 graph.invoke(state, config={"recursion_limit": 100})
        """
        # 保存递归限制到实例属性
        # 后续 get_graph_args 会读取这个值
        self.max_recur_limit = max_recur_limit

    # -------------------------------------------------------------------------
    # 核心方法：创建初始状态
    # -------------------------------------------------------------------------
    def create_initial_state(
        self, company_name: str, trade_date: str, horizon: str = "medium"
    ) -> dict[str, Any]:
        """
        创建代理图的初始状态。

        【状态结构说明】
        返回的字典包含图执行所需的全部初始字段：
        - messages：初始化为包含一个人类消息（公司名），触发第一个分析师
        - company_of_interest：目标公司名
        - trade_date：交易日期
        - investment_debate_state：投资辩论初始状态（所有字段为空，count=0）
        - risk_debate_state：风险辩论初始状态（所有字段为空，count=0）
        - 各种报告字段：初始化为空字符串，由各分析师填充

        【为什么 messages 初始化为 [("human", company_name)]？】
        LangGraph 的 LLM 节点使用消息列表作为对话上下文。
        初始消息是 "human" 角色发送的公司名，这会触发第一个分析师节点开始工作。
        分析师节点会看到这条消息，然后调用工具获取数据并进行分析。

        在 LangChain 中，消息可以用元组格式表示：("角色", "内容")。
        常见的角色有：
        - "human" / "user"：用户消息（来自人类）
        - "ai" / "assistant"：AI 消息（来自 LLM）
        - "system"：系统消息（设定 LLM 的行为）
        - "tool"：工具消息（工具执行的结果）

        参数:
            company_name (str): 股票代码或公司名称（如 "AAPL"）
            trade_date (str): 交易日期字符串（如 "2024-01-15"）

        返回:
            dict: 初始状态字典，包含所有 AgentState 所需字段

        运行时数据流示例（创建初始状态后）：

            init_state = propagator.create_initial_state("AAPL", "2024-01-15")

            # 返回的初始状态：
            init_state = {
                # 消息列表：只有一个初始消息
                "messages": [("human", "AAPL")],
                # 这相当于用户说："帮我分析 AAPL"
                # 第一个分析师（Market Analyst）会看到这条消息，然后开始工作

                # 目标公司
                "company_of_interest": "AAPL",

                # 交易日期（转为字符串确保格式一致）
                "trade_date": "2024-01-15",

                # 投资辩论初始状态（全部为空，等待牛/熊研究员填充）
                "investment_debate_state": {
                    "bull_history": "",       # 牛市方还没有发言
                    "bear_history": "",       # 熊市方还没有发言
                    "history": "",            # 还没有辩论记录
                    "current_response": "",   # 还没有当前发言
                    "judge_decision": "",     # 裁判还没有决策
                    "count": 0,               # 辩论计数器从 0 开始
                },

                # 风险辩论初始状态（全部为空，等待激进/保守/中性分析师填充）
                "risk_debate_state": {
                    "aggressive_history": "",           # 激进方还没发言
                    "conservative_history": "",         # 保守方还没发言
                    "neutral_history": "",              # 中性方还没发言
                    "history": "",                      # 还没有讨论记录
                    "latest_speaker": "",               # 还没有发言者
                    "current_aggressive_response": "",  # 激进方当前发言
                    "current_conservative_response": "", # 保守方当前发言
                    "current_neutral_response": "",     # 中性方当前发言
                    "judge_decision": "",               # 裁判还没决策
                    "count": 0,                         # 讨论计数器从 0 开始
                },

                # 分析报告字段（全部为空，等待各分析师填充）
                "market_report": "",         # 市场分析报告（由 Market Analyst 填充）
                "fundamentals_report": "",   # 基本面分析报告（由 Fundamentals Analyst 填充）
                "sentiment_report": "",      # 情绪分析报告（由 Social Analyst 填充）
                "news_report": "",           # 新闻分析报告（由 News Analyst 填充）
            }

            # 图执行过程中，各节点会逐步填充这些字段：
            # Market Analyst 执行后：init_state["market_report"] = "RSI: 72.3, MACD: ..."
            # Social Analyst 执行后：init_state["sentiment_report"] = "Sentiment: 78% positive..."
            # Bull Researcher 执行后：init_state["investment_debate_state"]["bull_history"] = "Bull: ..."
            # ...
            # Portfolio Manager 执行后：init_state["final_trade_decision"] = "BUY AAPL..."
        """
        return {
            # 消息列表：初始化为一个元组 ("human", company_name)
            # "human" 是 LangChain 中的消息角色，表示来自用户的消息
            # 这相当于用户说："帮我分析 AAPL"
            # Python 语法说明：("human", "AAPL") 是一个元组（Tuple），
            #   元组是 Python 的不可变序列，用圆括号 () 表示。
            #   在 LangChain 中，消息可以用元组格式表示：("角色", "内容")
            "messages": [("human", company_name)],

            # 目标公司名称
            "company_of_interest": company_name,

            # 交易日期（转为字符串确保格式一致）
            # Python 语法说明：str(trade_date) 把任意值转为字符串
            #   如果 trade_date 已经是字符串，str() 不会改变它
            #   如果 trade_date 是 date 对象（如 date(2024,1,15)），str() 会转为 "2024-01-15"
            "trade_date": str(trade_date),

            # 分析视角：short（1-2周）/ medium（1-3月）/ long（6月以上）
            "analysis_horizon": str(horizon or "medium"),

            # 投资辩论子状态：初始化为空的 InvestDebateState
            # InvestDebateState 是一个 TypedDict，这里传入一个字典来初始化
            # Python 语法说明：InvestDebateState({...}) 看起来像构造函数调用，
            #   但 TypedDict 本质就是字典，这行代码相当于创建一个普通字典
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",       # 牛市方发言历史（初始为空）
                    "bear_history": "",       # 熊市方发言历史（初始为空）
                    "history": "",            # 完整辩论历史（初始为空）
                    "current_response": "",   # 当前最新发言（初始为空）
                    "judge_decision": "",     # 裁判决策（初始为空）
                    "count": 0,               # 辩论轮数计数器（初始为 0）
                }
            ),

            # 风险辩论子状态：初始化为空的 RiskDebateState
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",           # 激进方发言历史
                    "conservative_history": "",         # 保守方发言历史
                    "neutral_history": "",              # 中性方发言历史
                    "history": "",                      # 完整辩论历史
                    "latest_speaker": "",               # 最新发言者
                    "current_aggressive_response": "",  # 激进方当前发言
                    "current_conservative_response": "", # 保守方当前发言
                    "current_neutral_response": "",     # 中性方当前发言
                    "judge_decision": "",               # 裁判决策
                    "count": 0,                         # 辩论轮数计数器
                }
            ),

            # 分析报告字段：由各分析师在运行时填充
            "market_report": "",         # 市场分析报告（技术面）
            "strategy_report": "",       # 策略扫描报告
            "strategy_result": {},       # 策略扫描结构化结果
            "fundamentals_report": "",   # 基本面分析报告
            "sentiment_report": "",      # 情绪分析报告（社交媒体）
            "news_report": "",           # 新闻分析报告

            # 决策链字段：必须预先初始化，否则在自定义精简流程中
            # （例如跳过多空辩论/交易员节点）直接索引会抛 KeyError。
            "investment_plan": "",           # 研究经理投资计划
            "trader_investment_plan": "",    # 交易员执行方案
            "final_trade_decision": "",      # 组合经理最终决策
        }

    # -------------------------------------------------------------------------
    # 获取图执行参数
    # -------------------------------------------------------------------------
    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """
        获取图调用（invoke/stream）所需的参数。

        【参数说明】
        - recursion_limit：LangGraph 的递归限制。
          如果图中存在循环（如分析师 <-> 工具节点的循环），
          LangGraph 会跟踪执行步数，超过此限制时抛出错误。
          这是防止无限循环的安全机制。
          详见上方"前置知识-概念 4：RecursionLimit"

        - stream_mode："values" 模式表示 stream() 返回每个节点的完整状态。
          其他模式包括 "updates"（只返回增量更新）等。
          详见上方"前置知识-概念 5：stream_mode 参数"

        - callbacks：回调处理器列表，用于监控工具执行。
          注意：LLM 的回调是通过 LLM 构造函数单独处理的，不在这里。

        参数:
            callbacks: 可选的回调处理器列表，用于工具执行追踪

        返回:
            dict: 图执行参数，包含 stream_mode 和 config

        运行时数据流示例：

            args = propagator.get_graph_args()
            # args = {
            #     "stream_mode": "values",
            #     "config": {"recursion_limit": 100},
            # }

            # 后续使用：
            # final_state = graph.invoke(init_state, **args)
            # **args 展开为：stream_mode="values", config={"recursion_limit": 100}
            #
            # 或（调试模式）：
            # for chunk in graph.stream(init_state, **args):
            #     # 每次 chunk 是一个节点执行后的完整状态
            #     print(chunk)

            # 带回调的示例：
            args_with_callbacks = propagator.get_graph_args(callbacks=[my_handler])
            # args_with_callbacks = {
            #     "stream_mode": "values",
            #     "config": {
            #         "recursion_limit": 100,
            #         "callbacks": [my_handler],
            #     },
            # }
        """
        # 构建配置字典
        # recursion_limit 是递归限制，防止图陷入无限循环
        # 值来自 self.max_recur_limit（在构造函数中设置，默认 100）
        config = {"recursion_limit": self.max_recur_limit}

        # 如果提供了回调处理器，添加到配置中
        # Python 语法说明：if callbacks:
        # 空列表 [] 和 None 都是"假值"，非空列表是"真值"。
        # 所以 if callbacks: 等价于 if callbacks is not None and len(callbacks) > 0:
        if callbacks:
            config["callbacks"] = callbacks

        # 返回完整参数
        # 这个返回值会被 **args 展开后传给 graph.invoke() 或 graph.stream()
        return {
            "stream_mode": "values",  # stream 模式：返回完整状态值
            "config": config,         # 配置字典（包含递归限制和可选回调）
        }
