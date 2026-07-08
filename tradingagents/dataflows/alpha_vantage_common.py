# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 Alpha Vantage API？
#    Alpha Vantage 是一个提供金融市场数据的在线 API 服务。
#    你通过发送 HTTP 请求（像浏览器访问网址一样）来获取股票数据。
#    使用 API 需要一个"密钥"（API Key），就像一把钥匙，每次请求都要带上。
#    免费版有频率限制（每分钟 5 次请求），付费版限制更少。
#
# 2. 什么是 API 密钥（API Key）？
#    API 密钥是一串字符（如 "ABCDE12345"），用于标识你是谁。
#    就像进入大楼需要刷门禁卡，调 API 也需要带上你的密钥。
#    密钥应该保密，不能写在代码里（用环境变量管理）。
#
# 3. 什么是环境变量？
#    环境变量是操作系统级别的配置，独立于代码存在。
#    在代码中用 os.getenv("变量名") 读取。
#    好处：密钥不写在代码中，避免泄露到 Git 等版本控制系统。
#    设置方式：在终端中 set ALPHA_VANTAGE_API_KEY=你的密钥（Windows）
#              或 export ALPHA_VANTAGE_API_KEY=你的密钥（Linux/Mac）
#
# 4. 什么是 HTTP 请求？
#    HTTP 是网页通信协议。最常见的两种请求：
#    - GET：从服务器获取数据（就像在浏览器输入网址访问网页）
#    - POST：向服务器提交数据
#    本文件使用 GET 请求获取 Alpha Vantage 的股票数据。
#    requests.get(url, params=字典) 会自动把字典参数拼接到 URL 后面。
#    示例：requests.get("https://api.example.com", params={"key": "123"})
#          实际请求的 URL 是 https://api.example.com?key=123
#
# 5. 什么是 API 限流（Rate Limit）？
#    服务器为了保护自己不被压垮，限制每个用户的请求频率。
#    例如免费版 Alpha Vantage 每分钟最多 5 次请求。
#    超过限制时，服务器返回错误信息（包含 "rate limit" 字样）。
#    本文件通过检查错误信息来识别限流。
#
# 6. 什么是 HTTP 状态码？
#    服务器响应请求时返回一个数字状态码：
#    - 2xx（如 200）：成功
#    - 4xx（如 404, 429）：客户端错误（404=找不到页面, 429=请求太多）
#    - 5xx（如 500）：服务器错误
#    response.raise_for_status() 在状态码非 2xx 时抛出异常。
#
# 7. 什么是 JSON 和 CSV？
#    JSON：一种轻量级数据交换格式，用 {} 和 [] 表示对象和数组。
#    例如 {"name": "Apple", "price": 185.64}
#    CSV：逗号分隔的文本格式，每行一条记录。
#    例如 Date,Close\n2024-01-15,185.64
#    Alpha Vantage 正常数据返回 CSV，错误信息返回 JSON。
#
# 8. 什么是 pandas DataFrame？
#    DataFrame 是 pandas 的核心数据结构，就像一个 Excel 表格。
#    有行有列，每列有列名。
#    本文件用 DataFrame 来过滤 CSV 数据的日期范围。
#
# 9. 什么是自定义异常类？
#    Python 允许你创建自己的异常类型，继承自 Exception 基类。
#    好处：调用者可以精确捕获特定类型的错误。
#    本文件创建了 AlphaVantageRateLimitError 表示"API 限流"错误。
#
# 10. 什么是 StringIO？
#     StringIO 把字符串当作"文件"来用。
#     pandas 的 read_csv() 通常需要文件路径，但用 StringIO 可以直接
#     把内存中的 CSV 字符串传给 pandas，无需写入磁盘。
#     例如 pd.read_csv(StringIO("a,b\n1,2")) 直接解析字符串。
#
# 11. 什么是 try-except 语句？
#     Python 的异常处理机制：
#     try:
#         可能出错的代码
#     except 异常类型:
#         出错后的处理代码
#     可以有多个 except 块，捕获不同类型的异常。
#
# 12. 什么是布尔索引？
#     pandas 中用 True/False 序列来筛选行的技术。
#     例如 df[df["Close"] > 100] 只保留 Close 大于 100 的行。
#     多个条件用 &（与）和 |（或）连接，每个条件要用括号括起来。
#
# 13. 什么是 dict.copy()？
#     创建字典的浅拷贝（复制一份）。
#     修改副本不会影响原字典。
#     用于避免修改调用方传入的参数。
#
# 14. 什么是 isinstance()？
#     检查对象是否为指定类型。
#     isinstance(obj, str) 检查 obj 是否为字符串类型。
#     比 type(obj) == str 更安全（支持继承）。
#
# 15. 什么是 globals()？
#     返回全局作用域中所有变量的字典。
#     globals().get("变量名") 安全地获取全局变量，不存在返回 None。
#     本文件用它来读取全局的 entitlement（授权）设置。
#
# 16. 什么是相对导入？
#     from .module import function 表示从当前包导入。
#     点号 . 表示"当前目录"。
# =============================================================================
#
# 模块名称：alpha_vantage_common.py
# 模块功能：Alpha Vantage API 公共工具模块
# =============================================================================
# 本模块是 Alpha Vantage 数据流层的共享基础设施，提供以下核心能力：
#   1. API 密钥管理          - get_api_key()
#   2. 日期格式转换          - format_datetime_for_api()
#   3. API 请求封装          - _make_api_request()
#   4. CSV 数据日期过滤      - _filter_csv_by_date_range()
#   5. 自定义异常类          - AlphaVantageRateLimitError
#
# 设计模式说明：
#   - 模板方法模式：_make_api_request() 封装了通用的 API 调用流程（参数组装、
#     请求发送、错误处理），各子模块只需传入不同的 function_name 即可复用
#   - 策略模式：_filter_csv_by_date_range() 提供可复用的日期过滤策略
# =============================================================================

