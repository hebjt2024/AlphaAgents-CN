# =============================================================================
# 模块名称：interface.py
# 模块功能：数据流接口路由层模块
# =============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是"数据流层"？为什么需要它？】
#   TradingAgents 是一个由多个 AI 智能体（Agent）协作的股票分析系统。
#   这些智能体在分析股票时，需要获取大量市场数据：股价、技术指标、
#   财务报表、新闻、内部人交易等。
#
#   获取这些数据的方式有很多种。比如，获取苹果公司（AAPL）的股价，
#   你可以从 Yahoo Finance（雅虎财经）免费获取，也可以从 Alpha Vantage
#   （需要 API Key 的付费/免费混合服务）获取。
#
#   问题来了：如果直接在每个智能体里写死"用 Yahoo Finance 获取股价"，
#   那么当 Yahoo Finance 服务器宕机、或者频率受限时，整个系统就挂了。
#   而且，如果以后想换成别的数据源，需要修改散落在各处的代码。
#
#   解决方案就是"数据流接口层"（也就是本文件）：
#   - 上层智能体只需要调用一个统一的函数名（如 route_to_vendor("get_stock_data", ...)），
#     不需要知道数据最终来自哪个网站。
#   - 本模块根据配置文件，自动决定用哪个数据源。
#   - 如果主数据源失败了，自动切换到备份数据源（这叫"降级"）。
#
# 【什么是 Python 模块（module）和 import？】
#   一个 .py 文件就是一个"模块"。模块里可以定义函数、变量、类。
#   如果 A 模块想用 B 模块里的东西，就用 `import B` 把 B 引入进来。
#   本文件（interface.py）会 import 其他几个模块，从而能调用它们的函数。
#
# 【什么是字典（dict）？】
#   字典是 Python 里最常用的数据结构之一，类似其他语言的"哈希表/映射表"。
#   它是"键值对"的集合：{键1: 值1, 键2: 值2}
#   例如：{"名字": "张三", "年龄": 25}
#   通过 键 可以快速查到对应的 值：person["名字"] 得到 "张三"。
#
# 【什么是列表（list）？】
#   列表是有序的元素集合，用方括号表示：[元素1, 元素2, 元素3]
#   例如：["yfinance", "alpha_vantage"]
#   可以通过下标访问：my_list[0] 得到第一个元素。
#
# 【什么是函数（function）？】
#   函数是一段可重复使用的代码块，接收输入（参数），产生输出（返回值）。
#   例如：def add(a, b): return a + b  定义了一个加法函数。
#   调用时：add(2, 3) 返回 5。
#
# 【什么是 *args 和 **kwargs？】
#   这是 Python 的特殊语法，用于"接收任意数量的参数"：
#   - *args：把所有"位置参数"（按位置传递的参数）打包成一个元组（tuple）。
#     例如 func(1, 2, 3) 中，*args 等于 (1, 2, 3)
#   - **kwargs：把所有"关键字参数"（参数名=值 的形式）打包成一个字典。
#     例如 func(a=1, b=2) 中，**kwargs 等于 {"a": 1, "b": 2}
#   它们让函数可以灵活地接收各种参数，然后原封不动地转发给其他函数。
#
# 【什么是 try / except？】
#   这是 Python 的错误处理机制（异常处理）：
#     try:
#         可能出错的代码
#     except 某种错误:
#         出错后执行的代码
#   如果 try 里的代码运行成功，就跳过 except；如果出了指定类型的错误，
#   就执行 except 里的代码。这样程序不会因为一个错误就整个崩溃。
#
# ============================================================================
# 设计模式说明（用大白话解释）
# ============================================================================
#
# 【策略模式（Strategy Pattern）—— "换轮胎"】
#   想象你开车去不同地方：
#   - 去雪地，换上雪地轮胎
#   - 去赛道，换上高性能轮胎
#   - 去越野，换上越野轮胎
#   车还是那辆车，只是根据情况换不同的"策略"（轮胎）。
#
#   在本模块中：
#   - 同一个方法名（如 "get_stock_data"）对应多个供应商实现函数。
#   - VENDOR_METHODS 字典就是"轮胎库"，存着每种轮胎。
#   - route_to_vendor 函数就是"司机"，根据配置选择用哪个轮胎。
#   - 运行时可以随时换轮胎（切换供应商），不影响上层的开车（调用）。
#
# 【责任链模式（Chain of Responsibility）—— "踢皮球"】
#   想象一个客服流程：
#   - 客户问题先到一线客服（主供应商）
#   - 一线客服处理不了 → 转给二线（备选供应商1）
#   - 二线处理不了 → 转给三线（备选供应商2）
#   - 三线也处理不了 → 报错"无法处理"
#   每一层只处理自己能处理的，处理不了就传给下一层。
#
#   在本模块中，route_to_vendor 实现了"降级链"（Fallback Chain）：
#   - 先尝试主供应商（如 alpha_vantage）
#   - 如果遇到频率限制错误（AlphaVantageRateLimitError），就"踢"给下一个
#   - 一直尝试直到成功，或者所有供应商都失败则报错
#   注意：只有"频率限制"这种错误才会触发降级，其他错误（如网络断开）
#   会直接抛给调用者，因为换一个供应商通常也解决不了网络问题。
#
# 【外观模式（Facade Pattern）—— "前台接待"】
#   想象去医院看病：
#   - 你不需要自己去找化验科、放射科、药房
#   - 你只需要在前台登记，前台会帮你协调所有科室
#   前台就是"外观"，把复杂的内部流程隐藏起来。
#
#   在本模块中，route_to_vendor 就是"前台"：
#   - 调用方只需要说"我要 get_stock_data"（登记）
#   - route_to_vendor 内部决定找哪个供应商、处理降级、传递参数
#   - 调用方完全不需要知道这些细节
#
# ============================================================================
# route_to_vendor 的完整工作流程（端到端示例）
# ============================================================================
#
#   假设上层代码调用：route_to_vendor("get_stock_data", "AAPL", "2026-07-07")
#
#   第 1 步：查找分类
#     get_category_for_method("get_stock_data")
#     → 遍历 TOOLS_CATEGORIES，发现 "get_stock_data" 属于 "core_stock_apis"
#
#   第 2 步：读取配置，确定主供应商
#     get_vendor("core_stock_apis", "get_stock_data")
#     → 从全局配置中读取，假设返回 "alpha_vantage,yfinance"
#     （逗号分隔表示主供应商是 alpha_vantage，备选是 yfinance）
#
#   第 3 步：构建完整的降级链
#     主供应商列表：["alpha_vantage", "yfinance"]
#     该方法在 VENDOR_METHODS 中可用的供应商：["alpha_vantage", "yfinance"]
#     合并去重后降级链：["alpha_vantage", "yfinance"]
#
#   第 4 步：依次尝试
#     尝试 alpha_vantage：
#       → 找到实现函数 get_alpha_vantage_stock
#       → 调用 get_alpha_vantage_stock("AAPL", "2026-07-07")
#       → 如果成功，返回结果！流程结束。
#       → 如果抛出 AlphaVantageRateLimitError（频率超限），进入下一个
#
#     尝试 yfinance：
#       → 找到实现函数 get_YFin_data_online
#       → 调用 get_YFin_data_online("AAPL", "2026-07-07")
#       → 返回结果！流程结束。
#
#   第 5 步（异常路径）：所有供应商都失败
#     → 抛出 RuntimeError("No available vendor for 'get_stock_data'")
#
# ============================================================================
# 支持的供应商
# ============================================================================
#   - yfinance: Yahoo Finance 数据源（免费，无需 API Key）
#   - alpha_vantage: Alpha Vantage 数据源（需要 API Key，有频率限制）
#
# 工具分类：
#   - core_stock_apis: 核心股票价格数据（OHLCV）
#   - technical_indicators: 技术分析指标
#   - fundamental_data: 基本面数据（财务报表等）
#   - news_data: 新闻和内部交易数据
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
# 下面每一行 import 都是把"别的模块里的东西"拿过来用。
# Python 中 import 之后，就可以用"模块名.函数名"或直接用导入的名字来调用。

