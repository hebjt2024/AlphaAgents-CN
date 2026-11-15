# =============================================================================
# TradingAgents/graph/trading_graph.py
# =============================================================================
# 模块：TradingAgentsGraph - 交易代理图的主编排类
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件涉及的核心概念，如果你完全没有 Python 和 LangChain 经验，请先阅读以下说明。
#
# --- 概念 1：什么是 LangGraph？ ---
# LangGraph 是一个用于构建"有状态的多代理工作流"的 Python 库。
# 你可以把它想象成一条工厂流水线：
#   - 流水线上有多个"工位"（节点 Node），每个工位由一个 AI 代理（Agent）负责。
#   - 工位之间用"传送带"（边 Edge）连接，产品（数据/状态 State）沿着传送带流动。
#   - 有些传送带是固定的（普通边：A 完了一定去 B），
#     有些传送带可以根据产品状态分叉（条件边：A 完了，根据条件去 B 或 C）。
#
# --- 概念 2：StateGraph 的完整生命周期 ---
# 一个 LangGraph 工作流图从创建到执行，经历以下步骤：
#   1. 创建（create）：
#        workflow = StateGraph(AgentState)
#        创建一个空的有向图，AgentState 定义了图中传递的数据结构（类似一个表格的列头）。
#   2. 添加节点（add_node）：
#        workflow.add_node("节点名", 处理函数)
#        把一个个工位加到流水线上，每个工位有一个名字和一个处理函数。
#   3. 添加边（add_edge）：
#        workflow.add_edge("节点A", "节点B")
#        用固定传送带连接工位：A 完了必定去 B。
#   4. 添加条件边（add_conditional_edges）：
#        workflow.add_conditional_edges("节点A", 条件函数, 路由映射)
#        用可分叉的传送带连接工位：A 完了，条件函数返回一个字符串，
#        在路由映射中查找这个字符串对应的目标节点。
#   5. 编译（compile）：
#        graph = workflow.compile()
#        compile() 做了三件事：
#          a. 验证图结构完整性——每个节点是否都有入边和出边（除了 START 和 END）。
#          b. 检查是否有孤立的节点——没有任何边连接的节点会报错。
#          c. 生成可执行对象——返回一个 CompiledGraph，可以调用 invoke() 或 stream()。
#        只有编译后的图才能被执行。compile 之前叫 workflow（设计图），之后叫 graph（可执行图）。
#   6. 执行（invoke / stream）：
#        graph.invoke(初始状态)   → 一次性跑完，返回最终状态
#        graph.stream(初始状态)   → 逐步返回，每次 yield 一个节点的中间结果
#
# --- 概念 3：invoke() vs stream() 的区别 ---
#   - invoke()：像一个"黑盒"——你把初始状态丢进去，它跑完整条流水线，最后把成品吐出来。
#     优点：简单，一行代码搞定。
#     缺点：你看不到中间过程，如果某个节点出错你不知道在哪里出的。
#   - stream()：像一个"玻璃流水线"——每经过一个工位，它都会把当前半成品展示给你看。
#     优点：可以观察每个节点的输出，适合调试和监控。
#     缺点：需要用 for 循环逐块接收结果。
#
# --- 概念 4：条件边的路由机制 ---
# 条件边是 LangGraph 实现"智能决策"的核心机制。
# 工作原理：
#   workflow.add_conditional_edges(
#       "源节点",           # 从哪个节点出发
#       条件函数,           # 一个 Python 函数，接收 state，返回一个字符串
#       route_map,          # 路由映射：{条件函数返回值: 目标节点名}
#   )
# 执行时：
#   1. 源节点执行完毕
#   2. LangGraph 调用条件函数(state)，得到一个字符串（如 "tools_market"）
#   3. 在 route_map 中查找 "tools_market" 对应的目标节点名
#   4. 将流程路由到该目标节点
# 这就像火车站的道岔——根据信号灯（条件函数返回值）决定列车走哪条轨道。
#
# --- 概念 5：ToolNode 的内部机制 ---
# ToolNode 是 LangGraph 预置的特殊节点，用于"工具调用"。
# 工作原理：
#   1. LLM（大语言模型）不能直接上网查数据，它只能输出文本。
#   2. 当 LLM 需要数据时，它在回复中包含一个特殊字段 tool_calls，
#      格式类似：tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}}]
#      这相当于 LLM 说："请帮我调用 get_stock_data 工具，参数是 ticker=AAPL"。
#   3. ToolNode 自动读取 LLM 输出中的 tool_calls 字段。
#   4. ToolNode 找到对应的工具函数（如 get_stock_data），用指定参数调用它。
#   5. ToolNode 将工具函数的返回结果包装成 ToolMessage（一种特殊消息类型）。
#   6. ToolMessage 被放回状态中，下次 LLM 执行时就能看到这些数据。
# 简单来说，ToolNode 是 LLM 和外部数据之间的"翻译官"——LLM 说要什么数据，
# ToolNode 去拿，拿回来再翻译成 LLM 能理解的消息格式。
#
# --- 概念 6：RecursionLimit（递归限制） ---
# LangGraph 图中可能存在循环（如：分析师→工具→分析师→工具→...）。
# 如果不限制循环次数，图可能陷入无限循环，永远跑不完。
# RecursionLimit 是安全机制：图执行的"步数"（每经过一个节点算一步）超过此限制时，
# LangGraph 抛出 RecursionError，强制停止执行。
# 这就像给流水线设了一个"最大加工次数"，防止卡在循环里出不来。
#
# --- 概念 7：反思机制（Reflection）的"为什么" ---
# 交易代理做完决策后，过一段时间可以知道实际收益是赚了还是亏了。
# 反思机制的核心思想是"事后复盘"：
#   - 把"当时的市场状况"+"当时的决策"+"实际收益"一起交给 LLM。
#   - LLM 分析：这个决策是对是错？为什么？下次遇到类似情况该怎么改进？
#   - 把分析结果（反思报告）存入"记忆系统"。
#   - 下次遇到类似市场状况时，先从记忆中检索相关反思，作为参考。
# 这类似于人类交易员的复盘习惯——每笔交易后回顾对错，积累经验。
#
# --- 概念 8：BM25 检索的通俗概念 ---
# BM25 是一种信息检索算法（搜索引擎的核心算法之一），用于"按关键词匹配度排序文档"。
# 通俗解释：假设你有一个文档库（历史经验），你想找到和当前情况最相关的几条经验。
#   - BM25 先统计每个关键词在各文档中出现的频率。
#   - 出现频率越高，相关度越高。
#   - 但如果一个词在所有文档中都出现（如"股票"），它的权重会降低（因为它没有区分度）。
#   - 最终给每条经验打一个相关度分数，按分数从高到低排序，取最相关的几条。
# 类比：就像搜索引擎搜索"苹果 股价 上涨"，会优先返回同时包含这三个词的网页，
# 而不是只包含其中一个词的网页。
# 在本系统中，FinancialSituationMemory 用 BM25 来检索与当前市场状况最相似的历史经验。
#
# --- 概念 9：Python 特有语法说明 ---
# 本文件使用了以下 Python 特有语法，零基础读者需要了解：
#
# a. from xxx import * （通配符导入）
#    把模块中所有公开的东西一次性全部导入。类似把工具箱里所有工具倒到桌上。
#
# b. **kwargs（字典解包 / 关键字参数展开）
#    如果 kwargs = {"reasoning_effort": "high", "temperature": 0.7}
#    那么 func(**kwargs) 等价于 func(reasoning_effort="high", temperature=0.7)
#    即把字典展开成一个个"键=值"形式的参数。类似把打包好的行李箱打开，一件件摆出来。
#
# c. getattr(obj, "方法名")（动态方法获取）
#    正常调用方法是 obj.方法名()，但方法名是固定写死的。
#    getattr 允许用字符串变量来指定方法名：
#      method_name = "should_continue_market"
#      getattr(self.conditional_logic, method_name)
#      等价于 self.conditional_logic.should_continue_market
#    好处：可以在循环中动态拼接方法名，避免写一堆 if-else。
#
# d. f"..."（f-string 格式化字符串）
#    f"Hello {name}" 会把变量 name 的值嵌入字符串中。
#    类似其他语言的模板字符串。
#
# e. A or B（短路求值）
#    self.config = config or DEFAULT_CONFIG
#    如果 config 不为 None（真值），就用 config；否则用 DEFAULT_CONFIG。
#    因为 None 在 Python 中是"假值"，所以 None or B 的结果是 B。
#
# f. if self.callbacks:（真值判断）
#    在 Python 中，空列表 [] 是"假值"，非空列表是"真值"。
#    所以 if self.callbacks: 等价于 if len(self.callbacks) > 0:
#
# g. os.makedirs(..., exist_ok=True)
#    创建目录。exist_ok=True 表示"如果目录已存在也不报错"。
#    没有 exist_ok=True 时，目录已存在会抛出 FileExistsError。
#
# h. with open(...) as f:（上下文管理器）
#    打开文件并自动管理资源的关闭。with 块结束时文件会自动关闭，
#    即使中间出错也不会忘记关闭文件。
#
# =============================================================================
#
# 【文件说明】
# 这是整个交易代理系统的"大脑"——主编排类。
# 它负责：
#   1. 初始化所有组件（LLM 客户端、记忆系统、工具节点、子模块）
#   2. 搭建 LangGraph 工作流图（委托给 GraphSetup）
#   3. 执行图（propagate 方法），即对某只股票在某个交易日运行完整分析流程
#   4. 记录结果（日志）和反思（reflection）
#   5. 处理最终交易信号
#
# 【LangGraph 核心概念】
# - StateGraph：LangGraph 的核心类，用于构建有向图工作流。
#   每个节点是一个处理函数，边定义了数据流动方向。
# - 节点（Node）：图中的处理单元，比如"市场分析师"、"牛市研究员"等。
# - 边（Edge）：节点之间的连接，分为普通边和条件边。
# - 条件边（Conditional Edge）：根据当前状态决定下一个节点，实现动态路由。
# - 状态（State）：在节点间传递的共享数据字典，所有节点都能读写。
# - ToolNode：LangGraph 预置的特殊节点，用于调用工具（如获取股票数据）。
# =============================================================================

