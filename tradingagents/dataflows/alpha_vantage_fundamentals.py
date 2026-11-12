# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是基本面数据（Fundamentals）？
#    基本面数据是描述公司"健康状况"的核心财务指标，包括：
#    - 市值（Market Cap）：公司值多少钱
#    - 市盈率（PE Ratio）：股价 / 每股收益，衡量估值高低
#    - 市净率（PB Ratio）：股价 / 每股净资产
#    - 利润率（Profit Margin）：净利润 / 收入
#    - 股息率（Dividend Yield）：每股分红 / 股价
#    - 基本面分析就是通过这些指标判断公司是否值得投资。
#
# 2. 什么是利润表（Income Statement）？
#    利润表反映公司在一段时期的经营成果：
#    收入 → 减去成本 → 毛利润 → 减去费用 → 营业利润 → 减去税 → 净利润
#    Alpha Vantage 提供 INCOME_STATEMENT 端点获取此数据。
#
# 3. 什么是资产负债表（Balance Sheet）？
#    资产负债表反映公司在某一时点的财务状况：
#    资产 = 负债 + 股东权益
#    - 资产：公司拥有的（现金、存货、设备等）
#    - 负债：公司欠的（借款、应付账款等）
#    - 股东权益：净资产（资产 - 负债）
#    Alpha Vantage 提供 BALANCE_SHEET 端点获取此数据。
#
# 4. 什么是现金流量表（Cash Flow Statement）？
#    现金流量表反映公司现金的流入和流出，分三部分：
#    - 经营活动现金流：日常业务产生的现金
#    - 投资活动现金流：买卖设备、投资等
#    - 筹资活动现金流：发债、分红、回购股票等
#    现金流是评估公司真实财务状况的关键（比利润更难造假）。
#    Alpha Vantage 提供 CASH_FLOW 端点获取此数据。
#
# 5. 什么是 Alpha Vantage API？
#    一个金融数据 API 服务。通过 HTTP 请求获取股票数据，需要 API 密钥。
#    本文件使用 OVERVIEW、INCOME_STATEMENT、BALANCE_SHEET、CASH_FLOW 端点。
#
# 6. 什么是 JSON 和 CSV？
#    JSON：轻量级数据交换格式，用 {} 表示对象，[] 表示数组。
#    例如 {"Name": "Apple", "MarketCap": "3000000000000"}
#    CSV：逗号分隔的文本格式。
#    注意：Alpha Vantage 的基本面数据默认返回 JSON 格式（不是 CSV）。
#    本文件用 json.loads() 把 JSON 字符串解析为 Python 字典。
#
# 7. 什么是 dict（字典）？
#    Python 的键值对容器，用 {} 定义。
#    例如 {"Name": "Apple", "PE": "32.45"}
#    通过 dict["Name"] 或 dict.get("Name", "N/A") 访问值。
#    .get(key, default) 在键不存在时返回默认值，不报错。
#
# 8. 什么是 json.loads()？
#    把 JSON 字符串解析为 Python 对象（通常是字典或列表）。
#    例如 json.loads('{"a": 1}') → {"a": 1}
#    如果字符串不是有效 JSON，抛出 json.JSONDecodeError 异常。
#
# 9. 什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 给类型注解附加元数据。
#    LangChain 框架读取"说明文字"作为 LLM 工具参数描述。
#
# 10.什么是 try-except 语句？
#    Python 异常处理机制，捕获异常后程序不崩溃。
#    可以有多个 except 块捕获不同类型的异常。
#
# 11.什么是 None 和空值检查？
#    None 是 Python 的空值。空字典 {}、空字符串 "" 都视为 False。
#    not data 检查 data 是否为 None 或空。
#    data is not None 检查 data 不为 None。
#
# 12.什么是 f-string？
#    在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Hello {name}" 中 {name} 被替换为变量值。
#
# 13.什么是 .get() 方法和默认值？
#    dict.get(key, default) 安全获取字典值。
#    键存在返回对应值，键不存在返回 default，不会报错。
#    例如 {"a":1}.get("b", "N/A") → "N/A"
#
# 14.什么是 API 限流（Rate Limit）？
#    服务器限制请求频率，超限时返回错误。
#    本文件通过捕获 AlphaVantageRateLimitError 处理。
#
# 15.什么是默认参数？
#    函数参数可以设默认值，如 curr_date: str = None
#    调用时不传此参数就使用默认值 None。
# =============================================================================
#
# 模块名称：alpha_vantage_fundamentals.py
# 模块功能：使用 Alpha Vantage API 获取公司基本面数据
# =============================================================================
# 本模块提供以下功能：
#   1. get_fundamentals()    - 获取公司概览（市值、PE、利润率等）
#   2. get_income_statement() - 获取利润表
#   3. get_balance_sheet()   - 获取资产负债表
#   4. get_cash_flow()       - 获取现金流量表
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
# json 模块：用于解析 JSON 格式的 API 响应
# Alpha Vantage 的基本面数据返回 JSON 格式（而非 CSV）
# json.loads(str) 把 JSON 字符串解析为 Python 字典/列表
# 例如 json.loads('{"Name": "Apple"}') → {"Name": "Apple"}
import json
from datetime import datetime

