# ============================================================================
# TradingAgents 主入口文件
# 这是整个项目的启动脚本，演示如何使用 TradingAgentsGraph 进行股票分析
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件是整个 TradingAgents 项目的入口脚本。以下是需要了解的核心概念：
#
# 1. 模块导入（Import）：
#    - Python 使用 import 语句导入其他模块中定义的类、函数或变量。
#    - 语法：from 模块路径 import 名称
#    - 示例：from tradingagents.graph.trading_graph import TradingAgentsGraph
#      表示从 tradingagents 包的 graph 子包的 trading_graph 模块中导入 TradingAgentsGraph 类
#    - 模块路径使用点号（.）分隔，对应文件系统的目录结构
#
# 2. 环境变量与 dotenv：
#    - 环境变量是操作系统级别的配置，用键值对存储（如 API_KEY=sk-xxx）。
#    - .env 文件是一种将环境变量存储在项目根目录的方式。
#    - python-dotenv 库（pip install python-dotenv）可以加载 .env 文件。
#    - load_dotenv() 读取 .env 文件，将其中的变量加载到 os.environ 字典中。
#    - 为什么用 .env？避免在代码中硬编码敏感信息（如 API 密钥），更安全。
#
# 3. 字典的 .copy() 方法：
#    - 字典（dict）是 Python 的键值对数据结构。
#    - dict.copy() 创建字典的"浅拷贝"（shallow copy）。
#    - 浅拷贝：创建新的字典对象，但值的引用仍指向原对象。
#    - 为什么要 copy？因为 DEFAULT_CONFIG 是全局共享的，直接修改会影响所有使用者。
#    - 示例：
#        config = DEFAULT_CONFIG.copy()  # 创建副本
#        config["deep_think_llm"] = "gpt-5-mini"  # 只修改副本，不影响 DEFAULT_CONFIG
#
# 4. TradingAgentsGraph（核心编排类）：
#    - 这是整个多智能体交易框架的"大脑"。
#    - 它基于 LangGraph（LangChain 的图编排框架）构建。
#    - 工作流程：
#      分析师团队收集数据 → 研究员多空辩论 → 风险管理评估 → 交易员做出决策
#    - 使用方式：
#        ta = TradingAgentsGraph(config=config)  # 创建实例
#        _, decision = ta.propagate("NVDA", "2024-05-10")  # 执行分析
#
# 5. 数据供应商（Data Vendors）：
#    - 框架支持多个数据源，按数据类型分别配置。
#    - yfinance：免费，使用 Yahoo Finance 数据，无需 API 密钥。
#    - alpha_vantage：需要免费注册获取 API 密钥，数据更全面。
#    - 每类数据（股票价格、技术指标、财务报表、新闻）可以独立选择数据源。
#
# 6. 本文件的运行时数据流：
#    用户运行 python main.py
#    → load_dotenv() 加载 .env 文件中的 API 密钥
#    → config = DEFAULT_CONFIG.copy() 创建配置副本
#    → 修改 config 中的模型、数据源等设置
#    → TradingAgentsGraph(config=config) 初始化分析引擎
#    → ta.propagate("NVDA", "2024-05-10") 启动分析：
#      → 市场分析师获取 NVDA 价格和技术指标
#      → 新闻分析师获取相关新闻
#      → 基本面分析师获取财务报表
#      → 多头研究员分析看涨理由
#      → 空头研究员分析看跌理由
#      → 多空辩论（1 轮）
#      → 风险管理评估（激进/保守/中立）
#      → 交易员做出最终决策
#    → 打印决策结果（如 "BUY" 或 "SELL" + 详细理由）
# ============================================================================

# 从 graph 模块导入核心编排类 TradingAgentsGraph
# 这个类是整个多智能体交易框架的"大脑"，负责协调所有智能体协作
# 导入路径解析：
#   tradingagents → 项目主包
#   graph → 子包，包含图编排相关代码
#   trading_graph → 模块文件
#   TradingAgentsGraph → 类名
from tradingagents.graph.trading_graph import TradingAgentsGraph

# 导入默认配置字典，包含所有可配置的参数
# DEFAULT_CONFIG 是一个字典，定义了所有参数的默认值
# 用户可以复制此字典并修改特定字段来自定义配置
from tradingagents.default_config import DEFAULT_CONFIG

