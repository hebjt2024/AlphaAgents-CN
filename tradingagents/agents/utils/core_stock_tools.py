# =============================================================================
# 前置知识：阅读本文件前需要理解的核心概念
# =============================================================================
#
# 【1. 装饰器（Decorator）】
#   Python 的装饰器是一种"语法糖"（简写方式），用于在"不修改原函数代码"的前提下，
#   给函数添加额外功能。
#
#   语法：在函数定义前加 @装饰器名
#   示例：
#       @my_decorator          # 装饰器
#       def hello():
#           print("Hello!")
#
#   这等价于：
#       def hello():
#           print("Hello!")
#       hello = my_decorator(hello)   # 用装饰器包装原函数
#
#   装饰器的工作原理：
#   1. Python 先定义原函数 hello
#   2. 然后把 hello 作为参数传给 my_decorator
#   3. my_decorator 返回一个"新函数"（通常是包装了原函数的增强版）
#   4. 新函数被赋值给 hello 这个名字
#   5. 之后调用 hello() 时，实际调用的是"新函数"
#
# 【2. @tool 装饰器（LangChain 工具装饰器）】
#   来自 langchain_core.tools 模块。
#   @tool 是 LangChain 提供的特殊装饰器，它把"普通 Python 函数"变成"LangChain 工具"。
#
#   什么是"LangChain 工具"？
#   LangChain 工具是一种特殊的对象，可以被 LLM（大语言模型）"看到"和"调用"。
#   当你用 @tool 装饰一个函数后，LangChain 会自动提取以下信息：
#
#   (a) name（工具名称）：
#       默认使用函数名。例如函数叫 get_stock_data，工具名就是 "get_stock_data"。
#       LLM 通过这个名字来请求调用工具。
#
#   (b) description（工具描述）：
#       自动提取函数的 docstring（三引号文档字符串）。
#       LLM 通过这段描述来理解"这个工具是干什么的、什么时候应该用它"。
#       因此 docstring 的质量直接影响 LLM 能否正确选择和使用工具。
#
#   (c) args_schema（参数模式/参数 schema）：
#       自动从函数签名（参数名和类型注解）生成。
#       Annotated[str, "描述"] 中的"描述"会被提取为参数的说明。
#       LLM 通过参数模式来理解"调用这个工具需要传什么参数、每个参数是什么意思"。
#
#   @tool 装饰器的工作流程：
#   1. Python 定义 get_stock_data 函数
#   2. @tool 装饰器接收 get_stock_data 函数作为参数
#   3. @tool 提取函数名、docstring、参数注解
#   4. @tool 创建一个 StructuredTool 对象，封装这些信息
#   5. 这个 StructuredTool 对象被赋值给 get_stock_data 这个名字
#   6. 之后 get_stock_data 可以被 bind_tools(tools) 绑定到 LLM
#   7. LLM "看到"工具信息后，可以在需要时请求调用它
#   8. LangGraph 的 ToolNode 收到调用请求后，实际执行原始函数
#
#   被装饰后的函数仍然可以像普通函数一样直接调用：
#       result = get_stock_data("AAPL", "2024-01-01", "2024-06-30")
#   但它额外拥有了 .name、.description、.args_schema 等属性。
#
# 【3. Annotated（类型注解包装器）在工具函数中的作用】
#   来自 typing 模块。
#   语法：Annotated[类型, "描述字符串"]
#
#   在普通代码中，Annotated 只是给类型"贴标签"（添加元数据）。
#   但在 @tool 装饰的函数中，Annotated 的描述字符串有特殊作用：
#   LangChain 会解析这些描述，生成工具的"参数 schema"，
#   这个 schema 会被发送给 LLM，帮助 LLM 理解每个参数的含义。
#
#   例如：
#       symbol: Annotated[str, "ticker symbol of the company"]
#   LangChain 会生成类似这样的参数描述：
#   {
#       "name": "symbol",
#       "type": "string",
#       "description": "ticker symbol of the company",
#       "required": true
#   }
#   LLM 看到这段描述后，就知道调用 get_stock_data 时需要传一个 symbol 参数，
#   这个参数是"公司的股票代码"。
#
#   如果没有 Annotated 描述，LLM 只知道参数名和类型，不知道参数的具体含义，
#   可能传入错误的值。
#
# 【4. route_to_vendor（供应商路由模式）】
#   来自 tradingagents.dataflows.interface 模块。
#   route_to_vendor 是一个统一的"数据供应商路由接口"。
#
#   什么是"路由"？
#   路由就是"根据请求类型，把请求转发到不同的处理器"。
#   类似于电话总机：你拨一个分机号，总机把你转到对应的部门。
#
#   route_to_vendor 的工作方式：
#   route_to_vendor("get_stock_data", symbol, start_date, end_date)
#   - 第一个参数 "get_stock_data" 是"路由键"，告诉接口要调用哪个功能
#   - 后面的参数是传给具体功能的参数
#
#   为什么用路由模式？
#   系统支持多个数据供应商（如 Yahoo Finance、Alpha Vantage、Polygon 等）。
#   通过 route_to_vendor，切换数据供应商只需要修改配置文件，
#   不需要修改工具代码——这就是"数据源与工具逻辑的解耦"。
#
#   工作流程：
#   1. get_stock_data 工具函数调用 route_to_vendor("get_stock_data", ...)
#   2. route_to_vendor 读取配置，确定当前使用的数据供应商
#   3. route_to_vendor 根据路由键 "get_stock_data" 找到供应商对应的 API 方法
#   4. 调用供应商的 API 方法，传入参数
#   5. 返回 API 的结果
#
# 【5. OHLCV 数据格式】
#   OHLCV 是金融领域标准的股价数据格式，每个字母代表一种价格：
#
#   - O (Open, 开盘价): 交易时段开始时的第一笔成交价格
#       示例：美股 9:30 开盘时的价格
#
#   - H (High, 最高价): 交易时段内的最高成交价格
#       示例：当天交易中出现的最高价格
#
#   - L (Low, 最低价): 交易时段内的最低成交价格
#       示例：当天交易中出现的最低价格
#
#   - C (Close, 收盘价): 交易时段结束时的最后一笔成交价格
#       示例：美股 16:00 收盘时的价格
#       收盘价是最重要的价格，技术指标通常基于收盘价计算
#
#   - V (Volume, 成交量): 交易时段内的总交易数量（股数）
#       示例：当天交易了多少股
#
#   OHLCV 数据通常以表格形式返回，每行代表一个交易日：
#   Date,        Open,   High,   Low,    Close,  Volume
#   2024-01-02,  189.52, 190.00, 187.45, 188.63, 85200000
#   2024-01-03,  185.46, 186.20, 184.15, 184.25, 78500000
#   2024-01-04,  183.52, 184.80, 181.91, 181.91, 67200000
#
# 【6. 函数签名中的类型注解】
#   Python 函数的参数和返回值可以添加类型注解：
#
#       def add(a: int, b: int) -> int:
#           return a + b
#
#   - a: int 表示参数 a 应该是整数类型
#   - b: int 表示参数 b 应该是整数类型
#   - -> int 表示返回值是整数类型
#
#   类型注解在运行时不做任何强制检查，但：
#   - IDE 可以显示类型提示和自动补全
#   - 静态检查工具（如 mypy）可以做类型检查
#   - @tool 装饰器会读取这些注解来生成工具的参数 schema
#
# 【7. 本文件在系统中的位置】
#   本文件定义了 get_stock_data 工具函数——获取股票历史价格数据的入口。
#   这是市场分析师（Market Analyst）智能体使用的两个核心工具之一。
#   当市场分析师需要获取股价数据来计算技术指标时，
#   LLM 会通过 bind_tools 机制请求调用 get_stock_data，
#   LangGraph 的 ToolNode 执行这个工具函数，
#   返回的 OHLCV 数据会传回给 LLM 用于分析。
# =============================================================================


