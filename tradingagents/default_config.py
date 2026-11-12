# ============================================================================
# 默认配置模块
# 定义了 TradingAgents 框架的所有可配置参数及其默认值
# 用户可以通过复制 DEFAULT_CONFIG 并修改特定字段来自定义行为
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件定义了 TradingAgents 框架的所有默认配置参数。
# 以下是需要了解的核心概念：
#
# 1. 字典（dict）作为配置容器：
#    - Python 字典（dict）是键值对集合，用花括号 {} 定义。
#    - 使用字典作为配置容器的优点：
#      * 灵活：可以随时添加/修改配置项
#      * 可读：键名描述配置含义，值是配置内容
#      * 易扩展：嵌套字典可以组织相关配置
#    - 示例：
#        config = {"model": "gpt-5", "max_tokens": 4096}
#        print(config["model"])  # 输出 "gpt-5"
#
# 2. os 模块（路径操作）：
#    - os.path.dirname(__file__)：获取当前文件所在目录的路径
#    - os.path.abspath()：将相对路径转为绝对路径
#    - os.path.join()：跨平台路径拼接（自动使用正确的分隔符）
#    - os.getenv(key, default)：从环境变量获取值，不存在时返回默认值
#    - 示例：
#        # 获取当前文件所在目录的绝对路径
#        base_dir = os.path.abspath(os.path.dirname(__file__))
#        # 拼接子目录路径
#        cache_dir = os.path.join(base_dir, "data_cache")
#
# 3. LLM（大语言模型）配置：
#    - deep_think_llm（深度思考模型）：用于复杂推理任务
#      如撰写分析报告、制定交易策略、综合多方观点
#      通常使用更强的模型（如 gpt-5.2），速度较慢但推理能力强
#    - quick_think_llm（快速思考模型）：用于简单快速任务
#      如数据格式化、简单判断、工具调用
#      通常使用更轻量的模型（如 gpt-5-mini），速度快成本低
#    - 这种"双模型"设计是性能与成本的平衡：
#      关键决策用强模型，辅助任务用轻量模型
#
# 4. 多空辩论机制（Bull-Bear Debate）：
#    - 多头（Bull）：认为价格会上涨，寻找看涨理由
#    - 空头（Bear）：认为价格会下跌，寻找看跌理由
#    - 辩论机制：多头研究员和空头研究员交替发表观点
#    - max_debate_rounds 控制辩论轮数：
#      * 1轮：多头发言 → 空头发言 → 结束（快速但可能不充分）
#      * 3轮：多空交替3次（中等充分）
#      * 5轮：多空交替5次（充分辩论但耗时更长）
#
# 5. 数据供应商（Data Vendors）：
#    - 框架支持多个数据源，按数据类型分别配置：
#      * core_stock_apis：核心股票数据（价格、成交量、公司信息）
#      * technical_indicators：技术指标（MACD、RSI、布林带等）
#      * fundamental_data：基本面数据（财务报表、估值指标）
#      * news_data：新闻数据（公司新闻、市场动态）
#    - 每类数据可选择不同供应商：
#      * yfinance：免费，使用 Yahoo Finance 数据，无需 API 密钥
#      * alpha_vantage：需要免费 API 密钥，数据更全面
#
# 6. 本文件的运行时数据流：
#    程序启动
#    → DEFAULT_CONFIG 在模块加载时被创建
#    → os.path 操作计算项目目录和缓存目录的绝对路径
#    → os.getenv 读取环境变量（如 TRADINGAGENTS_RESULTS_DIR）
#    → main.py 或 cli/main.py 调用 DEFAULT_CONFIG.copy() 创建配置副本
#    → 根据用户选择修改配置（如更改模型、数据源）
#    → TradingAgentsGraph(config=config) 使用配置初始化
#    → 各智能体根据配置选择 LLM 模型和数据源
# ============================================================================

# os 模块：Python 标准库，提供操作系统接口
# 本文件主要使用：
#   os.path：路径操作（拼接、获取绝对路径等）
#   os.getenv：读取环境变量
#   os.environ：访问环境变量字典
import os