# Annotated: 用于为类型注解添加元数据说明
# Annotated[str, "ticker symbol"] 在类型检查器眼中是 str
# 但"ticker symbol"被 LangChain 框架读取作为 LLM 工具参数描述
from typing import Annotated

# 从同目录的 alpha_vantage_common 模块导入公共工具
# . 表示相对导入——从当前包（dataflows 目录）导入
# 导入内容：
#   - _make_api_request: 统一的 API 请求函数，封装了 HTTP 请求和错误处理
#   - AlphaVantageRateLimitError: API 限流时抛出的自定义异常类
from .alpha_vantage_common import (
    AlphaVantageRateLimitError,  # API 速率限制超限异常
    _make_api_request,  # 统一的 API 请求函数
)


# =============================================================================
# 函数：get_fundamentals
# =============================================================================
def get_fundamentals(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"] = None
):
    """
    【公开函数】获取公司基本面数据概览。

    【调用时机】
      当系统需要了解公司的基本情况（估值、盈利能力、财务健康等）时调用。
      通常被 LangChain Agent 作为工具调用。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"）。
      curr_date (str, 可选): 当前日期（Alpha Vantage 不使用此参数，仅为接口一致性保留）。
        = None 表示有默认值 None，调用时可以不传。

    【输出是什么】
      str: 包含公司基本面关键指标的格式化文本。
      运行时输出示例：
        # Company Fundamentals for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        Symbol: AAPL
        Name: Apple Inc.
        Sector: Technology
        Industry: Consumer Electronics
        Market Capitalization: 3450000000000
        PE Ratio: 32.45
        PEG Ratio: 2.81
        ...

    【输出被谁使用】
      被传递给 LLM 或分析模块，用于评估公司投资价值。

    工作流程：
      1. 调用 Alpha Vantage 的 OVERVIEW 端点。
      2. 解析 JSON 响应为 Python 字典。
      3. 遍历所有字段并格式化输出。
      4. 添加元数据头部。

    关键概念：
      - OVERVIEW: Alpha Vantage 的 API 函数名，返回公司基本信息概览。
        响应是 JSON 格式的字典，包含数十个字段。
      - json.loads(): 将 JSON 字符串解析为 Python 字典。
      - dict.items(): 遍历字典的所有键值对。

    Python 语法要点：
      - try-except: 捕获异常，防止程序崩溃
      - .strip() 去除字符串首尾空白
      - not data 检查是否为 None 或空字符串
      - isinstance(obj, dict) 检查是否为字典类型
      - for key, value in dict.items() 遍历字典键值对
      - "\n".join(列表) 用换行符连接列表中的字符串
    """
    try:
        # 构建 API 请求参数
        # 运行时示例：params = {"symbol": "AAPL"}
        params = {
            "symbol": symbol,  # 股票代码
        }

        # 调用 Alpha Vantage API 获取公司概览数据
        # "OVERVIEW" 是 Alpha Vantage 的 API 函数名，返回 JSON 格式的公司信息
        # 运行时示例：data = '{"Symbol": "AAPL", "Name": "Apple Inc.", "MarketCapitalization": "3450000000000", ...}'
        data = _make_api_request("OVERVIEW", params)

        # 检查响应是否为有效数据
        # not data 检查 None 和空字符串（都是 False，not False = True）
        # .strip() 去除首尾空白，空白字符串 strip 后为空
        # or 运算符：两个条件任一为 True 即为 True
        if not data or not data.strip():
            return f"No fundamentals data found for symbol '{symbol}'"

        # 解析 JSON 响应为 Python 字典
        # json.loads() 把 JSON 字符串转为 Python 字典
        # 运行时示例：
        #   data = '{"Symbol": "AAPL", "Name": "Apple Inc.", ...}'
        #   parsed = {"Symbol": "AAPL", "Name": "Apple Inc.", ...}
        parsed = json.loads(data)

        # 检查解析结果是否为有效字典
        # None 在 Python 中为 False，非 None 字典为 True
        # isinstance(obj, dict) 检查 obj 是否为字典类型
        # and 运算符：两个条件都为 True 才为 True
        if not parsed or not isinstance(parsed, dict):
            return f"No fundamentals data found for symbol '{symbol}'"

        # 构建输出行列表
        # 遍历字典中的所有键值对
        # .items() 方法返回字典中所有 (key, value) 对的视图
        # for key, value in parsed.items() 是"元组解包"语法：
        #   每次循环中，key 和 value 分别接收字典的一个键和对应的值
        # 运行时示例：
        #   第1次循环：key="Symbol", value="AAPL"
        #   第2次循环：key="Name", value="Apple Inc."
        #   ...
        lines = []  # 创建空列表
        for key, value in parsed.items():
            # str(value) 把值转为字符串（确保统一为字符串类型）
            # f"{key}: {value}" 格式化为一行
            # .append() 把字符串添加到列表末尾
            lines.append(f"{key}: {value}")

        # 构建元数据头部
        # f"..." 是 f-string 格式化字符串，{变量} 被替换为实际值
        # .upper() 把股票代码转为大写（金融惯例）
        # \n 是换行符
        header = f"# Company Fundamentals for {symbol.upper()}\n"
        # datetime.now() 获取当前时间
        # .strftime('%Y-%m-%d %H:%M:%S') 格式化为 "2024-07-08 15:30:00" 格式
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 用换行符连接所有行并返回
        # "\n".join(lines) 把列表中的字符串用换行符连接成一个长字符串
        # 例如 ["a", "b", "c"] → "a\nb\nc"
        return header + "\n".join(lines)

    except AlphaVantageRateLimitError:
        # 捕获 API 速率限制异常
        # 当请求频率超过限制时抛出
        # 返回友好提示，而非让程序崩溃
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        # 捕获所有其他异常（网络错误、JSON 解析错误等）
        # Exception 是所有异常的基类
        # str(e) 把异常对象转为字符串描述
        return f"Error retrieving fundamentals for {symbol}: {str(e)}"