# typing.Annotated: 用于为类型注解添加元数据说明。
# 【什么是类型注解？】Python 是动态类型语言，变量不需要声明类型。
# 但为了代码可读性和 IDE 提示，可以加上类型注解：name: str = "Tom"
# Annotated 在类型的基础上额外附加说明文字，方便人和工具理解。
from typing import Annotated

# 从 y_finance 模块导入 Yahoo Finance 供应商的具体实现函数。
# 【什么是 `from ... import ...`？】
#   从某个模块里只取出需要的函数/变量，而不是导入整个模块。
#   好处：代码更简洁，直接用函数名调用，不用写模块前缀。
#
# 【为什么要用 `as` 重命名？】
#   因为 alpha_vantage 模块也有同名函数（如 get_fundamentals）。
#   如果不重命名，两个同名函数会冲突，后导入的会覆盖前面的。
#   用 `as` 给它们起不同的别名，就像两个人同名，用绰号区分。
#   例如：get_fundamentals 被重命名为 get_yfinance_fundamentals。
from .y_finance import (
    get_YFin_data_online,          # 获取股票在线价格数据
    get_stock_stats_indicators_window,  # 获取技术指标窗口数据
    get_fundamentals as get_yfinance_fundamentals,  # 获取基本面数据
    get_balance_sheet as get_yfinance_balance_sheet,  # 获取资产负债表
    get_cashflow as get_yfinance_cashflow,  # 获取现金流量表
    get_income_statement as get_yfinance_income_statement,  # 获取利润表
    get_insider_transactions as get_yfinance_insider_transactions,  # 获取内部交易
)