# ---------------------------------------------------------------------------
# 导入 os 模块
# 功能：提供操作系统相关的功能，这里主要用于读取环境变量
# os.getenv() 方法可以获取系统环境变量的值
# 用法：os.getenv("ALPHA_VANTAGE_API_KEY") 获取名为 ALPHA_VANTAGE_API_KEY 的环境变量
# ---------------------------------------------------------------------------
import os

# ---------------------------------------------------------------------------
# 导入 requests 库
# 功能：发送 HTTP 请求，是 Python 中最流行的 HTTP 客户端库
# 常用方法：
#   - requests.get(url, params={})  : 发送 GET 请求
#     requests.get 会自动把 params 字典拼接到 URL 后面
#     例如 requests.get("https://api.com", params={"key":"123"})
#     实际请求 URL 为 https://api.com?key=123
#   - response.text                 : 获取响应文本（字符串）
#   - response.raise_for_status()   : 检查 HTTP 状态码，非 2xx 则抛出异常
# ---------------------------------------------------------------------------
import requests

# ---------------------------------------------------------------------------
# 导入 pandas 库（别名 pd）
# 功能：数据分析和处理的核心库，提供 DataFrame 和 Series 等数据结构
# 这里主要用于：
#   - pd.read_csv() : 解析 CSV 格式的字符串（需要配合 StringIO）
#   - pd.to_datetime() : 将字符串转换为日期时间类型
#   - DataFrame 的过滤和切片操作
# ---------------------------------------------------------------------------
import pandas as pd

# ---------------------------------------------------------------------------
# 导入 json 模块
# 功能：编码和解码 JSON 数据
# json.loads(str) : 将 JSON 字符串解析为 Python 字典/列表
# 例如 json.loads('{"name":"Apple"}') → {"name": "Apple"}
# ---------------------------------------------------------------------------
import json

# ---------------------------------------------------------------------------
# 导入 datetime 模块
# 功能：处理日期和时间
# - datetime.strptime(字符串, 格式)：字符串 → 日期对象
# - dt.strftime(格式)：日期对象 → 字符串
# ---------------------------------------------------------------------------
from datetime import datetime

# ---------------------------------------------------------------------------
# 导入 StringIO 类（来自 io 模块）
# 功能：将字符串当作文件对象来操作
# 用途：pandas 的 read_csv() 通常需要文件路径或文件对象，通过 StringIO 可以
#        直接将内存中的 CSV 字符串传递给 pandas，无需写入磁盘
# 示例：df = pd.read_csv(StringIO("col1,col2\n1,2\n3,4"))
# ---------------------------------------------------------------------------
from io import StringIO

# =============================================================================
# 常量定义
# =============================================================================

# Alpha Vantage API 的基础 URL
# 所有 API 请求都发送到这个地址，通过不同的 function 参数区分功能
# 注意：变量名使用全大写字母（UPPER_CASE），这是 Python 中常量的命名约定
# 运行时示例：这个 URL 是固定的，所有请求都发到这里
API_BASE_URL = "https://www.alphavantage.co/query"

