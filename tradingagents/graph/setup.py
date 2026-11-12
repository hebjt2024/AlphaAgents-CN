# =============================================================================
# TradingAgents/graph/setup.py
# =============================================================================
# 模块：GraphSetup - LangGraph 工作流图的搭建与配置
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件是整个系统中最重要的文件之一——它定义了交易代理的完整工作流图。
# 基于 LangGraph 的 StateGraph，将多个 AI 代理组织成有序的流水线。
# 如果你完全没有 LangGraph 经验，请先阅读以下说明。
#
# --- 概念 1：StateGraph 是什么？ ---
# StateGraph 是 LangGraph 的核心类，用于构建"有状态的有向图"。
# 你可以把它想象成一张流程图：
#   - 图上有多个"方框"（节点 Node），每个方框里有一个 AI 代理在工作。
#   - 方框之间用"箭头"（边 Edge）连接，箭头表示数据流动的方向。
#   - 图有一个"入口"（START）和一个"出口"（END）。
#   - 所有方框共享同一个"数据表"（状态 State），每个方框可以读取和修改这个表。
#
# --- 概念 2：StateGraph 的完整生命周期（本文件的核心） ---
# 一个 LangGraph 工作流图从无到有，经历以下步骤（全部在本文件的 setup_graph 方法中完成）：
#
#   步骤 1 - 创建图：
#       workflow = StateGraph(AgentState)
#       创建一个空的有向图。AgentState 是一个 TypedDict（带类型提示的字典），
#       定义了图中所有节点共享的数据结构。
#       比喻：这就像创建了一张空白的流程图纸，AgentState 是流程图上的数据表格格式。
#
#   步骤 2 - 添加节点（add_node）：
#       workflow.add_node("节点名", 处理函数)
#       向图中添加一个个处理单元。每个节点有一个名字（字符串）和一个处理函数。
#       处理函数接收当前状态，执行某些操作（如调用 LLM），返回更新后的状态。
#       比喻：在流程图纸上画一个个方框，每个方框里写上名字和负责的工作。
#
#   步骤 3 - 添加普通边（add_edge）：
#       workflow.add_edge("节点A", "节点B")
#       用固定箭头连接两个方框：执行完 A 后必定执行 B。
#       比喻：画一条从 A 指向 B 的直线箭头。
#
#   步骤 4 - 添加条件边（add_conditional_edges）：
#       workflow.add_conditional_edges("节点A", 条件函数, 路由映射)
#       用可分叉的箭头连接方框：执行完 A 后，条件函数返回一个字符串，
#       在路由映射中查找这个字符串对应的目标节点。
#       路由映射的两种写法：
#         a. 列表形式：["tools_market", "Msg Clear Market"]
#            条件函数返回的字符串直接作为目标节点名。
#         b. 字典形式：{"返回值1": "节点名1", "返回值2": "节点名2"}
#            条件函数返回的字符串在字典中查找对应的目标节点名。
#       比喻：画一个分叉路口，根据信号灯（条件函数返回值）决定走哪条路。
#
#   步骤 5 - 编译（compile）：
#       graph = workflow.compile()
#       compile() 做了三件关键的事：
#         a. 验证图结构完整性：检查每个节点是否都有合理的入边和出边。
#            - START 必须有出边（不能是死路入口）。
#            - END 必须有入边（不能是无人到达的出口）。
#            - 中间节点必须有入边和出边（不能是孤岛）。
#         b. 检查是否有孤立的节点：如果某个节点没有被任何边连接，编译会报错。
#            这能防止你忘记连接某个节点导致图不完整。
#         c. 生成可执行对象：返回一个 CompiledGraph 实例。
#            compile 之前叫 workflow（设计图，只能看不能跑），
#            compile 之后叫 graph（可执行图，能调用 invoke() 或 stream()）。
#       比喻：把流程图纸变成真正的流水线——检查所有传送带是否连接好，
#             确认没有孤立的工位，然后开机通电。
#
#   步骤 6 - 执行（invoke / stream）：
#       graph.invoke(初始状态)   → 一次性跑完，返回最终状态
#       graph.stream(初始状态)   → 逐步返回，每次 yield 一个节点的中间结果
#       注意：执行不在本文件中完成，本文件只负责搭建和编译图。
#       执行在 trading_graph.py 的 propagate 方法中完成。
#
# --- 概念 3：invoke() vs stream() 的区别 ---
#   - invoke()：像一个"黑盒"——你把初始状态丢进去，它跑完整条流水线，最后把成品吐出来。
#     你看不到中间过程，只得到最终结果。适合生产环境使用。
#   - stream()：像一个"玻璃流水线"——每经过一个工位，它都会把当前半成品展示给你看。
#     返回一个生成器，需要用 for 循环逐块接收。适合调试和监控。
#
# --- 概念 4：条件边的路由机制（本文件大量使用） ---
# 条件边是 LangGraph 实现"智能决策"和"循环"的核心机制。
#
# 工作原理（以 Market Analyst 为例）：
#   workflow.add_conditional_edges(
#       "Market Analyst",               # 源节点
#       should_continue_market,          # 条件函数：接收 state，返回字符串
#       ["tools_market", "Msg Clear Market"]  # 路由映射（列表形式）
#   )
#
# 执行时：
#   1. Market Analyst 节点执行完毕（LLM 生成了回复）
#   2. LangGraph 调用 should_continue_market(state)
#   3. should_continue_market 检查最后一条消息是否有 tool_calls：
#      - 有 tool_calls → 返回 "tools_market" → 路由到 tools_market 节点（继续获取数据）
#      - 无 tool_calls → 返回 "Msg Clear Market" → 路由到消息清除节点（分析完成）
#   4. LangGraph 根据返回值将流程路由到对应节点
#
# 路由映射的两种形式：
#   - 列表形式 ["A", "B"]：条件函数返回的字符串直接是节点名。
#   - 字典形式 {"key1": "Node1", "key2": "Node2"}：条件函数返回 key，查找对应的 Node。
#     字典形式的好处：返回值和节点名可以不同，更灵活。
#
# --- 概念 5：ToolNode 的内部机制 ---
# ToolNode 是 LangGraph 预置的特殊节点，用于"工具调用"。
# 工作原理：
#   1. LLM 不能直接上网查数据，它只能输出文本。
#   2. 当 LLM 需要数据时，它在回复中包含 tool_calls 字段：
#      tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "call_123"}]
#   3. 条件函数检测到 tool_calls，返回工具节点名（如 "tools_market"）。
#   4. 流程路由到 ToolNode 节点。
#   5. ToolNode 自动读取 tool_calls，找到对应的工具函数，用参数调用它。
#   6. ToolNode 把工具函数的返回结果包装成 ToolMessage（一种特殊消息类型）。
#   7. ToolMessage 被添加到状态中。
#   8. 流程从 ToolNode 回到分析师节点，LLM 看到 ToolMessage 中的数据，继续分析。
#   9. 如果 LLM 还需要更多数据，它会再次输出 tool_calls，形成循环。
#   10. 当 LLM 不再需要数据（分析完成），它不再输出 tool_calls，
#       条件函数返回消息清除节点名，流程进入下一个分析师。
#
# --- 概念 6：RecursionLimit（递归限制） ---
# 图中存在循环（如：分析师→工具→分析师→工具→...），如果不限制循环次数，
# 图可能陷入无限循环。RecursionLimit 是安全机制：
# 每经过一个节点算"一步"，步数超过限制时 LangGraph 抛出 RecursionError。
# 本系统的默认限制是 100 步，足够处理正常的分析流程。
# 注意：递归限制不在本文件设置，在 propagation.py 的 Propagator 中设置。
#
# --- 概念 7：Python 特有语法说明 ---
#
# a. enumerate(列表)（带索引的循环）
#    for i, analyst_type in enumerate(["market", "social", "news"]):
#    enumerate 会同时给出"索引"和"值"：
#      i=0, analyst_type="market"
#      i=1, analyst_type="social"
#      i=2, analyst_type="news"
#    好处：不需要手动用 range(len(...)) 来获取索引。
#
# b. getattr(obj, "方法名")（动态方法获取）
#    正常调用：obj.should_continue_market()，方法名固定写死。
#    getattr 允许用字符串变量指定方法名：
#      method_name = f"should_continue_{analyst_type}"  # 如 "should_continue_market"
#      getattr(self.conditional_logic, method_name)
#    好处：在循环中动态拼接方法名，避免写一堆 if-else。
#
# c. f"...{variable}..."（f-string 格式化字符串）
#    f"tools_{analyst_type}" 当 analyst_type="market" 时，结果是 "tools_market"。
#
# d. .capitalize()（首字母大写）
#    "market".capitalize() → "Market"
#    "social".capitalize() → "Social"
#
# e. dict.items()（字典遍历）
#    for key, value in some_dict.items():
#    同时遍历字典的键和值。
#
# f. in（成员判断）
#    if "market" in selected_analysts:
#    判断 "market" 是否在列表 selected_analysts 中。
#
# =============================================================================
#
# 【工作流图概览】
# 以下是整个交易代理系统的完整工作流图：
#
#      START
#        |
#   [Market Analyst] <--> [tools_market]     # 市场分析师：获取数据 <-> 分析（循环）
#        | (Msg Clear)                        # 消息清除，准备下一个分析师
#   [Social Analyst] <--> [tools_social]     # 社交媒体分析师
#        | (Msg Clear)
#   [News Analyst] <--> [tools_news]         # 新闻分析师
#        | (Msg Clear)
#   [Fundamentals Analyst] <--> [tools_fundamentals]  # 基本面分析师
#        | (Msg Clear)
#   [Bull Researcher] <--> [Bear Researcher]  # 牛/熊方辩论（循环）
#        | (条件：达到最大轮数)
#   [Research Manager]                       # 研究经理：综合辩论结果
#        |
#   [Trader]                                 # 交易员：制定交易计划
#        |
#   [Aggressive] <--> [Conservative] <--> [Neutral]  # 风险分析三方辩论（循环）
#        | (条件：达到最大轮数)
#   [Portfolio Manager]                      # 投资组合经理：最终决策
#        |
#       END
#
# 图中的循环通过条件边实现：
#   - 分析师 ↔ 工具：条件边检查是否有 tool_calls
#   - 牛 ↔ 熊：条件边检查辩论轮数
#   - 激进 ↔ 保守 ↔ 中性：条件边检查讨论轮数
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
                        # Dict[str, Any] 表示"键为字符串、值为任意类型的字典"
                        # 类型提示不影响运行，只是给人和 IDE 看的"说明书"
