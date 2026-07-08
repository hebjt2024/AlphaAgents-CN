# =============================================================================
# 模块名称：technical_indicators_tools.py
# 模块功能：技术指标数据获取工具 - 获取股票技术分析指标
# =============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是 LangChain？】
#   LangChain 是一个帮助开发者构建"AI 智能体应用"的框架。
#   它的核心思想是：让大语言模型（LLM，如 GPT）不仅能聊天，还能"使用工具"。
#   比如你问 AI"苹果公司的 RSI 指标是多少？"，AI 不只是说"我不知道"，
#   而是能主动调用一个叫 get_indicators 的函数去查询数据，然后回答你。
#
# 【什么是 @tool 装饰器？它如何工作？】
#   @tool 是 LangChain 提供的装饰器，它的作用是把"普通 Python 函数"
#   变成"LLM 可以识别和调用的工具"。
#
#   工作原理（逐步说明）：
#   1. @tool 装饰器读取函数的以下信息：
#      - 函数名（如 get_indicators）→ 作为工具的名称
#      - 函数的 docstring（三引号文档字符串）→ 作为工具的描述
#      - 函数参数的类型注解和 Annotated 描述 → 作为参数说明
#   2. @tool 把这些信息打包成一个"工具描述"（tool schema），格式类似 JSON：
#      {
#        "name": "get_indicators",
#        "description": "获取指定股票代码的单个技术指标分析报告...",
#        "parameters": {
#          "symbol": {"type": "string", "description": "ticker symbol..."},
#          "indicator": {"type": "string", "description": "technical indicator..."},
#          ...
#        }
#      }
#   3. 这个工具描述会发送给 LLM。LLM 看到后就知道"有这样一个工具可以用"
#   4. 当 LLM 决定调用工具时，它会生成一个函数调用请求，包含工具名和参数值
#   5. LangChain 框架接收请求，找到对应的 Python 函数，用 LLM 提供的参数调用它
#   6. 函数的返回值被送回给 LLM，LLM 根据结果生成最终回答
#
#   【通俗比喻】@tool 就像把你的函数登记到"工具菜单"上。
#   LLM 看菜单点菜（决定用哪个工具），厨房（你的函数）做好菜（返回数据），
#   LLM 再把菜端给用户（组织成自然语言回答）。
#
# 【Annotated 如何成为参数 schema？】
#   参数声明：indicator: Annotated[str, "technical indicator to get..."]
#   - str → 告诉 LLM 这个参数是字符串类型
#   - "technical indicator to get..." → 告诉 LLM 这个参数是什么含义
#   LangChain 的 @tool 装饰器会读取 Annotated 里的描述文字，
#   把它放进工具的参数 schema 中，这样 LLM 就知道每个参数该填什么。
#   没有这个描述，LLM 不知道参数要传什么值，就无法正确调用工具。
#
# 【什么是技术分析（Technical Analysis）？】
#   - 通过分析历史价格和成交量数据来预测未来价格走势
#   - 与基本面分析（分析公司价值）不同，技术分析关注价格行为本身
#   - 核心理念：市场行为包含一切信息，历史会重演
#
# 【什么是技术指标（Technical Indicators）？】
#   - RSI（相对强弱指数）：衡量价格变动的速度和幅度，判断超买/超卖
#     RSI > 70 通常被认为"超买"（可能要跌），RSI < 30 "超卖"（可能要涨）
#   - MACD（移动平均线收敛散度）：跟踪趋势方向和动量
#   - SMA/EMA（简单/指数移动平均线）：平滑价格数据，识别趋势
#   - 布林带（Bollinger Bands）：衡量价格波动性
#   - 成交量指标：确认价格趋势的强度
#
# 【什么是逗号分隔的容错处理？】
#   - LLM 有时会传入逗号分隔的多个指标，如 "rsi, macd"
#   - 本模块智能识别这种情况，自动拆分并逐个处理
#   - 这是一种"防御性编程"的实践，提高系统的鲁棒性
#   【什么是防御性编程？】写代码时考虑到"输入可能不规范"，
#   提前处理各种意外情况，让程序更健壮。
# ============================================================================
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：技术分析智能体想获取苹果公司(AAPL)的 RSI 指标
#
#   第 1 步：LLM 决定调用工具
#     LLM 分析任务后，生成函数调用请求：
#     {tool: "get_indicators", args: {symbol: "AAPL", indicator: "rsi",
#       curr_date: "2026-07-07", look_back_days: 30}}
#
#   第 2 步：LangChain 框架调用 Python 函数
#     get_indicators(symbol="AAPL", indicator="rsi",
#                    curr_date="2026-07-07", look_back_days=30)
#
#   第 3 步：容错处理
#     indicator.split(",") → ["rsi"]  （只有一个，不触发拆分逻辑）
#     直接走单指标路径
#
#   第 4 步：调用路由层
#     route_to_vendor("get_indicators", "AAPL", "rsi", "2026-07-07", 30)
#     → interface.py 根据配置选择供应商（如 yfinance）
#     → 调用 get_stock_stats_indicators_window("AAPL", "rsi", ...)
#     → 返回 RSI 分析报告字符串
#
#   第 5 步：结果返回给 LLM
#     LLM 拿到 RSI 报告，组织成自然语言回答用户
# =============================================================================

