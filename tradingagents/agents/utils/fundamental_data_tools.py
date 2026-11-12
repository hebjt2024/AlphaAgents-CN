# =============================================================================
# 模块名称：fundamental_data_tools.py
# 模块功能：基本面数据获取工具集 - 提供公司财务报表相关的数据查询
# =============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是基本面分析（Fundamental Analysis）？】
#   基本面分析是通过研究公司的"内在价值"来判断股票是否值得投资。
#   就像买一家奶茶店前，你要看它的账本：赚多少钱、欠多少债、现金流好不好。
#
#   核心三大财务报表：
#   - 资产负债表（Balance Sheet）：某一天的"快照"，反映公司有多少资产、多少负债
#     公式：资产 = 负债 + 所有者权益
#   - 利润表（Income Statement）：一段时间的"成绩单"，反映赚了多少、花了多少
#     公式：收入 - 成本 = 利润
#   - 现金流量表（Cash Flow Statement）：一段时间的"流水账"，反映钱从哪来、花到哪去
#     分为：经营活动、投资活动、筹资活动
#
# 【什么是 @tool 装饰器？它如何工作？】
#   @tool 是 LangChain 提供的装饰器，把普通 Python 函数变成 LLM 可调用的"工具"。
#
#   工作原理：
#   1. @tool 读取函数名、docstring（文档字符串）、参数的 Annotated 描述
#   2. 自动生成工具描述（JSON schema 格式），包含工具名、说明、参数规格
#   3. 工具描述发送给 LLM，LLM 据此决定何时调用、传什么参数
#   4. LangChain 框架负责在 LLM 决定调用时，执行对应的 Python 函数
#
#   【通俗比喻】@tool 就像在餐厅菜单上登记一道菜：
#   - 菜名 = 函数名
#   - 菜的描述 = docstring
#   - 需要告知的选项（辣度、份量）= Annotated 参数描述
#   LLM 是顾客，看菜单点菜；你的函数是厨房，做好菜端出来。
#
# 【Annotated 如何成为参数 schema？】
#   参数声明：ticker: Annotated[str, "ticker symbol"]
#   - str → 参数类型是字符串
#   - "ticker symbol" → 告诉 LLM "这里填股票代码"
#   LangChain 读取这段描述，放进工具的参数 schema 中。
#   LLM 看到 schema 后就知道每个参数该填什么类型的值。
#   如果没有 Annotated 描述，LLM 不知道参数含义，可能传错值。
#
# 【什么是"函数调用"（Function Calling）？】
#   这是 LLM 的一种能力：模型不仅能生成文字，还能生成"调用函数的请求"。
#   例如 LLM 可能输出：{"name": "get_balance_sheet", "arguments": {"ticker": "AAPL"}}
#   LangChain 框架接收这个请求，找到 get_balance_sheet 函数并调用它。
#   这让 LLM 能够"动手做事"，而不只是"动嘴说话"。
#
# 【四大财务报表工具的关系】
#   - get_fundamentals: 综合概览（关键指标和比率，如 P/E、ROE）
#   - get_balance_sheet: 资产负债表（公司的"家底"）
#   - get_cashflow: 现金流量表（公司的"血脉"）
#   - get_income_statement: 利润表（公司的"成绩单"）
#   互相配合可以全面评估一家公司的财务健康状况。
# ============================================================================
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：基本面分析智能体想查看苹果公司的资产负债表
#
#   第 1 步：LLM 决定调用工具
#     LLM 输出：{tool: "get_balance_sheet", args: {ticker: "AAPL", freq: "quarterly"}}
#
#   第 2 步：LangChain 框架调用 Python 函数
#     get_balance_sheet(ticker="AAPL", freq="quarterly", curr_date=None)
#
#   第 3 步：调用路由层
#     route_to_vendor("get_balance_sheet", "AAPL", "quarterly", None)
#     → interface.py 查找分类："get_balance_sheet" 属于 "fundamental_data"
#     → 读取配置，确定供应商（如 "yfinance"）
#     → 调用 get_yfinance_balance_sheet("AAPL", "quarterly", None)
#     → 返回资产负债表数据字符串
#
#   第 4 步：结果返回给 LLM
#     LLM 拿到数据，分析苹果的资产负债情况，生成投资建议
# =============================================================================