# 从 yfinance_news 模块导入新闻相关函数
# 【什么是 `.` 相对导入？】from .xxx 中的一个点表示"当前包目录下"。
# 即从与 interface.py 同目录的 yfinance_news.py 文件中导入。
from .yfinance_news import get_news_yfinance, get_global_news_yfinance

# 从 alpha_vantage 模块导入 Alpha Vantage 供应商的具体实现函数。
# 同样使用 as 重命名，便于后续统一管理。
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_technical_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cash_flow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)

# 导入 Alpha Vantage 的频率限制异常，用于降级逻辑
# 【什么是异常类？】当程序出错时，Python 会"抛出"一个异常对象。
# AlphaVantageRateLimitError 是一种自定义异常，表示"调用太频繁，被限流了"。
# route_to_vendor 会捕获这个特定异常来触发降级。
from .alpha_vantage_common import AlphaVantageRateLimitError

# 从 akshare_adapter 模块导入 AkShare 供应商的具体实现函数和异常类
from .akshare_adapter import (
    get_akshare_stock,
    get_akshare_indicators,
    get_akshare_fundamentals,
    get_akshare_balance_sheet,
    get_akshare_cashflow,
    get_akshare_income_statement,
    get_akshare_insider_transactions,
    get_akshare_news,
    get_akshare_global_news,
    AkShareRateLimitError,
    AkShareTimeoutError,
)

# 从 cache_provider 模块导入缓存数据供应商（网络不可用时的降级方案）
from .cache_provider import (
    get_cached_stock_data,
    get_cached_indicators,
    get_cached_fundamentals,
    get_cached_balance_sheet,
    get_cached_cashflow,
    get_cached_income_statement,
    get_cached_insider_transactions,
    get_cached_news,
    get_cached_global_news,
)

# 从 config 模块导入配置获取函数
# get_config() 返回当前的全局配置字典（详见 config.py 的注释）
from .config import get_config

# ---------------------------------------------------------------------------
# 工具分类定义
# ---------------------------------------------------------------------------

# TOOLS_CATEGORIES: 定义工具方法的功能分类。
# 这是一个嵌套字典结构，每个分类包含：
#   - description: 分类的中文描述
#   - tools: 该分类下的工具方法名列表
# 这种分类方式使得可以按类别配置数据源（如"所有基本面数据使用 yfinance"）。
#
# 【什么是嵌套字典？】就是"字典里的值还是字典"。
# 外层键是分类名（如 "core_stock_apis"），外层值是一个内层字典。
# 内层字典又包含 "description" 和 "tools" 两个键。
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",  # OHLCV = Open, High, Low, Close, Volume
        # OHLCV 是股票数据的五个核心字段：
        # O(Open) 开盘价、H(High) 最高价、L(Low) 最低价、
        # C(Close) 收盘价、V(Volume) 成交量
        "tools": [
            "get_stock_data"  # 获取股票 OHLCV 数据
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",  # 技术分析指标
        # 技术指标是通过对价格和成交量做数学计算得出的分析信号，
        # 比如 RSI（相对强弱指标）、MACD（趋势指标）等
        "tools": [
            "get_indicators"  # 获取技术指标（如 RSI、MACD 等）
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",  # 公司基本面
        # 基本面数据反映公司的财务健康状况和价值
        "tools": [
            "get_fundamentals",      # 公司基本面概览
            "get_balance_sheet",     # 资产负债表（资产=负债+权益）
            "get_cashflow",          # 现金流量表（钱的进出）
            "get_income_statement"   # 利润表（收入-成本=利润）
        ]
    },
    "news_data": {
        "description": "News and insider data",  # 新闻和内部交易
        "tools": [
            "get_news",               # 公司新闻
            "get_global_news",        # 全球市场新闻
            "get_insider_transactions",  # 内部交易数据
        ]
    }
}

