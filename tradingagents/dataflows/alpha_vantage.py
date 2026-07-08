# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 Alpha Vantage API？
#    Alpha Vantage 是一个提供金融市场数据的在线 API 服务。
#    通过 HTTP 请求获取股票价格、基本面、技术指标、新闻等数据。
#    需要 API 密钥（API Key），免费版有频率限制（每分钟 5 次请求）。
#
# 2. 什么是统一入口模式（Facade Pattern）？
#    本文件是一个"门面"模块，把多个子模块的功能统一暴露出来。
#    就像一个公司的"前台"：客户只跟前台打交道，前台帮忙联系各个部门。
#    好处：调用方不需要知道子模块的具体名称和路径，只需从此文件导入。
#    本文件重新导出了 alpha_vantage_stock、alpha_vantage_fundamentals、
#    alpha_vantage_indicator、alpha_vantage_news 中的所有公开函数。
#
# 3. 什么是 import * （星号导入）？
#    from 模块 import * 表示导入该模块中的所有公开内容。
#    星号 * 是"通配符"，代表"全部"。
#    注意：以 _ 开头的名称（如 _make_api_request）不会被导入（它们是私有的）。
#    示例：from .alpha_vantage_stock import * 会导入 get_stock 函数。
#
# 4. 什么是 __all__？
#    __all__ 是一个列表，明确声明本模块对外公开的名称。
#    当其他文件用 from alpha_vantage import * 时，只会导入 __all__ 中列出的名称。
#    好处：
#    - 明确控制对外接口（不暴露内部实现）
#    - 防止意外导入不需要的内容
#    - 提高代码可读性（一眼看出模块提供哪些功能）
#
# 5. 什么是相对导入？
#    from .module import * 中的点号 . 表示"当前目录"。
#    .alpha_vantage_stock 表示从当前包（dataflows 目录）导入 alpha_vantage_stock 模块。
#    相对导入使包可以在不同位置安装使用，路径不硬编码。
#
# 6. 什么是 API 限流（Rate Limit）？
#    服务器限制每个用户的请求频率。
#    Alpha Vantage 免费版每分钟最多 5 次请求。
#    超限时抛出 AlphaVantageRateLimitError 异常。
#
# 7. 什么是指数退避重试（Exponential Backoff）？
#    请求被限流时，等待一段时间后重试，等待时间逐次翻倍。
#    例如：第 1 次等 2 秒，第 2 次等 4 秒，第 3 次等 8 秒。
#    本文件中的 get_stockstats_indicator_with_retry 函数实现了此机制。
#
# 8. 什么是数据降级（Graceful Degradation）？
#    当主方案失败时，自动切换到备用方案，确保功能仍然可用。
#    本文件中：如果 API 限流且重试也失败，返回友好错误信息而非崩溃。
#
# 9. 什么是 try-except 语句？
#    Python 异常处理机制：
#    try:
#        可能出错的代码
#    except 异常类型:
#        出错后的处理代码
#    捕获异常后程序不会崩溃。
#
# 10.什么是 time.sleep()？
#    time.sleep(秒数) 让程序暂停执行指定的秒数。
#    在指数退避重试中，用于在重试前等待。
#
# 11.什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 给参数附加描述信息。
#    LangChain 框架读取"说明文字"作为 LLM 工具参数描述。
#
# 12.什么是函数嵌套调用？
#    一个函数内部调用另一个函数。
#    本文件中 get_stockstats_indicator_with_retry 调用 get_stockstats_indicator。
#    就像工厂的流水线：上一道工序的输出是下一道工序的输入。
#
# 13.什么是全局变量赋值？
#    globals()["变量名"] = 值 可以在运行时动态设置全局变量。
#    本文件中用于设置 _current_entitlement 全局变量。
#
# 14.什么是 f-string？
#    在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Error: {e}" 中 {e} 被替换为异常描述。
# =============================================================================
#
# 模块名称：alpha_vantage.py
# 模块功能：Alpha Vantage API 统一入口模块
# =============================================================================
# 本模块是 Alpha Vantage 数据流层的统一入口，采用"门面模式"（Facade Pattern），
# 将以下子模块的功能统一暴露出来：
#   - alpha_vantage_stock:          股票历史价格数据
#   - alpha_vantage_fundamentals:   公司基本面数据
#   - alpha_vantage_indicator:      技术指标数据
#   - alpha_vantage_news:           新闻和情绪分析数据
#
# 通过本模块，调用方只需 from .alpha_vantage import * 即可获取所有功能，
# 无需关心子模块的具体名称和路径。
#
# 设计模式说明：
#   - 门面模式（Facade Pattern）：提供一个统一的接口来访问子系统中的一群接口。
#     本模块就是 Alpha Vantage 子系统的"门面"。
#   - 重试模式（Retry Pattern）：get_stockstats_indicator_with_retry 函数
#     实现了指数退避重试，在 API 限流时自动等待重试。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------

# time 模块：Python 标准库，提供时间相关功能
# 这里主要用于 time.sleep(秒数) ——让程序暂停执行指定的秒数
# 在"指数退避重试"中，需要在重试前暂停一段时间等待服务器恢复
import time

