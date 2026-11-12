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
#     → 返回保留原始原因的消息，例如
#       "未获取到 get_stock_data 数据：No data found for symbol '600519' between ..."
#       （这样上层/Agent 能区分"这个标的就是没有数据"和"数据源故障"，
#        而不是只看到一个笼统的 "No available vendor"）
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
#
# dataclasses.dataclass: 用于定义带字段的数据类（这里用于 VendorResult 结果信封）。
# 【什么是数据类？】它帮你自动生成 __init__ / __repr__ / __eq__ 等方法，
# 适合表达"一组有名字的字段"，比到处传元组可读得多。
#
# logging 模块：Python 标准的日志记录库（不使用 print，避免污染标准输出）

import logging
import os
from dataclasses import dataclass

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
# 从 akshare_adapter 模块导入 AkShare 供应商的具体实现函数和异常类
from .akshare_adapter import (
    NOT_APPLICABLE_MARKER,
    AkShareRateLimitError,
    AkShareTimeoutError,
    get_akshare_balance_sheet,
    get_akshare_capital_flow,
    get_akshare_cashflow,
    get_akshare_dragon_tiger,
    get_akshare_fundamentals,
    get_akshare_global_news,
    get_akshare_income_statement,
    get_akshare_indicators,
    get_akshare_insider_transactions,
    get_akshare_news,
    get_akshare_northbound_flow,
    get_akshare_stock,
)
from .alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
)
from .alpha_vantage import (
    get_cash_flow as get_alpha_vantage_cashflow,
)
from .alpha_vantage import (
    get_fundamentals as get_alpha_vantage_fundamentals,
)
from .alpha_vantage import (
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage import (
    get_income_statement as get_alpha_vantage_income_statement,
)
from .alpha_vantage import (
    get_news as get_alpha_vantage_news,
)

# 从 alpha_vantage 模块导入 Alpha Vantage 供应商的具体实现函数。
# 同样使用 as 重命名，便于后续统一管理。
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
)
from .alpha_vantage import (
    # get_stockstats_indicator 的签名是 (symbol, indicator, curr_date, look_back_days)，
    # 与 route_to_vendor("get_indicators", ...) 的调用方式一致。
    # 不能使用 get_technical_indicator，它的第三个参数是 interval，会导致日期被当作 interval。
    get_stockstats_indicator as get_alpha_vantage_indicator,
)

# 导入 Alpha Vantage 的频率限制异常，用于降级逻辑
# 【什么是异常类？】当程序出错时，Python 会"抛出"一个异常对象。
# AlphaVantageRateLimitError 是一种自定义异常，表示"调用太频繁，被限流了"。
# route_to_vendor 会捕获这个特定异常来触发降级。
from .alpha_vantage_common import AlphaVantageRateLimitError

# A 股标的判定：用于"仅 A 股"闸门（拒绝 NVDA/AAPL 等美股标的）
from .ashare_rules import contains_cjk, is_a_share_code

# 从 baostock_adapter 模块导入 BaoStock 供应商（A 股数据备选方案）
# BaoStock 免费、无需注册，数据来源稳定，作为 akshare 的首选降级方案
from .baostock_adapter import (
    get_baostock_balance_sheet,
    get_baostock_cashflow,
    get_baostock_fundamentals,
    get_baostock_income_statement,
    get_baostock_stock,
)

# 从 cache_provider 模块导入缓存数据供应商（网络不可用时的降级方案）
# SyntheticPayload 是离线演示数据的显式标记类型（str 子类），
# 路由层用它精确判断"这段数据是伪造的"，不再依赖脆弱的字符串嗅探。
from .cache_provider import (
    SyntheticPayload,
    get_cached_balance_sheet,
    get_cached_cashflow,
    get_cached_fundamentals,
    get_cached_global_news,
    get_cached_income_statement,
    get_cached_indicators,
    get_cached_insider_transactions,
    get_cached_news,
    get_cached_stock_data,
)