# ---------------------------------------------------------------------------
# 供应商列表
# ---------------------------------------------------------------------------

# VENDOR_LIST: 定义所有可用的数据供应商。
# 列表顺序可能影响降级优先级（但具体的降级顺序由 VENDOR_METHODS 字典的键顺序决定）。
# 这是一个 Python 列表（list），用方括号定义，元素用逗号分隔。
VENDOR_LIST = [
    "yfinance",       # Yahoo Finance - 免费，无需 API 密钥
    "alpha_vantage",  # Alpha Vantage - 需要 API 密钥
]

# ---------------------------------------------------------------------------
# 供应商方法映射表
# ---------------------------------------------------------------------------

# VENDOR_METHODS: 核心映射表，将每个工具方法映射到各供应商的具体实现函数。
# 数据结构：{方法名: {供应商名: 实现函数}}
# 这是一个"策略映射表"（Strategy Map），是策略模式的核心。
# 每个内部字典的键顺序决定了降级时的优先级（先尝试排在前面的供应商）。
#
# 【通俗理解】想象一个餐厅菜单：
#   "炒饭" 这道菜：
#     - 厨师A（alpha_vantage）会做：用 get_alpha_vantage_stock 这个做法
#     - 厨师B（yfinance）也会做：用 get_YFin_data_online 这个做法
#   点"炒饭"时，先让厨师A做；厨师A忙不过来，就让厨师B做。
VENDOR_METHODS = {
    # ---- 核心股票数据 API ----
    "get_stock_data": {
        "akshare": get_akshare_stock,               # AkShare 的股价获取（A股）
        "alpha_vantage": get_alpha_vantage_stock,   # Alpha Vantage 的股价获取实现
        "yfinance": get_YFin_data_online,           # Yahoo Finance 的股价获取实现
        "cache": get_cached_stock_data,             # 内置缓存数据（网络不可用时的降级方案）
    },
    # ---- 技术指标 ----
    "get_indicators": {
        "akshare": get_akshare_indicators,
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "cache": get_cached_indicators,
    },
    # ---- 基本面数据 ----
    "get_fundamentals": {
        "akshare": get_akshare_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "cache": get_cached_fundamentals,
    },
    "get_balance_sheet": {
        "akshare": get_akshare_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "cache": get_cached_balance_sheet,
    },
    "get_cashflow": {
        "akshare": get_akshare_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "cache": get_cached_cashflow,
    },
    "get_income_statement": {
        "akshare": get_akshare_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "cache": get_cached_income_statement,
    },
    # ---- 新闻数据 ----
    "get_news": {
        "akshare": get_akshare_news,
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "cache": get_cached_news,
    },
    "get_global_news": {
        "akshare": get_akshare_global_news,
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
        "cache": get_cached_global_news,
    },
    "get_insider_transactions": {
        "akshare": get_akshare_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        "cache": get_cached_insider_transactions,
    },
}


