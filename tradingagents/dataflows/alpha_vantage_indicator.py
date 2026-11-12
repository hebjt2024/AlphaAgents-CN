# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是技术指标（Technical Indicator）？
#    技术指标是用数学公式从股价和成交量数据中算出的数值，
#    帮助判断股票的趋势和买卖时机。
#    常见指标：
#    - RSI（相对强弱指数）：判断超买（>70）或超卖（<30）
#    - MACD（指数平滑异同移动平均线）：判断趋势变化
#    - SMA（简单移动平均线）：N 天收盘价的平均值，判断趋势方向
#    - EMA（指数移动平均线）：加权版 SMA，更重视近期价格
#    - 布林带（Bollinger Bands）：上下轨之间的"通道"，判断波动率
#    - ATR（平均真实波幅）：衡量波动率
#    - STOCH（随机振荡器）：判断超买超卖
#
# 2. 什么是 Alpha Vantage API？
#    一个金融数据 API 服务。通过 HTTP 请求获取技术指标数据。
#    本文件使用多种技术指标端点：RSI、MACD、SMA、EMA、BBANDS、ATR、STOCH。
#    每个 API 调用需要指定：股票代码、指标类型、时间周期等参数。
#
# 3. 什么是时间间隔（interval）？
#    股票数据可以按不同时间间隔获取：
#    - daily：每日数据（一天一条记录）
#    - weekly：每周数据
#    - monthly：每月数据
#    本文件主要使用 daily（每日）间隔。
#
# 4. 什么是时间周期（time_period）？
#    技术指标的计算窗口大小。
#    例如 RSI 的时间周期为 14 表示用过去 14 天的数据计算 RSI。
#    SMA 的时间周期为 50 表示 50 日均线。
#
# 5. 什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 给参数附加描述信息。
#    LangChain 框架读取"说明文字"作为 LLM 工具参数描述。
#
# 6. 什么是 f-string？
#    在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Hello {name}" 中 {name} 被替换为变量值。
#
# 7. 什么是 try-except 语句？
#    Python 异常处理机制，捕获异常后程序不崩溃。
#    可以有多个 except 块捕获不同类型的异常。
#
# 8. 什么是 CSV 格式？
#    逗号分隔的文本格式，每行一条记录。
#    Alpha Vantage 的技术指标数据默认返回 CSV 格式。
#
# 9. 什么是 API 限流（Rate Limit）？
#    服务器限制请求频率，超限时返回错误。
#    本文件通过捕获 AlphaVantageRateLimitError 处理。
#
# 10.什么是数据降级（Graceful Degradation）？
#    主方案失败时返回友好错误信息，而非让程序崩溃。
#
# 11.什么是 None 和空值检查？
#    None 是 Python 的空值。空字符串 "" 视为 False。
#    not data 检查 data 是否为 None 或空。
#
# 12.什么是 .strip() 方法？
#    去除字符串首尾的空白字符（空格、换行等）。
#
# 13.什么是默认参数？
#    函数参数可以设默认值。
#    如 interval: str = "daily" 调用时不传就用 "daily"。
#
# 14.什么是字典（dict）？
#    Python 的键值对容器，用 {} 定义。
#    例如 {"symbol": "AAPL", "interval": "daily"}
#
# 15.什么是 datetime 和 strftime？
#    datetime 是日期时间类。strftime(格式) 把日期格式化为字符串。
#    格式码：%Y=年, %m=月, %d=日, %H=时, %M=分, %S=秒
#
# 16.什么是 indicator_config 字典？
#    本文件定义了一个配置字典，把指标名映射到 API 参数。
#    包含：function_name（API 函数名）、time_period（时间周期）、series_type（价格类型）。
#    这种设计模式叫"配置驱动"——用配置字典控制行为，避免大量 if-else。
#
# 17.什么是 series_type？
#    技术指标可以基于不同的价格类型计算：
#    - close：收盘价（最常用）
#    - open：开盘价
#    - high：最高价
#    - low：最低价
# =============================================================================
#
# 模块名称：alpha_vantage_indicator.py
# 模块功能：使用 Alpha Vantage API 获取股票技术指标数据
# =============================================================================
# 本模块提供以下技术指标的获取功能：
#   1. get_technical_indicator() - 通用技术指标获取函数
#   2. get_stockstats_indicator() - 获取技术指标的时间窗口数据
#
# 支持的技术指标：
#   - RSI     : Relative Strength Index（相对强弱指数）
#   - MACD    : Moving Average Convergence Divergence（指数平滑异同移动平均线）
#   - SMA     : Simple Moving Average（简单移动平均线）
#   - EMA     : Exponential Moving Average（指数移动平均线）
#   - BBANDS  : Bollinger Bands（布林带）
#   - ATR     : Average True Range（平均真实波幅）
#   - STOCH   : Stochastic Oscillator（随机振荡器）
#
# 依赖说明：
#   - alpha_vantage_common: 提供 _make_api_request() 和 AlphaVantageRateLimitError
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------