import logging
import os  # 并行开关/并发度环境变量
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_openai import ChatOpenAI  # OpenAI 兼容的聊天模型接口

# ChatOpenAI 是 LangChain 提供的 LLM 封装类，
# 它可以调用 OpenAI 的 API 来生成回复。
# 本系统也支持 Google、Anthropic 等其他提供商，
# 但最终都会适配成类似 ChatOpenAI 的接口。
from langgraph.graph import END, START, StateGraph  # LangGraph 核心组件

# StateGraph：用于创建有状态的有向图（核心类）
# START：特殊节点，表示图的执行入口
# END：特殊节点，表示图的执行出口
# 执行流程：START → 各个节点 → END
from langgraph.prebuilt import ToolNode  # 预置工具节点

# ToolNode 详见上方"前置知识-概念 5：ToolNode 的内部机制"
# 导入所有代理创建函数
# 这些函数返回 LangGraph 可用的节点函数
# Python 语法说明：from xxx import * 把 xxx 模块中所有公开名字一次性导入
from tradingagents.agents import *

# AgentState：TypedDict，定义了图中所有节点共享的状态结构
# TypedDict 是 Python 的一个类型工具，它让字典像有类型的结构体一样，
# 明确声明了哪些键存在、每个键的值是什么类型。
# AgentState 包含的字段：messages, company_of_interest, trade_date,
# investment_debate_state, risk_debate_state, market_report, ...
from tradingagents.agents.utils.agent_states import AgentState