# ---------------------------------------------------------------------------
# 导入区：每个导入模块的作用说明
# ---------------------------------------------------------------------------

# @tool: LangChain 工具装饰器，将普通函数转换为智能体可调用的工具
# 【详细说明见文件开头的前置知识部分】
# Annotated: 类型注解包装器，用于为参数添加元数据描述
# LLM 需要读取这些描述来理解参数的含义
# 【为什么 LLM 需要描述？】因为 LLM 只看类型(str)不知道该填什么值，
# 但看描述("ticker symbol")就知道该填股票代码如 "AAPL"
from typing import Annotated

from langchain_core.tools import tool

# route_to_vendor: 统一的数据供应商路由接口
# 将数据请求转发到配置的供应商（如 Financial Modeling Prep、Alpha Vantage 等）
# 这是 dataflows/interface.py 中定义的核心函数
from tradingagents.dataflows.interface import route_to_vendor


# =============================================================================
# get_fundamentals - 综合基本面数据获取工具
# =============================================================================
@tool  # 注册为 LangChain 工具
# @tool 把这个函数注册为 LLM 可调用的工具
# LLM 会看到工具名 "get_fundamentals" 和下面的 docstring 描述
def get_fundamentals(
    # ticker 参数：股票代码
    # Annotated[str, "ticker symbol"] 告诉 LLM：参数是字符串，含义是"股票代码"
    # LLM 调用时会填入如 "AAPL"、"MSFT" 等值
    ticker: Annotated[str, "ticker symbol"],

    # curr_date 参数：当前交易日期
    # 格式为 yyyy-mm-dd，例如 "2024-06-30"
    # 这个日期用于确定获取哪个季度/年度的财务数据。
    # 默认 None：允许 LLM 只传 ticker，由供应商使用最新可用数据，
    # 与其它财务工具（get_balance_sheet 等）保持一致。
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    获取指定股票代码的综合基本面数据

    基本面数据通常包括：
    - 公司概况（行业、市值、员工数等）
    - 关键财务指标（P/E 市盈率、P/B 市净率、ROE 净资产收益率等）
    - 估值指标（EV/EBITDA、股息率等）
    - 成长性指标（收入增长率、利润增长率等）

    参数：
        ticker (str): 公司股票代码，例如 "AAPL"、"MSFT"
        curr_date (str): 当前交易日期，格式为 yyyy-mm-dd
                        这个日期用于确定获取哪个季度的数据

    返回：
        str: 格式化的综合基本面数据报告字符串

    与三大报表工具的关系：
        get_fundamentals 提供概览数据（关键比率和指标），
        而 get_balance_sheet/get_cashflow/get_income_statement 提供详细报表数据，
        两者配合使用可以进行深入的基本面分析。
    """
    # 调用统一路由接口，将请求转发到配置的基本面数据供应商
    # route_to_vendor 会：
    #   1. 查找 "get_fundamentals" 所属分类（fundamental_data）
    #   2. 读取配置决定用哪个供应商
    #   3. 调用对应供应商的实现函数
    #   4. 如果主供应商失败，自动降级到备选供应商
    # 参数透传：ticker 和 curr_date 原封不动传给供应商实现函数
    return route_to_vendor("get_fundamentals", ticker, curr_date)


# =============================================================================
# get_balance_sheet - 资产负债表数据获取工具
# =============================================================================
@tool  # 注册为 LangChain 工具
def get_balance_sheet(
    # ticker 参数：股票代码
    ticker: Annotated[str, "ticker symbol"],

    # freq 参数：报告频率
    # 默认值为 "quarterly"（季度），可选 "annual"（年度）
    # 季度报告提供更细粒度的数据，年度报告提供更宏观的视角
    # 【默认值机制】如果 LLM 不传 freq 参数，就用 "quarterly"
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",

    # curr_date 参数：当前交易日期
    # 默认值为 None，表示使用最新可用数据
    # 【None 表示什么？】不指定日期，由供应商返回最新的财报数据
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    获取指定股票代码的资产负债表数据

    资产负债表（Balance Sheet）是三大核心财务报表之一，反映企业在特定日期的
    财务状况，遵循会计恒等式：资产 = 负债 + 所有者权益

    资产负债表关键项目：
    - 资产（Assets）：流动资产（现金、应收账款、存货）、非流动资产（固定资产、无形资产）
    - 负债（Liabilities）：流动负债（应付账款、短期借款）、非流动负债（长期借款）
    - 所有者权益（Equity）：股本、资本公积、留存收益

    参数：
        ticker (str): 公司股票代码
        freq (str): 报告频率，"annual"（年度）或 "quarterly"（季度），默认为季度
        curr_date (str): 当前交易日期，格式为 yyyy-mm-dd，默认为 None

    返回：
        str: 格式化的资产负债表数据字符串

    分析要点：
        - 资产负债率 = 总负债 / 总资产（衡量财务杠杆）
        - 流动比率 = 流动资产 / 流动负债（衡量短期偿债能力）
        - 权益乘数 = 总资产 / 所有者权益（衡量财务杠杆倍数）
    """
    # 调用统一路由接口，将请求转发到配置的基本面数据供应商
    # 三个参数透传：ticker, freq, curr_date
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


# =============================================================================
# get_cashflow - 现金流量表数据获取工具
# =============================================================================
@tool  # 注册为 LangChain 工具
def get_cashflow(
    # ticker 参数：股票代码
    ticker: Annotated[str, "ticker symbol"],

    # freq 参数：报告频率
    # 默认值为 "quarterly"（季度），可选 "annual"（年度）
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",

    # curr_date 参数：当前交易日期
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    获取指定股票代码的现金流量表数据

    现金流量表（Cash Flow Statement）是三大核心财务报表之一，反映企业在一定
    期间内现金及现金等价物的流入和流出情况。

    现金流量表三大分类：
    - 经营活动现金流（Operating）：日常经营产生的现金，如销售收入、支付供应商
    - 投资活动现金流（Investing）：长期资产投资，如购买设备、收购公司
    - 筹资活动现金流（Financing）：融资活动，如发行股票、借款、分红

    参数：
        ticker (str): 公司股票代码
        freq (str): 报告频率，"annual"（年度）或 "quarterly"（季度），默认为季度
        curr_date (str): 当前交易日期，格式为 yyyy-mm-dd，默认为 None

    返回：
        str: 格式化的现金流量表数据字符串

    分析要点：
        - 自由现金流 = 经营现金流 - 资本支出（衡量可自由支配的现金）
        - 经营现金流 > 净利润 说明盈利质量高
        - 持续负的自由现金流可能是危险信号
    """
    # 调用统一路由接口，将请求转发到配置的基本面数据供应商
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


# =============================================================================
# get_income_statement - 利润表数据获取工具
# =============================================================================
@tool  # 注册为 LangChain 工具
def get_income_statement(
    # ticker 参数：股票代码
    ticker: Annotated[str, "ticker symbol"],

    # freq 参数：报告频率
    # 默认值为 "quarterly"（季度），可选 "annual"（年度）
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",

    # curr_date 参数：当前交易日期
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    获取指定股票代码的利润表数据

    利润表（Income Statement）是三大核心财务报表之一，反映企业在一定期间内的
    经营成果，遵循公式：收入 - 费用 = 利润

    利润表关键项目（自上而下）：
    - 营业收入（Revenue/Sales）：主营业务收入
    - 营业成本（COGS）：直接生产成本
    - 毛利润（Gross Profit）= 营业收入 - 营业成本
    - 营业费用（Operating Expenses）：销售、管理、研发费用
    - 营业利润（Operating Income）= 毛利润 - 营业费用
    - 净利润（Net Income）：税后最终利润

    参数：
        ticker (str): 公司股票代码
        freq (str): 报告频率，"annual"（年度）或 "quarterly"（季度），默认为季度
        curr_date (str): 当前交易日期，格式为 yyyy-mm-dd，默认为 None

    返回：
        str: 格式化的利润表数据字符串

    分析要点：
        - 毛利率 = 毛利润 / 营业收入（衡量产品竞争力）
        - 净利率 = 净利润 / 营业收入（衡量整体盈利能力）
        - 收入增长率 = (本期收入 - 上期收入) / 上期收入（衡量成长性）
        - EPS（每股收益）= 净利润 / 流通股数（投资者最关注的指标之一）
    """
    # 调用统一路由接口，将请求转发到配置的基本面数据供应商
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)