# =============================================================================
# API 密钥管理
# =============================================================================

def get_api_key() -> str:
    """
    【密钥获取函数】从环境变量中获取 Alpha Vantage API 密钥。

    为什么使用环境变量而不是硬编码？
    - 安全性：密钥不会出现在代码中，避免泄露到版本控制系统（如 Git）
    - 灵活性：不同环境（开发、测试、生产）可以使用不同的密钥
    - 最佳实践：遵循 12-Factor App 的配置管理原则

    【调用时机】
      每次 API 请求时调用，在 _make_api_request() 中使用。
      每次请求都重新获取密钥，确保密钥更新后能立即生效。

    【输入是什么】
      无参数。

    【输出是什么】
      str: API 密钥字符串
      运行时示例："ABCDE12345FGHIJ67890"

    【输出被谁使用】
      被 _make_api_request() 调用，添加到每次 API 请求的参数中。

    异常:
        ValueError: 如果环境变量 ALPHA_VANTAGE_API_KEY 未设置
    """
    # os.getenv() 读取环境变量的值
    # 环境变量是操作系统级别的配置，在终端中可以用 export/set 命令设置
    # 如果环境变量不存在，os.getenv() 返回 None
    # 运行时示例：api_key = os.getenv("ALPHA_VANTAGE_API_KEY") → "ABCDE12345FGHIJ67890"
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")

    # 如果 API 密钥不存在（返回 None），则抛出异常
    # not api_key 检查：None 和空字符串都是 False，not None = True
    # 这是一种"快速失败"（Fail Fast）策略：尽早发现配置问题，避免后续错误
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY environment variable is not set.")
        # raise 关键字用于抛出异常，中断程序执行
        # ValueError 是 Python 内置异常，表示"值不正确"的情况

    return api_key

# =============================================================================
# 日期格式转换
# =============================================================================

def format_datetime_for_api(date_input) -> str:
    """
    【日期转换函数】将各种日期格式统一转换为 Alpha Vantage API 要求的格式。

    Alpha Vantage API 要求的日期格式：YYYYMMDDTHHMM
    示例：2024年1月15日 → "20240115T0000"

    【调用时机】
      在构建 Alpha Vantage 新闻 API 请求参数时调用。
      Alpha Vantage 的 NEWS_SENTIMENT 端点要求日期格式为 YYYYMMDDTHHMM。

    【输入是什么】
      date_input: 输入日期，可以是以下类型：
                 - str  : 字符串格式的日期，如 "2024-01-15" 或 "2024-01-15 14:30"
                 - datetime : Python 的 datetime 对象

    【输出是什么】
      str: 格式化后的日期字符串，格式为 YYYYMMDDTHHMM
      运行时示例：
        "2024-01-15" → "20240115T0000"
        "2024-01-15 14:30" → "20240115T1430"
        datetime(2024,1,15,14,30) → "20240115T1430"

    【输出被谁使用】
      被 alpha_vantage_news.py 中的 get_news() 和 get_global_news() 调用，
      用于构建 API 请求的 time_from 和 time_to 参数。

    异常:
        ValueError: 如果输入格式不支持
    """
    # isinstance(obj, type) 检查对象是否为指定类型
    # 这是 Python 中类型检查的标准方式，比 type(obj) == type 更安全（支持继承）
    if isinstance(date_input, str):
        # 如果已经是正确格式（长度13且包含 'T'），直接返回
        # len() 函数返回字符串的长度
        # 'T' in date_input 检查字符串中是否包含 'T'
        # and 运算符：两个条件都为 True 才为 True
        if len(date_input) == 13 and 'T' in date_input:
            return date_input

        # 尝试解析常见日期格式
        try:
            # 尝试格式1：yyyy-mm-dd（如 "2024-01-15"）
            # strptime(字符串, 格式) 把字符串解析为 datetime 对象
            # 运行时示例：datetime.strptime("2024-01-15", "%Y-%m-%d") → datetime(2024, 1, 15)
            dt = datetime.strptime(date_input, "%Y-%m-%d")
            # 转换为 API 格式，时间部分默认为 0000（午夜）
            # strftime(格式) 把 datetime 格式化为字符串
            # 运行时示例：dt.strftime("%Y%m%dT0000") → "20240115T0000"
            return dt.strftime("%Y%m%dT0000")
        except ValueError:
            # 如果第一种格式解析失败，尝试第二种格式
            try:
                # 尝试格式2：yyyy-mm-dd HH:MM（如 "2024-01-15 14:30"）
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M")
                return dt.strftime("%Y%m%dT%H%M")
            except ValueError:
                # 两种格式都失败，抛出明确的错误信息
                raise ValueError(f"Unsupported date format: {date_input}")
                # f-string 中的 f 前缀表示格式化字符串，{date_input} 会被替换为实际值
                # raise 抛出异常，程序中断在此处

    elif isinstance(date_input, datetime):
        # 如果输入已经是 datetime 对象，直接格式化
        # 运行时示例：datetime(2024,1,15,14,30).strftime("%Y%m%dT%H%M") → "20240115T1430"
        return date_input.strftime("%Y%m%dT%H%M")

    else:
        # 如果输入既不是字符串也不是 datetime 对象，抛出类型错误
        # type() 返回对象的类型信息
        # 运行时示例：type(123) → <class 'int'>
        raise ValueError(f"Date must be string or datetime object, got {type(date_input)}")