# ---------------------------------------------------------------------------
# Python 标准库导入
# ---------------------------------------------------------------------------
# import 语句：把其他模块中的功能引入当前文件使用。
# 以下是 Python 标准库（Python 自带的库，不需要额外安装）。
# Path 比 os.path 更现代、更易读。
# 例如 Path("a/b/c").mkdir(parents=True) 可以递归创建多级目录。
import json  # JSON 序列化/反序列化，用于保存日志。
import logging  # 日志：记录记忆读写等非关键路径的异常。
import os  # 操作系统接口，用于创建目录、拼接路径等。

# json.dump(字典, 文件) 把 Python 字典保存为 JSON 文件。
# json.load(文件) 把 JSON 文件读回 Python 字典。
# 例如 os.makedirs("data/cache") 可以创建文件夹。
from pathlib import Path  # 面向对象的文件路径操作。

# 例如 date(2024, 1, 15) 表示 2024 年 1 月 15 日。
from typing import Any  # 类型提示，提高代码可读性。

# Dict[str, Any] 表示"键为字符串、值为任意类型的字典"。
# Optional[List] 表示"可以是列表，也可以是 None"。
# 类型提示不影响运行，只是给人和 IDE 看的"说明书"。
# ---------------------------------------------------------------------------
# 第三方库导入
# ---------------------------------------------------------------------------
# ToolNode：LangGraph 预置的工具节点
# 它可以将 LLM 的 tool_calls 请求自动转换为工具调用，并返回结果给 LLM
# 详见上方"前置知识-概念 5：ToolNode 的内部机制"
from langgraph.prebuilt import ToolNode

# 导入所有代理创建函数（如 create_market_analyst、create_bull_researcher 等）
# 使用 * 通配符导入，因为代理类型较多，且都在 __all__ 中声明
# __all__ 是模块中定义的一个列表，明确声明了 * 会导入哪些名字
# Python 特有语法说明：from xxx import * 相当于把 xxx 模块中所有公开的名字一次性导入
from tradingagents.agents import *

# 代理状态数据类：定义图中传递的状态结构
# 抽象工具函数：封装了各种数据获取操作（股票数据、新闻、基本面等）
# 这些函数被包装成 ToolNode，供 LLM 代理调用
# 详见上方"前置知识-概念 5：ToolNode 的内部机制"
from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,  # 获取资产负债表（资产、负债、股东权益）
    get_capital_flow,  # 获取 A 股资金流向（主力/超大单/大单）
    get_cashflow,  # 获取现金流量表（经营/投资/筹资活动现金流）
    get_dragon_tiger,  # 获取龙虎榜数据（A 股）
    # 运行时数据流示例：
    #   → 返回 ToolMessage(content="RSI: 72.3, MACD: bullish crossover, Bollinger: upper band...")
    get_fundamentals,  # 获取公司基本面数据（如市盈率、市净率、股息率等）
    get_global_news,  # 获取全球宏观新闻（影响整体市场的宏观事件）
    get_income_statement,  # 获取利润表（收入、成本、利润）
    # 运行时数据流示例：
    #   LLM 输出 tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}}]
    #   → ToolNode 调用 get_stock_data(ticker="AAPL")
    #   → 返回 ToolMessage(content="Date,Open,High,Low,Close,Volume\n2024-01-15,185.5,187.2,...")
    get_indicators,  # 获取技术指标（如 RSI、MACD、布林带等）
    get_insider_transactions,  # 获取内部人交易信息（高管买入/卖出自家股票的记录）
    get_news,  # 获取个股新闻（与该公司相关的新闻报道）
    get_northbound_flow,  # 获取北向资金（A 股宏观资金面）
    get_stock_data,  # 获取股票价格数据（OHLCV：开盘价、最高价、最低价、收盘价、成交量）
    search_stock_news,  # Tavily 个股新闻搜索
    search_web,  # Tavily 通用联网搜索
)

# 金融情景记忆系统：用于存储和检索历史决策经验
# FinancialSituationMemory 内部使用 BM25 算法来检索与当前市场状况最相似的历史经验
# 详见上方"前置知识-概念 8：BM25 检索的通俗概念"
from tradingagents.agents.utils.memory import FinancialSituationMemory

# 配置设置函数：将配置字典应用到全局运行时
# set_config 会把 config 中的参数设置到全局变量中，
# 这样其他模块（如数据获取工具）就能读取到这些配置
from tradingagents.dataflows.config import set_config

# 默认配置字典，包含所有可配置参数（LLM 模型名、最大辩论轮数等）
# DEFAULT_CONFIG 是一个字典，例如：
#   {
#     "llm_provider": "openai",
#     "deep_think_llm": "o3-mini",
#     "quick_think_llm": "gpt-4o-mini",
#     "max_debate_rounds": 1,
#     "max_risk_discuss_rounds": 1,
#     "project_dir": "/path/to/project",
#     ...
#   }
from tradingagents.default_config import DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# 项目内部模块导入
# ---------------------------------------------------------------------------
# LLM 客户端工厂函数：根据 provider 配置创建对应的 LLM 客户端（如 OpenAI、Google 等）
from tradingagents.llm_clients import create_llm_client