# datetime 类：Python 标准库的日期时间处理类
# 用于获取当前时间并格式化（在元数据头部中显示检索时间）
# 常用方法：
#   - datetime.now()：获取当前时间
#   - dt.strftime(格式)：日期对象 → 字符串
from datetime import datetime

# Annotated: 用于为类型注解添加元数据说明
# LangChain 框架读取"说明文字"作为 LLM 工具参数描述
from typing import Annotated

# 从同目录的 alpha_vantage_common 模块导入公共工具
# . 表示相对导入——从当前包（dataflows 目录）导入
# 导入内容：
#   - _make_api_request: 统一的 API 请求函数
#   - AlphaVantageRateLimitError: API 限流异常类
from .alpha_vantage_common import (
    AlphaVantageRateLimitError,  # API 速率限制超限异常
    _make_api_request,  # 统一的 API 请求函数
)

# =============================================================================
# 技术指标配置字典
# =============================================================================
# 这是一个"配置驱动"设计模式：
# 不用大量 if-else 判断指标类型，而是用字典查找配置。
# 运行时示例：
#   indicator_config["rsi"]["function_name"] → "RSI"
#   indicator_config["macd"]["function_name"] → "MACD"
# 每个指标配置包含：
#   - function_name: Alpha Vantage API 的函数名
#   - time_period: 计算时间周期（天数），None 表示该指标不需要时间周期
#   - series_type: 使用哪种价格计算（close=收盘价）
# =============================================================================

indicator_config = {
    # RSI（相对强弱指数）：衡量价格动量，判断超买（>70）或超卖（<30）
    "rsi": {
        "function_name": "RSI",      # Alpha Vantage API 函数名
        "time_period": 14,           # 用过去 14 天数据计算
        "series_type": "close",      # 基于收盘价计算
    },
    # MACD（指数平滑异同移动平均线）：通过两条 EMA 的差值判断趋势变化
    "macd": {
        "function_name": "MACD",     # Alpha Vantage API 函数名
        "time_period": None,         # MACD 不需要 time_period 参数（有默认参数）
        "series_type": "close",      # 基于收盘价计算
    },
    # SMA（简单移动平均线）：N 天收盘价的算术平均值
    "sma": {
        "function_name": "SMA",      # Alpha Vantage API 函数名
        "time_period": 50,           # 默认 50 日均线
        "series_type": "close",      # 基于收盘价计算
    },
    # EMA（指数移动平均线）：加权版 SMA，更重视近期价格
    "ema": {
        "function_name": "EMA",      # Alpha Vantage API 函数名
        "time_period": 12,           # 默认 12 日均线
        "series_type": "close",      # 基于收盘价计算
    },
    # BBANDS（布林带）：由中轨（SMA）和上下轨（标准差）组成的通道
    "bbands": {
        "function_name": "BBANDS",   # Alpha Vantage API 函数名
        "time_period": 20,           # 默认 20 日布林带
        "series_type": "close",      # 基于收盘价计算
    },
    # ATR（平均真实波幅）：衡量市场波动率，常用于设置止损
    "atr": {
        "function_name": "ATR",      # Alpha Vantage API 函数名
        "time_period": 14,           # 默认 14 日
        "series_type": "close",      # 基于收盘价计算
    },
    # STOCH（随机振荡器）：用收盘价在近期价格区间中的位置判断超买超卖
    "stoch": {
        "function_name": "STOCH",    # Alpha Vantage API 函数名
        "time_period": None,         # STOCH 不需要 time_period（有默认参数）
        "series_type": "close",      # 基于收盘价计算
    },
}