# =============================================================================
# 模块名称：core_stock_tools.py
# 模块功能：股票价格数据获取工具 - 获取 OHLCV 格式的股票历史价格数据
#
# 本文件只定义了一个函数：get_stock_data
# 这个函数被 @tool 装饰器标记为 LangChain 工具，
# 可以被市场分析师智能体通过 bind_tools 机制调用。
# =============================================================================


# ---------------------------------------------------------------------------
# 导入区：每个 import 语句的作用和原因
# ---------------------------------------------------------------------------

# 【@tool】
#   为什么需要：LangChain 的工具装饰器。
#   解决什么问题：把普通 Python 函数转换为 LangChain 工具对象，
#   使 LLM 能够"看到"这个工具的名称、描述和参数模式，
#   并在需要时请求调用它。
#   工作原理：@tool 装饰器会自动提取函数的：
#   - name: 工具名称（默认使用函数名，如 "get_stock_data"）
#   - description: 工具描述（来自函数的 docstring 文档字符串）
#   - args_schema: 参数模式（来自函数签名和 Annotated 注解）
# 【Annotated】
#   为什么需要：给参数类型添加描述性元数据。
#   解决什么问题：在 @tool 装饰的函数中，Annotated 的描述字符串
#   会被 LangChain 解析，生成工具的参数 schema，
#   帮助 LLM 理解每个参数的含义和应该如何传值。
#   例如：Annotated[str, "ticker symbol of the company"]
#   告诉 LLM 这个参数是"公司的股票代码"。
from typing import Annotated

