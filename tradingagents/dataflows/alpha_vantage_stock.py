# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 Alpha Vantage API？
#    Alpha Vantage 是一个提供金融市场数据的在线 API 服务。
#    通过发送 HTTP 请求获取股票数据，需要 API 密钥（API Key）。
#    本文件使用 TIME_SERIES_DAILY_ADJUSTED 端点获取每日调整后的股价数据。
#
# 2. 什么是 OHLCV？
#    O=Open（开盘价）, H=High（最高价）, L=Low（最低价）,
#    C=Close（收盘价）, V=Volume（成交量）。
#    Alpha Vantage 还提供 Adjusted Close（调整后收盘价）和 Dividend Amount（分红）。
#
# 3. 什么是调整后收盘价（Adjusted Close）？
#    由于拆股和分红会导致股价"跳变"，调整后收盘价会"回溯调整"历史价格，
#    使价格序列连续可比。例如：1拆10后，历史价格除以10。
#    技术分析和回测通常使用调整后价格。
#
# 4. 什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 给类型注解附加元数据。
#    在类型检查器眼中是 str，但"说明文字"被 LangChain 框架读取，
#    作为 LLM 工具的参数描述，帮助 AI 模型理解参数用途。
#
# 5. 什么是 f-string？
#    在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Hello {name}" 中 {name} 会被替换为变量 name 的值。
#
# 6. 什么是 datetime 和 strftime？
#    datetime 是 Python 标准库的日期时间类。
#    strftime(格式) 把日期对象格式化为字符串。
#    格式码：%Y=年, %m=月, %d=日
#
# 7. 什么是 try-except 语句？
#    Python 异常处理机制：
#    try:
#        可能出错的代码
#    except 异常类型:
#        出错后的处理代码
#    可以有多个 except 块，捕获不同类型的异常。
#
# 8. 什么是 CSV 格式？
#    CSV（逗号分隔值）是纯文本格式，每行一条记录，字段间用逗号分隔。
#    示例：timestamp,open,high,low,close,volume
#          2024-01-15,187.15,188.44,183.89,185.64,82488700
#
# 9. 什么是 API 限流（Rate Limit）？
#    服务器限制每个用户的请求频率（如每分钟 5 次）。
#    超过限制时返回错误信息。
#    本文件通过捕获 AlphaVantageRateLimitError 异常来处理限流。
#
# 10. 什么是数据降级（Graceful Degradation）？
#     当主方案失败时，自动切换到备用方案。
#     本文件中：如果 API 返回的数据为空或出错，返回友好的错误信息而非崩溃。
#
# 11. 什么是布尔检查和空值？
#     None 是 Python 的空值。空字符串 ""、空列表 [] 都视为 False。
#     not data 检查 data 是否为 None 或空。
#     data is not None 检查 data 是否不为 None。
#
# 12. 什么是 .strip() 方法？
#     .strip() 去除字符串首尾的空白字符（空格、换行、制表符）。
#     常用于检查字符串是否为空或纯空白。
#
# 13. 什么是相对导入？
#     from .module import function 从当前包导入。
#     点号 . 表示"当前目录"。
# =============================================================================
#
# 模块名称：alpha_vantage_stock.py
# 模块功能：使用 Alpha Vantage API 获取股票历史价格数据
# =============================================================================
# 本模块通过 Alpha Vantage 的 TIME_SERIES_DAILY_ADJUSTED 端点获取
# 每日调整后的股价数据（OHLCV + 调整后收盘价 + 分红金额）。
#
# 依赖说明：
#   - alpha_vantage_common: 提供通用的 API 请求封装和 CSV 过滤工具
#     _make_api_request(): 发送 HTTP 请求并返回响应文本
#     _filter_csv_by_date_range(): 按日期范围过滤 CSV 数据
#     AlphaVantageRateLimitError: 速率限制异常类
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------

# datetime 类：Python 标准库的日期时间处理类
# 用于日期格式化和当前时间获取
# 常用方法：
#   - datetime.now()：获取当前时间
#   - dt.strftime(格式)：日期对象 → 字符串
from datetime import datetime

# 从同目录下的 alpha_vantage_common 模块导入公共工具
# . 表示"相对导入"——从当前包（dataflows 目录）导入
# 导入的函数：
#   - _make_api_request: 封装了 HTTP 请求的发送和错误处理
#   - _filter_csv_by_date_range: 按日期范围过滤 CSV 数据
#   - AlphaVantageRateLimitError: API 限流时抛出的自定义异常
from .alpha_vantage_common import (
    _make_api_request,                # 发送 API 请求的统一入口函数
    _filter_csv_by_date_range,       # 按日期范围过滤 CSV 数据
    AlphaVantageRateLimitError,      # API 速率限制超限异常
)

# Annotated: 用于为类型注解添加元数据说明
# LangChain 框架会读取这些说明作为 LLM 工具的参数描述
from typing import Annotated