# 从 config 模块导入配置获取函数
# get_config() 返回当前的全局配置字典（详见 config.py 的注释）
from .config import get_config
from .y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,  # 获取资产负债表
)
from .y_finance import (
    get_cashflow as get_yfinance_cashflow,  # 获取现金流量表
)
from .y_finance import (
    get_fundamentals as get_yfinance_fundamentals,  # 获取基本面数据
)
from .y_finance import (
    get_income_statement as get_yfinance_income_statement,  # 获取利润表
)
from .y_finance import (
    get_insider_transactions as get_yfinance_insider_transactions,  # 获取内部交易
)
from .y_finance import (
    get_stock_stats_indicators_window,  # 获取技术指标窗口数据
    get_YFin_data_online,  # 获取股票在线价格数据
)

# 从 yfinance_news 模块导入新闻相关函数
# 【什么是 `.` 相对导入？】from .xxx 中的一个点表示"当前包目录下"。
# 即从与 interface.py 同目录的 yfinance_news.py 文件中导入。
from .yfinance_news import get_global_news_yfinance, get_news_yfinance

# 模块级日志记录器（放在全部 import 之后，避免打断 import 块）
logger = logging.getLogger(__name__)

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
    },
    "capital_flow": {
        "description": "A-share capital flow (main funds / northbound / LHB)",
        "tools": [
            "get_capital_flow",       # 个股资金流向（主力/超大单/大单）
            "get_northbound_flow",    # 北向资金（沪深股通）
            "get_dragon_tiger",       # 龙虎榜
        ],
    },
}

# ---------------------------------------------------------------------------
# 供应商列表
# ---------------------------------------------------------------------------