# =============================================================================
# 自定义异常类
# =============================================================================

class AlphaVantageRateLimitError(Exception):
    """
    【自定义异常类】Alpha Vantage API 速率限制超限异常。

    继承自 Exception 基类，表示 API 请求频率超过限制。
    自定义异常类的优势：
    - 调用者可以精确捕获这类特定错误
    - 可以添加额外的上下文信息（如建议的等待时间）
    - 提高代码可读性和可维护性

    使用示例：
        try:
            _make_api_request(...)
        except AlphaVantageRateLimitError:
            print("请求太频繁了，请稍后再试")

    【调用时机】
      在 _make_api_request() 中，当检测到 API 返回 "rate limit" 错误信息时抛出。

    注意：pass 语句表示类体为空，所有行为继承自父类 Exception
    """
    # pass 是 Python 的空语句，表示"什么都不做"
    # 这里类体只有 pass，表示所有行为都继承自 Exception 基类
    # 异常的创建和抛出由 _make_api_request() 函数完成
    pass

# =============================================================================
# API 请求封装
# =============================================================================

def _make_api_request(function_name: str, params: dict) -> dict | str:
    """
    【核心函数】封装 Alpha Vantage API 的 HTTP 请求调用。

    函数名以 _ 开头，表示这是模块内部使用的私有函数，不应对模块外暴露。

    【调用时机】
      被 alpha_vantage_stock.py、alpha_vantage_fundamentals.py、
      alpha_vantage_indicator.py、alpha_vantage_news.py 中的各函数调用。
      是所有 Alpha Vantage API 调用的统一入口。

    【输入是什么】
      function_name (str): Alpha Vantage API 的函数名，如 "TIME_SERIES_DAILY_ADJUSTED"
      params (dict): API 请求参数，如 {"symbol": "IBM"}
      运行时示例：
        function_name = "OVERVIEW"
        params = {"symbol": "AAPL"}

    【输出是什么】
      dict | str: API 响应文本（可能是 JSON 或 CSV 格式）
      - 正常数据返回 CSV 字符串
      - 错误信息可能是 JSON 字符串
      运行时示例（CSV 格式）：
        "timestamp,open,high,low,close,volume\n2024-01-15,187.15,..."

    【输出被谁使用】
      被各子模块的函数使用，返回的数据会被进一步解析和处理。

    返回值类型注解 dict | str 表示函数可能返回字典或字符串：
    - 当 API 返回 JSON 格式的错误信息时，可能是字典
    - 当 API 返回 CSV 格式的数据时，是字符串
    注：| 是 Python 3.10+ 的联合类型语法

    异常:
        AlphaVantageRateLimitError: 当 API 速率限制超限时
        requests.HTTPError: 当 HTTP 状态码非 2xx 时
    """
    # 创建 params 的副本（浅拷贝），避免修改原始字典
    # .copy() 方法创建字典的浅拷贝：
    #   浅拷贝只复制字典本身，值（如果是可变对象）仍然是引用
    #   这里使用浅拷贝是因为 params 的值都是简单类型（字符串），修改副本不会影响原字典
    # 为什么要拷贝？因为后面会往 api_params 中添加通用字段，
    # 如果不拷贝就会修改调用方传入的原始字典，可能导致意外的副作用
    api_params = params.copy()

    # 向参数中添加通用字段
    # .update() 方法将另一个字典的键值对合并到当前字典中
    # 运行时示例：api_params 更新后变为
    #   {"symbol": "AAPL", "function": "OVERVIEW", "apikey": "ABCDE12345", "source": "trading_agents"}
    api_params.update({
        "function": function_name,  # API 功能名称
        "apikey": get_api_key(),    # 每次请求时动态获取 API 密钥
        "source": "trading_agents", # 数据来源标识（用于追踪和统计）
    })

    # 处理 entitlement（授权/套餐）参数
    # globals() 函数返回全局作用域的变量字典
    # .get() 方法安全地获取字典中的值，如果键不存在返回 None
    # 这里读取全局变量 _current_entitlement，它可能在外部被设置
    current_entitlement = globals().get('_current_entitlement')

    # 优先使用 params 中传入的 entitlement，其次使用全局变量
    # Python 的 or 运算符会返回第一个"真值"（truthy value）
    # api_params.get("entitlement") 如果有值就返回它（非 None）
    # 如果为 None，则尝试 current_entitlement
    # 如果两者都为 None，entitlement 为 None
    entitlement = api_params.get("entitlement") or current_entitlement

    if entitlement:
        # 如果有有效的 entitlement，添加到请求参数中
        api_params["entitlement"] = entitlement
    elif "entitlement" in api_params:
        # 如果 entitlement 为 None 或空，但参数中仍有该键，则移除
        # .pop(key, default) 删除并返回指定键的值，如果键不存在则返回 default
        # 第二个参数 None 是默认值，避免键不存在时抛出 KeyError
        api_params.pop("entitlement", None)

    # 发送 HTTP GET 请求
    # requests.get(url, params=dict) 自动将字典参数拼接到 URL 后面
    # 运行时示例：
    #   requests.get("https://www.alphavantage.co/query",
    #                params={"function": "OVERVIEW", "symbol": "AAPL", "apikey": "..."})
    #   实际请求的 URL：
    #   https://www.alphavantage.co/query?function=OVERVIEW&symbol=AAPL&apikey=...
    response = requests.get(API_BASE_URL, params=api_params)

    # 检查 HTTP 响应状态码
    # .raise_for_status() 如果状态码不是 2xx，会抛出 HTTPError 异常
    # 2xx 表示成功（如 200 OK）
    # 4xx 表示客户端错误（如 404 Not Found, 429 Too Many Requests）
    # 5xx 表示服务器错误（如 500 Internal Server Error）
    response.raise_for_status()

    # 获取响应文本
    # .text 属性返回服务器响应的字符串内容
    # 运行时示例：可能是 CSV 格式的数据，也可能是 JSON 格式的错误信息
    response_text = response.text

    # 检查响应是否为 JSON 格式的错误信息
    # Alpha Vantage 的速率限制信息以 JSON 格式返回，而正常数据以 CSV 格式返回
    try:
        # json.loads() 尝试将文本解析为 JSON
        # 如果文本不是有效的 JSON，会抛出 json.JSONDecodeError
        # 运行时示例：
        #   json.loads('{"Information": "Thank you for using Alpha Vantage..."}')
        #   → {"Information": "Thank you for using Alpha Vantage..."}
        response_json = json.loads(response_text)

        # 检查 JSON 中是否包含 "Information" 字段（错误信息的标志）
        # in 运算符检查键是否在字典中
        if "Information" in response_json:
            info_message = response_json["Information"]
            # .lower() 将字符串转换为小写，便于不区分大小写的匹配
            # "Rate Limit" → "rate limit"
            if "rate limit" in info_message.lower() or "api key" in info_message.lower():
                # 抛出自定义异常，包含 API 返回的原始错误信息
                # raise 抛出异常，程序中断在此处
                raise AlphaVantageRateLimitError(f"Alpha Vantage rate limit exceeded: {info_message}")
    except json.JSONDecodeError:
        # 响应不是 JSON 格式（通常是 CSV 数据），这是正常情况，无需处理
        # json.JSONDecodeError 是 json.loads() 解析失败时抛出的异常
        # pass 语句表示"什么都不做"，继续执行后面的代码
        # 也就是说：如果返回的不是 JSON，说明是正常的 CSV 数据，直接跳过错误检查
        pass

    # 返回原始响应文本（由调用者决定如何处理）
    return response_text