# ---------------------------------------------------------------------------
# 函数：get_category_for_method
# ---------------------------------------------------------------------------
def get_category_for_method(method: str) -> str:
    """
    根据方法名查找其所属的分类。

    参数：
      method (str): 工具方法名（如 "get_stock_data"）。

    返回值：
      str: 方法所属的分类名（如 "core_stock_apis"）。

    异常：
      ValueError: 当方法名在所有分类中都不存在时抛出。

    实现说明：
      遍历 TOOLS_CATEGORIES 字典，在每个分类的 tools 列表中查找 method。
      使用 .items() 方法同时获取键（分类名）和值（分类信息）。

    关键概念：
      - dict.items():
        返回字典的（键, 值）元组迭代器。
        例如：for key, value in my_dict.items()
      - in 运算符（成员检查）：
        检查某个元素是否在列表/集合/字典的键中。
        例如："apple" in ["apple", "banana"] 返回 True。
    """
    # 遍历 TOOLS_CATEGORIES 字典的每一个 (分类名, 分类信息) 键值对
    # category 是分类名（如 "core_stock_apis"）
    # info 是分类信息字典（包含 description 和 tools）
    for category, info in TOOLS_CATEGORIES.items():
        # 检查传入的 method 是否在当前分类的工具列表中
        # info["tools"] 是一个列表，如 ["get_stock_data"]
        # `in` 运算符检查元素是否在列表中
        if method in info["tools"]:
            return category  # 找到了！返回这个分类名
    # 如果遍历完所有分类都没有找到，抛出 ValueError 异常
    # f"..." 是 f-string 格式化字符串，{method} 会被替换为实际的方法名
    raise ValueError(f"Method '{method}' not found in any category")


# ---------------------------------------------------------------------------
# 函数：get_vendor
# ---------------------------------------------------------------------------
def get_vendor(category: str, method: str = None) -> str:
    """
    获取指定分类或工具方法对应的供应商配置。

    参数：
      category (str): 数据分类名（如 "fundamental_data"）。
      method (str, 可选): 具体的工具方法名。如果提供，则优先检查工具级别的
        供应商配置；如果不提供，则只使用分类级别的配置。

    返回值：
      str: 供应商名称字符串（可能是逗号分隔的多个供应商，如 "yfinance,alpha_vantage"）。

    配置优先级（从高到低）：
      1. 工具级别配置（tool_vendors）：为特定方法指定的供应商。
      2. 分类级别配置（data_vendors）：为整个分类指定的供应商。
      3. 默认值 "default"。

    关键概念：
      - dict.get(key, default):
        安全地从字典中获取值。如果 key 不存在，返回 default 值而不是抛出异常。
        例如：{"a": 1}.get("b", 0) 返回 0，而 {"a": 1}["b"] 会抛出 KeyError。

      - 配置分层（Configuration Hierarchy）：
        工具级别 > 分类级别的设计允许用户进行精细控制——
        例如，可以设置所有基本面数据使用 yfinance，但 get_balance_sheet
        单独使用 alpha_vantage。
    """
    # 获取全局配置（返回一个配置字典的副本）
    # get_config() 内部会确保配置已初始化，详见 config.py
    config = get_config()

    # 如果提供了 method 参数，先检查工具级别的配置
    # 这里的 if method: 表示 method 不为 None 且不为空字符串时为真
    if method:
        # tool_vendors 是一个字典，格式如 {"get_balance_sheet": "alpha_vantage"}
        # config.get("tool_vendors", {}) 表示：如果配置里没有 "tool_vendors" 键，
        # 就返回一个空字典 {}，避免 KeyError
        tool_vendors = config.get("tool_vendors", {})
        # 如果该工具有专门配置的供应商
        if method in tool_vendors:
            return tool_vendors[method]  # 直接返回工具级别的供应商

    # 降级到分类级别配置
    # data_vendors 格式如 {"fundamental_data": "yfinance"}
    # 这里用了链式 .get()：
    #   config.get("data_vendors", {}) → 如果没有 data_vendors 键，返回空字典
    #   .get(category, "default") → 如果该分类没配置，返回 "default"
    return config.get("data_vendors", {}).get(category, "default")