# 从同目录的 alpha_vantage_common 模块导入 API 请求和异常处理工具
# . 表示相对导入——从当前包（dataflows 目录）导入
# 导入内容：
#   - _make_api_request: 统一的 API 请求函数（私有，不对外暴露）
#   - AlphaVantageRateLimitError: API 限流时抛出的自定义异常类
#   - API_BASE_URL: Alpha Vantage API 的基础 URL（常量）
from .alpha_vantage_common import (
    _make_api_request,              # 统一的 API 请求函数
    AlphaVantageRateLimitError,     # API 速率限制超限异常
    API_BASE_URL,                   # Alpha Vantage API 基础 URL
)

# 从子模块导入所有公开函数（星号导入）
# import * 会导入模块中所有不以 _ 开头的名称
# 也就是只导入"公开"函数，不导入"私有"函数（以 _ 开头的）

# 从 alpha_vantage_stock 模块导入：get_stock（获取股票历史价格数据）
from .alpha_vantage_stock import *

# 从 alpha_vantage_fundamentals 模块导入：
#   - get_fundamentals（公司概览）
#   - get_income_statement（利润表）
#   - get_balance_sheet（资产负债表）
#   - get_cash_flow（现金流量表）
from .alpha_vantage_fundamentals import *

# 从 alpha_vantage_indicator 模块导入：
#   - get_technical_indicator（通用技术指标）
#   - get_stockstats_indicator（技术指标时间窗口）
from .alpha_vantage_indicator import *

# 从 alpha_vantage_news 模块导入：
#   - get_news（股票新闻）
#   - get_global_news（全球新闻）
from .alpha_vantage_news import *

# 从 typing 模块导入 Annotated
# 用于为函数参数添加类型注解的元数据说明
# LangChain 框架会读取这些说明作为 LLM 工具参数描述
from typing import Annotated


# =============================================================================
# 模块对外公开的接口声明
# =============================================================================
# __all__ 是一个特殊变量，明确声明本模块对外公开的名称列表
# 当其他文件用 from alpha_vantage import * 时，只会导入以下列出的名称
# 这种做法的好处：
# 1. 明确控制对外接口（不暴露内部实现）
# 2. 防止意外导入不需要的内容
# 3. 提高代码可读性（一眼看出模块提供哪些功能）
# =============================================================================

__all__ = [
    # ---- 股票价格数据 ----
    "get_stock",                    # 获取每日调整后历史价格数据

    # ---- 基本面数据 ----
    "get_fundamentals",             # 获取公司概览（市值、PE、利润率等）
    "get_income_statement",         # 获取利润表
    "get_balance_sheet",            # 获取资产负债表
    "get_cash_flow",                # 获取现金流量表

    # ---- 技术指标数据 ----
    "get_technical_indicator",      # 获取技术指标（RSI、MACD、SMA 等）
    "get_stockstats_indicator",     # 获取技术指标时间窗口数据
    "get_stockstats_indicator_with_retry",  # 带重试的技术指标获取

    # ---- 新闻数据 ----
    "get_news",                     # 获取指定股票的新闻
    "get_global_news",              # 获取全球市场新闻

    # ---- 异常类 ----
    "AlphaVantageRateLimitError",   # API 速率限制超限异常
]