from langchain_core.tools import tool

# 【route_to_vendor】
#   为什么需要：统一的数据供应商路由接口。
#   解决什么问题：系统支持多个数据供应商（如 Yahoo Finance、Alpha Vantage），
#   route_to_vendor 通过路由键将请求转发到配置的供应商，
#   实现数据源与工具逻辑的解耦。
#   切换数据供应商只需修改配置，无需修改工具代码。
from tradingagents.dataflows.interface import route_to_vendor


# =============================================================================
# get_stock_data - 股票价格数据获取工具
# =============================================================================
# 【调用时机】
#   当市场分析师智能体需要获取股价数据时，LLM 会通过 bind_tools 机制
#   请求调用此工具。LangGraph 的 ToolNode 收到请求后，实际执行此函数。
#
#   典型调用流程：
#   1. 市场分析师的系统提示词告诉 LLM "先调用 get_stock_data 获取 CSV 数据"
#   2. LLM 生成 tool_call：{"name": "get_stock_data", "args": {"symbol": "AAPL", ...}}
#   3. LangGraph 的 ToolNode 执行 get_stock_data("AAPL", "2024-01-01", "2024-06-15")
#   4. 函数返回 OHLCV 数据（CSV 字符串）
#   5. ToolNode 将结果包装成 ToolMessage 返回给 LLM
#   6. LLM 基于 OHLCV 数据生成技术分析报告
#
# 【输入】
#   symbol (str): 公司股票代码，如 "AAPL"（苹果）、"TSM"（台积电）
#   start_date (str): 起始日期，格式 yyyy-mm-dd，如 "2024-01-01"
#   end_date (str): 结束日期，格式 yyyy-mm-dd，如 "2024-06-30"
#
#   这些参数的值由 LLM 决定。LLM 根据系统提示词中的指示，
#   自主决定传入什么股票代码和日期范围。
#   参数的描述（来自 Annotated）帮助 LLM 理解应该传什么值。
#
# 【输出】
#   str: OHLCV 格式的数据字符串，通常是 CSV 格式。
#   示例输出：
#   "Date,Open,High,Low,Close,Volume
#    2024-01-02,189.52,190.00,187.45,188.63,85200000
#    2024-01-03,185.46,186.20,184.15,184.25,78500000
#    ..."
#
# 【输出被谁使用】
#   返回值被 LangGraph 的 ToolNode 包装成 ToolMessage，
#   追加到 state["messages"] 中。
#   LLM 在下一次调用时会看到这条 ToolMessage，
#   基于其中的 OHLCV 数据来计算技术指标或生成分析报告。
#
# 【运行时数据流示例】
#   假设 LLM 请求调用：get_stock_data(symbol="AAPL", start_date="2024-01-01", end_date="2024-01-05")
#
#   执行过程：
#   1. 函数接收参数：symbol="AAPL", start_date="2024-01-01", end_date="2024-01-05"
#   2. 调用 route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2024-01-05")
#   3. route_to_vendor 读取配置，确定数据供应商（如 Yahoo Finance）
#   4. route_to_vendor 调用 Yahoo Finance API 获取 AAPL 的股价数据
#   5. API 返回 OHLCV 数据
#   6. 函数返回数据字符串
#
#   返回值示例：
#   "            Open       High        Low      Close    Volume
#    Date
#    2024-01-02  189.52    190.00     187.45    188.63   85200000
#    2024-01-03  185.46    186.20     184.15    184.25   78500000
#    2024-01-04  183.52    184.80     181.91    181.91   67200000
#    2024-01-05  182.16    183.90     180.78    181.18   64500000"
# =============================================================================
@tool  # @tool 装饰器：将此函数注册为 LangChain 工具，智能体可以直接调用
def get_stock_data(
    # symbol 参数：股票代码
    # ----------------------------------------------------------------
    # Annotated[str, "ticker symbol of the company"] 的含义：
    #   - str: 参数类型是字符串（如 "AAPL"）
    #   - "ticker symbol of the company": 参数描述
    #     LangChain 的 @tool 装饰器会提取这段描述，生成参数 schema，
    #     发送给 LLM。LLM 通过这段描述理解：
    #     "这个参数需要传入公司的股票代码"
    #
    # 为什么用 Annotated 而不是普通类型注解？
    #   普通类型注解 symbol: str 只告诉 LLM 类型是字符串，
    #   但不告诉 LLM "这个字符串应该是什么内容"。
    #   Annotated[str, "ticker symbol of the company"] 额外提供了描述，
    #   LLM 就知道要传入"公司的股票代码"，而不是随便传一个字符串。
    symbol: Annotated[str, "ticker symbol of the company"],

    # start_date 参数：起始日期
    # ----------------------------------------------------------------
    # Annotated[str, "Start date in yyyy-mm-dd format"] 的含义：
    #   - str: 参数类型是字符串
    #   - "Start date in yyyy-mm-dd format": 告诉 LLM 要传入日期格式的字符串
    #   LLM 看到这段描述后，会传入如 "2024-01-01" 这样的日期字符串，
    #   而不是 "January 1st" 或 "01/01/2024" 等其他格式。
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],

    # end_date 参数：结束日期
    # ----------------------------------------------------------------
    # Annotated[str, "End date in yyyy-mm-dd format"] 的含义：
    #   - str: 参数类型是字符串
    #   - "End date in yyyy-mm-dd format": 告诉 LLM 要传入日期格式的字符串
    #   LLM 看到这段描述后，会传入如 "2024-06-30" 这样的日期字符串。
    #
    # start_date 和 end_date 定义了要获取的股价数据的时间范围。
    # 例如：start_date="2024-01-01", end_date="2024-06-30"
    # 表示获取 2024年1月1日 到 2024年6月30日 之间的每日股价数据。
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    # 返回值类型注解 -> str 表示函数返回一个字符串。
    # 这个字符串包含 OHLCV 格式的股价数据（通常是 CSV 格式的文本）。
    """
    获取指定股票代码的 OHLCV 价格数据

    此函数是智能体调用股票数据的入口。它不直接获取数据，而是通过
    route_to_vendor 将请求转发到配置的数据供应商。

    参数：
        symbol (str): 公司股票代码，例如 "AAPL"（苹果）、"TSM"（台积电）
        start_date (str): 起始日期，格式为 yyyy-mm-dd，例如 "2024-01-01"
        end_date (str): 结束日期，格式为 yyyy-mm-dd，例如 "2024-06-30"

    返回：
        str: 格式化的数据框（DataFrame）字符串，包含指定日期范围内的 OHLCV 数据

    底层实现：
        route_to_vendor("get_stock_data", symbol, start_date, end_date)
        会将请求路由到 core_stock_apis 配置的供应商，例如：
        - Yahoo Finance API
        - Alpha Vantage API
        - 或其他配置的数据源

    使用示例：
        get_stock_data("AAPL", "2024-01-01", "2024-06-30")
        # 返回苹果公司 2024 年上半年每日 OHLCV 数据
    """
    # 调用统一路由接口 route_to_vendor，将请求转发到配置的数据供应商。
    #
    # 【route_to_vendor 的工作原理】
    # route_to_vendor 接收的第一个参数 "get_stock_data" 是"路由键"。
    # 路由键告诉接口："我要调用获取股票数据的功能"。
    # 后面的 symbol、start_date、end_date 是传给具体功能函数的参数。
    #
    # route_to_vendor 内部的处理流程：
    # 1. 读取全局配置（get_config），确定当前使用的数据供应商
    #    例如：配置中指定 supplier = "yahoo_finance"
    # 2. 根据路由键 "get_stock_data" 在供应商的 API 列表中找到对应方法
    #    例如：yahoo_finance 的 get_stock_data 方法
    # 3. 调用供应商的 API 方法，传入 symbol、start_date、end_date 参数
    #    例如：yahoo_finance.get_stock_data("AAPL", "2024-01-01", "2024-06-30")
    # 4. API 返回 OHLCV 数据（通常是 pandas DataFrame 或 CSV 字符串）
    # 5. 将结果转换为字符串格式返回
    #
    # 这种"路由模式"的好处：
    # - 如果要切换数据供应商（如从 Yahoo Finance 换成 Alpha Vantage），
    #   只需要修改配置文件，不需要修改这个工具函数的代码
    # - 多个工具函数可以共享同一个路由接口，统一管理数据源
    #
    # 【运行时数据流示例】
    # 当此函数被调用时：
    #   get_stock_data("AAPL", "2024-01-01", "2024-06-30")
    # 等价于执行：
    #   route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2024-06-30")
    #
    # route_to_vendor 内部会：
    #   1. 读取配置 → 供应商是 Yahoo Finance
    #   2. 调用 Yahoo Finance API → 下载 AAPL 的历史数据
    #   3. 返回结果（CSV 字符串）：
    #      "            Open       High        Low      Close    Volume
    #       Date
    #       2024-01-02  189.52    190.00     187.45    188.63   85200000
    #       2024-01-03  185.46    186.20     184.15    184.25   78500000
    #       ..."
    #
    # 这个字符串会被 ToolNode 包装成 ToolMessage 返回给 LLM，
    # LLM 基于这些数据来计算技术指标或生成分析报告。
    return route_to_vendor("get_stock_data", symbol, start_date, end_date)