# VENDOR_LIST: 定义所有可用的数据供应商。
# 列表顺序可能影响降级优先级（但具体的降级顺序由 VENDOR_METHODS 字典的键顺序决定）。
# 这是一个 Python 列表（list），用方括号定义，元素用逗号分隔。
VENDOR_LIST = [
    "akshare",        # AkShare - 东方财富 API，A 股数据最全
    "baostock",       # BaoStock - 证券宝，A 股备选，稳定可靠
    "yfinance",       # Yahoo Finance - 美股/全球数据，免费无需 API 密钥
    "alpha_vantage",  # Alpha Vantage - 需要 API 密钥
    "cache",          # 内置缓存 - 最终兜底方案
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
        "akshare": get_akshare_stock,               # AkShare 的股价获取（A股，首选）
        "baostock": get_baostock_stock,             # BaoStock 的股价获取（A股备选，稳定可靠）
        "alpha_vantage": get_alpha_vantage_stock,   # Alpha Vantage 的股价获取实现
        "yfinance": get_YFin_data_online,           # Yahoo Finance 的股价获取实现
        "cache": get_cached_stock_data,             # 内置缓存数据（最终降级方案）
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
        "baostock": get_baostock_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "cache": get_cached_fundamentals,
    },
    "get_balance_sheet": {
        "akshare": get_akshare_balance_sheet,
        "baostock": get_baostock_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "cache": get_cached_balance_sheet,
    },
    "get_cashflow": {
        "akshare": get_akshare_cashflow,
        "baostock": get_baostock_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "cache": get_cached_cashflow,
    },
    "get_income_statement": {
        "akshare": get_akshare_income_statement,
        "baostock": get_baostock_income_statement,
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
    # ---- A 股资金流向 ----
    "get_capital_flow": {
        "akshare": get_akshare_capital_flow,
    },
    "get_northbound_flow": {
        "akshare": get_akshare_northbound_flow,
    },
    "get_dragon_tiger": {
        "akshare": get_akshare_dragon_tiger,
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
# 路由结果模型与失败语义（FIX 5）
# ---------------------------------------------------------------------------
# WHY 需要这套结构：
#   过去路由层判断"供应商是否失败"靠嗅探结果字符串是否以 "Error"/"No " 开头，
#   并且在所有供应商耗尽时统一抛出 RuntimeError("No available vendor for ...")。
#   这会丢掉数据源给出的可执行信息（例如
#   "No data found for symbol '600519' between ..."），
#   让上层/Agent 只看到一个像"系统故障"的错误，无法区分
#   “这个标的就是没有数据”和“数据源挂了”这两种完全不同的情况。
#   现在：
#     - 用 VendorResult 统一表达一次供应商调用的结果与原因码；
#     - 用 is_error_result() 把结果字符串解析成 (是否失败, 原因码)；
#     - route_to_vendor 失败时返回保留原始原因的消息，原因码供上层区分。

# 失败原因码（机器可读）
REASON_NO_DATA = "no_data"                # 数据确实不存在（该标的/该区间没有行情）
REASON_VENDOR_ERROR = "vendor_error"      # 数据源故障（网络、接口异常等）
REASON_UNAVAILABLE = "unavailable"        # 数据源/接口本身不可用
REASON_NOT_APPLICABLE = "not_applicable"  # 该标的不适用这类数据（非 A 股等）
REASON_RATE_LIMITED = "rate_limited"      # 被数据源限流（429 类错误）
REASON_TIMEOUT = "timeout"                # 请求超时

# 统一失败出口的消息前缀（便于上层稳定识别"这是一次取数失败"）
FAILURE_MESSAGE_PREFIX = "未获取到 "

# 明确没有备选数据源的数据项（FIX 6）
# WHY：这三个资金流向接口只有 AkShare 一个数据源，既没有 baostock / yfinance
# 备选，也没有本地缓存。以前它们失败时会抛出
# RuntimeError("No available vendor for ...")，看起来像"数据源链路被耗尽"，
# 实际上只是"这个数据项根本没有第二个数据源"。
# 与其伪装成供应商链耗尽，不如如实说明：
#   该数据项当前没有可用数据源（AkShare 接口不可用）
# 注意：这里只描述事实，绝不伪造数据。
NO_FALLBACK_METHODS = {
    "get_capital_flow": "该数据项当前没有可用数据源（AkShare 接口不可用）",
    "get_northbound_flow": "该数据项当前没有可用数据源（AkShare 接口不可用）",
    "get_dragon_tiger": "该数据项当前没有可用数据源（AkShare 接口不可用）",
}


@dataclass
class VendorResult:
    """一次供应商调用的结果信封。

    字段：
      ok: 是否成功取到可用数据
      data: 供应商返回的原始文本（失败时通常是可执行的错误说明）
      vendor: 供应商名称（如 "akshare"）
      reason: 机器可读的原因码（成功时为空字符串）
    """

    ok: bool
    data: str
    vendor: str
    reason: str = ""


class SyntheticDataRefusedError(RuntimeError):
    """拒绝使用离线演示（合成）数据的异常。

    WHY 单独定义：这是 fail-closed 的安全闸门 —— 与"取数失败"不同，
    它必须显式向上抛出，不能被降级链里的通用 except 吞掉。
    """


def is_error_result(result) -> tuple[bool, str]:
    """判断供应商返回的结果是否代表失败，并给出机器可读的原因码。

    参数：
      result: 供应商返回值（一般是字符串）。

    返回：
      (is_error, reason)
        - is_error=True 时 reason 为原因码，取值见上面的 REASON_* 常量；
        - is_error=False 时 reason 为空字符串。

    WHY 需要显式的原因码：
      "数据不存在"（no_data）与"数据源故障"（vendor_error / unavailable）
      对上层是完全不同的处理方式：前者应当如实告诉用户"这个标的这段时间
      没有数据"，后者才需要提示重试或换源。过去两者都被压成一句
      "No available vendor"，Agent 只能当成系统故障处理。
    """
    if result is None:
        return True, REASON_VENDOR_ERROR
    if not isinstance(result, str):
        return False, ""

    text = result.strip()
    if not text:
        return True, REASON_VENDOR_ERROR

    # 路由层统一失败出口产生的消息：解析出原始原因码
    if text.startswith(FAILURE_MESSAGE_PREFIX):
        return True, _reason_from_failure_message(text)

    # 数据源明确表示"该标的不适用这类数据"（例如非 A 股标的的资金流向）
    if text.startswith(NOT_APPLICABLE_MARKER):
        return True, REASON_NOT_APPLICABLE

    lowered = text.lower()
    if lowered.startswith("no ") or lowered.startswith("no\t"):
        return True, REASON_NO_DATA
    if lowered.startswith("error") or lowered.startswith("[error"):
        return True, REASON_VENDOR_ERROR
    if lowered.startswith("news api temporarily unavailable"):
        return True, REASON_VENDOR_ERROR
    if "接口不可用" in text:
        return True, REASON_UNAVAILABLE
    if "限流" in text or "请求过于频繁" in text:
        return True, REASON_RATE_LIMITED
    return False, ""


def _reason_from_failure_message(text: str) -> str:
    """从统一失败消息中还原原因码。"""
    tail = text[len(FAILURE_MESSAGE_PREFIX):]
    if tail.startswith(NOT_APPLICABLE_MARKER):
        return REASON_NOT_APPLICABLE
    # 限流 / 超时优先于"没有可用数据源"：前者才是真正的根因
    if "限流" in tail or "429" in tail:
        return REASON_RATE_LIMITED
    if "超时" in tail:
        return REASON_TIMEOUT
    if "没有可用数据源" in tail:
        return REASON_UNAVAILABLE
    lowered = tail.lower()
    if "no data" in lowered or "not a trading day" in lowered:
        return REASON_NO_DATA
    if "数据源故障" in tail:
        return REASON_VENDOR_ERROR
    return REASON_VENDOR_ERROR


def _build_failure_message(
    method: str,
    failure: "VendorResult | None",
    synthetic_blocked: bool = False,
) -> str:
    """构造保留原始原因的统一失败消息。

    WHY 保留原始原因：上游 Agent 需要知道到底是"这个标的这段时间没有数据"
    还是"数据源挂了"，才能给出正确的下一步（换标的 / 重试 / 等待）。

    synthetic_blocked：表示"离线演示数据兜底因为默认关闭而被跳过"，
    这时必须如实说明，否则调用方会以为所有数据源都被尝试过了。
    """
    detail = (failure.data or "").strip() if failure is not None else ""
    # WHY 压成单行：下游有调用方会把这段文本当 CSV 解析（回测服务），
    # 多行文本会被解析成多行脏数据。统一压成一行更安全。
    detail = " ".join(detail.split())
    reason = failure.reason if failure is not None else REASON_VENDOR_ERROR

    # 没有任何供应商返回过结果（例如唯一的数据源就是被禁用的离线演示数据）
    if failure is None:
        message = f"{FAILURE_MESSAGE_PREFIX}{method} 数据：没有可用的数据源"
        if synthetic_blocked:
            message += "；离线演示数据兜底已禁用（ALLOW_SYNTHETIC_DATA=0）"
        return message

    # 数据源给出的明确结论（没有数据 / 不适用）原样保留，最有信息量
    if reason in (REASON_NO_DATA, REASON_NOT_APPLICABLE) and detail:
        return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：{detail}"

    # 明确没有备选数据源的数据项：如实说明"没有可用数据源"
    if method in NO_FALLBACK_METHODS:
        base = NO_FALLBACK_METHODS[method]
        if reason == REASON_UNAVAILABLE:
            return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：{base}"
        if detail:
            return (
                f"{FAILURE_MESSAGE_PREFIX}{method} 数据：{base}；最后错误：{detail}"
            )
        return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：{base}"

    if reason == REASON_UNAVAILABLE:
        if detail:
            return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源不可用（{detail}）"
        return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源不可用"

    # 限流 / 超时：与"硬故障"区分开，便于上层提示"稍后重试"
    if reason == REASON_RATE_LIMITED:
        if detail:
            return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源限流（{detail}）"
        return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源限流"

    if reason == REASON_TIMEOUT:
        if detail:
            return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源超时（{detail}）"
        return f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源超时"

    if detail:
        message = f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源故障（{detail}）"
    else:
        message = f"{FAILURE_MESSAGE_PREFIX}{method} 数据：数据源故障"

    if synthetic_blocked:
        # 如实说明：离线演示数据兜底因为默认关闭而没有被使用
        message += "；离线演示数据兜底已禁用（ALLOW_SYNTHETIC_DATA=0）"
    return message


# ---------------------------------------------------------------------------
# 函数：route_to_vendor（核心路由函数）
# ---------------------------------------------------------------------------
# 合成（离线演示）数据的识别。
#
# WHY 不再用字符串嗅探（FIX 4）：
#   旧实现靠查找 "(cached)" / "simulated data" 等子串来判断数据是否伪造，
#   问题有两点：
#     1. 误伤：真实数据正文里完全可能出现这些子串（例如新闻里提到 cached），
#        于是真实数据被当成伪造数据丢弃；
#     2. 漏判：伪造数据只要换个措辞就识别不出来。
#   现在改用显式标记：cache_provider 返回 SyntheticPayload（str 子类），
#   上层用 isinstance 精确判断；另外 "cache" 供应商按定义就是离线演示数据源，
#   直接按供应商名兜底判定，杜绝伪造数据被当成真实数据返回。
def _is_synthetic_payload(result) -> bool:
    """判断结果是否来自离线演示（合成）数据生成器。"""
    if isinstance(result, SyntheticPayload):
        return True
    return bool(getattr(result, "is_synthetic", False))


# ---------------------------------------------------------------------------
# A 股模式（默认开启）
#
# 本项目当前只面向 A 股。开启该模式后：
#   1. 非 A 股标的在做任何取数之前就被拒绝，不会打到 yfinance 等美股源；
#   2. 降级链只使用显式配置的供应商（见 route_to_vendor 步骤 4）。
# 需要恢复多市场能力时设置 A_SHARE_ONLY=0。
# ---------------------------------------------------------------------------

#: 这些方法的第一个位置参数是标的代码，需要做 A 股校验。
_TICKER_METHODS = frozenset(
    {
        "get_stock_data",
        "get_indicators",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_news",
        "get_insider_transactions",
        "get_capital_flow",
        "get_northbound_flow",
        "get_dragon_tiger",
    }
)


def _is_a_share_only() -> bool:
    """是否启用"仅 A 股"模式（默认启用）。"""
    env_value = os.getenv("A_SHARE_ONLY")
    if env_value is not None:
        return env_value.strip().lower() in ("1", "true", "yes", "on")
    return bool(get_config().get("a_share_only", True))


def _first_symbol_arg(method: str, args, kwargs) -> str:
    """取出方法调用中的标的代码（没有则返回空串）。"""
    if method not in _TICKER_METHODS:
        return ""
    if args:
        return str(args[0] or "").strip()
    for key in ("ticker", "symbol", "code"):
        if key in kwargs and kwargs[key]:
            return str(kwargs[key]).strip()
    return ""


def _looks_like_a_share(symbol: str) -> bool:
    """判断标的看起来是否属于 A 股。

    - 6 位数字代码（含 sh/sz/bj 前缀、.SH 后缀等写法）→ A 股；
    - 中文名称（如"贵州茅台"）→ 视为 A 股意图，交由后续代码归一化；
    - 其他（AAPL / NVDA / 0700.HK …）→ 非 A 股。
    """
    text = str(symbol or "").strip()
    if not text:
        return True
    if is_a_share_code(text):
        return True
    if contains_cjk(text):
        return True
    # 允许纯数字（可能需要补零）——例如 "1"、"000001"
    if text.isdigit() and len(text) <= 6:
        return True
    return False


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
      所有供应商都失败时，返回一条**保留原始原因**的失败消息
      （形如 "未获取到 get_stock_data 数据：No data found for symbol ..."），
      而不是笼统的 "No available vendor"。
      上层可用 is_error_result(result) 判断是否失败及其原因码。

    异常：
      ValueError: 当方法名不在 VENDOR_METHODS 中时抛出。
      RuntimeError: 当所有真实数据源都失败、且拒绝使用离线演示数据时抛出
        （仅在显式开启 ALLOW_SYNTHETIC_DATA 时才会发生）。

    工作流程：
      1. 根据方法名查找所属分类。
      2. 根据分类（和方法名）获取配置的供应商列表。
      3. 构建降级链：主供应商 -> 备选供应商。
      4. 按降级链依次尝试，成功则返回，失败则继续尝试下一个。
      5. 所有供应商都失败时返回保留原始原因的失败消息。

    关键概念：
      - 拆包运算符 * 和 **：
        *args 和 **kwargs 在函数定义中用于"打包"参数，
        在函数调用中用于"拆包"参数。
        例如：func(*[1, 2, 3]) 等价于 func(1, 2, 3)

      - 降级链（Fallback Chain）：
        主供应商失败时自动切换到备选供应商的机制，提高了系统的可用性。
        频率限制、超时等异常同样会触发降级；
        只有在所有供应商都失败时才统一给出失败结论。
    """
    # ---------------------------------------------------------------------------
    # A 股模式闸门：默认只分析 A 股，非 A 股标的直接拒绝
    #
    # WHY：项目当前只服务 A 股。若放任 NVDA/AAPL 这类标的进入链路，
    # 会一路打到 yfinance（美股数据源），既产生 429 噪音，也把不属于
    # 本系统口径的数据混进分析。这里在做任何取数之前就短路返回。
    # 需要恢复多市场能力时，设置 A_SHARE_ONLY=0 即可。
    # ---------------------------------------------------------------------------
    if _is_a_share_only():
        symbol = _first_symbol_arg(method, args, kwargs)
        if symbol and not _looks_like_a_share(symbol):
            return (
                f"[不适用] 当前配置为仅分析 A 股（A_SHARE_ONLY=1），"
                f"已拒绝非 A 股标的：{symbol}。"
                "如需分析美股/港股，请设置 A_SHARE_ONLY=0。"
            )

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
    #     K -->|"否，全部失败"| L["返回保留原始原因的失败消息 ❌<br/>未获取到 {method} 数据：..."]
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
    # 只使用**显式配置**的供应商。
    #
    # WHY（重要修复）：此前这里会把 VENDOR_METHODS 里的**所有**供应商
    # 无条件追加到降级链末尾，于是哪怕配置里只写了 akshare,baostock，
    # 取数失败后仍会依次去打 Alpha Vantage 与 Yahoo Finance。后果是：
    #   - A 股分析失败时产生大量 yfinance 429 / YFRateLimitError 噪音；
    #   - 美股数据源会被无谓地唤醒（本项目当前只做 A 股）；
    #   - 配置项实际上只能调整优先级，无法真正"禁用"某个数据源。
    # 现在只有显式配置的供应商才会被调用；确需"尽力而为"时可通过
    # allow_unconfigured_vendors 打开旧行为。
    all_available_vendors = list(VENDOR_METHODS[method].keys())

    # 复制主供应商列表（用 .copy() 是为了避免修改原始列表）
    fallback_vendors = primary_vendors.copy()

    if bool(get_config().get("allow_unconfigured_vendors", False)):
        for vendor in all_available_vendors:
            if vendor not in fallback_vendors:
                fallback_vendors.append(vendor)

    # 确保 cache（内置缓存）作为最后降级选项 —— 但它默认是关闭的
    # （见下方 allow_synthetic），是否真的使用由配置决定。
    if "cache" in all_available_vendors and "cache" not in fallback_vendors:
        fallback_vendors.append("cache")
    # 如果 cache 已经在列表中但不在最后，移到末尾
    elif "cache" in fallback_vendors and fallback_vendors[-1] != "cache":
        fallback_vendors.remove("cache")
        fallback_vendors.append("cache")

    # 读取全局配置，决定是否允许使用模拟数据兜底。
    # 默认 False（fail-closed）：真实数据源全部失败时直接报错，
    # 避免把伪造行情当作真实数据交给 LLM 生成投资建议。
    allow_synthetic = bool(get_config().get("allow_synthetic_data", False))

    # 步骤5：按降级链依次尝试
    # 遍历降级链中的每一个供应商
    # last_failure 记录最后一次失败的原因与原始说明，
    # 用于在所有供应商都失败时生成"保留原始原因"的失败消息（FIX 5）
    last_failure: VendorResult | None = None
    # 记录"离线演示数据兜底因默认关闭而被跳过"，用于如实说明失败原因
    synthetic_blocked = False
    for vendor in fallback_vendors:
        # 跳过映射表中不存在的供应商（防御性编程）
        # 防御性编程：即使配置写错了供应商名，也不会崩溃
        if vendor not in VENDOR_METHODS[method]:
            continue  # 跳过这个供应商，尝试下一个

        # 默认禁止使用 cache 离线演示数据
        if vendor == "cache" and not allow_synthetic:
            synthetic_blocked = True
            continue

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
                # 合成（离线演示）数据判定：
                #   1) payload 自带 SyntheticPayload 标记；
                #   2) 来自 "cache" 供应商 —— 按定义就是离线演示数据源。
                # WHY 不再用字符串嗅探：真实数据正文可能包含 "(cached)" 等
                # 子串，会被误判为伪造数据而丢弃。
                is_synthetic = vendor == "cache" or _is_synthetic_payload(result)

                # 默认拒绝离线演示数据，除非用户显式开启 ALLOW_SYNTHETIC_DATA
                if is_synthetic and not allow_synthetic:
                    if vendor != fallback_vendors[-1]:
                        continue
                    raise SyntheticDataRefusedError(
                        f"All real data vendors failed for '{method}'. "
                        "Refusing to use simulated data (set ALLOW_SYNTHETIC_DATA=1 "
                        "to allow it for development)."
                    )

                # 统一失败判定：既能识别"没有数据"，也能识别"数据源故障"
                failed, reason = is_error_result(result)
                if failed:
                    last_failure = VendorResult(
                        ok=False, data=result, vendor=vendor, reason=reason
                    )
                    # 若当前供应商不是降级链中的最后一个，则降级到下一个供应商
                    if vendor != fallback_vendors[-1]:
                        continue
                    # 最后一个供应商也失败 → 走统一失败出口（保留原始原因）
                    break

                # 用户显式允许离线演示数据时，加上醒目前缀，便于前端/日志识别
                if is_synthetic and not result.lstrip().startswith("[SIMULATED"):
                    result = SyntheticPayload("[SIMULATED DATA] " + result)

            return result
        except SyntheticDataRefusedError:
            # 拒绝使用离线演示数据是显式的安全闸门，必须向上抛出，
            # 不能被下面的通用 except 吞掉（否则会悄悄降级成"取数失败"）
            raise
        except AlphaVantageRateLimitError as exc:
            # Alpha Vantage 频率限制，继续尝试下一个供应商
            last_failure = VendorResult(
                ok=False, data=str(exc), vendor=vendor, reason=REASON_RATE_LIMITED
            )
            continue
        except AkShareRateLimitError as exc:
            # AkShare 频率限制（东方财富 API 限流），继续尝试下一个供应商
            # 东方财富 API 限流时，等待一段时间后重试可能有效，
            # 但为了快速响应，这里选择直接降级到下一个供应商
            last_failure = VendorResult(
                ok=False, data=str(exc), vendor=vendor, reason=REASON_RATE_LIMITED
            )
            continue
        except AkShareTimeoutError as exc:
            # AkShare 请求超时，继续尝试下一个供应商
            # 超时可能是网络问题或东方财富服务器响应缓慢
            last_failure = VendorResult(
                ok=False, data=str(exc), vendor=vendor, reason=REASON_TIMEOUT
            )
            continue
        except Exception as exc:
            # 其他异常（网络错误、数据不可用等）也尝试降级
            # 多供应商设计就是为了容错——一个供应商挂了，自动切换下一个
            last_failure = VendorResult(
                ok=False, data=str(exc), vendor=vendor, reason=REASON_VENDOR_ERROR
            )
            if vendor == fallback_vendors[-1]:
                # 最后一个供应商仍然失败：不再抛出笼统的 RuntimeError，
                # 而是生成保留原始原因的消息（数据不存在 ≠ 数据源故障）
                logger.warning(
                    "route_to_vendor(%s) 所有数据源均失败：%s", method, exc
                )
                break
            continue

    # 步骤6：所有供应商都失败
    # 统一失败出口：返回保留原始原因的消息，并带上机器可读的原因语义
    message = _build_failure_message(method, last_failure, synthetic_blocked)
    logger.warning(
        "route_to_vendor(%s) 取数失败（vendor=%s, reason=%s）：%s",
        method,
        last_failure.vendor if last_failure else "-",
        last_failure.reason if last_failure else "-",
        message,
    )
    return message