# python-dotenv 库：用于从 .env 文件中加载环境变量（如 API 密钥等敏感信息）
# 这样可以避免在代码中硬编码密钥，更安全
# 安装方式：pip install python-dotenv
# .env 文件示例：
#   OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
#   ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
#   GOOGLE_API_KEY=AIzaxxxxxxxxxxxx
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
# .env 文件通常包含 OPENAI_API_KEY、ANTHROPIC_API_KEY 等敏感信息
# 加载后，这些变量会通过 os.getenv() 或 os.environ 访问
# load_dotenv() 会：
#   1. 在当前目录及父目录中查找 .env 文件
#   2. 读取文件中的每一行，解析为 key=value 格式
#   3. 将键值对加载到 os.environ 字典中
#   4. 之后代码中可以通过 os.getenv("OPENAI_API_KEY") 获取密钥
load_dotenv()

# ----------------------------------------------------------------------------
# 创建自定义配置
# ----------------------------------------------------------------------------
# 使用 .copy() 复制默认配置，避免修改原始字典影响其他地方的引用
# .copy() 创建字典的浅拷贝（新字典对象，但嵌套的字典/列表仍共享引用）
# 如果直接 config = DEFAULT_CONFIG（不 copy），修改 config 会影响 DEFAULT_CONFIG
config = DEFAULT_CONFIG.copy()

# 设置"深度思考"模型：用于需要复杂推理的任务（如分析报告、策略制定）
# gpt-5-mini 是 OpenAI 的轻量级模型，速度快、成本低
# 注意：生产环境建议使用 gpt-5.2 等更强的模型以获得更好的分析质量
config["deep_think_llm"] = "gpt-5-mini"

# 设置"快速思考"模型：用于简单、快速响应的任务（如数据查询、简单判断）
config["quick_think_llm"] = "gpt-5-mini"

# 设置辩论轮数：多空双方研究员辩论的最大轮次
# 设为 1 表示只进行一轮辩论，值越大辩论越充分但耗时越长
# 每轮辩论：多头研究员发表观点 → 空头研究员反驳 → 进入下一轮
config["max_debate_rounds"] = 1

# ----------------------------------------------------------------------------
# 配置数据供应商
# ----------------------------------------------------------------------------
# data_vendors 字典指定每类数据使用哪个数据源
# yfinance 是默认选项，免费且不需要 API 密钥
# alpha_vantage 是备选方案，需要注册获取免费 API 密钥
config["data_vendors"] = {
    "core_stock_apis": "yfinance",           # 核心股票数据（价格、成交量等）
    "technical_indicators": "yfinance",      # 技术指标（MACD、RSI、布林带等）
    "fundamental_data": "yfinance",          # 基本面数据（财报、资产负债表等）
    "news_data": "yfinance",                 # 新闻数据
}

# ----------------------------------------------------------------------------
# 初始化 TradingAgentsGraph
# ----------------------------------------------------------------------------
# debug=True：开启调试模式，会输出更多日志信息，方便排查问题
# config=config：传入我们自定义的配置
# TradingAgentsGraph 的初始化过程：
#   1. 读取配置参数
#   2. 创建 LLM 实例（根据 llm_provider 和模型名称）
#   3. 创建各个智能体（分析师、研究员、风险管理者、交易员）
#   4. 构建工作流图（定义智能体之间的执行顺序和数据流）
ta = TradingAgentsGraph(debug=True, config=config)

# ----------------------------------------------------------------------------
# 执行前向传播（Forward Propagation）
# ----------------------------------------------------------------------------
# propagate() 是核心分析方法，它会让所有智能体协作分析一支股票
# 参数1：股票代码（ticker symbol），"NVDA" 是英伟达的股票代码
# 参数2：分析日期，格式为 "YYYY-MM-DD"，表示基于该日期之前的数据进行分析
# 返回值：
#   - 第一个返回值（_）：分析过程中的中间状态数据（这里用下划线忽略）
#   - decision：最终的投资决策，包含买入/卖出/持有建议及理由
# Python 的多返回值解包：_, decision = ta.propagate(...)
#   _ 是 Python 惯例，表示"不关心这个返回值"
#   decision 是最终的投资决策文本
_, decision = ta.propagate("NVDA", "2024-05-10")

# 打印最终的投资决策
# decision 通常包含：
#   - 决策类型（BUY/SELL/HOLD）
#   - 决策理由（详细的分析逻辑）
#   - 建议的仓位大小
#   - 风险提示
print(decision)

# ----------------------------------------------------------------------------
# 反思与记忆（可选步骤）
# ----------------------------------------------------------------------------
# reflect_and_remember() 用于事后反思：根据实际收益来评估之前的决策是否正确
# 参数 1000 表示本次投资的收益（正数=盈利，负数=亏损）
# 这个功能帮助系统"从错误中学习"，提升未来决策质量
# 注释掉是因为这只是演示代码，实际使用时可以根据交易结果调用
# ta.reflect_and_remember(1000)  # 参数是持仓收益