# =============================================================================
# 函数：get_income_statement
# =============================================================================
def get_income_statement(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"] = None
):
    """
    【公开函数】获取公司利润表数据。

    【调用时机】
      当系统需要分析公司的盈利能力时调用。

    【输入是什么】
      symbol (str): 股票代码。
      curr_date (str, 可选): 当前日期（Alpha Vantage 不使用）。

    【输出是什么】
      str: JSON 格式的利润表数据，带元数据头部。
      运行时输出示例：
        # Income Statement for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        {
          "symbol": "AAPL",
          "annualReports": [
            {
              "fiscalDateEnding": "2023-09-30",
              "totalRevenue": "383285000000",
              "netIncome": "96995000000",
              ...
            }
          ],
          "quarterlyReports": [...]
        }

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司盈利能力。

    关键概念：
      - INCOME_STATEMENT: Alpha Vantage 的 API 函数名。
        返回年度和季度的利润表数据（JSON 格式）。
      - 利润表包含收入、成本、毛利润、营业利润、净利润等字段。
    """
    try:
        # 构建 API 请求参数
        params = {"symbol": symbol}

        # 调用 Alpha Vantage API 获取利润表
        # 返回 JSON 格式的年度和季度利润表数据
        data = _make_api_request("INCOME_STATEMENT", params)

        # 检查响应是否为有效数据
        if not data or not data.strip():
            return f"No income statement data found for symbol '{symbol}'"

        # 构建元数据头部
        header = f"# Income Statement for {symbol.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 注意：这里直接返回原始 JSON 数据（未解析）
        # 因为利润表是嵌套结构，直接返回 JSON 字符串更完整
        return header + data

    except AlphaVantageRateLimitError:
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        return f"Error retrieving income statement for {symbol}: {str(e)}"