# ---------------------------------------------------------------------------
# 导入区：每个导入模块的作用说明
# ---------------------------------------------------------------------------

# @tool: LangChain 工具装饰器，将普通函数转换为智能体可调用的工具
# 【导入语法】from langchain_core.tools import tool
# 从 langchain_core 包的 tools 模块中导入 tool 装饰器
# langchain_core 是 LangChain 的核心库，提供基础工具和接口
from langchain_core.tools import tool

# Annotated: 类型注解包装器，用于为参数添加元数据描述
# 【为什么需要 Annotated？】
# 普通类型注解 str 只告诉 Python "这是字符串"，但 LLM 需要知道
# "这个字符串代表什么"（如股票代码、日期、指标名称等）。
# Annotated[str, "描述"] 就是在类型上附加这段描述。
from typing import Annotated

# route_to_vendor: 统一的数据供应商路由接口
# 将数据请求转发到配置的供应商（如 Yahoo Finance、Alpha Vantage 等）
# 这是 dataflows/interface.py 中定义的核心函数（详见该文件的注释）
from tradingagents.dataflows.interface import route_to_vendor


# =============================================================================
# get_indicators - 技术指标数据获取工具
# =============================================================================
@tool  # 注册为 LangChain 工具
# 【@tool 做了什么？】
# 它读取下面的函数名(get_indicators)、docstring、参数注解，
# 生成一个工具描述对象，注册到 LangChain 的工具列表中。
# 之后 LLM 就能"看到"这个工具并决定是否调用它。
def get_indicators(
    # symbol 参数：股票代码
    # Annotated[str, "ticker symbol of the company"] 的含义：
    #   - str：参数类型是字符串
    #   - "ticker symbol of the company"：告诉 LLM "这里填公司股票代码"
    # LLM 会根据这段描述，在调用时填入如 "AAPL"、"TSLA" 等值
    symbol: Annotated[str, "ticker symbol of the company"],

    # indicator 参数：技术指标名称
    # 注意：虽然设计上是单个指标，但 LLM 可能传入逗号分隔的多个指标
    # 例如 indicator="rsi, macd, bollinger" 会被自动拆分为多个独立调用
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],

    # curr_date 参数：当前交易日期
    # 日期格式为 YYYY-mm-dd（如 "2026-07-07"）
    # 用于确定获取哪个时间点的数据
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],

    # look_back_days 参数：回顾天数
    # 默认值为 30，表示获取过去 30 天的数据用于计算指标
    # 更长的回顾期能提供更可靠的信号，但可能不够及时
    # 【什么是默认值？】参数名: 类型 = 默认值
    # 如果 LLM 调用时不传这个参数，就用 30
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    获取指定股票代码的单个技术指标分析报告

    此函数设计的初衷是"每次调用获取一个指标"，但 LLM 有时会传入
    逗号分隔的多个指标名称。为此，代码内置了容错逻辑：
    自动检测逗号分隔，拆分后逐个调用，然后合并结果。

    参数：
        symbol (str): 公司股票代码，例如 "AAPL"、"TSM"
        indicator (str): 单个技术指标名称，例如 "rsi"、"macd"
                        建议每次调用只传入一个指标
        curr_date (str): 当前交易日期，格式为 YYYY-mm-dd
        look_back_days (int): 回顾天数，默认为 30 天

    返回：
        str: 格式化的技术指标分析报告字符串

    常用技术指标参考：
        - "rsi": 相对强弱指数（0-100，>70 超买，<30 超卖）
        - "macd": 移动平均线收敛散度（趋势跟踪）
        - "sma": 简单移动平均线（价格平滑）
        - "ema": 指数移动平均线（近期价格权重更高）
        - "bollinger": 布林带（波动性指标）
        - "volume": 成交量分析
        - "stochastic": 随机指标（动量振荡器）

    容错处理说明：
        如果 LLM 传入 indicator="rsi, macd, bollinger"：
        1. 代码检测到逗号，将其拆分为 ["rsi", "macd", "bollinger"]
        2. 对每个指标分别调用 route_to_vendor
        3. 用 "\n\n" 连接所有结果并返回
        4. 如果只有一个指标，直接调用，不做拆分
    """
    # ---- 容错处理：处理 LLM 可能传入逗号分隔的多个指标 ----
    # 使用字符串的 split(",") 方法按逗号分割
    # 【split 方法】"a,b,c".split(",") → ["a", "b", "c"]
    # i.strip() 去除每个指标名称前后的空格
    # 【strip 方法】" rsi ".strip() → "rsi"
    # if i.strip() 过滤掉空字符串（防止 "rsi," 这种末尾逗号的情况）
    # 【列表推导式】[表达式 for 变量 in 可迭代对象 if 条件]
    # 这一行等价于：
    #   indicators = []
    #   for i in indicator.split(","):
    #       cleaned = i.strip()
    #       if cleaned:
    #           indicators.append(cleaned)
    indicators = [i.strip() for i in indicator.split(",") if i.strip()]

    # 如果拆分后有多于一个指标（即 LLM 传入了逗号分隔的多个指标）
    # len() 返回列表长度，> 1 表示有多个指标
    if len(indicators) > 1:
        results = []  # 用于收集每个指标的结果（创建空列表）
        for ind in indicators:  # 遍历每个指标名称
            # 逐个调用 route_to_vendor，获取每个指标的分析结果
            # route_to_vendor 会根据配置路由到具体供应商实现
            # 参数：方法名、股票代码、指标名、当前日期、回顾天数
            results.append(route_to_vendor("get_indicators", symbol, ind, curr_date, look_back_days))
        # 用两个换行符连接所有结果，使输出清晰可读
        # "\n\n".join(results) 把列表中的字符串用 "\n\n" 连接成一个字符串
        # 例如：["结果1", "结果2"] → "结果1\n\n结果2"
        return "\n\n".join(results)

    # 如果只有一个指标（正常情况），直接调用
    # indicator.strip() 确保去除可能的前后空格
    # 【为什么用 indicator 而不是 indicators[0]？】
    # 因为如果原始输入没有逗号，indicators 列表只有一个元素，
    # 但直接用 indicator.strip() 更直观，且避免了不必要的列表操作
    return route_to_vendor("get_indicators", symbol, indicator.strip(), curr_date, look_back_days)