# =============================================================================
# 函数：get_technical_indicator
# =============================================================================
def get_technical_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator type (rsi, macd, sma, ema, bbands, atr, stoch)"],
    interval: Annotated[str, "time interval (daily, weekly, monthly)"] = "daily",
    time_period: Annotated[str, "time period for the indicator"] = None,
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"] = None,
) -> str:
    """
    【公开函数】获取指定股票的技术指标数据。

    【调用时机】
      当系统需要获取某支股票的技术指标（如 RSI、MACD）时调用。
      通常被 LangChain Agent 作为工具调用。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"）。
      indicator (str): 指标类型，支持 "rsi"、"macd"、"sma"、"ema"、"bbands"、"atr"、"stoch"。
      interval (str): 时间间隔，默认 "daily"（每日）。可选 "weekly"、"monthly"。
      time_period (str, 可选): 时间周期。如果为 None，使用指标配置中的默认值。
      curr_date (str, 可选): 当前日期（Alpha Vantage 不使用）。

    【输出是什么】
      str: CSV 格式的技术指标数据，带元数据头部。
      运行时输出示例（RSI 指标）：
        # Technical Indicator: RSI for AAPL
        # Interval: daily, Time Period: 14
        # Data retrieved on: 2024-07-08 15:30:00

        time,RSI
        2024-07-08,55.23
        2024-07-05,48.12
        2024-07-03,62.87
        ...

    【输出被谁使用】
      被传递给 LLM 或分析模块，用于辅助交易决策。

    工作流程：
      1. 从配置字典中获取指标参数。
      2. 构建 API 请求参数。
      3. 调用 _make_api_request 发送请求。
      4. 检查响应并添加元数据头部。

    关键概念：
      - indicator_config: 一个字典，把指标名映射到 API 参数。
        通过配置字典避免大量 if-else，这是"配置驱动"设计模式。
      - series_type: 技术指标基于哪种价格计算（close=收盘价最常用）。

    Python 语法要点：
      - .lower() 将字符串转为小写，实现不区分大小写的匹配
      - in 运算符检查键是否在字典中
      - .get(key) 安全获取字典值，不存在返回 None
      - if value is not None 检查值不为 None
      - .update() 合并字典
    """
    try:
        # 将指标名转为小写，实现不区分大小写的匹配
        # .lower() 方法：如 "RSI" → "rsi"，"Macd" → "macd"
        indicator_lower = indicator.lower()

        # 检查指标是否在配置字典中
        # in 运算符检查键是否在字典中，返回 True/False
        # 如果指标不支持，raise 抛出 ValueError 异常
        if indicator_lower not in indicator_config:
            raise ValueError(
                f"Unsupported indicator: {indicator}. "
                f"Supported: {list(indicator_config.keys())}"
            )
            # list(dict.keys()) 把字典的键转为列表
            # 运行时示例：list(indicator_config.keys()) → ['rsi', 'macd', 'sma', 'ema', 'bbands', 'atr', 'stoch']

        # 从配置字典中获取该指标的配置
        # 运行时示例：config = {"function_name": "RSI", "time_period": 14, "series_type": "close"}
        config = indicator_config[indicator_lower]

        # 构建 API 请求参数
        # 字典用 {} 定义，键值对用冒号分隔
        # 运行时示例：params = {"symbol": "AAPL", "interval": "daily", "series_type": "close"}
        params = {
            "symbol": symbol,                        # 股票代码
            "interval": interval,                    # 时间间隔（daily/weekly/monthly）
            "series_type": config["series_type"],    # 价格类型（close=收盘价）
        }

        # 如果指标需要 time_period 参数且用户未指定，使用配置中的默认值
        # config.get("time_period") 获取配置中的时间周期，可能为 None
        # time_period is None 检查用户是否传入了 time_period
        # and 运算符：两个条件都为 True 才执行
        if config.get("time_period") is not None and time_period is None:
            # 使用配置中的默认时间周期
            # str() 把数字转为字符串（API 参数必须是字符串）
            params["time_period"] = str(config["time_period"])
        elif time_period is not None:
            # 用户指定了 time_period，使用用户的值
            params["time_period"] = time_period

        # 调用 Alpha Vantage API 获取技术指标数据
        # config["function_name"] 从配置中获取 API 函数名
        # 运行时示例：
        #   _make_api_request("RSI", {"symbol": "AAPL", "interval": "daily", ...})
        #   返回值 = "time,RSI\n2024-07-08,55.23\n2024-07-05,48.12\n..."
        data = _make_api_request(config["function_name"], params)

        # 检查响应是否为有效数据
        # not data 检查 None 和空字符串
        # .strip() 去除首尾空白
        # or 运算符：任一条件为 True 即为 True
        if not data or not data.strip():
            return f"No {indicator} data found for symbol '{symbol}'"

        # 构建元数据头部
        # f"..." 是 f-string 格式化字符串
        # .upper() 把指标名转为大写（显示更美观）
        # \n 是换行符
        header = f"# Technical Indicator: {indicator.upper()} for {symbol.upper()}\n"
        header += f"# Interval: {interval}"
        # 如果有 time_period，添加到头部
        # in 运算符检查键是否在字典中
        if "time_period" in params:
            header += f", Time Period: {params['time_period']}"
        header += "\n"
        # datetime.now() 获取当前时间，strftime 格式化
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 返回头部 + 原始 CSV 数据
        # 字符串拼接用 + 号
        return header + data

    except AlphaVantageRateLimitError:
        # 捕获 API 速率限制异常
        # 当请求频率超过 Alpha Vantage 的限制时抛出
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        # 捕获所有其他异常（网络错误、解析错误等）
        # str(e) 把异常对象转为字符串描述
        return f"Error retrieving {indicator} for {symbol}: {str(e)}"