# =============================================================================
# 函数：get_balance_sheet
# =============================================================================
def get_balance_sheet(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"] = None
):
    """
    【公开函数】获取公司资产负债表数据。

    【调用时机】
      当系统需要分析公司的财务状况（资产、负债、股东权益）时调用。

    【输入是什么】
      symbol (str): 股票代码。
      curr_date (str, 可选): 当前日期（Alpha Vantage 不使用）。

    【输出是什么】
      str: JSON 格式的资产负债表数据，带元数据头部。
      运行时输出示例：
        # Balance Sheet for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        {
          "symbol": "AAPL",
          "annualReports": [
            {
              "fiscalDateEnding": "2023-09-30",
              "totalAssets": "352755000000",
              "totalLiabilities": "290437000000",
              ...
            }
          ],
          "quarterlyReports": [...]
        }

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司财务健康状况。

    关键概念：
      - BALANCE_SHEET: Alpha Vantage 的 API 函数名。
        返回年度和季度的资产负债表数据（JSON 格式）。
      - 资产负债表包含总资产、总负债、股东权益、现金、存货等字段。
    """
    try:
        # 构建 API 请求参数
        params = {"symbol": symbol}

        # 调用 Alpha Vantage API 获取资产负债表
        # 返回 JSON 格式的年度和季度资产负债表数据
        data = _make_api_request("BALANCE_SHEET", params)

        # 检查响应是否为有效数据
        if not data or not data.strip():
            return f"No balance sheet data found for symbol '{symbol}'"

        # 构建元数据头部
        header = f"# Balance Sheet for {symbol.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 直接返回原始 JSON 数据
        return header + data

    except AlphaVantageRateLimitError:
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        return f"Error retrieving balance sheet for {symbol}: {str(e)}"


# =============================================================================
# 函数：get_cash_flow
# =============================================================================
def get_cash_flow(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used by Alpha Vantage)"] = None
):
    """
    【公开函数】获取公司现金流量表数据。

    【调用时机】
      当系统需要分析公司的现金流状况时调用。
      现金流是评估公司真实财务状况的关键指标（比利润更难造假）。

    【输入是什么】
      symbol (str): 股票代码。
      curr_date (str, 可选): 当前日期（Alpha Vantage 不使用）。

    【输出是什么】
      str: JSON 格式的现金流量表数据，带元数据头部。
      运行时输出示例：
        # Cash Flow Statement for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        {
          "symbol": "AAPL",
          "annualReports": [
            {
              "fiscalDateEnding": "2023-09-30",
              "operatingCashflow": "110543000000",
              "capitalExpenditures": "-10959000000",
              ...
            }
          ],
          "quarterlyReports": [...]
        }

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司现金流健康状况。

    关键概念：
      - CASH_FLOW: Alpha Vantage 的 API 函数名。
        返回年度和季度的现金流量表数据（JSON 格式）。
      - 现金流量表包含经营现金流、投资现金流、筹资现金流、资本支出等字段。
    """
    try:
        # 构建 API 请求参数
        params = {"symbol": symbol}

        # 调用 Alpha Vantage API 获取现金流量表
        # 返回 JSON 格式的年度和季度现金流量表数据
        data = _make_api_request("CASH_FLOW", params)

        # 检查响应是否为有效数据
        if not data or not data.strip():
            return f"No cash flow data found for symbol '{symbol}'"

        # 构建元数据头部
        header = f"# Cash Flow Statement for {symbol.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 直接返回原始 JSON 数据
        return header + data

    except AlphaVantageRateLimitError:
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        return f"Error retrieving cash flow for {symbol}: {str(e)}"
