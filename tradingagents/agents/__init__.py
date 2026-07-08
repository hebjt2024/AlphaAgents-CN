# =============================================================================
# 模块名称：agents/__init__.py
# 模块功能：智能体包初始化文件 - 统一导出所有智能体相关的类、函数和状态
#
# =============================================================================

# =============================================================================
# 【前置知识】
# =============================================================================
#
# 本文件是 agents 智能体包的初始化文件（__init__.py）。
# 以下是需要了解的核心概念：
#
# 1. __init__.py 文件：
#    - Python 包的标识文件，告诉 Python 解释器该目录是一个包
#    - 当包被导入时，__init__.py 中的代码会自动执行
#    - 用于控制包的公开接口（通过 __all__ 列表）
#    - 提供便捷的导入路径，用户可以直接从包名导入，无需深入到子模块
#
# 2. 相对导入（Relative Import）：
#    - 使用 . 前缀表示从当前包（或父包）导入
#    - 例如：from .utils.agent_utils import create_msg_delete
#      表示从当前包下的 utils 子包中的 agent_utils 模块导入
#    - 相对导入的优势：代码更具可移植性，不依赖绝对包名
#
# 3. __all__ 列表：
#    - 定义了包对外公开的符号列表
#    - 当用户使用 from tradingagents.agents import * 时，只导入 __all__ 中的内容
#    - 这是一种"白名单"机制，控制包的公共 API
#    - 不在 __all__ 中的内容仍然可以通过完整路径导入
#
# 4. LangChain 智能体（Agent）概念：
#    - 在 LangChain 中，智能体是能自主调用工具和 LLM 完成任务的 AI 程序
#    - 智能体的核心能力：
#      * 推理：使用 LLM 分析问题和制定计划
#      * 工具调用：调用外部工具获取数据或执行操作
#      * 记忆：保留对话历史和上下文
#    - 在 TradingAgents 中，每个智能体负责特定领域的分析任务
#
# 5. TradingAgents 的智能体架构：
#    智能体分层设计，模拟真实交易团队的组织结构：
#
#    分析师层（Analysts）→ 收集和分析市场数据
#      ├── 市场分析师（Market Analyst）：分析价格走势和技术指标
#      ├── 新闻分析师（News Analyst）：分析相关新闻的影响
#      ├── 社交媒体分析师（Social Media Analyst）：分析舆论情绪
#      └── 基本面分析师（Fundamentals Analyst）：分析财务报表
#
#    研究员层（Researchers）→ 多空辩论，评估投资机会
#      ├── 多头研究员（Bull Researcher）：寻找看涨理由
#      └── 空头研究员（Bear Researcher）：寻找看跌理由
#
#    风险管理层（Risk Management）→ 从不同风险偏好评估
#      ├── 激进型（Aggressive Debator）：追求高回报，容忍高风险
#      ├── 保守型（Conservative Debator）：注重安全，偏好低风险
#      └── 中立型（Neutral Debator）：平衡风险和回报
#
#    管理层（Managers）→ 协调和整合
#      ├── 研究管理智能体（Research Manager）：总结多空辩论
#      └── 投资组合管理智能体（Portfolio Manager）：总结风险评估
#
#    交易层（Trader）→ 做出最终决策
#      └── 交易员（Trader）：综合所有信息，给出买卖建议
#
# 6. 工具类模块（Utils）：
#    - agent_utils：智能体工具函数（如创建消息删除函数）
#    - agent_states：智能体状态定义（AgentState, InvestDebateState, RiskDebateState）
#    - memory：记忆系统（FinancialSituationMemory），基于 BM25 算法检索相似历史案例
#
# 7. 本文件的运行时数据流：
#    用户启动分析引擎
#    → import tradingagents.agents 执行 __init__.py
#    → 导入所有智能体创建函数和状态类
#    → TradingAgentsGraph 使用这些函数创建智能体实例：
#      create_market_analyst(llm, toolkit) → 市场分析师 Agent
#      create_bull_researcher(llm) → 多头研究员 Agent
#      create_trader(llm) → 交易员 Agent
#      ...
#    → 构建工作流图，定义智能体之间的执行顺序
#    → 分析引擎按图执行：分析师 → 研究员 → 风险管理 → 交易员
# =============================================================================