# =============================================================================
# CSV 数据日期过滤
# =============================================================================

def _filter_csv_by_date_range(csv_data: str, start_date: str, end_date: str) -> str:
    """
    【CSV 过滤函数】按日期范围过滤 CSV 格式的股票数据。

    Alpha Vantage API 返回的 CSV 数据可能包含超出请求范围的数据，
    本函数使用 pandas 进行精确的日期过滤。

    【调用时机】
      被 alpha_vantage_stock.py 的 get_stock() 调用，
      在获取到 API 返回的 CSV 数据后，按用户指定的日期范围过滤。

    【输入是什么】
      csv_data (str): 原始 CSV 字符串（来自 Alpha Vantage API 的响应）
      start_date (str): 开始日期，格式 yyyy-mm-dd
      end_date (str): 结束日期，格式 yyyy-mm-dd
      运行时示例（csv_data 的内容）：
        timestamp,open,high,low,close,volume
        2024-01-31,185.64,187.00,184.00,185.00,50000000
        2024-01-30,184.25,186.00,183.00,185.64,60000000
        2024-01-29,183.00,185.00,182.00,184.25,55000000
        ...

    【输出是什么】
      str: 过滤后的 CSV 字符串。如果过滤失败，返回原始数据
      运行时示例（只保留 2024-01-15 到 2024-01-25 的数据）：
        timestamp,open,high,low,close,volume
        2024-01-25,186.00,187.00,185.00,186.50,52000000
        2024-01-24,185.00,186.50,184.50,185.50,48000000
        ...

    【输出被谁使用】
      被 alpha_vantage_stock.py 的 get_stock() 返回给调用者。
    """
    # 检查输入是否为空
    # .strip() 方法去除字符串首尾的空白字符（空格、换行、制表符等）
    # not csv_data 检查是否为 None 或空字符串
    # csv_data.strip() == "" 检查是否为纯空白字符串
    if not csv_data or csv_data.strip() == "":
        return csv_data

    try:
        # 使用 pandas 解析 CSV 数据
        # StringIO 将字符串转换为类似文件的对象，使 pandas 能直接读取
        # pd.read_csv() 把 CSV 字符串解析为 DataFrame（二维表格）
        # df 是 DataFrame 对象，可以理解为一个二维表格
        # 运行时示例：df 的结构：
        #     timestamp     open     high     low      close    volume
        # 0   2024-01-31   185.64   187.00   184.00   185.00   50000000
        # 1   2024-01-30   184.25   186.00   183.00   185.64   60000000
        df = pd.read_csv(StringIO(csv_data))

        # 假设第一列是日期列（timestamp）
        # df.columns 返回所有列名的 Index 对象
        # df.columns[0] 获取第一列的列名
        # 运行时示例：date_col = "timestamp"
        date_col = df.columns[0]

        # 将日期列转换为 pandas 的 datetime 类型
        # pd.to_datetime() 可以自动识别多种日期格式并转换为标准 datetime
        # 运行时示例：df["timestamp"] 转换前是字符串 "2024-01-31"
        #             转换后是 Timestamp('2024-01-31') 日期对象
        df[date_col] = pd.to_datetime(df[date_col])

        # 将字符串日期转换为 pandas datetime 类型
        # 运行时示例：pd.to_datetime("2024-01-15") → Timestamp('2024-01-15')
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        # 布尔索引过滤：这是 pandas 的核心操作之一
        # df[date_col] >= start_dt 返回一个布尔 Series（True/False 序列）
        # 运行时示例：
        #   df["timestamp"] >= Timestamp('2024-01-15')
        #   → [False, False, True, True, ...]  （每行对应一个 True/False）
        # & 是按位与运算符，用于组合两个布尔条件
        # 注意：必须使用 & 而不是 Python 的 and 关键字，因为 pandas 重载了 & 运算符
        # (条件1) & (条件2) 表示两个条件都为 True 的行
        # 运行时示例：
        #   (df["timestamp"] >= start_dt) & (df["timestamp"] <= end_dt)
        #   → [False, True, True, False, ...]  （只保留日期在范围内的行）
        filtered_df = df[(df[date_col] >= start_dt) & (df[date_col] <= end_dt)]

        # 将过滤后的 DataFrame 转换回 CSV 字符串
        # index=False 表示不输出行索引（行号），只保留数据
        # 运行时示例：filtered_df.to_csv(index=False) → CSV 格式字符串
        filtered_csv = filtered_df.to_csv(index=False)

        return filtered_csv

    except Exception as e:
        # 如果过滤过程中出现任何错误（如列名不匹配、格式问题等），
        # 打印警告并返回原始数据，确保程序不会因为过滤失败而崩溃
        # f-string 格式化：{e} 被替换为异常描述
        print(f"Warning: Failed to filter CSV data by date range: {e}")
        return csv_data