# 条件逻辑类：提供条件边的路由判断函数
# ConditionalLogic 包含 should_continue_market, should_continue_debate 等方法，
# 这些方法被用作条件边的条件函数。
# 详见 conditional_logic.py 的注释
from .conditional_logic import ConditionalLogic

ANALYST_REPORT_FIELDS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

logger = logging.getLogger(__name__)


# =============================================================================
# 类：GraphSetup
# =============================================================================
class GraphSetup:
    """
    负责搭建和配置代理工作流图。

    【职责】
    1. 根据 selected_analysts 参数创建对应的分析师节点
    2. 创建研究员、交易员、风险管理等节点
    3. 用 StateGraph 组织所有节点，添加边和条件边
    4. 编译图（compile），返回可执行的图对象

    【为什么需要编译？】
    LangGraph 的 compile() 方法会验证图结构的正确性，
    并将图编译为高效的执行格式。只有编译后的图才能被 invoke() 或 stream()。

    compile() 具体做了什么：
    1. 验证图结构完整性：检查每个节点是否有合理的入边和出边
    2. 检查是否有孤立的节点：没有边连接的节点会报错
    3. 生成可执行对象：返回 CompiledGraph，可以调用 invoke() 或 stream()

    运行时数据流示例（使用 GraphSetup）：
        # 创建 GraphSetup 实例
        graph_setup = GraphSetup(
            quick_thinking_llm=ChatOpenAI(model="gpt-4o-mini"),
            deep_thinking_llm=ChatOpenAI(model="o3-mini"),
            tool_nodes={"market": ToolNode([...]), ...},
            bull_memory=FinancialSituationMemory(...),
            ...,
            conditional_logic=ConditionalLogic(max_debate_rounds=1, ...),
        )

        # 搭建并编译图
        graph = graph_setup.setup_graph(["market", "fundamentals"])

        # 此时 graph 是一个编译后的 CompiledGraph 对象
        # 可以调用：
        #   result = graph.invoke(initial_state)  # 一次性执行
        #   for chunk in graph.stream(initial_state): ...  # 逐块执行
    """

    # -------------------------------------------------------------------------
    # 构造函数：接收所有依赖组件
    # -------------------------------------------------------------------------
    # Python 语法说明：
    # __init__ 是构造函数。self 是对当前对象的引用（类似其他语言的 this）。
    # 参数后面的 : 类型 是"类型提示"，不影响运行，只是提示参数应该是什么类型。
    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,        # 快速思考 LLM（用于大多数代理）
        deep_thinking_llm: ChatOpenAI,         # 深度思考 LLM（用于研究经理和投资组合经理）
        tool_nodes: dict[str, ToolNode],       # 工具节点字典（键为分析师类型）
        bull_memory,                           # 牛市研究员的情景记忆
        bear_memory,                           # 熊市研究员的情景记忆
        trader_memory,                         # 交易员的情景记忆
        invest_judge_memory,                   # 投资裁判的情景记忆
        portfolio_manager_memory,              # 投资组合经理的情景记忆
        conditional_logic: ConditionalLogic,   # 条件路由逻辑
        pipeline: dict | None = None,          # 自定义图编排配置
    ):
        """
        初始化图搭建器。

        参数:
            quick_thinking_llm: 快速思考 LLM 实例
            deep_thinking_llm: 深度思考 LLM 实例
            tool_nodes: 工具节点字典
            bull_memory: 牛市方记忆
            bear_memory: 熊市方记忆
            trader_memory: 交易员记忆
            invest_judge_memory: 投资裁判记忆
            portfolio_manager_memory: 投资组合经理记忆
            conditional_logic: 条件路由逻辑

        运行时数据流示例（构造函数内部）：
            # 传入参数的运行时值示例：
            # quick_thinking_llm = ChatOpenAI(model="gpt-4o-mini", ...)
            # deep_thinking_llm = ChatOpenAI(model="o3-mini", ...)
            # tool_nodes = {"market": ToolNode([get_stock_data, get_indicators]), ...}
            # bull_memory = FinancialSituationMemory("bull_memory", config)
            # conditional_logic = ConditionalLogic(max_debate_rounds=1, max_risk_discuss_rounds=1)

            # 构造函数只是把传入的参数保存到 self 属性中：
            # self.quick_thinking_llm = quick_thinking_llm
            # self.deep_thinking_llm = deep_thinking_llm
            # self.tool_nodes = tool_nodes
            # ...
            # 这些属性后续在 setup_graph 方法中使用

        以下是可视化的流程图（Mermaid），展示 GraphSetup 的组件依赖关系
        （构造函数接收的依赖如何组装成可执行图）：
        ```mermaid
        flowchart LR
            LLM1["quick_thinking_llm<br/>快速思考 LLM<br/>（分析师/研究员/交易员/风险分析师）"]
            LLM2["deep_thinking_llm<br/>深度思考 LLM<br/>（研究经理/组合经理）"]
            TN["tool_nodes<br/>工具节点字典<br/>market/social/news/fundamentals"]
            MEM1["bull_memory<br/>多头研究员记忆"]
            MEM2["bear_memory<br/>空头研究员记忆"]
            MEM3["trader_memory<br/>交易员记忆"]
            MEM4["invest_judge_memory<br/>投资裁判记忆"]
            MEM5["portfolio_manager_memory<br/>组合经理记忆"]
            CL["conditional_logic<br/>条件路由逻辑<br/>（辩论轮数/工具循环判断）"]

            GS["GraphSetup<br/>图搭建器"]

            LLM1 --> GS
            LLM2 --> GS
            TN --> GS
            MEM1 --> GS
            MEM2 --> GS
            MEM3 --> GS
            MEM4 --> GS
            MEM5 --> GS
            CL --> GS

            GS -->|"setup_graph(selected_analysts)"| CG["CompiledGraph<br/>编译后的可执行图<br/>（可 invoke/stream）"]
        ```
        以上是可视化的流程图：GraphSetup 组件依赖关系。
        """
        # 将所有传入的依赖保存为实例属性
        # self.xxx = xxx 表示把参数值保存到对象的属性中
        # 这样 setup_graph 方法就能通过 self.xxx 访问这些依赖
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.portfolio_manager_memory = portfolio_manager_memory
        self.conditional_logic = conditional_logic
        # pipeline 控制哪些阶段参与图编排：
        #   enable_debate  多空辩论（Bull/Bear + Research Manager）
        #   enable_trader  交易员节点
        #   enable_risk    三方风险辩论
        # Portfolio Manager（最终裁决）始终保留，保证 final_trade_decision 一定生成。
        self.pipeline = pipeline or {}

    # -------------------------------------------------------------------------
    # 核心方法：搭建完整的工作流图
    # -------------------------------------------------------------------------
    def setup_graph(self, selected_analysts=None):
        """
        搭建并编译代理工作流图。

        【图结构说明】
        整个图分为以下几个阶段：

        阶段 1 - 分析阶段（分析师流水线）：
        - 每个选中的分析师依次执行：分析师节点 -> 工具节点（循环）-> 消息清除节点
        - 分析师节点调用 LLM 分析数据，LLM 可能请求工具调用
        - 工具节点执行工具调用并返回结果
        - 条件边判断是否继续调用工具还是进入下一个分析师

        阶段 2 - 投资辩论阶段（牛/熊方辩论）：
        - 牛市研究员和熊市研究员交替发言
        - 条件边控制辩论轮数，达到上限后进入研究经理

        阶段 3 - 交易决策阶段：
        - 研究经理综合辩论结果
        - 交易员制定交易计划

        阶段 4 - 风险分析阶段（三方辩论）：
        - 激进/保守/中性分析师三方辩论
        - 条件边控制辩论轮数，达到上限后进入投资组合经理

        阶段 5 - 最终决策：
        - 投资组合经理综合所有信息，做出最终决策

        以下是可视化的流程图（Mermaid），完整展示 setup_graph() 构建的工作流拓扑与条件边路由：
        ```mermaid
        flowchart TD
            START(["START"])
            MA["Market Analyst"] <-->|"条件边"| TM["tools_market"]
            MA -->|"条件边"| CMA["Msg Clear Market"]
            CMA --> SA["Social Analyst"]
            SA <-->|"条件边"| TS["tools_social"]
            SA -->|"条件边"| CSA["Msg Clear Social"]
            CSA --> NA["News Analyst"]
            NA <-->|"条件边"| TN["tools_news"]
            NA -->|"条件边"| CNA["Msg Clear News"]
            CNA --> FA["Fundamentals Analyst"]
            FA <-->|"条件边"| TF["tools_fundamentals"]
            FA -->|"条件边"| CFA["Msg Clear Fundamentals"]
            CFA --> BR["Bull Researcher"]
            BR <-->|"条件边"| RR["Bear Researcher"]
            BR & RR -->|"条件边"| RM["Research Manager"]
            RM --> TR["Trader"]
            TR --> AA["Aggressive"]
            AA <-->|"条件边"| CA["Conservative"]
            CA <-->|"条件边"| NA2["Neutral"]
            AA & CA & NA2 -->|"条件边"| PM["Portfolio Manager"]
            PM --> END(["END"])
        ```
        以上是可视化的流程图。

        参数:
            selected_analysts (list): 要启用的分析师类型列表

        返回:
            CompiledGraph: 编译后的 LangGraph 图对象，可以调用 invoke() 或 stream()

        运行时数据流示例（setup_graph 的完整执行过程）：

            # 调用 setup_graph
            graph = graph_setup.setup_graph(["market", "social", "news", "fundamentals"])

            # 内部执行步骤：

            # 1. 创建分析师节点
            #    analyst_nodes = {
            #        "market": create_market_analyst(quick_thinking_llm),
            #        "social": create_social_media_analyst(quick_thinking_llm),
            #        ...
            #    }
            #    delete_nodes = {"market": create_msg_delete(), ...}
            #    tool_nodes = {"market": ToolNode([get_stock_data, get_indicators]), ...}

            # 2. 创建研究员、交易员、风险管理节点
            #    bull_researcher_node = create_bull_researcher(quick_thinking_llm, bull_memory)
            #    bear_researcher_node = create_bear_researcher(quick_thinking_llm, bear_memory)
            #    research_manager_node = create_research_manager(deep_thinking_llm, invest_judge_memory)
            #    trader_node = create_trader(quick_thinking_llm, trader_memory)
            #    aggressive_analyst = create_aggressive_debator(quick_thinking_llm)
            #    ...

            # 3. 创建 StateGraph
            #    workflow = StateGraph(AgentState)

            # 4. 添加所有节点
            #    workflow.add_node("Market Analyst", analyst_nodes["market"])
            #    workflow.add_node("tools_market", tool_nodes["market"])
            #    workflow.add_node("Msg Clear Market", delete_nodes["market"])
            #    workflow.add_node("Bull Researcher", bull_researcher_node)
            #    ...

            # 5. 添加边和条件边
            #    workflow.add_edge(START, "Market Analyst")
            #    workflow.add_conditional_edges("Market Analyst", should_continue_market, [...])
            #    workflow.add_edge("tools_market", "Market Analyst")  # 循环
            #    workflow.add_edge("Msg Clear Market", "Social Analyst")
            #    ...
            #    workflow.add_edge("Portfolio Manager", END)

            # 6. 编译并返回
            #    return workflow.compile()
            #    → 返回一个 CompiledGraph 对象，可以调用 invoke() 或 stream()
        """
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals"]

        # 安全检查：必须至少选择一个分析师
        # 如果 selected_analysts 是空列表，后面的循环不会执行，图会缺少入口
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # 读取自定义图编排配置。
        # 风险辩论需要交易员方案作为评估对象，因此 enable_risk 依赖 enable_trader。
        pipeline = self.pipeline or {}
        enable_debate = bool(pipeline.get("enable_debate", True))
        enable_trader = bool(pipeline.get("enable_trader", True))
        enable_risk = bool(pipeline.get("enable_risk", True)) and enable_trader

        # =====================================================================
        # 阶段 1：创建分析师节点
        # =====================================================================
        # 三个字典分别存储：
        #   - analyst_nodes：分析师处理节点（LLM 分析节点）
        #   - delete_nodes：消息清除节点（清空消息列表，准备下一个分析师）
        #   - tool_nodes：工具执行节点（ToolNode，执行数据获取工具）
        #
        # 为什么需要消息清除节点？
        # 每个分析师用完后，消息列表中累积了大量对话历史（含工具调用结果）。
        # 下一个分析师不需要看这些历史（它有自己的系统提示词和工具）。
        # 消息清除节点把消息列表清空（保留前两条系统消息），为新分析师准备干净的上下文。
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        # 如果选中了市场分析师，创建相关节点
        # Python 语法说明：if "market" in selected_analysts:
        # in 是 Python 的成员运算符，判断 "market" 是否在列表 selected_analysts 中
        if "market" in selected_analysts:
            # create_market_analyst() 返回一个节点函数，该函数：
            # 1. 接收当前状态
            # 2. 调用 LLM（使用 market 分析师的系统提示）
            # 3. 返回更新后的状态（包含 LLM 的响应消息）
            #
            # 运行时数据流示例：
            #   create_market_analyst(quick_thinking_llm) 返回一个函数 market_analyst_func
            #   当图执行到 "Market Analyst" 节点时，LangGraph 调用：
            #     market_analyst_func(state)
            #   state = {
            #     "messages": [HumanMessage("AAPL"), ToolMessage("Open:185.5, High:187.2...")],
            #     "company_of_interest": "AAPL",
            #     ...
            #   }
            #   函数内部调用 LLM，LLM 分析数据并返回：
            #     AIMessage(content="Based on the data, RSI is at 72.3 indicating overbought...")
            #   函数返回更新后的 state：
            #     {
            #       "messages": [..., AIMessage("Based on the data, RSI is at 72.3...")],
            #       "market_report": "RSI is at 72.3 indicating overbought...",  # 同时更新报告字段
            #     }
            analyst_nodes["market"] = create_market_analyst(
                self.quick_thinking_llm
            )
            # create_msg_delete() 返回一个节点函数，该函数：
            # 清空消息列表（除了前两条系统消息），为新分析师准备干净的上下文
            #
            # 运行时数据流示例：
            #   执行前 state["messages"] = [SystemMsg1, SystemMsg2, HumanMsg, AIMsg, ToolMsg, AIMsg, ...]
            #   执行后 state["messages"] = [SystemMsg1, SystemMsg2]  # 只保留前两条
            delete_nodes["market"] = create_msg_delete()
            # 市场分析师专用的工具节点
            # tool_nodes["market"] 是一个 ToolNode 实例，包含 get_stock_data 和 get_indicators 两个工具
            tool_nodes["market"] = self.tool_nodes["market"]

        if "social" in selected_analysts:
            # 社交媒体分析师：分析社交媒体上的情绪和讨论
            analyst_nodes["social"] = create_social_media_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["social"] = create_msg_delete()
            tool_nodes["social"] = self.tool_nodes["social"]

        if "news" in selected_analysts:
            # 新闻分析师：分析新闻事件对股价的影响
            analyst_nodes["news"] = create_news_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["news"] = create_msg_delete()
            tool_nodes["news"] = self.tool_nodes["news"]

        if "fundamentals" in selected_analysts:
            # 基本面分析师：分析财务报表和公司基本面
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.quick_thinking_llm
            )
            delete_nodes["fundamentals"] = create_msg_delete()
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        # =====================================================================
        # 阶段 2-5：创建研究员、交易员、风险管理节点
        # =====================================================================

        # 创建牛市研究员节点
        # 它从看多角度分析股票，并引用记忆中的历史经验
        # create_bull_researcher 返回一个节点函数
        # 运行时数据流示例：
        #   bull_researcher_node(state)
        #   → LLM 收到：市场状况 + 熊方论点（如有） + 历史经验
        #   → LLM 返回："Bull: AAPL shows strong revenue growth of 15% YoY..."
        #   → 更新 state["investment_debate_state"]["bull_history"]
        #   → 更新 state["investment_debate_state"]["current_response"] = "Bull: ..."
        #   → 更新 state["investment_debate_state"]["count"] += 1
        bull_researcher_node = create_bull_researcher(
            self.quick_thinking_llm, self.bull_memory
        )

        # 创建熊市研究员节点
        # 它从看空角度分析股票，并引用记忆中的历史经验
        # 运行时数据流示例：
        #   bear_researcher_node(state)
        #   → LLM 收到：市场状况 + 牛方论点（如有） + 历史经验
        #   → LLM 返回："Bear: P/E ratio of 28.5 is above industry average..."
        #   → 更新 state["investment_debate_state"]["bear_history"]
        #   → 更新 state["investment_debate_state"]["current_response"] = "Bear: ..."
        #   → 更新 state["investment_debate_state"]["count"] += 1
        bear_researcher_node = create_bear_researcher(
            self.quick_thinking_llm, self.bear_memory
        )

        # 创建研究经理节点
        # 使用深度思考 LLM，综合牛/熊方辩论结果，做出投资判断
        # 运行时数据流示例：
        #   research_manager_node(state)
        #   → LLM 收到：牛方历史 + 熊方历史 + 完整辩论记录
        #   → LLM 返回："After analyzing both sides, the bull argument is more compelling
        #      due to strong revenue growth. Recommendation: BUY"
        #   → 更新 state["investment_debate_state"]["judge_decision"]
        research_manager_node = create_research_manager(
            self.deep_thinking_llm, self.invest_judge_memory
        )

        # 创建交易员节点
        # 根据研究经理的投资判断，制定具体的交易计划
        # 运行时数据流示例：
        #   trader_node(state)
        #   → LLM 收到：投资判断 + 市场报告 + 历史经验
        #   → LLM 返回："Based on the BUY recommendation, buy 100 shares of AAPL
        #      at current price of $185 with stop loss at $175..."
        #   → 更新 state["trader_investment_plan"]
        trader_node = create_trader(self.quick_thinking_llm)

        # 创建风险分析三方辩论节点
        # 激进分析师：主张高风险高回报策略（如加杠杆、全仓买入）
        # 保守分析师：主张低风险策略（如减仓、设止损）
        # 中性分析师：主张平衡策略（如分批建仓、适度仓位）
        # 运行时数据流示例：
        #   aggressive_analyst(state)
        #   → LLM 返回："Aggressive: Given the strong fundamentals, leverage up and
        #      buy additional 200 shares on margin..."
        #   → 更新 state["risk_debate_state"]["aggressive_history"]
        #   → 更新 state["risk_debate_state"]["latest_speaker"] = "Aggressive"
        #   → 更新 state["risk_debate_state"]["count"] += 1
        aggressive_analyst = create_risk_aggressive(self.quick_thinking_llm)
        neutral_analyst = create_risk_neutral(self.quick_thinking_llm)
        conservative_analyst = create_risk_conservative(self.quick_thinking_llm)

        # 创建投资组合经理节点
        # 使用深度思考 LLM，综合风险分析辩论结果，做出最终交易决策
        # 运行时数据流示例：
        #   portfolio_manager_node(state)
        #   → LLM 收到：三方辩论历史 + 交易员计划 + 历史经验
        #   → LLM 返回："Based on comprehensive analysis of all risk perspectives,
        #      final decision: BUY AAPL with 60% position size, target $200..."
        #   → 更新 state["risk_debate_state"]["judge_decision"]
        #   → 更新 state["final_trade_decision"]
        portfolio_manager_node = create_portfolio_manager(
            self.deep_thinking_llm, self.portfolio_manager_memory
        )

        # 策略扫描节点：在分析师之前运行本次选中的策略组合
        strategy_scanner_node = create_strategy_scanner_node()

        # 并行分析师：每个分析师使用独立的 messages 副本，报告字段在节点结束时合并。
        use_parallel_analysts = (
            os.getenv("TRADINGAGENTS_PARALLEL_ANALYSTS", "1").lower()
            in ("1", "true", "yes", "on")
            and len(selected_analysts) > 1
        )
        try:
            parallel_workers = max(
                1,
                min(
                    int(os.getenv("TRADINGAGENTS_PARALLEL_ANALYSTS_WORKERS", "4")),
                    len(selected_analysts),
                ),
            )
        except ValueError:
            parallel_workers = min(4, len(selected_analysts))

        def _run_analyst(
            analyst_type: str, state: dict, config: dict | None = None
        ) -> str:
            node = analyst_nodes[analyst_type]
            report_field = ANALYST_REPORT_FIELDS[analyst_type]
            local = dict(state)
            local["messages"] = list(state.get("messages") or [])
            error = None
            for _ in range(6):
                update = node(local) or {}
                messages = update.get("messages") or []
                if messages:
                    local["messages"].extend(messages)
                for key, value in update.items():
                    if key != "messages":
                        local[key] = value
                ai_message = messages[-1] if messages else None
                tool_calls = getattr(ai_message, "tool_calls", None) or []
                if not tool_calls:
                    break
                try:
                    tool_update = tool_nodes[analyst_type].invoke(
                        {"messages": [ai_message]},
                        config=config,
                    )
                    local["messages"].extend(
                        tool_update.get("messages") or []
                    )
                except Exception as exc:
                    error = exc
                    break
            report = local.get(report_field, "")
            if report:
                return report
            last_message = (
                local["messages"][-1] if local["messages"] else None
            )
            content = str(getattr(last_message, "content", "") or "").strip()
            if content:
                return content
            if error is not None:
                return (
                    f"（{analyst_type} 工具调用失败："
                    f"{type(error).__name__}: {error}）"
                )
            return f"（{analyst_type} 未返回报告）"

        def parallel_analysts_node(
            state: dict, config: dict | None = None
        ) -> dict:
            reports: dict[str, str] = {}
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=parallel_workers) as pool:
                futures = {
                    pool.submit(
                        _run_analyst, analyst_type, state, config
                    ): analyst_type
                    for analyst_type in selected_analysts
                }
                for future in as_completed(futures):
                    analyst_type = futures[future]
                    field = ANALYST_REPORT_FIELDS[analyst_type]
                    try:
                        reports[field] = future.result()
                    except Exception as exc:
                        reports[field] = (
                            f"（{analyst_type} 分析失败："
                            f"{type(exc).__name__}: {exc}）"
                        )
                    logger.info(
                        "并行分析师 %s 完成，累计耗时 %.1fs",
                        analyst_type,
                        time.perf_counter() - started,
                    )
            logger.info(
                "并行分析师全部完成，共 %.1fs（workers=%s）",
                time.perf_counter() - started,
                parallel_workers,
            )
            return reports

        # =====================================================================
        # 搭建 StateGraph
        # 这是 StateGraph 生命周期的第 1 步：创建
        # =====================================================================

        # 创建 StateGraph 实例
        # AgentState 是状态类型（TypedDict），定义了图中传递的数据结构
        # 所有节点都共享同一个 AgentState 实例
        # StateGraph 内部维护了节点列表和边列表，此时图是空的
        workflow = StateGraph(AgentState)

        # 策略扫描作为流程的第一个阶段
        workflow.add_node("Strategy Scanner", strategy_scanner_node)

        # -----------------------------------------------------------------
        # 添加分析师节点到图中
        # 这是 StateGraph 生命周期的第 2 步：add_node
        #
        # 每个分析师有三个节点：
        #   1. 分析师节点（如 "Market Analyst"）：LLM 分析节点
        #   2. 消息清除节点（如 "Msg Clear Market"）：清理消息历史
        #   3. 工具节点（如 "tools_market"）：执行工具调用
        #
        # Python 语法说明：for key, value in dict.items()
        #   dict.items() 返回字典中所有(键, 值)对的视图，
        #   for 循环同时遍历键和值。
        #   例如 analyst_nodes = {"market": func1, "social": func2}
        #   则遍历：("market", func1), ("social", func2)
        # -----------------------------------------------------------------
        if use_parallel_analysts:
            workflow.add_node("Parallel Analysts", parallel_analysts_node)
        else:
            for analyst_type, node in analyst_nodes.items():
                # 添加分析师节点，名称首字母大写（如 "market" → "Market Analyst"）
                workflow.add_node(f"{analyst_type.capitalize()} Analyst", node)

                # 添加消息清除节点
                workflow.add_node(
                    f"Msg Clear {analyst_type.capitalize()}",
                    delete_nodes[analyst_type],
                )

                # 添加工具节点
                workflow.add_node(
                    f"tools_{analyst_type}", tool_nodes[analyst_type]
                )

        # 按 pipeline 配置添加可选阶段节点。
        # 未启用的节点不加入图，避免出现孤立节点导致 compile 失败。
        if enable_debate:
            workflow.add_node("Bull Researcher", bull_researcher_node)
            workflow.add_node("Bear Researcher", bear_researcher_node)
            workflow.add_node("Research Manager", research_manager_node)

        if enable_trader:
            workflow.add_node("Trader", trader_node)

        if enable_risk:
            workflow.add_node("Aggressive Analyst", aggressive_analyst)
            workflow.add_node("Neutral Analyst", neutral_analyst)
            workflow.add_node("Conservative Analyst", conservative_analyst)

        # 最终裁决节点始终保留，确保一定生成 final_trade_decision
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # =====================================================================
        # 定义边（Edge）—— 数据流动的路径
        # 支持按 pipeline 配置跳过部分阶段，形成“精简 / 标准 / 深度”等流程。
        # =====================================================================
        if enable_debate:
            analysts_next_node = "Bull Researcher"
        elif enable_trader:
            analysts_next_node = "Trader"
        else:
            analysts_next_node = "Portfolio Manager"

        workflow.add_edge(START, "Strategy Scanner")

        if use_parallel_analysts:
            workflow.add_edge("Strategy Scanner", "Parallel Analysts")
            workflow.add_edge("Parallel Analysts", analysts_next_node)
        else:
            first_analyst = selected_analysts[0]
            workflow.add_edge(
                "Strategy Scanner", f"{first_analyst.capitalize()} Analyst"
            )

            # 分析师流水线：分析师 <-> 工具 -> 消息清理 -> 下一个阶段
            for i, analyst_type in enumerate(selected_analysts):
                current_analyst = f"{analyst_type.capitalize()} Analyst"
                current_tools = f"tools_{analyst_type}"
                current_clear = f"Msg Clear {analyst_type.capitalize()}"

                workflow.add_conditional_edges(
                    current_analyst,
                    getattr(
                        self.conditional_logic,
                        f"should_continue_{analyst_type}",
                    ),
                    [current_tools, current_clear],
                )
                workflow.add_edge(current_tools, current_analyst)

                if i < len(selected_analysts) - 1:
                    next_analyst = (
                        f"{selected_analysts[i + 1].capitalize()} Analyst"
                    )
                    workflow.add_edge(current_clear, next_analyst)
                else:
                    workflow.add_edge(current_clear, analysts_next_node)

        # 多空辩论阶段（可选）
        if enable_debate:
            workflow.add_conditional_edges(
                "Bull Researcher",
                self.conditional_logic.should_continue_debate,
                {
                    "Bear Researcher": "Bear Researcher",
                    "Research Manager": "Research Manager",
                },
            )
            workflow.add_conditional_edges(
                "Bear Researcher",
                self.conditional_logic.should_continue_debate,
                {
                    "Bull Researcher": "Bull Researcher",
                    "Research Manager": "Research Manager",
                },
            )

            if enable_trader:
                workflow.add_edge("Research Manager", "Trader")
            else:
                workflow.add_edge("Research Manager", "Portfolio Manager")

        # 交易员阶段（可选）
        if enable_trader:
            if enable_risk:
                workflow.add_edge("Trader", "Aggressive Analyst")
            else:
                workflow.add_edge("Trader", "Portfolio Manager")

        # 三方风险辩论（可选，依赖交易员方案）
        if enable_risk:
            workflow.add_conditional_edges(
                "Aggressive Analyst",
                self.conditional_logic.should_continue_risk_analysis,
                {
                    "Conservative Analyst": "Conservative Analyst",
                    "Portfolio Manager": "Portfolio Manager",
                },
            )
            workflow.add_conditional_edges(
                "Conservative Analyst",
                self.conditional_logic.should_continue_risk_analysis,
                {
                    "Neutral Analyst": "Neutral Analyst",
                    "Portfolio Manager": "Portfolio Manager",
                },
            )
            workflow.add_conditional_edges(
                "Neutral Analyst",
                self.conditional_logic.should_continue_risk_analysis,
                {
                    "Aggressive Analyst": "Aggressive Analyst",
                    "Portfolio Manager": "Portfolio Manager",
                },
            )

        # 最终裁决 -> END（Portfolio Manager 始终存在）
        workflow.add_edge("Portfolio Manager", END)

        # =====================================================================
        # 编译图并返回
        # 这是 StateGraph 生命周期的第 5 步：compile
        #
        # compile() 做了什么：
        #   1. 验证图结构完整性——每个节点是否都有合理的入边和出边
        #   2. 检查是否有孤立的节点——没有边连接的节点会报错
        #   3. 生成可执行对象——返回 CompiledGraph，可以调用 invoke() 或 stream()
        #
        # 编译前叫 workflow（设计图），编译后叫 graph（可执行图）
        # 编译后的图才能被 invoke() 或 stream() 执行
        # =====================================================================
        return workflow.compile()