# ---------------------------------------------------------------------------
# 工具模块（Utils）导入
# ---------------------------------------------------------------------------

# create_msg_delete: 工厂函数，创建消息删除函数（用于清空对话历史）
# "工厂函数"是一种设计模式：返回新创建的函数的函数
# 在 LangChain 中，对话历史会不断增长，需要定期清理以控制 token 使用量
# create_msg_delete 返回一个函数，该函数可以删除指定数量的历史消息
from .utils.agent_utils import create_msg_delete

# AgentState: 主智能体状态类，继承自 MessagesState，贯穿整个交易流程
# 在 LangGraph 中，"状态"（State）是在图节点之间传递的数据容器
# AgentState 包含：
#   - messages：对话消息列表（分析师报告、研究员观点等）
#   - company_of_interest：当前分析的股票代码
#   - risk_debate_state：风险辩论的当前状态
#   - investment_debate_state：投资辩论的当前状态
#
# InvestDebateState: 投资辩论状态，记录多头/空头辩论过程
# 包含多头和空头的历次发言内容
#
# RiskDebateState: 风险辩论状态，记录激进/保守/中立三方辩论
# 包含三种风险偏好分析师的历次发言内容
from .utils.agent_states import AgentState, InvestDebateState, RiskDebateState

# FinancialSituationMemory: 金融情景记忆系统，基于 BM25 算法进行相似度检索
# 记忆系统的作用：
#   1. 存储历史分析案例（股票代码 + 分析结果 + 实际收益）
#   2. 当遇到类似股票时，检索相似案例作为参考
#   3. 根据"反思"（reflect_and_remember）更新记忆
# BM25 算法：一种文本相似度检索算法，用于快速找到最相似的历史案例
from .utils.memory import FinancialSituationMemory

# ---------------------------------------------------------------------------
# 分析智能体（Analysts）导入
# ---------------------------------------------------------------------------
# 这四个分析智能体负责收集和分析不同类型的市场数据
# 每个分析师使用 LLM 和工具集（toolkit）分析特定领域的数据
# 分析结果以消息形式存入 AgentState.messages，供后续智能体使用

# create_fundamentals_analyst: 创建基本面分析智能体
# 分析公司财务报表（资产负债表、利润表、现金流量表）和估值指标
# 数据来源：yfinance 或 alpha_vantage 的财务数据 API
from .analysts.fundamentals_analyst import create_fundamentals_analyst

# create_market_analyst: 创建市场分析智能体
# 分析股票价格走势、技术指标和市场趋势
# 技术指标包括：MACD、RSI、布林带、移动平均线等
from .analysts.market_analyst import create_market_analyst

# create_news_analyst: 创建新闻分析智能体
# 分析公司新闻和全球宏观新闻对股价的影响
# 评估新闻的情感倾向（利好/利空/中性）和影响程度
from .analysts.news_analyst import create_news_analyst

# create_social_media_analyst: 创建社交媒体分析智能体
# 分析社交媒体情绪和舆论趋势
# 数据来源：Reddit、Twitter 等社交媒体平台的讨论
from .analysts.social_media_analyst import create_social_media_analyst

# ---------------------------------------------------------------------------
# 研究智能体（Researchers）导入
# ---------------------------------------------------------------------------
# 这两个研究智能体进行多空辩论，从不同角度审视投资机会
# 多头和空头研究员交替发言，形成多轮辩论
# 辩论结果由研究管理智能体总结

# create_bear_researcher: 创建空头研究员智能体
# 从悲观角度分析，寻找投资风险和负面因素
# 会阅读分析师的数据，寻找看跌的理由
from .researchers.bear_researcher import create_bear_researcher