# =============================================================================
# 函数：get_stockstats_indicator
# =============================================================================
def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator type (rsi, macd, sma, ema, bbands, atr, stoch)"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"],
    look_back_days: Annotated[int, "number of days to look back"] = 10,
) -> str:
    """
    【公开函数】获取技术指标的时间窗口数据（多日数据）。

    与 get_technical_indicator() 的区别：
    - get_technical_indicator() 返回完整的历史数据
    - 本函数通过 interval 参数控制返回的天数，更适合"看过去 N 天"的需求

    【调用时机】
      当系统需要分析某支股票在过去 N 天的技术指标走势时调用。

    【输入是什么】
      symbol (str): 股票代码。
      indicator (str): 指标类型。
      curr_date (str): 当前日期（Alpha Vantage 不使用）。
      look_back_days (int): 回溯天数，默认 10 天。

    【输出是什么】
      str: CSV 格式的技术指标数据，带元数据头部。

    【输出被谁使用】
      被传递给 LLM 或分析模块。

    工作流程：
      1. 从配置字典获取指标参数。
      2. 构建 API 请求参数（包含 outputsize 限制数据量）。
      3. 调用 API 获取数据。
      4. 添加元数据头部并返回。

    关键概念：
      - look_back_days 转换为 outputsize 参数：
        Alpha Vantage 的 compact 模式返回最近 100 条数据，
        full 模式返回所有历史数据。
        本函数根据 look_back_days 的大小选择模式。
    """
    try:
        # 将指标名转为小写
        indicator_lower = indicator.lower()

        # 检查指标是否在配置字典中
        if indicator_lower not in indicator_config:
            raise ValueError(
                f"Unsupported indicator: {indicator}. "
                f"Supported: {list(indicator_config.keys())}"
            )

        # 从配置字典中获取指标配置
        config = indicator_config[indicator_lower]

        # 构建 API 请求参数
        params = {
            "symbol": symbol,
            "interval": "daily",                   # 固定使用每日数据
            "series_type": config["series_type"],
        }

        # 如果指标需要 time_period 参数，添加到请求中
        if config.get("time_period") is not None:
            params["time_period"] = str(config["time_period"])

        # 调用 Alpha Vantage API 获取技术指标数据
        data = _make_api_request(config["function_name"], params)

        # 检查响应是否为有效数据
        if not data or not data.strip():
            return f"No {indicator} data found for symbol '{symbol}'"

        # 构建元数据头部
        # str() 把整数转为字符串
        header = f"# Technical Indicator: {indicator.upper()} for {symbol.upper()}\n"
        header += f"# Look back: {look_back_days} days\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 返回头部 + 原始 CSV 数据
        return header + data

    except AlphaVantageRateLimitError:
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        return f"Error retrieving {indicator} for {symbol}: {str(e)}"