# =============================================================================
# 函数：get_stock
# =============================================================================
def get_stock(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    【公开函数】获取股票的每日调整后历史价格数据。

    【调用时机】
      当系统需要获取某支股票在指定日期范围内的历史价格数据时调用。
      通常被 LangChain Agent 作为工具调用。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"、"TSLA"）。
      start_date (str): 开始日期，格式 "YYYY-MM-DD"。
      end_date (str): 结束日期，格式 "YYYY-MM-DD"。

    【输出是什么】
      str: 格式化的 CSV 格式股价数据字符串，带元数据头部。
      运行时输出示例：
        # Alpha Vantage Daily Adjusted Stock Data for AAPL
        # Date range: 2024-01-01 to 2024-01-31
        # Data retrieved on: 2024-07-08 15:30:00

        timestamp,open,high,low,close,adjusted_close,volume,dividend_amount
        2024-01-31,187.15,188.44,183.89,185.64,185.64,82488700,0.0000
        2024-01-30,184.22,185.88,183.43,184.25,184.25,58414500,0.0000
        ...

    【输出被谁使用】
      被传递给 LLM 或分析模块，用于技术分析、趋势判断等。

    工作流程：
      1. 构建 API 请求参数。
      2. 调用 _make_api_request 发送请求。
      3. 检查响应是否为有效数据。
      4. 按日期范围过滤数据。
      5. 添加元数据头部并返回。

    关键概念：
      - TIME_SERIES_DAILY_ADJUSTED: Alpha Vantage 的 API 函数名，
        表示"每日调整后时间序列"。返回包含开盘价、最高价、最低价、
        收盘价、调整后收盘价、成交量和分红金额的数据。
      - outputsize="full": 请求完整的历史数据（20年以上），
        而不是默认的最近 100 天。
      - datatype="csv": 指定返回 CSV 格式（而非 JSON），
        CSV 格式更适合表格数据的处理和传递。

    Python 语法要点：
      - try-except-else-finally 结构：
        try: 尝试执行可能出错的代码
        except 类型1: 捕获类型1的异常
        except 类型2: 捕获类型2的异常
        else: 没有异常时执行
        finally: 无论是否异常都执行
      - .strip() 去除字符串首尾空白字符
      - f"..." 格式化字符串
      - None 和空字符串在布尔判断中都为 False
    """
    try:
        # 构建 API 请求参数
        # 字典（dict）用花括号 {} 定义，键值对用冒号分隔
        # 运行时示例：
        #   params = {"symbol": "AAPL", "outputsize": "full", "datatype": "csv"}
        params = {
            "symbol": symbol,           # 股票代码
            "outputsize": "full",       # full = 获取完整历史数据（20+年）
                                         # compact = 仅最近 100 天（默认）
            "datatype": "csv",          # 返回 CSV 格式（而非 JSON）
        }

        # 调用公共函数发送 API 请求
        # _make_api_request 会自动添加 API 密钥、function 等通用参数
        # 运行时示例：
        #   实际请求参数 = {"symbol":"AAPL", "outputsize":"full", "datatype":"csv",
        #                  "function":"TIME_SERIES_DAILY_ADJUSTED", "apikey":"..."}
        #   返回值 = "timestamp,open,high,low,close,adjusted_close,volume,dividend_amount\n2024-01-31,..."
        data = _make_api_request("TIME_SERIES_DAILY_ADJUSTED", params)

        # 检查 API 响应是否为有效数据
        # data 可能是 None 或空字符串（如 API 返回错误）
        # not data 检查：None 和空字符串都是 False，not False = True
        # .strip() 去除首尾空白字符，空白字符串 .strip() 后为空
        # or 运算符：两个条件只要一个为 True 就为 True
        if not data or not data.strip():
            return f"No data found for symbol '{symbol}'"

        # 按日期范围过滤 CSV 数据
        # Alpha Vantage 的 "full" 模式返回所有历史数据（可能20年以上），
        # 需要用 pandas 过滤出用户指定日期范围内的数据
        # _filter_csv_by_date_range 内部使用 pandas 的布尔索引进行过滤
        filtered_data = _filter_csv_by_date_range(data, start_date, end_date)

        # 构建元数据头部
        # f"..." 是 f-string 格式化字符串，{变量} 被替换为实际值
        # \n 是换行符
        header = f"# Alpha Vantage Daily Adjusted Stock Data for {symbol.upper()}\n"
        header += f"# Date range: {start_date} to {end_date}\n"
        # datetime.now() 获取当前时间，strftime 格式化为字符串
        # %Y-%m-%d %H:%M:%S 格式如 "2024-07-08 15:30:00"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + filtered_data

    except AlphaVantageRateLimitError:
        # 专门捕获 API 速率限制异常
        # 当请求频率超过 Alpha Vantage 的限制时抛出
        # 返回友好的错误信息，而不是让程序崩溃
        return (
            f"Alpha Vantage API rate limit exceeded. "
            f"Please wait a minute and try again."
        )

    except Exception as e:
        # 捕获所有其他异常（网络错误、解析错误等）
        # Exception 是所有异常的基类
        # str(e) 把异常对象转为字符串描述
        return f"Error retrieving stock data for {symbol}: {str(e)}"