# create_bull_researcher: 创建多头研究员智能体
# 从乐观角度分析，寻找投资机会和正面因素
# 会阅读分析师的数据，寻找看涨的理由
from .researchers.bull_researcher import create_bull_researcher

# ---------------------------------------------------------------------------
# 风险管理智能体（Risk Management）导入
# ---------------------------------------------------------------------------
# 这三个风险分析师从不同风险偏好角度评估投资决策
# 激进、保守、中立三种观点形成风险讨论
# 讨论结果由投资组合管理智能体总结

# create_aggressive_debator: 创建激进型风险分析师
# 倾向于承担更高风险以追求更高回报
# 会关注收益潜力而非下行风险
from .risk_mgmt.aggressive_debator import create_aggressive_debator

# create_conservative_debator: 创建保守型风险分析师
# 注重本金安全，偏好低风险投资
# 会关注下行风险和资本保护
from .risk_mgmt.conservative_debator import create_conservative_debator

# create_neutral_debator: 创建中立型风险分析师
# 在风险和回报之间寻求平衡
# 会综合考虑上行潜力和下行风险
from .risk_mgmt.neutral_debator import create_neutral_debator

# ---------------------------------------------------------------------------
# 管理智能体（Managers）导入
# ---------------------------------------------------------------------------

# create_research_manager: 创建研究管理智能体
# 协调研究团队的工作流，整合各方分析结果
# 在多空辩论结束后，总结双方观点并给出研究结论
from .managers.research_manager import create_research_manager

# create_portfolio_manager: 创建投资组合管理智能体
# 管理整体投资组合，进行资产配置和风险控制
# 在风险讨论结束后，总结三种风险偏好观点并给出风险评估
from .managers.portfolio_manager import create_portfolio_manager

# ---------------------------------------------------------------------------
# 交易智能体（Trader）导入
# ---------------------------------------------------------------------------

# create_trader: 创建交易员智能体
# 根据研究结果和风险管理建议，制定具体的交易执行方案
# 这是分析流程的最后一个智能体，输出最终的买卖决策
# 决策内容包含：
#   - 决策类型（BUY/SELL/HOLD）
#   - 决策理由（综合分析师数据、多空辩论、风险评估）
#   - 建议的仓位大小
from .trader.trader import create_trader


# ---------------------------------------------------------------------------
# __all__ 列表 - 定义包的公开接口
# ---------------------------------------------------------------------------
# 当用户使用 from tradingagents.agents import * 时，
# 只会导入 __all__ 中列出的这些符号。
# 不在列表中的内部实现细节不会被导入，实现了信息隐藏。
# 这种设计的好处：
#   1. 简化用户导入：只需 from tradingagents.agents import create_market_analyst
#   2. 隐藏内部细节：用户不需要知道模块结构
#   3. 降低耦合：内部模块重构不影响公开 API
__all__ = [
    # 工具类
    "FinancialSituationMemory",  # 金融情景记忆系统
    "AgentState",                # 主智能体状态
    "create_msg_delete",         # 消息删除工厂函数
    "InvestDebateState",         # 投资辩论状态
    "RiskDebateState",           # 风险辩论状态

    # 研究员
    "create_bear_researcher",    # 创建空头研究员
    "create_bull_researcher",    # 创建多头研究员

    # 管理智能体
    "create_research_manager",   # 创建研究管理智能体

    # 分析智能体
    "create_fundamentals_analyst",    # 创建基本面分析智能体
    "create_market_analyst",          # 创建市场分析智能体
    "create_neutral_debator",         # 创建中立型风险分析师
    "create_news_analyst",            # 创建新闻分析智能体
    "create_aggressive_debator",      # 创建激进型风险分析师
    "create_portfolio_manager",       # 创建投资组合管理智能体
    "create_conservative_debator",    # 创建保守型风险分析师
    "create_social_media_analyst",    # 创建社交媒体分析智能体

    # 交易智能体
    "create_trader",             # 创建交易员智能体
]