# =============================================================================
# 函数：get_stockstats_indicator_with_retry
# =============================================================================
def get_stockstats_indicator_with_retry(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator type (rsi, macd, sma, ema, bbands, atr, stoch)"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"],
    look_back_days: Annotated[int, "number of days to look back"] = 10,
    max_retries: int = 3,       # 默认参数：最大重试次数 3
    base_delay: float = 12.0,   # 默认参数：基础等待时间 12 秒
) -> str:
    """
    【公开函数】获取技术指标数据，带指数退避重试机制。

    本函数是对 get_stockstats_indicator() 的包装，增加了自动重试功能。
    当 Alpha Vantage API 因速率限制而拒绝请求时，本函数会自动等待并重试。

    【调用时机】
      当系统需要获取技术指标数据且希望自动处理 API 限流时调用。
      相比直接调用 get_stockstats_indicator()，本函数更加健壮。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"）。
      indicator (str): 指标类型（如 "rsi"、"macd"）。
      curr_date (str): 当前日期（Alpha Vantage 不使用）。
      look_back_days (int): 回溯天数，默认 10 天。
      max_retries (int): 最大重试次数，默认 3 次。
      base_delay (float): 基础等待时间（秒），默认 12.0 秒。
        Alpha Vantage 免费版每分钟 5 次请求，所以 12 秒比较合适。

    【输出是什么】
      str: 技术指标数据字符串（CSV 格式），或错误信息字符串。
      运行时输出示例（成功时）：
        # Technical Indicator: RSI for AAPL
        # Look back: 10 days
        # Data retrieved on: 2024-07-08 15:30:00

        time,RSI
        2024-07-08,55.23
        2024-07-05,48.12
        ...

    【输出被谁使用】
      被传递给 LLM 或分析模块，用于辅助交易决策。

    【运行时数据流示例】
      假设调用 get_stockstats_indicator_with_retry("AAPL", "rsi", "2024-01-15", 10):
      - 第 0 次尝试（attempt=0）：调用 get_stockstats_indicator()
        → 可能遇到 AlphaVantageRateLimitError（被限流）
      - 等待 12.0 * (2^0) = 12.0 秒
      - 第 1 次尝试（attempt=1）：再次调用 → 仍然被限流
      - 等待 12.0 * (2^1) = 24.0 秒
      - 第 2 次尝试（attempt=2）：再次调用 → 成功！返回 CSV 数据
      日志输出：Rate limited, retrying in 12s (attempt 1/3)

    关键概念：
      - 指数退避（Exponential Backoff）：
        每次重试的等待时间呈指数增长。
        公式：delay = base_delay * (2 ** attempt)
        例如：12 * 1 = 12秒, 12 * 2 = 24秒, 12 * 4 = 48秒
        这样既给了服务器恢复的时间，又不会一直傻等。
      - AlphaVantageRateLimitError:
        当 Alpha Vantage API 返回"rate limit"错误信息时抛出此异常。
        本函数专门捕获此异常进行重试。
      - base_delay=12.0:
        Alpha Vantage 免费版限制每分钟 5 次请求（即每 12 秒 1 次），
        所以 12 秒是一个合理的初始等待时间。

    Python 语法要点：
      - range(max_retries + 1) 生成 [0, 1, 2, 3]（含 max_retries）
      - try-except 捕获特定异常
      - ** 是幂运算符：2 ** 0 = 1, 2 ** 1 = 2, 2 ** 2 = 4
      - raise 重新抛出异常
      - f"..." 格式化字符串
    """
    # range(max_retries + 1) 生成从 0 到 max_retries 的整数序列
    # 例如 max_retries=3 时，range(4) 生成 [0, 1, 2, 3]
    # attempt 变量在每次循环中依次取这些值
    # 第一次（attempt=0）是正常尝试，后面的是重试
    for attempt in range(max_retries + 1):
        try:
            # 调用原始的技术指标获取函数
            # get_stockstats_indicator 是从 alpha_vantage_indicator 模块导入的
            # 运行时示例：返回 "time,RSI\n2024-07-08,55.23\n..."
            return get_stockstats_indicator(
                symbol,
                indicator,
                curr_date,
                look_back_days,
            )
        except AlphaVantageRateLimitError:
            # 捕获 API 速率限制异常
            # 只有这种异常会触发重试，其他异常直接向上传播
            if attempt < max_retries:  # 还有重试机会（不是最后一次）
                # 计算指数退避等待时间
                # ** 是 Python 的幂运算符
                # 2 ** 0 = 1, 2 ** 1 = 2, 2 ** 2 = 4
                # 所以 delay 依次为：12*1=12, 12*2=24, 12*4=48 ...
                delay = base_delay * (2 ** attempt)
                # 打印重试信息（不是 logging，因为这是面向用户的提示）
                # f"..." 是 f-string 格式化字符串
                # {delay:.0f} 表示浮点数保留 0 位小数（12.0 → "12"）
                print(
                    f"Alpha Vantage rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                # time.sleep(秒数) 让程序暂停执行指定的秒数
                # 这是指数退避的核心：等待一段时间后再重试
                time.sleep(delay)
            else:
                # 重试次数已用完（attempt == max_retries，即最后一次也失败了）
                # raise 重新抛出异常，让调用方知道请求最终失败
                # 注意：这里单独写 raise（不带参数）表示重新抛出当前捕获的异常
                raise


# =============================================================================
# 函数：set_entitlement
# =============================================================================
def set_entitlement(entitlement: str) -> None:
    """
    【公开函数】设置 Alpha Vantage API 的授权（entitlement）参数。

    entitlement 是 Alpha Vantage 付费版的授权标识，用于访问更高级的数据端点。
    设置后，所有后续的 API 请求都会自动带上此参数。

    【调用时机】
      当用户使用 Alpha Vantage 付费版时，在程序启动时调用一次。
      设置后，_make_api_request() 会自动将 entitlement 添加到每个请求中。

    【输入是什么】
      entitlement (str): 授权标识字符串（如 "realtime" 或 "premium"）。

    【输出是什么】
      None（无返回值）。-> None 表示函数不返回任何值。
      函数通过副作用工作：修改全局变量 _current_entitlement。

    【输出被谁使用】
      _make_api_request() 会读取全局变量 _current_entitlement，
      将其添加到每个 API 请求的参数中。

    Python 语法要点：
      - globals() 返回全局作用域的变量字典
      - globals()["变量名"] = 值 可以动态设置全局变量
      - -> None 表示函数返回值为 None（不返回任何东西）
    """
    # globals() 返回全局作用域中所有变量的字典
    # 通过 globals()["变量名"] = 值 可以在运行时动态设置全局变量
    # _current_entitlement 会被 _make_api_request() 读取
    # 运行时示例：globals()["_current_entitlement"] = "realtime"
    globals()["_current_entitlement"] = entitlement