# ---------------------------------------------------------------------------
# 函数：route_to_vendor（核心路由函数）
# ---------------------------------------------------------------------------
def route_to_vendor(method: str, *args, **kwargs):
    """
    将方法调用路由到合适的供应商实现，并支持自动降级（Fallback）。

    这是整个接口层的核心函数，实现了"供应商路由"和"自动降级"两个关键功能。

    参数：
      method (str): 要调用的工具方法名（如 "get_stock_data"）。
      *args: 位置参数，会被透传给供应商的实现函数。
        【Python 语法】*args 表示"可变位置参数"——可以接收任意数量的
        位置参数，它们会被打包成一个元组（tuple）。
      **kwargs: 关键字参数，会被透传给供应商的实现函数。
        【Python 语法】**kwargs 表示"可变关键字参数"——可以接收任意数量的
        关键字参数，它们会被打包成一个字典（dict）。

    返回值：
      供应商实现函数的返回值（类型取决于具体的方法）。

    异常：
      ValueError: 当方法名不在 VENDOR_METHODS 中时抛出。
      RuntimeError: 当所有供应商都不可用时抛出。

    工作流程：
      1. 根据方法名查找所属分类。
      2. 根据分类（和方法名）获取配置的供应商列表。
      3. 构建降级链：主供应商 -> 备选供应商。
      4. 按降级链依次尝试，成功则返回，失败则继续尝试下一个。
      5. 所有供应商都失败时抛出异常。

    关键概念：
      - 拆包运算符 * 和 **：
        *args 和 **kwargs 在函数定义中用于"打包"参数，
        在函数调用中用于"拆包"参数。
        例如：func(*[1, 2, 3]) 等价于 func(1, 2, 3)

      - 降级链（Fallback Chain）：
        主供应商失败时自动切换到备选供应商的机制，提高了系统的可用性。
        只有 AlphaVantageRateLimitError（频率限制）会触发降级，
        其他异常（如网络错误）会直接向上传播。
    """
    # ---------------------------------------------------------------------------
    # 以下是可视化的流程图：降级链的构建与尝试过程
    # ---------------------------------------------------------------------------
    # 该图展示 route_to_vendor 内部的完整流程：
    # 从方法分类查找、供应商配置读取、降级链构建，到依次尝试各供应商。
    # 只有 AlphaVantageRateLimitError 才会触发降级，其他异常直接传播。
    # （参见图解指南.md 附录 A6）
    # ---------------------------------------------------------------------------
    # ```mermaid
    # flowchart TD
    #     A["调用 route_to_vendor(method='get_stock_data', *args, **kwargs)"]
    #
    #     A --> B["1. 查找方法分类<br/>get_category_for_method('get_stock_data')<br/>→ 'core_stock_apis'"]
    #
    #     B --> C["2. 获取配置的供应商<br/>get_vendor(category, method)<br/>→ 'alpha_vantage,yfinance'"]
    #
    #     C --> D["3. 构建降级链<br/>主供应商在前(按配置顺序)<br/>其他可用供应商追加在后<br/>['alpha_vantage', 'yfinance']"]
    #
    #     D --> E["4. 依次尝试降级链中的供应商"]
    #
    #     E --> F{"尝试 alpha_vantage"}
    #
    #     F -->|"成功返回数据"| G["返回结果 ✅<br/>流程结束"]
    #     F -->|"AlphaVantageRateLimitError<br/>(频率超限)"| H["降级到下一个供应商"]
    #
    #     H --> I{"尝试 yfinance"}
    #
    #     I -->|"成功返回数据"| G
    #     I -->|"AlphaVantageRateLimitError"| J["降级到下一个供应商"]
    #
    #     J --> K{"降级链是否还有供应商？"}
    #
    #     K -->|"是"| E
    #     K -->|"否，全部失败"| L["抛出 RuntimeError<br/>No available vendor ❌"]
    #
    #     style G fill:#27AE60,color:#fff
    #     style L fill:#C0392B,color:#fff
    # ```
    # ---------------------------------------------------------------------------
    # 步骤1：查找方法所属的分类
    # 例如 method="get_stock_data" → category="core_stock_apis"
    category = get_category_for_method(method)

    # 步骤2：获取配置的供应商（可能是逗号分隔的多个供应商）
    # 例如返回 "alpha_vantage,yfinance"
    vendor_config = get_vendor(category, method)

    # 将逗号分隔的供应商字符串拆分为列表
    # 【拆分过程示例】
    #   vendor_config = "alpha_vantage, yfinance"
    #   vendor_config.split(',') → ["alpha_vantage", " yfinance"]
    #   v.strip() 去除每个供应商名称前后的空白字符
    #   最终 primary_vendors = ["alpha_vantage", "yfinance"]
    # 【什么是列表推导式？】
    #   [表达式 for 变量 in 可迭代对象] 是一种简洁地创建列表的写法。
    #   等价于先建空列表，再 for 循环 append。
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    # 步骤3：验证方法是否在映射表中
    # 如果方法名不在 VENDOR_METHODS 字典的键中，说明不支持该方法
    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # 步骤4：构建降级链
    # 先放主供应商（按配置顺序），然后追加其他可用的供应商
    # list(VENDOR_METHODS[method].keys()) 获取该方法所有可用的供应商列表
    # 例如 VENDOR_METHODS["get_stock_data"].keys() → ["alpha_vantage", "yfinance"]
    all_available_vendors = list(VENDOR_METHODS[method].keys())

    # 复制主供应商列表（用 .copy() 是为了避免修改原始列表）
    fallback_vendors = primary_vendors.copy()

    # 将不在主供应商列表中的其他供应商追加到末尾
    # 这样就形成了一个完整的降级链：
    #   [主供应商1, 主供应商2, ...其他可用供应商]
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:  # 如果这个供应商还不在降级链中
            fallback_vendors.append(vendor)  # 追加到末尾

    # 确保 cache（内置缓存）始终作为最后降级选项
    # 当所有外部数据源都不可用时，使用缓存/模拟数据确保流水线可运行
    if "cache" in all_available_vendors and "cache" not in fallback_vendors:
        fallback_vendors.append("cache")
    # 如果 cache 已经在列表中但不在最后，移到末尾
    elif "cache" in fallback_vendors and fallback_vendors[-1] != "cache":
        fallback_vendors.remove("cache")
        fallback_vendors.append("cache")

    # 步骤5：按降级链依次尝试
    # 遍历降级链中的每一个供应商
    for vendor in fallback_vendors:
        # 跳过映射表中不存在的供应商（防御性编程）
        # 防御性编程：即使配置写错了供应商名，也不会崩溃
        if vendor not in VENDOR_METHODS[method]:
            continue  # 跳过这个供应商，尝试下一个

        # 获取供应商的实现函数
        # VENDOR_METHODS[method] 是一个字典，如 {"alpha_vantage": some_func}
        # VENDOR_METHODS[method][vendor] 取出对应的实现函数
        vendor_impl = VENDOR_METHODS[method][vendor]

        # 处理实现函数可能是列表的情况（元组包装）
        # isinstance() 检查 vendor_impl 是否是 list 类型
        # 如果是列表，取第一个元素 [0]；否则直接使用
        # 这是为了兼容某些供应商实现可能是 [func] 这种包装形式
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            # 调用供应商实现函数，传递所有参数
            # *args 和 **kwargs 将参数原封不动地传递给实现函数
            result = impl_func(*args, **kwargs)

            # 检查返回值是否为错误/空数据
            # 某些供应商实现会将错误以字符串形式返回
            # 这些情况下需要触发降级，而不是将其当作正常结果
            if isinstance(result, str):
                is_error = (
                    result.startswith("Error") or
                    result.startswith("No ") or
                    result.startswith("News API temporarily unavailable") or
                    len(result.strip()) == 0
                )
                if is_error:
                    # 若当前供应商不是降级链中的最后一个，则降级到下一个供应商
                    if vendor != fallback_vendors[-1]:
                        continue
                    # 若是最后一个兜底供应商（cache），则不再抛错崩溃，
                    # 直接返回其结果（可能是模拟/缓存数据），确保流水线可运行
                    return result

            return result
        except AlphaVantageRateLimitError:
            # Alpha Vantage 频率限制，继续尝试下一个供应商
            continue
        except AkShareRateLimitError:
            # AkShare 频率限制（东方财富 API 限流），继续尝试下一个供应商
            # 东方财富 API 限流时，等待一段时间后重试可能有效，
            # 但为了快速响应，这里选择直接降级到下一个供应商
            continue
        except AkShareTimeoutError:
            # AkShare 请求超时，继续尝试下一个供应商
            # 超时可能是网络问题或东方财富服务器响应缓慢
            continue
        except Exception as e:
            # 其他异常（网络错误、数据不可用等）也尝试降级
            # 多供应商设计就是为了容错——一个供应商挂了，自动切换下一个
            # 只有当这是最后一个供应商时才抛出异常
            if vendor == fallback_vendors[-1]:
                raise
            continue

    # 步骤6：所有供应商都失败
    # 如果循环走完了都没有 return（所有供应商都失败），抛出异常
    raise RuntimeError(f"No available vendor for '{method}'")