# 运行指标（token / 成本 / 逐阶段耗时）。
# WHY：此前系统没有任何 LLM 用量与耗时口径，用户无法知道一次分析花了
# 多少钱、慢在哪一环。
from tradingagents.services.observability import (
    RunMetrics,
    clear_current_metrics,
    set_current_metrics,
)

# 记忆持久化：把角色记忆落到 SQLite，使记忆能跨分析、跨进程存活。
# WHY：``services/runner.py`` 每次分析都会新建一个 TradingAgentsGraph，
# 只放在内存里的记忆会随对象一起消亡，"学习闭环"从未真正闭合。
from tradingagents.storage.memory_store import (
    load_all_memories,
    save_memory,
)
from tradingagents.storage.run_log_store import save_run_log

# 从当前包导入子模块
# 相对导入：. 表示当前包（graph 包），从同级文件导入
from .conditional_logic import ConditionalLogic  # 条件路由逻辑：定义条件边的判断函数
from .propagation import Propagator  # 状态传播：负责创建初始状态和图执行参数
from .reflection import (  # 决策反思：交易后根据收益反思决策质量，并更新记忆
    MEMORY_ROLES,
    Reflector,
    RuleBasedReflector,  # 规则化（非 LLM）记忆写入：分析结束即写入
)
from .setup import GraphSetup  # 图结构搭建：负责创建 StateGraph 并添加节点和边
from .signal_processing import SignalProcessor  # 信号处理：从完整信号文本中提取核心决策词

logger = logging.getLogger(__name__)