def _env_str(name: str, default: str) -> str:
    """读取环境变量；为空时回退到默认值。"""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；无效时回退到默认值。"""
    try:
        return int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# DEFAULT_CONFIG 字典：项目所有默认配置的集合
# 每个键值对代表一个可配置项，含义如下：
# ----------------------------------------------------------------------------
# 这是一个字典（dict），用花括号 {} 定义
# 键（key）是配置项名称（字符串），值（value）是配置内容
# 使用 .copy() 创建副本后可以安全修改，不影响原始配置
DEFAULT_CONFIG = {
    # ====== 目录路径配置 ======
    # 项目根目录：使用 os.path 获取当前文件所在目录的绝对路径
    # __file__ 是 Python 内置变量，表示当前脚本的文件路径
    # os.path.dirname(__file__) 获取当前文件所在目录
    # os.path.abspath() 转为绝对路径（不包含 ../ 或 ./ 等相对路径）
    # os.path.join() 跨平台路径拼接（Windows 用 \，Linux/Mac 用 /）
    # "." 表示当前目录，所以最终就是 tradingagents/ 目录的绝对路径
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),

    # 结果保存目录：分析结果（如报告、决策记录）的存储位置
    # 优先从环境变量 TRADINGAGENTS_RESULTS_DIR 读取，如果没设置则默认为 "./results"
    # os.getenv(key, default)：
    #   如果环境变量 key 存在，返回其值
    #   如果不存在，返回 default（第二个参数）
    # 用户可以通过设置环境变量来自定义结果保存位置：
    #   export TRADINGAGENTS_RESULTS_DIR=/path/to/results
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),

    # 数据缓存目录：缓存已获取的股票数据，避免重复请求 API
    # 位于项目 dataflows/data_cache 目录下
    # 缓存的作用：
    #   1. 减少网络请求（相同的股票数据只获取一次）
    #   2. 加速重复分析（第二次分析同一股票时直接读取缓存）
    #   3. 降低 API 调用次数（避免触发频率限制）
    # os.path.join() 将项目目录和子目录路径拼接
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),

    # ====== LLM（大语言模型）配置 ======
    # LLM 服务提供商：可选 "openai"、"anthropic"、"google" 等
    # 可通过 .env 的 TRADINGAGENTS_LLM_PROVIDER 覆盖
    "llm_provider": _env_str("TRADINGAGENTS_LLM_PROVIDER", "anthropic"),

    # 深度思考模型：用于需要复杂推理的任务（如撰写分析报告、制定策略）
    # 可通过 .env 的 TRADINGAGENTS_DEEP_THINK_LLM 覆盖
    # 深度思考模型用于：
    #   - 综合分析师的数据生成分析报告
    #   - 多头/空头研究员的深度分析
    #   - 风险管理者的风险评估
    #   - 交易员的最终决策
    "deep_think_llm": _env_str("TRADINGAGENTS_DEEP_THINK_LLM", "MiniMax-M3"),

    # 快速思考模型：用于简单、快速响应的任务（如数据格式化、简单判断）
    # 可通过 .env 的 TRADINGAGENTS_QUICK_THINK_LLM 覆盖
    # 快速思考模型用于：
    #   - 工具调用的参数解析
    #   - 数据格式转换
    #   - 简单的条件判断
    "quick_think_llm": _env_str("TRADINGAGENTS_QUICK_THINK_LLM", "MiniMax-M3.1-Flash"),

    # 后端 API 地址：LLM 服务的 API 端点
    # 可通过 .env 的 TRADINGAGENTS_BACKEND_URL 覆盖；WebUI 中也可临时覆盖
    # 替换示例（使用代理）：
    #   "backend_url": "https://your-proxy.com/v1"
    "backend_url": _env_str(
        "TRADINGAGENTS_BACKEND_URL", "https://api.minimax.cn/anthropic"
    ),

    # ====== 各供应商特有的思考配置 ======
    # 这些参数控制 LLM 在推理时投入的"思考"量
    # None 表示使用默认值，由 API 决定

    # Google Gemini 的思考级别：控制模型推理深度
    # None 表示使用默认值；可选 "high"（高）或 "minimal"（最低）
    # "high"：启用深度思考，模型会进行更深入的推理
    # "minimal"：最小化思考，响应更快但推理较浅
    "google_thinking_level": None,

    # OpenAI 的推理努力程度：控制 o1/o3 等推理模型的思考深度
    # None 表示使用默认值；可选 "medium"、"high"、"low"
    # 注意：此参数主要影响 OpenAI 的推理系列模型（如 o1、o3）
    # 对非推理模型（如 gpt-4o）可能没有效果
    "openai_reasoning_effort": None,

    # Anthropic Claude 的思考努力程度
    # None 表示使用默认值；可选 "high"、"medium"、"low"
    # 控制 Claude 4.5+ 和 4.6 模型的 token 使用量和响应详尽程度
    "anthropic_effort": None,

    # ====== 辩论与讨论设置 ======
    # 多空辩论最大轮数：多头研究员和空头研究员之间辩论的回合数
    # 每轮辩论双方各发表一次观点，值越大辩论越充分但耗时也越长
    # 辩论流程（以 max_debate_rounds=2 为例）：
    #   第1轮：多头研究员发表看涨观点 → 空头研究员反驳
    #   第2轮：多头研究员再发表 → 空头研究员再反驳
    #   结束 → 研究管理智能体总结辩论结果
    "max_debate_rounds": _env_int("TRADINGAGENTS_MAX_DEBATE_ROUNDS", 1),

    # 风险讨论最大轮数：激进、保守、中性三种风险偏好的辩论回合数
    # 风险讨论流程（以 max_risk_discuss_rounds=1 为例）：
    #   第1轮：激进型发言 → 保守型发言 → 中立型发言
    #   结束 → 投资组合管理智能体总结风险评估
    "max_risk_discuss_rounds": _env_int(
        "TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS", 1
    ),

    # 最大递归限制：防止 LangGraph 工作流陷入无限循环的保护机制
    # 当图执行步骤超过此值时，系统会强制终止
    # LangGraph 是 LangChain 的图编排框架，用于定义智能体之间的执行流程
    # 图（Graph）由节点（Node，即智能体）和边（Edge，即数据流）组成
    # 100 步通常足够完成一次完整的股票分析
    "max_recur_limit": 100,

    # ====== 数据供应商配置 ======
    # 类别级别的数据源配置：为每类数据指定默认供应商
    # 每个键对应的值可以是 "yfinance"（免费，无需 API 密钥）或 "alpha_vantage"（需要免费 API 密钥）
    "data_vendors": {
        "core_stock_apis": "akshare,baostock",       # 核心股票数据：akshare 优先，baostock 备选
        "technical_indicators": "akshare",            # 技术指标：akshare（baostock 不支持）
        "fundamental_data": "akshare,baostock",       # 基本面数据：akshare 优先，baostock 备选
        "news_data": "akshare",                       # 新闻数据：akshare（baostock 不支持）
        "capital_flow": "akshare",                    # 资金流向：akshare（东方财富）
    },

    # ====== 模拟数据开关 ======
    # 默认关闭（fail-closed）：当 AkShare / BaoStock / yfinance 等真实数据源
    # 全部失败时，直接报错，而不是用 cache_provider 生成的模拟行情继续分析。
    # 交易类系统默认绝不能把伪造数据当作真实行情输出。
    # 仅用于开发/联调时，可通过环境变量显式开启：
    #   ALLOW_SYNTHETIC_DATA=1
    "allow_synthetic_data": os.getenv("ALLOW_SYNTHETIC_DATA", "0").lower()
    in ("1", "true", "yes", "on"),

    # ====== 自定义图编排 ======
    # 控制哪些阶段参与分析。Portfolio Manager（最终裁决）始终保留。
    # WebUI 会覆盖这些值；CLI/定时任务可直接修改配置。
    "pipeline": {
        "analysts": ["market", "social", "news", "fundamentals"],
        "enable_debate": True,   # 多空辩论（Bull/Bear + Research Manager）
        "enable_trader": True,   # 交易员节点
        "enable_risk": True,     # 三方风险辩论（依赖交易员）
    },

    # ====== 策略选择 ======
    # 默认只启用核心策略组合，避免每次分析都运行全部 15 个策略。
    # WebUI 可在“策略选择”中覆盖；CLI/定时任务可修改此列表。
    "strategy_keys": [
        "bull_trend",
        "ma_golden_cross",
        "volume_breakout",
        "shrink_pullback",
        "bottom_volume",
        "box_oscillation",
    ],

    # ====== 工具级别的数据源配置 ======
    # 可以为单个工具函数指定不同于类别默认的数据源
    # 工具级别的配置优先级高于类别级别
    # 优先级：tool_vendors > data_vendors（更具体的配置覆盖更通用的配置）
    "tool_vendors": {
        # 示例：强制 "get_stock_data" 这个工具使用 alpha_vantage，覆盖类别默认值
        # 取消注释并修改即可启用工具级别的数据源覆盖
        # "get_stock_data": "alpha_vantage",
    },
}