# =============================================================================
# 类：TradingAgentsGraph
# =============================================================================
class TradingAgentsGraph:
    """
    交易代理图的主编排类。

    【职责】
    这是整个框架的入口类，负责：
    1. 初始化 LLM 客户端（深度思考 + 快速思考两种模式）
    2. 初始化记忆系统（5 个独立的 FinancialSituationMemory 实例）
    3. 创建工具节点（为不同分析师提供不同的数据获取工具）
    4. 组装子模块（条件逻辑、图搭建、传播器、反思器、信号处理器）
    5. 搭建 LangGraph 工作流图
    6. 执行图流程（propagate 方法）
    7. 记录结果和反思

    【典型用法】
        # 第 1 步：创建交易代理图实例
        graph = TradingAgentsGraph(debug=True)

        # 第 2 步：对某只股票在某个交易日运行完整分析
        final_state, signal = graph.propagate("AAPL", "2024-01-15")

        # 第 3 步：（可选）交易后根据收益进行反思
        graph.reflect_and_remember(returns_losses=0.05)

    【运行时数据流总览】
    当调用 propagate("AAPL", "2024-01-15") 时，内部发生的事情：

    1. 创建初始状态：
       init_agent_state = {
           "messages": [("human", "AAPL")],
           "company_of_interest": "AAPL",
           "trade_date": "2024-01-15",
           "investment_debate_state": {"count": 0, ...},
           "risk_debate_state": {"count": 0, ...},
           "market_report": "",  # 空的，等待填充
           ...
       }

    2. 执行图（简化版流程）：
       START
         → Market Analyst（LLM 分析，可能调用 get_stock_data 工具）
         → Social Analyst（LLM 分析，可能调用 get_news 工具）
         → News Analyst（LLM 分析，可能调用 get_global_news 工具）
         → Fundamentals Analyst（LLM 分析，可能调用 get_fundamentals 工具）
         → Bull Researcher ↔ Bear Researcher（牛熊辩论，循环 max_debate_rounds 轮）
         → Research Manager（综合辩论，做出投资判断）
         → Trader（制定交易计划）
         → Aggressive ↔ Conservative ↔ Neutral（风险辩论，循环 max_risk_discuss_rounds 轮）
         → Portfolio Manager（最终决策）
       END

    3. 返回结果：
       final_state = {  # 完整的最终状态，包含所有报告和决策
           "market_report": "RSI: 72.3, MACD: ...",
           "final_trade_decision": "Based on comprehensive analysis, BUY AAPL...",
           ...
       }
       signal = "BUY"  # 从 final_state["final_trade_decision"] 中提取的简洁决策词
    """

    # -------------------------------------------------------------------------
    # 构造函数：初始化所有组件
    # -------------------------------------------------------------------------
    # Python 语法说明：
    # __init__ 是 Python 的构造函数，在创建对象实例时自动调用。
    # 例如 graph = TradingAgentsGraph() 会自动执行 __init__ 中的代码。
    # self 是对当前对象实例的引用，类似其他语言中的 this。
    def __init__(
        self,
        selected_analysts=None,
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """
        初始化交易代理图。

        参数:
            selected_analysts (list): 要启用的分析师类型列表。可选值：
                - "market"：市场分析师（技术面分析）
                - "social"：社交媒体分析师（情绪分析）
                - "news"：新闻分析师（新闻事件分析）
                - "fundamentals"：基本面分析师（财务报表分析）
            debug (bool): 是否开启调试模式。
                调试模式下会逐块输出图的执行过程，便于观察每个节点的输出。
            config (dict): 配置字典。如果为 None，则使用 DEFAULT_CONFIG。
            callbacks (list): 可选的回调处理器列表，用于监控 LLM 调用和工具执行。

        运行时数据流示例（创建对象时）：
            graph = TradingAgentsGraph(
                selected_analysts=["market", "fundamentals"],
                debug=True,
                config=None,  # 使用 DEFAULT_CONFIG
            )
            # 此时 __init__ 内部执行：
            #   self.debug = True
            #   self.config = DEFAULT_CONFIG（因为 config 是 None）
            #   self.config["llm_provider"] = "openai"
            #   self.config["deep_think_llm"] = "o3-mini"
            #   self.config["quick_think_llm"] = "gpt-4o-mini"
            #   → 创建 deep_thinking_llm（深度思考 LLM 客户端）
            #   → 创建 quick_thinking_llm（快速思考 LLM 客户端）
            #   → 创建 5 个记忆实例
            #   → 创建 4 个工具节点
            #   → 创建 5 个子模块实例
            #   → 搭建并编译 LangGraph 图
            #   → self.graph = 编译后的可执行图对象
        """
        # 保存调试标志。True 时使用 stream() 逐块输出，False 时使用 invoke() 一次性执行。
        self.debug = debug

        # Python 语法说明：config or DEFAULT_CONFIG
        # 这是 Python 的"短路求值"特性：
        #   - 如果 config 不为 None（真值），self.config = config
        #   - 如果 config 为 None（假值），self.config = DEFAULT_CONFIG
        # None 在 Python 中是"假值"，所以 None or B 的结果是 B。
        # 使用传入的配置，如果为 None 则使用默认配置
        self.config = config or DEFAULT_CONFIG

        # 自定义图编排：控制分析师集合与可选阶段（辩论/交易员/风控）
        self.pipeline = self.config.get("pipeline") or {}
        if selected_analysts is None:
            selected_analysts = (
                self.pipeline.get("analysts")
                or ["market", "social", "news", "fundamentals"]
            )

        # Python 语法说明：callbacks or []
        # 同样的短路求值：如果 callbacks 为 None，则用空列表 []。
        # 回调处理器列表，用于外部监控（如统计 token 消耗）
        self.callbacks = callbacks or []

        # 将配置应用到全局运行时（如设置数据缓存目录、API 密钥等）
        # set_config 会把 config 中的参数写入全局变量，
        # 这样其他模块（如 get_stock_data 等工具函数）就能读取到配置
        set_config(self.config)

        # 创建数据缓存目录（如果不存在）
        # os.path.join 用系统正确的路径分隔符拼接路径（Windows 用 \，Linux 用 /）
        # exist_ok=True 表示目录已存在时不会报错（如果没有这个参数，重复创建会抛错）
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # -----------------------------------------------------------------
        # 初始化 LLM 客户端
        # 系统使用两种 LLM：
        #   - deep_thinking_llm：用于需要深度推理的任务（如研究经理、投资组合经理）
        #     通常是推理能力更强但速度较慢、成本较高的模型（如 o3-mini）
        #   - quick_thinking_llm：用于快速响应的任务（如分析师、交易员）
        #     通常是速度快、成本低但推理能力稍弱的模型（如 gpt-4o-mini）
        # -----------------------------------------------------------------

        # 获取特定 LLM 提供商的关键字参数，用于配置推理能力
        # 不同提供商有不同的"思考强度"参数名，这个方法统一处理
        llm_kwargs = self._get_provider_kwargs()

        # 如果提供了回调处理器，将其添加到 LLM 构造参数中
        # Python 语法说明：if self.callbacks:
        # 空列表 [] 是"假值"，非空列表是"真值"。
        # 所以 if self.callbacks: 等价于 if len(self.callbacks) > 0:
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        # 创建深度思考 LLM 客户端
        # create_llm_client 是工厂函数：根据 provider 参数创建不同提供商的客户端
        # Python 语法说明：**llm_kwargs（字典解包）
        #   如果 llm_kwargs = {"reasoning_effort": "high"}
        #   那么 create_llm_client(provider="openai", model="o3-mini", base_url=None, reasoning_effort="high")
        #   ** 会把字典展开成一个个"键=值"形式的参数
        # 使用 config 中的 deep_think_llm 模型（通常是一个更强的推理模型）
        deep_client = create_llm_client(
            provider=self.config["llm_provider"],      # LLM 提供商（如 "openai", "google", "anthropic"）
            model=self.config["deep_think_llm"],        # 深度思考模型名称（如 "o3-mini"）
            base_url=self.config.get("backend_url"),     # 自定义 API 地址（可选）
            **llm_kwargs,                                # 展开提供商特定参数（如 reasoning_effort="high"）
        )

        # 创建快速思考 LLM 客户端
        # 使用 config 中的 quick_think_llm 模型（通常是一个更快的模型）
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        # 获取 LangChain 兼容的 LLM 对象（ChatOpenAI 实例）
        # create_llm_client 返回的是一个客户端包装器，
        # .get_llm() 从中提取出 LangChain 能直接使用的 LLM 对象
        # 之后这些 LLM 对象会被传给各个代理节点，用于调用 LLM 生成回复
        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        # -----------------------------------------------------------------
        # 初始化记忆系统
        # 每个角色有独立的记忆实例，用于存储该角色的历史决策经验
        # FinancialSituationMemory 是一种"情景记忆"：
        #   存储（市场状况, 决策反思）对，在后续决策时检索相关经验
        #
        # 为什么需要 5 个独立的记忆？
        # 每个角色的职责不同，经验也不同。
        # 牛市研究员积累的是"看多分析"的经验，
        # 熊市研究员积累的是"看空分析"的经验，
        # 混在一起会互相干扰，所以分开存储。
        #
        # 运行时数据流示例（记忆存储与检索）：
        #   # 存储：反思后存入经验
        #   bull_memory.add_situations([("市场状况A", "反思：看多分析正确，因为...")])
        #
        #   # 检索：下次分析时，用当前市场状况去搜索相似的历史经验
        #   similar_experiences = bull_memory.get_similarity("当前市场状况B")
        #   # 返回：[("市场状况A", "反思：看多分析正确，因为..."), ...]
        #   # 内部使用 BM25 算法按关键词匹配度排序
        # -----------------------------------------------------------------
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.portfolio_manager_memory = FinancialSituationMemory("portfolio_manager_memory", self.config)

        # -----------------------------------------------------------------
        # 记忆持久化配置
        #   memory_enabled: 是否启用记忆的读写（默认启用）
        #   memory_load_limit: 从 SQLite 恢复记忆时每个角色最多加载的条数
        # WHY 要恢复：TradingAgentsGraph 每次分析都会新建（见 services/runner.py），
        # 只放在内存里的记忆会随对象消亡，检索永远为空。
        # -----------------------------------------------------------------
        self.memory_enabled = bool(self.config.get("memory_enabled", True))
        self.memory_load_limit = max(1, int(self.config.get("memory_load_limit", 200)))

        # 从 SQLite 恢复历史记忆，使记忆跨分析、跨进程存活
        self._load_memories_from_store()

        # -----------------------------------------------------------------
        # 创建工具节点
        # 每个分析师类型有自己专属的工具节点，内含不同的数据获取函数
        # 详见上方"前置知识-概念 5：ToolNode 的内部机制"
        # -----------------------------------------------------------------
        self.tool_nodes = self._create_tool_nodes()

        # -----------------------------------------------------------------
        # 初始化子模块
        # 将功能拆分到不同模块中，遵循"单一职责原则"：
        #   - ConditionalLogic：只管条件路由判断
        #   - GraphSetup：只管图的搭建
        #   - Propagator：只管初始状态和执行参数
        #   - Reflector：只管反思
        #   - SignalProcessor：只管信号提取
        # -----------------------------------------------------------------

        # 条件逻辑模块：决定图中条件边的路由
        # - max_debate_rounds：牛/熊方辩论的最大轮数（1 轮 = 牛方 + 熊方各发言一次）
        # - max_risk_discuss_rounds：风险分析辩论的最大轮数（1 轮 = 激进 + 保守 + 中性各发言一次）
        # 这些参数直接影响条件函数的判断：达到轮数上限时结束辩论
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )

        # 图搭建模块：负责创建 LangGraph StateGraph 并添加节点和边
        # 传入所有依赖：两种 LLM、工具节点、5 个记忆实例、条件逻辑
        # GraphSetup.setup_graph() 会用这些组件搭建完整的 StateGraph
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.portfolio_manager_memory,
            self.conditional_logic,
            pipeline=self.pipeline,
        )

        # 传播器模块：负责创建初始状态和图执行参数
        # 详见上方"前置知识-概念 6：RecursionLimit"
        self.propagator = Propagator()

        # 反思器模块：负责在交易后根据收益反思决策质量，并更新记忆
        # 详见上方"前置知识-概念 7：反思机制的'为什么'"
        # 反思使用快速思考 LLM（反思是总结性任务，不需要深度推理）
        self.reflector = Reflector(self.quick_thinking_llm)

        # 规则化反思写入器：**默认**的记忆写入路径。
        # WHY 单独一个类：Reflector 每次要 5 次 LLM 调用（延迟 + 成本），
        # 而"学习闭环"只需要把当时的情况与决策记下来。默认路径零 LLM 调用。
        self.rule_reflector = RuleBasedReflector()

        # 信号处理器模块：负责从完整的交易信号文本中提取核心决策
        # 详见 signal_processing.py 的注释
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # -----------------------------------------------------------------
        # 状态跟踪变量
        # -----------------------------------------------------------------
        self.curr_state = None       # 当前（最近一次）图执行的最终状态。
                                     # 用于后续反思：reflect_and_remember 会读取 self.curr_state
        self.ticker = None           # 当前分析的股票代码（如 "AAPL"）
                                     # 用于日志文件路径命名
        self.log_states_dict = {}    # 日期 -> 完整状态字典的映射，用于日志记录
                                     # 运行时数据流示例：
                                     #   self.log_states_dict = {
                                     #     "2024-01-15": {"company_of_interest": "AAPL", "market_report": "...", ...},
                                     #     "2024-01-16": {"company_of_interest": "AAPL", "market_report": "...", ...},
                                     #   }

        # -----------------------------------------------------------------
        # 搭建 LangGraph 工作流图
        # 调用 graph_setup.setup_graph() 构建完整的 StateGraph 并编译
        #
        # 这一步完成了 StateGraph 的完整生命周期：
        #   1. 创建：StateGraph(AgentState)
        #   2. add_node：添加所有节点（分析师、研究员、交易员、风险分析师等）
        #   3. add_edge：添加普通边（如 Research Manager → Trader）
        #   4. add_conditional_edges：添加条件边（如 Bull Researcher → 条件判断 → Bear 或 Manager）
        #   5. compile：编译为可执行图
        #
        # 编译后的 self.graph 可以调用 invoke() 或 stream()
        # -----------------------------------------------------------------
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    # -------------------------------------------------------------------------
    # 私有方法：获取 LLM 提供商特定参数
    # -------------------------------------------------------------------------
    # Python 语法说明：方法名前加 _ 表示"私有方法"（约定俗成，不是强制）。
    # 外部代码不应直接调用 _ 开头的方法，但技术上是可以调用的。
    def _get_provider_kwargs(self) -> dict[str, Any]:
        """
        获取特定 LLM 提供商的关键字参数。

        【背景】
        不同的 LLM 提供商对"推理/思考"能力有不同的参数名称：
        - Google：thinking_level（如 "low", "medium", "high"）
        - OpenAI：reasoning_effort（如 "low", "medium", "high"）
        - Anthropic：effort（如 "low", "medium", "high"）

        这个方法根据配置中的 provider 字段，提取对应的参数。

        返回:
            dict: 包含提供商特定参数的关键字参数字典

        运行时数据流示例：
            # 假设 config = {"llm_provider": "openai", "openai_reasoning_effort": "high"}
            kwargs = self._get_provider_kwargs()
            # kwargs = {"reasoning_effort": "high"}
            #
            # 后续创建 LLM 时：
            # create_llm_client(provider="openai", model="o3-mini", **kwargs)
            # 等价于：
            # create_llm_client(provider="openai", model="o3-mini", reasoning_effort="high")

            # 另一个示例：config = {"llm_provider": "google", "google_thinking_level": "medium"}
            kwargs = self._get_provider_kwargs()
            # kwargs = {"thinking_level": "medium"}
        """
        # 初始化空的参数字典
        kwargs = {}
        # 获取 LLM 提供商名称，并转为小写以确保大小写不敏感
        # Python 语法说明：.get("key", "") 是字典的安全取值方法
        #   如果 "llm_provider" 键不存在，返回默认值 "" 而不是报错
        #   .lower() 把字符串转为小写，确保 "OpenAI" 和 "openai" 都能匹配
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            # Google Gemini 的思考级别配置
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            # OpenAI o1/o3 系列模型的推理强度配置
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            # Anthropic Claude 的推理强度配置
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    # -------------------------------------------------------------------------
    # 私有方法：创建工具节点
    # -------------------------------------------------------------------------
    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """
        为不同类型的数据源创建工具节点。

        【什么是 ToolNode？】
        ToolNode 是 LangGraph 预置的一个特殊节点类型。
        当 LLM 输出中包含 tool_calls（工具调用请求）时，
        ToolNode 会自动执行对应的工具函数并返回结果。

        【工具分配策略】
        不同的分析师需要不同的数据源：
        - market 分析师：需要股价数据 + 技术指标
        - social 分析师：需要社交媒体/新闻数据
        - news 分析师：需要新闻 + 内部人交易 + 全球新闻
        - fundamentals 分析师：需要基本面 + 三大财务报表

        返回:
            dict: 键为分析师类型名，值为对应的 ToolNode 实例

        运行时数据流示例（ToolNode 的工作过程）：
            # 假设 Market Analyst 的 LLM 输出：
            # AIMessage(content="Let me check the stock data...",
            #           tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "call_123"}])
            #
            # 条件函数检测到 tool_calls，返回 "tools_market"
            # → 流程路由到 tools_market 节点（即 ToolNode）
            #
            # ToolNode 内部执行：
            #   1. 读取最后一条消息的 tool_calls
            #   2. 找到 get_stock_data 函数
            #   3. 调用 get_stock_data(ticker="AAPL")
            #   4. 把返回值包装成 ToolMessage:
            #      ToolMessage(content="Date,Open,High,Low,Close,Volume\\n2024-01-15,185.5,...",
            #                  tool_call_id="call_123")
            #   5. 将 ToolMessage 添加到 state["messages"]
            #
            # 然后流程回到 Market Analyst，LLM 看到 ToolMessage 中的数据，继续分析
        """
        # 返回一个字典，键是分析师类型名，值是对应的 ToolNode
        # ToolNode 的构造函数接收一个工具函数列表
        return {
            "market": ToolNode(
                [
                    # 核心股票数据工具
                    get_stock_data,      # 获取股票价格数据（OHLCV）
                    # 技术指标工具
                    get_indicators,      # 获取技术指标（如 RSI、MACD、布林带等）
                    # A 股资金面工具
                    get_capital_flow,    # 获取主力资金流向（A 股特有）
                    get_dragon_tiger,    # 获取龙虎榜（A 股特有）
                ]
            ),
            "social": ToolNode(
                [
                    # 新闻工具用于社交媒体情绪分析
                    get_news,            # 获取个股相关新闻
                    search_web,          # 联网补充最新舆情/事件
                ]
            ),
            "news": ToolNode(
                [
                    # 新闻和内部人信息工具
                    get_news,            # 获取个股相关新闻
                    get_global_news,     # 获取全球宏观新闻
                    get_insider_transactions,  # 获取内部人（高管）交易记录
                    get_northbound_flow,  # 获取北向资金（A 股宏观资金面）
                    search_web,          # 联网补充新闻/公告
                    search_stock_news,   # 联网搜索个股新闻
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # 基本面分析工具
                    get_fundamentals,    # 获取公司基本面概况
                    get_balance_sheet,   # 获取资产负债表
                    get_cashflow,        # 获取现金流量表
                    get_income_statement,  # 获取利润表（损益表）
                ]
            ),
        }

    # -------------------------------------------------------------------------
    # 核心方法：执行图流程
    # -------------------------------------------------------------------------
    def propagate(self, company_name, trade_date, horizon: str = "medium"):
        """
        对特定公司在特定交易日运行完整的交易代理图。

        【执行流程】
        1. 创建初始状态（包含公司名、日期、空的辩论状态等）
        2. 调用 graph.stream() 或 graph.invoke() 执行图
        3. 从最终状态中提取交易决策
        4. 记录日志
        5. 返回最终状态和处理后的信号

        参数:
            company_name (str): 股票代码或公司名称（如 "AAPL"）
            trade_date (str): 交易日期（如 "2024-01-15"）

        返回:
            tuple: (final_state, processed_signal)
                - final_state: 图的最终状态字典
                - processed_signal: 处理后的交易信号（如 "BUY", "SELL"）

        运行时数据流示例（完整执行过程）：

            graph = TradingAgentsGraph(debug=False)
            final_state, signal = graph.propagate("AAPL", "2024-01-15")

            # 内部执行过程：

            # 步骤 1：创建初始状态
            init_agent_state = {
                "messages": [("human", "AAPL")],
                "company_of_interest": "AAPL",
                "trade_date": "2024-01-15",
                "investment_debate_state": {"count": 0, "bull_history": "", ...},
                "risk_debate_state": {"count": 0, "aggressive_history": "", ...},
                "market_report": "",
                "sentiment_report": "",
                "news_report": "",
                "fundamentals_report": "",
            }

            # 步骤 2：获取执行参数
            args = {"stream_mode": "values", "config": {"recursion_limit": 100}}

            # 步骤 3：执行图（invoke 模式，一次性执行）
            final_state = graph.invoke(init_agent_state, **args)
            # 图内部执行：
            #   START → Market Analyst → (tools_market ↔ Market Analyst 循环) → Msg Clear Market
            #   → Social Analyst → ... → Fundamentals Analyst → Msg Clear Fundamentals
            #   → Bull Researcher ↔ Bear Researcher (辩论) → Research Manager
            #   → Trader → Aggressive ↔ Conservative ↔ Neutral (辩论) → Portfolio Manager
            #   → END

            # 步骤 4：final_state 现在包含所有结果
            final_state = {
                "company_of_interest": "AAPL",
                "trade_date": "2024-01-15",
                "market_report": "AAPL shows RSI at 72.3, indicating overbought...",
                "sentiment_report": "Social media sentiment is positive...",
                "news_report": "Apple announced record quarterly earnings...",
                "fundamentals_report": "P/E ratio: 28.5, Revenue growth: 15%...",
                "investment_debate_state": {"count": 2, "bull_history": "...", ...},
                "risk_debate_state": {"count": 3, "judge_decision": "...", ...},
                "trader_investment_plan": "Buy 100 shares of AAPL at $185...",
                "final_trade_decision": "Based on comprehensive analysis, BUY AAPL...",
            }

            # 步骤 5：提取信号
            signal = graph.process_signal("Based on comprehensive analysis, BUY AAPL...")
            # signal = "BUY"

            # 返回
            return final_state, "BUY"
        """
        # 以下是可视化的流程图（Mermaid）：propagate 调用链（对应图解指南 6.1 节）
        """
```mermaid
flowchart TD
    P["propagate(company_name='AAPL',<br/>trade_date='2024-01-15')"]

    P --> S1["步骤1: 创建初始状态"]
    S1 --> S1a["self.propagator.create_initial_state('AAPL', '2024-01-15')<br/>→ 返回 init_agent_state 字典"]

    P --> S2["步骤2: 获取执行参数"]
    S2 --> S2a["self.propagator.get_graph_args()<br/>→ 返回 {stream_mode:'values',<br/>config:{recursion_limit:100}}"]

    P --> S3["步骤3: 执行图"]
    S3 --> S3a{"self.debug?"}

    S3a -->|"True 调试模式"| S3b["self.graph.stream(init_agent_state, **args)<br/>→ 返回生成器，逐块输出"]
    S3b --> S3c["for chunk in stream:<br/>  pretty_print(chunk['messages'][-1])<br/>  trace.append(chunk)"]
    S3c --> S3d["final_state = trace[-1]"]

    S3a -->|"False 正常模式"| S3e["self.graph.invoke(init_agent_state, **args)<br/>→ 一次性执行完毕，返回最终状态"]

    S3d & S3e --> S4["步骤4: 保存状态"]
    S4 --> S4a["self.curr_state = final_state<br/>（供后续 reflect_and_remember 使用）"]

    S4 --> S5["步骤5: 记录日志"]
    S5 --> S5a["self._log_state(trade_date, final_state)<br/>→ 写入 JSON 文件"]

    S5 --> S6["步骤6: 返回结果"]
    S6 --> S6a["return final_state,<br/>self.process_signal(final_state['final_trade_decision'])"]

    style P fill:#2C3E50,color:#fff
    style S3a fill:#E74C3C,color:#fff
    style S6a fill:#27AE60,color:#fff
```
以上是可视化的流程图：propagate 调用链。
        """
        # 记录当前分析的股票代码
        self.ticker = company_name

        # -----------------------------------------------------------------
        # 步骤 0：开启本次运行的指标采集（token / 成本 / 逐阶段耗时）
        #
        # 此前系统没有任何 LLM 用量与耗时口径，用户无法知道一次分析花了
        # 多少钱、慢在哪里。这里建立 run 级指标对象，并在 finally 中落库。
        #
        # 注意：必须与 LLM 调用发生在**同一线程**（ContextVar 不跨线程），
        # 因此 set_current_metrics 要在调用图的线程内完成 —— 本函数正是在
        # 该线程内被调用的。
        # -----------------------------------------------------------------
        from tradingagents.runtime import set_current_trade_date

        # 工具层据此过滤未来数据（公告披露日、研报日期、宏观发布日等）。
        set_current_trade_date(str(trade_date))
        metrics = RunMetrics(
            ticker=str(company_name),
            trade_date=str(trade_date),
            source=str(self.config.get("run_source") or "unknown"),
            horizon=str(horizon),
        )
        set_current_metrics(metrics)

        # -----------------------------------------------------------------
        # 步骤 1：创建初始状态
        # Propagator.create_initial_state() 返回一个字典，包含：
        #   - messages：初始消息列表（以 human 角色发送公司名）
        #   - company_of_interest：目标公司
        #   - trade_date：交易日期
        #   - investment_debate_state：投资辩论初始状态（空的）
        #   - risk_debate_state：风险辩论初始状态（空的）
        #   - 各种报告字段（初始为空字符串）
        # -----------------------------------------------------------------
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date, horizon
        )

        # 获取图执行参数（如 recursion_limit 防止无限循环）
        # args = {"stream_mode": "values", "config": {"recursion_limit": 100}}
        # recursion_limit 详见上方"前置知识-概念 6：RecursionLimit"
        args = self.propagator.get_graph_args()

        # -----------------------------------------------------------------
        # 步骤 2：执行图
        # 有两种模式：
        #   - debug 模式：使用 stream() 逐块获取输出，可观察每个节点的执行
        #   - 正常模式：使用 invoke() 一次性执行完毕，只返回最终状态
        #
        # 详见上方"前置知识-概念 3：invoke() vs stream() 的区别"
        # -----------------------------------------------------------------
        try:
            if self.debug:
                # 调试模式：逐块执行图
                # graph.stream() 返回一个生成器（generator），每次 yield 一个节点的输出
                # 生成器是 Python 的一种"惰性序列"——它不会一次性生成所有结果，
                # 而是每次迭代时才计算下一个值，适合处理大量数据或流式输出
                trace = []  # 用于记录所有节点的输出
                # for chunk in ... 会逐块迭代 stream() 的输��
                # 每次迭代，chunk 是当前节点执行后的状态快照
                for chunk in self.graph.stream(init_agent_state, **args):
                    # 如果当前 chunk 没有消息，跳过（某些节点可能不产生消息）
                    if len(chunk["messages"]) == 0:
                        pass
                    else:
                        # pretty_print() 以可读格式打印最后一条消息
                        # chunk["messages"][-1] 取消息列表的最后一条（最新消息）
                        chunk["messages"][-1].pretty_print()
                        # 将当前 chunk 添加到跟踪列表
                        trace.append(chunk)

                # 最终状态是跟踪列表中的最后一个元素
                # 因为 stream() 每次输出一个节点执行后的状态，
                # 最后一个输出就是图执行完毕后的最终状态
                final_state = trace[-1] if trace else init_agent_state
            else:
                # 正常模式：一次调用完成整个图执行
                # graph.invoke() 返回图的最终状态
                # invoke 会自动从 START 开始，按边的定义执行所有节点，直到 END
                # **args 展开为 stream_mode="values", config={"recursion_limit": 100}
                final_state = self.graph.invoke(init_agent_state, **args)
        finally:
            # 无论成功、异常还是被取消，都要收尾指标并落库
            metrics.finish()
            clear_current_metrics()
            try:
                save_run_log(metrics.to_dict())
            except Exception:  # 落库失败不能影响分析结果
                logger.debug("运行指标落库失败", exc_info=True)

        # -----------------------------------------------------------------
        # 步骤 3：保存当前状态（用于后续反思）
        # -----------------------------------------------------------------
        self.curr_state = final_state

        # -----------------------------------------------------------------
        # 步骤 4：记录日志
        # 将最终状态保存到 JSON 文件，便于后续分析
        # -----------------------------------------------------------------
        self._log_state(trade_date, final_state)

        # -----------------------------------------------------------------
        # 步骤 4.5：写入角色记忆（规则化，零 LLM 调用）
        # 把"当时的市场状况 + 本次决策"存进各角色记忆并落库，
        # 使下一次分析时 get_memories() 能检索到内容，
        # 提示词中的 past_memory_str 不再是空串。
        # -----------------------------------------------------------------
        self.remember_analysis(final_state)

        # -----------------------------------------------------------------
        # 步骤 5：返回最终状态和处理后的交易信号
        # process_signal() 从完整信号文本中提取出简洁的决策词
        # 例如："Based on comprehensive analysis, BUY AAPL..." → "BUY"
        # -----------------------------------------------------------------
        # 正常流程由 Portfolio Manager 写入 final_trade_decision；
        # 若用户自定义流程或节点异常导致该字段为空，则回退到交易员/研究经理方案，
        # 避免直接索引 KeyError 或空信号。
        decision_text = (
            final_state.get("final_trade_decision")
            or final_state.get("trader_investment_plan")
            or final_state.get("investment_plan")
            or "HOLD"
        )
        final_state["final_trade_decision"] = decision_text
        # 结构化决策：prompt 要求组合经理在末尾输出 JSON 块。解析失败时
        # 不影响原有信号提取；解析成功时把置信度/论点/引用/风险标记带出去。
        from tradingagents.services.decision_schema import (
            parse_structured_decision,
        )

        structured = parse_structured_decision(decision_text)
        signal = self.process_signal(decision_text)
        if (
            structured.get("structured")
            and structured.get("valid")
            and signal == "HOLD"
            and structured.get("decision") not in (None, "HOLD")
        ):
            # 文本提取失败退化成 HOLD，但 JSON 明确给出方向时，以 JSON 为准。
            signal = structured["decision"]
        final_state["decision_structured"] = structured
        return final_state, signal

    # -------------------------------------------------------------------------
    # 私有方法：记录状态日志
    # -------------------------------------------------------------------------
    def _log_state(self, trade_date, final_state):
        """
        将图的最终状态记录到 JSON 日志文件。

        【日志内容】
        包含以下关键信息：
        - 公司名和交易日期
        - 四份分析报告（市场、情绪、新闻、基本面）
        - 投资辩论状态（牛/熊方历史、对话历史、裁判决策）
        - 风险辩论状态（激进/保守/中性方历史、裁判决策）
        - 交易员投资计划和最终交易决策

        参数:
            trade_date: 交易日期
            final_state: 图的最终状态字典

        运行时数据流示例：
            # final_state 中的内容：
            # final_state = {
            #     "company_of_interest": "AAPL",
            #     "trade_date": "2024-01-15",
            #     "market_report": "RSI: 72.3, MACD: bullish...",
            #     "sentiment_report": "Social sentiment: 78% positive...",
            #     "news_report": "Apple announced record earnings...",
            #     "fundamentals_report": "P/E: 28.5, Revenue: $394B...",
            #     "investment_debate_state": {
            #         "bull_history": "Bull: Strong revenue growth...",
            #         "bear_history": "Bear: Valuation too high...",
            #         "judge_decision": "Bull argument is stronger...",
            #         "count": 2,
            #     },
            #     "trader_investment_plan": "Buy 100 shares at $185...",
            #     "risk_debate_state": {
            #         "aggressive_history": "Aggressive: Leverage up...",
            #         "judge_decision": "Moderate approach recommended...",
            #         "count": 3,
            #     },
            #     "final_trade_decision": "BUY AAPL with target $200...",
            # }
            #
            # 日志保存路径：eval_results/AAPL/TradingAgentsStrategy_logs/full_states_log_2024-01-15.json
        """
        # 构建日志字典，从 final_state 中提取关键字段
        # self.log_states_dict[str(trade_date)] 用日期字符串作为键
        # 这样多次调用 propagate 后，log_states_dict 会累积多个日期的状态
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # 创建日志目录（如果不存在）
        # Path.mkdir(parents=True) 会递归创建所有父目录
        # 例如 Path("a/b/c").mkdir(parents=True) 会依次创建 a/、a/b/、a/b/c/
        # exist_ok=True 表示目录已存在时不报错
        directory = Path(f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        # 将日志字典写入 JSON 文件
        # Python 语法说明：with open(...) as f:（上下文管理器）
        #   with 语句会自动管理资源：进入时打开文件，退出时（无论正常退出还是出错）自动关闭文件。
        #   这样就不需要手动调用 f.close()，更安全。
        # indent=4 使 JSON 格式化输出（每层缩进 4 个空格），便于人类阅读
        # encoding="utf-8" 确保中文字符正确保存（不指定可能用系统默认编码导致乱码）
        # f"..." 是 f-string，{self.ticker} 和 {trade_date} 会被替换为变量值
        with open(
            f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/full_states_log_{trade_date}.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4)

    # -------------------------------------------------------------------------
    # 记忆管理：加载 / 写入 / 落库
    # -------------------------------------------------------------------------
    def _memories_by_role(self) -> dict[str, FinancialSituationMemory]:
        """
        返回 ``{role: 记忆实例}`` 映射。

        role 与 ``reflection.MEMORY_ROLES`` 的取值一致（bull/bear/
        invest_judge/trader/portfolio_manager），与 SQLite 中
        ``agent_memories.role`` 的取值也一致。映射关系只在
        ``reflection.MEMORY_ROLES`` 里定义一次，避免两处各写一份后跑偏。
        """
        instances = {
            "bull_memory": self.bull_memory,
            "bear_memory": self.bear_memory,
            "invest_judge_memory": self.invest_judge_memory,
            "trader_memory": self.trader_memory,
            "portfolio_manager_memory": self.portfolio_manager_memory,
        }
        return {role: instances[attr] for role, attr in MEMORY_ROLES.items()}

    def _load_memories_from_store(self) -> dict[str, int]:
        """
        从 SQLite 恢复各角色记忆。

        WHY：``services/runner.py`` 每次分析都新建一个 TradingAgentsGraph，
        内存里的记忆会随对象消亡。没有这一步，``get_memories()`` 永远返回
        空列表，提示词里的 ``past_memory_str`` 永远是空串。

        返回：
            dict[str, int]: 每个角色恢复的条数
        """
        if not self.memory_enabled:
            return {}
        try:
            stored = load_all_memories(limit_per_role=self.memory_load_limit)
        except Exception as exc:  # 记忆恢复失败不应阻断分析
            logger.warning("读取历史记忆失败，本次以空记忆运行：%s", exc)
            return {}

        loaded: dict[str, int] = {}
        for role, memory in self._memories_by_role().items():
            rows = stored.get(role) or []
            pairs = [
                (row.get("situation") or "", row.get("recommendation") or "")
                for row in rows
                if row.get("situation")
            ]
            if not pairs:
                continue
            # 存储按时间倒序返回，内存按写入顺序追加 → 反转后保持时间正序
            memory.add_situations(list(reversed(pairs)))
            loaded[role] = len(pairs)
        return loaded

    def _persist_memories_to_store(
        self, before: dict[str, int]
    ) -> int:
        """
        把"上次写入之后新增"的记忆条目落到 SQLite。

        参数：
            before (dict): 写入前的各角色条目数（``{role: count}``）

        返回：
            int: 实际落库的条数
        """
        if not self.memory_enabled:
            return 0
        written = 0
        for role, memory in self._memories_by_role().items():
            start = int(before.get(role, 0))
            for index in range(start, len(memory.documents)):
                try:
                    save_memory(
                        role=role,
                        situation=memory.documents[index],
                        recommendation=memory.recommendations[index],
                        ticker=self.ticker or "",
                        trade_date=str(
                            (self.curr_state or {}).get("trade_date") or ""
                        ),
                        meta={"source": "reflector"},
                    )
                    written += 1
                except Exception as exc:  # 落库失败不应阻断分析
                    logger.warning("记忆落库失败（role=%s）：%s", role, exc)
        return written

    def remember_analysis(
        self,
        final_state: dict[str, Any] | None = None,
        outcome: str | None = None,
        persist: bool = True,
    ) -> dict[str, int]:
        """
        规则化记忆写入（**默认路径，不调用 LLM**）。

        把本次分析的"市场状况 + 最终决策"写入 5 个角色记忆，使下一次分析
        时 ``get_memories()`` 能检索到内容，提示词中的 ``past_memory_str``
        不再是空串。

        参数：
            final_state (dict | None): 图执行后的最终状态，默认取 ``self.curr_state``
            outcome (str | None): 可选的结果描述（如"5 日前瞻收益 +3.2%"）
            persist (bool): 是否同时写入 SQLite

        返回：
            dict[str, int]: 每个角色写入的条数
        """
        state = final_state or self.curr_state
        if not state:
            return {}
        try:
            return self.rule_reflector.write(
                state,
                self._memories_by_role(),
                ticker=self.ticker or state.get("company_of_interest") or "",
                trade_date=state.get("trade_date") or "",
                outcome=outcome,
                persist=persist,
            )
        except Exception as exc:  # 记忆写入失败不应影响分析结果
            logger.warning("记忆写入失败：%s", exc)
            return {}

    # -------------------------------------------------------------------------
    # 反思方法：根据交易收益反思决策质量
    # -------------------------------------------------------------------------
    def reflect_and_remember(self, returns_losses):
        """
        根据交易收益反思所有代理的决策，并更新各自的记忆。

        【注意：这是"显式（LLM）路径"】
        本方法会为 5 个角色各调用一次 LLM（共 5 次模型调用），因此**不会**
        在正常分析流程中自动触发。默认的记忆写入路径是零 LLM 调用的
        :meth:`remember_analysis`（在 :meth:`propagate` 结束时自动执行）。
        本方法适用于"已知真实盈亏、需要一段自然语言复盘"的场景。

        【反思流程】
        对以下 5 个角色分别进行反思：
        1. 牛市研究员（Bull Researcher）：反思看多分析的准确性
        2. 熊市研究员（Bear Researcher）：反思看空分析的准确性
        3. 交易员（Trader）：反思交易决策的质量
        4. 投资裁判（Invest Judge）：反思投资辩论裁判的评判
        5. 投资组合经理（Portfolio Manager）：反思风险管理的决策

        每个角色的反思结果会存入对应的 FinancialSituationMemory，
        以便在未来的决策中检索相关经验；随后新增条目会写入 SQLite。

        参数:
            returns_losses: 收益/损失数据，用于评估决策质量

        运行时数据流示例：
            # 交易完成后调用反思
            graph.reflect_and_remember(returns_losses=0.05)  # 赚了 5%

            # 内部对每个角色执行反思：
            #
            # 1. 提取市场状况（合并四份报告）
            #    situation = "RSI: 72.3...\n\nSocial sentiment: 78% positive...\n\n..."
            #
            # 2. 提取角色决策内容
            #    bull_debate_history = "Bull: AAPL has strong revenue growth..."
            #
            # 3. 调用 LLM 反思
            #    LLM 收到：
            #      - 收益：+5%
            #      - 决策：看多 AAPL
            #      - 市场状况：RSI 超买，但营收增长强劲...
            #    LLM 返回反思报告：
            #      "The bull analysis was correct. Revenue growth was the key driver.
            #       However, RSI indicated overbought conditions which were not adequately
            #       addressed. Future recommendation: consider technical indicators more..."
            #
            # 4. 存入记忆
            #    bull_memory.add_situations([("RSI: 72.3...\n\n...", "反思报告...")])
            #    → 下次分析时，BM25 会根据当前市场状况检索这条反思
        """
        # 以下是可视化的流程图（Mermaid）：反思调用链（对应图解指南 6.2 节）
        """
```mermaid
flowchart TD
    R["reflect_and_remember(returns_losses=0.05)"]

    R --> R1["self.reflector.reflect_bull_researcher(<br/>curr_state, returns, bull_memory)"]
    R --> R2["self.reflector.reflect_bear_researcher(<br/>curr_state, returns, bear_memory)"]
    R --> R3["self.reflector.reflect_trader(<br/>curr_state, returns, trader_memory)"]
    R --> R4["self.reflector.reflect_invest_judge(<br/>curr_state, returns, invest_judge_memory)"]
    R --> R5["self.reflector.reflect_portfolio_manager(<br/>curr_state, returns, portfolio_manager_memory)"]

    R1 --> C1["每个 reflect_xxx 内部："]
    C1 --> C1a["1. 提取市场状况（合并4份报告）"]
    C1 --> C1b["2. 提取该角色的决策内容"]
    C1 --> C1c["3. 调用 LLM 反思<br/>输入：市场状况+决策+收益<br/>输出：反思报告"]
    C1 --> C1d["4. memory.add_situations(<br/>[(市场状况, 反思报告)])"]
    C1 --> C1e["→ 存入记忆，供下次 BM25 检索"]

    style R fill:#8E44AD,color:#fff
    style C1 fill:#3498DB,color:#fff
```
以上是可视化的流程图：反思调用链。
        """
        if not self.curr_state:
            logger.warning("没有可反思的状态，跳过 reflect_and_remember")
            return {}

        # 记录写入前的条目数，便于把新增条目落到 SQLite
        before = {
            role: len(memory.documents)
            for role, memory in self._memories_by_role().items()
        }

        # 反思牛市研究员的分析
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        # 反思熊市研究员的分析
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        # 反思交易员的决策
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        # 反思投资裁判的决策
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        # 反思投资组合经理的决策
        self.reflector.reflect_portfolio_manager(
            self.curr_state, returns_losses, self.portfolio_manager_memory
        )

        # 落库：让 LLM 反思出的经验也能跨进程存活
        self._persist_memories_to_store(before)
        return {role: 1 for role in self._memories_by_role()}

    # -------------------------------------------------------------------------
    # 信号处理方法：提取核心交易决策
    # -------------------------------------------------------------------------
    def process_signal(self, full_signal):
        """
        从完整的交易信号文本中提取核心决策。

        【为什么需要处理？】
        图的最终输出是一段详细的分析文本（可能包含推理过程、风险提示等）。
        但实际使用时只需要一个简洁的决策词（如 BUY、SELL、HOLD）。
        这个方法使用 LLM 从长文本中提取出简洁的决策。

        参数:
            full_signal (str): 完整的交易信号文本

        返回:
            str: 核心决策词（BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL）

        运行时数据流示例：
            full_signal = (
                "Based on the comprehensive analysis of market conditions, "
                "fundamentals, and risk factors, I recommend a BUY rating "
                "for AAPL with a target price of $200. The company shows "
                "strong revenue growth of 15% year-over-year..."
            )
            # ↑ 这是一段很长的分析文本

            signal = graph.process_signal(full_signal)
            # signal = "BUY"
            # ↑ 提取出的简洁决策词

            # 信号处理器内部使用 LLM 来提取：
            #   系统提示词："Extract the rating as exactly one of: BUY, OVERWEIGHT,
            #               HOLD, UNDERWEIGHT, SELL. Output only the single rating word."
            #   LLM 收到完整的分析文本，只返回一个决策词
        """
        return self.signal_processor.process_signal(full_signal)
