# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 pandas DataFrame？
#    DataFrame 是 pandas 库的核心数据结构，可以理解为一个"Excel 表格"。
#    它有行（row）和列（column），每列有列名（如 "Open"、"Close"）。
#    示例：
#         Date        Open    High    Low     Close   Volume
#    0    2024-01-02  187.15  188.44  183.89  185.64  82488700
#    1    2024-01-03  184.22  185.88  183.43  184.25  58414500
#    DataFrame 的每一列叫做 Series，是一维数据序列。
#
# 2. 什么是 yfinance？
#    yfinance 是一个免费的开源 Python 库，用于从"雅虎财经"（Yahoo Finance）
#    网站获取股票数据（价格、新闻、财务报表等）。
#    它不是雅虎官方的 API，而是社区维护的第三方工具。
#    常用功能：
#    - yf.Ticker("AAPL")：创建一个代表苹果股票的对象
#    - ticker.history()：获取历史价格数据
#    - ticker.info：获取公司基本面信息（字典格式）
#    - yf.download("AAPL", start="2024-01-01")：批量下载历史数据
#
# 3. 什么是 stockstats？
#    stockstats 是基于 pandas 的技术指标计算库。
#    "技术指标"是用数学公式从价格数据中算出来的数值，用于判断股票趋势。
#    用法：wrap(DataFrame) 把普通 DataFrame 包装成 StockDataFrame，
#    然后通过 df["rsi"] 这样的列名访问就会自动计算指标。
#
# 4. 什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 是 Python 类型提示的增强工具。
#    对 Python 而言它仍然是 str 类型；
#    但"说明文字"会被 LangChain 框架读取，作为 LLM 工具的参数描述。
#    这让 AI 模型能理解每个参数的用途。
#
# 5. 什么是 lambda 表达式？
#    lambda 是创建"小型匿名函数"的语法。格式：lambda 参数: 返回值
#    例如 lambda: ticker.history() 创建一个无参数函数，
#    调用时才执行 ticker.history()。
#    用于"延迟执行"——避免在传参时就触发 API 请求。
#
# 6. 什么是 try-except 语句？
#    Python 的异常处理机制：
#    try:
#        可能出错的代码
#    except 异常类型:
#        出错后的处理代码
#    捕获异常后程序不会崩溃，而是执行 except 块中的处理逻辑。
#
# 7. 什么是数据降级（Graceful Degradation）？
#    "降级"是指当主方案失败时，自动切换到备用方案，确保功能仍然可用。
#    例如：批量获取指标数据失败 → 自动降级为逐个日期查询。
#    就像手机信号不好时，从 5G 降级到 4G，虽然慢一点但仍然能用。
#
# 8. 什么是缓存机制（Cache-Aside）？
#    把下载好的数据保存到本地 CSV 文件。
#    下次请求时先检查本地有没有，有就直接读（快），没有才去网络下载。
#    就像图书馆：先看书架有没有，有就借；没有才去采购。
#
# 9. 什么是指数退避重试（Exponential Backoff）？
#    请求被限流时，等待一段时间后重试，等待时间逐次翻倍。
#    例如：第 1 次等 2 秒，第 2 次等 4 秒，第 3 次等 8 秒。
#
# 10. 什么是 relativedelta？
#     dateutil 库提供的日期差计算工具。
#     relativedelta(days=30) 表示 30 天的偏移量。
#     比标准库 timedelta 更灵活（可以按年、月计算）。
#
# 11. 什么是 f-string？
#     Python 的格式化字符串语法。在字符串前加 f，用 {} 嵌入变量。
#     例如 f"Hello {name}" 中，{name} 会被替换为变量 name 的值。
#
# 12. 什么是 dict（字典）？
#     Python 的键值对容器，用 {} 定义。
#     例如 {"name": "Apple", "price": 185.64}
#     通过 dict["name"] 或 dict.get("name") 访问值。
#     .get() 方法在键不存在时返回 None 而不是报错。
#
# 13. 什么是 OHLCV？
#     O=Open（开盘价）, H=High（最高价）, L=Low（最低价）,
#     C=Close（收盘价）, V=Volume（成交量）。
#     这是股票每日行情的标准数据格式。
#
# 14. 什么是 tz_localize？
#     pandas 的时间戳可以带时区信息（如 UTC）。
#     tz_localize(None) 移除时区信息，使数据不带时区，便于处理。
#
# 15. 什么是 datetime.strptime 和 strftime？
#     strptime(字符串, 格式)：把字符串解析为日期对象
#     strftime(格式)：把日期对象格式化为字符串
#     格式码：%Y=年, %m=月, %d=日, %H=时, %M=分, %S=秒
#
# 16. 什么是 CSV 格式？
#     CSV（Comma-Separated Values，逗号分隔值）是纯文本格式。
#     每行是一条记录，字段之间用逗号分隔。
#     示例：Date,Open,High,Low,Close,Volume
#           2024-01-02,187.15,188.44,183.89,185.64,82488700
#     DataFrame.to_csv() 把表格转为 CSV 字符串，pd.read_csv() 把 CSV 读为表格。
#
# 17. 什么是 while 循环和 for 循环？
#     while 条件:  —— 条件为 True 时反复执行
#     for 变量 in 序列:  —— 遍历序列中的每个元素
#
# 18. 什么是 with 语句？
#     with 语句用于资源管理（如打开文件），确保资源使用后自动释放。
#     格式：
#       with open("文件") as f:
#           data = f.read()
#     即使中间出错，文件也会自动关闭。
#     （本文件中未直接使用 with，但 pandas 的 read_csv 内部会用到）
# =============================================================================
#
# 模块名称：y_finance.py
# 模块功能：Yahoo Finance 数据获取模块
# 描述：
#   本模块基于 yfinance 库封装了 Yahoo Finance 的数据获取功能，提供了股票
#   价格数据、技术指标、基本面数据、财务报表和内部交易数据的完整接口。
#   它是数据流层中 yfinance 供应商的具体实现。
#
# 核心依赖库说明：
#   - yfinance: Yahoo Finance 的 Python 封装库。Yahoo Finance 是一个免费的
#     金融市场数据平台，提供股票、ETF、指数等的历史价格和基本面数据。
#   - stockstats: 基于 pandas 的技术指标计算库，通过 wrap() 函数包装
#     DataFrame 后，可以直接通过列名访问技术指标（如 df['rsi']）。
#   - dateutil.relativedelta: 提供了比标准库 timedelta 更灵活的日期计算，
#     支持按年、月、日等单位进行日期偏移。
#
# 设计模式说明：
#   - 缓存模式（Cache-Aside Pattern）：_get_stock_stats_bulk 函数实现了
#     数据缓存，先从本地 CSV 读取，未命中时再从网络下载并缓存。
#   - 批量优化模式（Bulk Optimization）：_get_stock_stats_bulk 一次性获取
#     所有日期的指标数据，避免逐个日期请求的 N+1 问题。
#   - 降级模式（Graceful Degradation）：get_stock_stats_indicators_window
#     在批量方法失败时自动回退到逐个日期查询的原始方法。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# 每一行 import 都会在程序启动时执行，把外部库的功能加载进来。
# ---------------------------------------------------------------------------

# typing.Annotated: 用于为类型注解添加元数据说明。
# 例如 Annotated[str, "股票代码"] 在类型检查时仍被视为 str，但附加了语义信息。
# LangChain 框架会读取第二个参数作为 LLM 工具的参数描述。
from typing import Annotated

# datetime: 标准库中的日期时间处理类。
# 用于日期格式化、解析和当前时间获取。
# 常用方法：
#   - datetime.strptime(字符串, 格式)：字符串 → 日期对象
#   - datetime.now()：获取当前时间
#   - dt.strftime(格式)：日期对象 → 字符串
from datetime import datetime

# relativedelta: dateutil 库提供的相对日期差计算工具。
# 与标准库 timedelta 的区别：
#   timedelta 只能按天、秒、微秒计算偏移。
#   relativedelta 可以按年、月、周、天计算偏移，更灵活。
# 例如：relativedelta(days=30) 表示 30 天前，relativedelta(months=1) 表示 1 个月前。
from dateutil.relativedelta import relativedelta

# yfinance（别名 yf）：Yahoo Finance 的 Python 封装库。
# 主要功能：
#   - yf.Ticker(symbol): 创建股票对象
#   - ticker.history(): 获取历史价格数据
#   - ticker.info: 获取公司基本面信息
#   - yf.download(): 批量下载股票数据
# 这里用 as yf 起别名，后面写 yf.Ticker() 就等于写 yfinance.Ticker()。
import yfinance as yf

# os 模块：用于文件和目录操作。
# 这里用于创建缓存目录、检查文件是否存在。
# 常用函数：
#   - os.path.exists(路径)：检查文件/目录是否存在
#   - os.path.join(目录, 文件名)：拼接路径
#   - os.makedirs(目录, exist_ok=True)：创建目录
import os

# 从同目录下的 stockstats_utils 模块导入工具函数和类。
# . 表示相对导入（从当前包导入）。
# 导入的函数：
#   - StockstatsUtils: 股票统计工具类，提供单日指标查询
#   - _clean_dataframe: 数据清洗函数（以下划线开头表示模块私有）
#   - yf_retry: 带指数退避的 API 重试包装器
# 这三个工具在 y_finance.py 中被反复使用。
from .stockstats_utils import StockstatsUtils, _clean_dataframe, yf_retry


# =============================================================================
# 函数：get_YFin_data_online
# =============================================================================
# A 股代码转换工具
# =============================================================================

def to_yfinance_symbol(symbol: str) -> str:
    """
    将 A 股代码转换为 Yahoo Finance 格式。

    Yahoo Finance 对 A 股使用以下格式：
      - 上海交易所 (60xxxx, 68xxxx): 加 ".SS" 后缀，如 "601118.SS"
      - 深圳交易所 (00xxxx, 30xxxx): 加 ".SZ" 后缀，如 "000001.SZ"
      - 美股代码: 原样返回，如 "AAPL", "NVDA"

    参数：
      symbol: 原始股票代码

    返回：
      Yahoo Finance 格式的股票代码
    """
    symbol = symbol.strip().upper()

    # 已经是 Yahoo Finance 格式（包含 .SS / .SZ 后缀）
    if "." in symbol:
        return symbol

    # A 股 6 位数字代码
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith("60") or symbol.startswith("68"):
            return f"{symbol}.SS"  # 上海交易所
        else:
            return f"{symbol}.SZ"  # 深圳交易所

    return symbol

# =============================================================================
def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    """
    从 Yahoo Finance 获取股票的在线历史价格数据。

    【调用时机】
      当系统需要获取某支股票在指定日期范围内的历史价格数据时调用。
      通常被 LangChain Agent 作为工具调用，用于获取 OHLCV（量价）数据。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"、"TSLA"）。
      start_date (str): 数据起始日期，格式 "YYYY-MM-DD"（如 "2024-01-01"）。
      end_date (str): 数据结束日期，格式 "YYYY-MM-DD"。

    【输出是什么】
      str: 包含 CSV 格式数据的字符串，带有元数据头部信息。
        如果找不到数据，返回错误提示字符串。
      运行时输出示例：
        # Stock data for AAPL from 2024-01-01 to 2024-01-31
        # Total records: 21
        # Data retrieved on: 2024-07-08 15:30:00

        Date,Open,High,Low,Close,Adj Close,Volume
        2024-01-02,187.15,188.44,183.89,185.64,185.64,82488700
        2024-01-03,184.22,185.88,183.43,184.25,184.25,58414500
        ...

    【输出被谁使用】
      返回的 CSV 字符串会被传递给 LLM（大语言模型）或下游分析模块，
      用于技术分析、趋势判断等。

    关键概念：
      - yf.Ticker(symbol): 创建一个 Ticker 对象，代表一支股票。
        Ticker 对象提供了访问该股票各种数据的方法。
      - .upper(): 字符串方法，将字符串转换为大写。
        股票代码通常使用大写字母（如 "AAPL" 而非 "aapl"），这是金融领域的惯例。
      - tz_localize(None): 移除时区信息。
        yfinance 返回的数据可能带有时区（如 UTC），移除时区可以简化处理
        并避免时区相关的显示问题。
      - .round(2): 将数值四舍五入到小数点后两位，使输出更整洁。

    数据验证：
      - datetime.strptime() 调用用于验证日期格式是否正确。
        如果格式不正确，会抛出 ValueError 异常。
        注意：这里的返回值没有被使用，仅用于验证。

    Python 语法要点：
      - lambda: 创建匿名函数，用于延迟执行（配合 yf_retry 使用）
      - f-string: f"..." 格式化字符串，{变量} 被替换为实际值
      - for col in 列表: 遍历列表中的每个元素
      - if col in data.columns: 检查列是否存在（in 运算符）
    """
    # 验证日期格式（如果格式不正确会抛出 ValueError）
    # datetime.strptime(字符串, 格式) 尝试把字符串按指定格式解析为日期
    # 如果字符串格式不对（如 "2024/01/01"），会抛出 ValueError
    # 这里的返回值没有被使用，仅用于验证——如果格式不对，函数会在此中断
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    # 创建 Yahoo Finance Ticker 对象
    # yf.Ticker(代码) 创建一个代表该股票的对象
    # .upper() 将代码转为大写，符合金融数据惯例
    # 例如：symbol="aapl" → "AAPL"
    ticker = yf.Ticker(to_yfinance_symbol(symbol))

    # 获取指定日期范围的历史数据
    # lambda 表达式创建延迟执行的可调用对象，供 yf_retry 控制重试
    # 为什么用 lambda？因为 yf_retry 内部会调用 func()，
    # 如果不用 lambda 而是直接传 ticker.history(...)，
    # API 请求会在传参时立即执行，无法被 yf_retry 控制重试
    # ticker.history() 返回的是 pandas DataFrame，包含 OHLCV 数据
    # 运行时示例（data 的样子）：
    #                 Open    High    Low     Close   Volume
    #   Date
    #   2024-01-02  187.15  188.44  183.89  185.64  82488700
    #   2024-01-03  184.22  185.88  183.43  184.25  58414500
    data = yf_retry(lambda: ticker.history(start=start_date, end=end_date))

    # 检查数据是否为空（可能是股票代码错误或日期范围内无交易）
    # .empty 是 DataFrame 的属性，如果 DataFrame 没有行则返回 True
    if data.empty:
        return (
            f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    # 移除时区信息，简化输出
    # data.index.tz 检查索引是否带有时区信息
    # yfinance 返回的 DataFrame 默认以 Date 为索引，且可能带时区
    if data.index.tz is not None:
        # tz_localize(None) 移除时区信息
        # 为什么移除？因为不同时区可能导致日期显示不一致
        data.index = data.index.tz_localize(None)  # 移除时区

    # 将数值列四舍五入到两位小数
    # 定义需要处理的列名列表
    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    # for 循环遍历列名列表
    for col in numeric_columns:
        if col in data.columns:  # 检查列是否存在（某些列可能缺失）
            # .round(2) 将该列的所有数值四舍五入到小数点后两位
            # 例如：187.14999 → 187.15
            data[col] = data[col].round(2)  # 保留两位小数

    # 将 DataFrame 转换为 CSV 格式字符串
    # .to_csv() 不带参数时返回 CSV 格式的字符串
    # 运行时示例（csv_string 的样子）：
    # "Date,Open,High,Low,Close,Adj Close,Volume\n2024-01-02,187.15,188.44,..."
    csv_string = data.to_csv()

    # 添加元数据头部信息
    # f-string 格式化：{变量} 会被替换为变量的值
    # \n 是换行符
    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    # len(data) 返回 DataFrame 的行数（即数据条数）
    header += f"# Total records: {len(data)}\n"  # len(data) 返回行数
    # datetime.now() 获取当前时间，strftime 格式化为字符串
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # 返回头部 + CSV 数据
    # 字符串拼接：header 和 csv_string 都是字符串，用 + 连接
    return header + csv_string


# =============================================================================
# 函数：get_stock_stats_indicators_window
# =============================================================================
def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """
    获取指定回溯天数范围内的技术指标值，并附上指标说明。

    【调用时机】
      当系统需要分析某支股票在过去 N 天的技术指标走势时调用。
      例如：分析 AAPL 过去 10 天的 RSI 指标变化。

    【输入是什么】
      symbol (str): 股票代码。
      indicator (str): 技术指标名称（如 "rsi"、"macd"、"close_50_sma"）。
      curr_date (str): 当前交易日期，格式 "YYYY-MM-DD"。
      look_back_days (int): 回溯天数（从当前日期往前看多少天）。

    【输出是什么】
      str: 包含日期-指标值列表和指标说明的格式化字符串。
      运行时输出示例（调用 get_stock_stats_indicators_window("AAPL", "rsi", "2024-01-15", 5)）：
        ## rsi values from 2024-01-10 to 2024-01-15:

        2024-01-15: 55.23
        2024-01-14: N/A: Not a trading day (weekend or holiday)
        2024-01-13: N/A: Not a trading day (weekend or holiday)
        2024-01-12: 48.12
        2024-01-11: 62.87
        2024-01-10: 51.34

        RSI: Measures momentum to flag overbought/oversold conditions. ...

    【输出被谁使用】
      返回的字符串被传递给 LLM 或分析模块，用于辅助交易决策。

    工作流程：
      1. 验证指标是否在支持列表中。
      2. 计算回溯起始日期。
      3. 尝试批量获取指标数据（性能优化）。
      4. 如果批量方法失败，降级到逐个日期查询。
      5. 构建并返回格式化的结果字符串。

    关键概念：
      - best_ind_params: 一个字典，键是指标名，值是指标的详细说明。
        这种设计将数据（指标说明）与代码逻辑分离，方便维护和扩展。
      - 批量优化 vs 逐条查询：
        批量方法（_get_stock_stats_bulk）一次性获取所有历史数据并计算指标，
        然后从中提取需要的日期。这比逐条查询（每个日期都重新下载数据）
        效率高得多，避免了 N+1 查询问题。
      - 降级策略（Fallback）：
        当批量方法失败时（如网络问题），自动回退到逐个日期查询的原始方法，
        确保功能的可用性。

    Python 语法要点：
      - datetime.strptime(date_str, format): 将字符串按格式解析为 datetime 对象。
      - relativedelta(days=n): 创建一个表示 n 天的时间偏移量。
      - while 循环：当条件为 True 时持续执行。这里用于从当前日期往回遍历每一天。
      - try-except: 异常处理，try 中的代码出错时执行 except 块。
      - 字典 .get(key, default): 安全获取字典值，不存在时返回默认值。
    """
    # -------------------------------------------------------------------------
    # 指标参数说明字典
    # 每个指标都有详细的英文说明，帮助用户理解指标的含义和使用方法。
    # 字典的键是指标名（用于匹配），值是指标的详细描述。
    # 运行时示例：
    #   best_ind_params["rsi"] → "RSI: Measures momentum to flag overbought/oversold..."
    # -------------------------------------------------------------------------
    best_ind_params = {
        # ---- 移动平均线 (Moving Averages) ----
        "close_50_sma": (
            "50 SMA: A medium-term trend indicator. "
            "Usage: Identify trend direction and serve as dynamic support/resistance. "
            "Tips: It lags price; combine with faster indicators for timely signals."
        ),
        "close_200_sma": (
            "200 SMA: A long-term trend benchmark. "
            "Usage: Confirm overall market trend and identify golden/death cross setups. "
            "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
        ),
        "close_10_ema": (
            "10 EMA: A responsive short-term average. "
            "Usage: Capture quick shifts in momentum and potential entry points. "
            "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
        ),
        # ---- MACD 相关 ----
        "macd": (
            "MACD: Computes momentum via differences of EMAs. "
            "Usage: Look for crossovers and divergence as signals of trend changes. "
            "Tips: Confirm with other indicators in low-volatility or sideways markets."
        ),
        "macds": (
            "MACD Signal: An EMA smoothing of the MACD line. "
            "Usage: Use crossovers with the MACD line to trigger trades. "
            "Tips: Should be part of a broader strategy to avoid false positives."
        ),
        "macdh": (
            "MACD Histogram: Shows the gap between the MACD line and its signal. "
            "Usage: Visualize momentum strength and spot divergence early. "
            "Tips: Can be volatile; complement with additional filters in fast-moving markets."
        ),
        # ---- 动量指标 (Momentum) ----
        "rsi": (
            "RSI: Measures momentum to flag overbought/oversold conditions. "
            "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
            "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
        ),
        # ---- 波动率指标 (Volatility) ----
        "boll": (
            "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
            "Usage: Acts as a dynamic benchmark for price movement. "
            "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
        ),
        "boll_ub": (
            "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
            "Usage: Signals potential overbought conditions and breakout zones. "
            "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
        ),
        "boll_lb": (
            "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
            "Usage: Indicates potential oversold conditions. "
            "Tips: Use additional analysis to avoid false reversal signals."
        ),
        "atr": (
            "ATR: Averages true range to measure volatility. "
            "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
            "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
        ),
        # ---- 成交量加权指标 (Volume-Based) ----
        "vwma": (
            "VWMA: A moving average weighted by volume. "
            "Usage: Confirm trends by integrating price action with volume data. "
            "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
        ),
        "mfi": (
            "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
            "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
            "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
        ),
    }

    # 验证指标名称是否在支持列表中
    # in 运算符检查元素是否在字典的键中
    # 如果不在，raise 抛出 ValueError 异常
    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    # 设置数据结束日期为当前日期
    end_date = curr_date
    # 将字符串日期解析为 datetime 对象
    # 运行时示例：curr_date="2024-01-15" → curr_date_dt=datetime(2024,1,15)
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # 计算回溯起始日期：当前日期减去回溯天数
    # relativedelta(days=look_back_days) 表示 N 天的时间偏移
    # 运行时示例：curr_date_dt=datetime(2024,1,15), look_back_days=5
    #             → before=datetime(2024,1,10)
    before = curr_date_dt - relativedelta(days=look_back_days)

    # 尝试批量获取指标数据（性能优化）
    try:
        # _get_stock_stats_bulk 一次性获取所有历史数据并计算指标
        # 返回 {日期字符串: 指标值} 的字典
        # 运行时示例：indicator_data = {"2024-01-10": "51.34", "2024-01-11": "62.87", ...}
        indicator_data = _get_stock_stats_bulk(symbol, indicator, curr_date)

        # 生成需要的日期范围，从当前日期回溯到 before
        current_dt = curr_date_dt
        date_values = []  # 存储 (日期, 指标值) 元组的列表

        # while 循环：从当前日期逐天往回遍历，直到回溯起始日期
        # 条件 current_dt >= before 为 True 时持续循环
        while current_dt >= before:
            # .strftime('%Y-%m-%d') 把日期对象格式化为字符串
            # 运行时示例：current_dt=datetime(2024,1,15) → date_str="2024-01-15"
            date_str = current_dt.strftime('%Y-%m-%d')  # 格式化为字符串

            # 在指标数据字典中查找该日期的指标值
            # in 运算符检查日期是否是字典的键
            if date_str in indicator_data:
                indicator_value = indicator_data[date_str]
            else:
                # 日期不在字典中（非交易日，如周末或节假日）
                # 股市周末和节假日不开盘，没有交易数据
                indicator_value = "N/A: Not a trading day (weekend or holiday)"

            # 将 (日期, 指标值) 添加到结果列表
            # append() 是列表的方法，在末尾添加一个元素
            # (date_str, indicator_value) 是一个元组（tuple），用圆括号定义
            date_values.append((date_str, indicator_value))
            # 向前移动一天
            # relativedelta(days=1) 表示 1 天的偏移
            current_dt = current_dt - relativedelta(days=1)

        # 构建结果字符串
        # 使用空字符串初始化，然后逐行追加
        ind_string = ""
        # for 循环遍历 date_values 列表中的每个元组
        # date_str, value 是"元组解包"——把元组的两个元素分别赋给两个变量
        for date_str, value in date_values:
            ind_string += f"{date_str}: {value}\n"

    except Exception as e:
        # ==================== 降级处理（Graceful Degradation）====================
        # 批量方法失败时的降级处理
        # 打印错误信息（f-string 格式化，{e} 被替换为异常描述）
        print(f"Error getting bulk stockstats data: {e}")
        # 回退到逐个日期查询的原始方法
        ind_string = ""
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        # 逐个日期查询指标值
        while curr_date_dt >= before:
            # 调用 get_stockstats_indicator 获取单个日期的指标值
            # 这个函数会为每个日期单独查询，速度较慢但更可靠
            indicator_value = get_stockstats_indicator(
                symbol, indicator, curr_date_dt.strftime("%Y-%m-%d")
            )
            # 追加到结果字符串
            ind_string += f"{curr_date_dt.strftime('%Y-%m-%d')}: {indicator_value}\n"
            # 向前移动一天
            curr_date_dt = curr_date_dt - relativedelta(days=1)

    # 构建最终的结果字符串
    # 包含：指标名、日期范围、逐日数据、指标说明
    # .get(indicator, "No description available.") 是字典的安全取值方法
    # 如果 indicator 存在则返回对应的值，否则返回默认值
    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + best_ind_params.get(indicator, "No description available.")  # 添加指标说明
    )

    return result_str


# =============================================================================
# 函数：_get_stock_stats_bulk（批量指标计算）
# =============================================================================
def _get_stock_stats_bulk(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to calculate"],
    curr_date: Annotated[str, "current date for reference"]
) -> dict:
    """
    批量计算股票技术指标（优化版）。

    与逐个日期查询相比，本函数一次性获取所有历史数据并计算指标，
    然后返回所有日期的指标值字典，大幅提升了多日期查询的性能。

    【调用时机】
      被 get_stock_stats_indicators_window() 调用，作为批量获取指标的首选方案。
      如果本函数失败，调用方会降级为逐个日期查询。

    【输入是什么】
      symbol (str): 股票代码。
      indicator (str): 要计算的技术指标名称。
      curr_date (str): 当前日期（用于确定数据范围），格式 "YYYY-MM-DD"。

    【输出是什么】
      dict: {日期字符串: 指标值字符串} 的映射字典。
      运行时示例：
        {
          "2024-01-10": "51.34",
          "2024-01-11": "62.87",
          "2024-01-12": "48.12",
          "2024-01-15": "55.23",
          ...
        }

    【输出被谁使用】
      被 get_stock_stats_indicators_window() 调用，从中提取需要日期范围的指标值。

    工作流程：
      1. 获取配置，判断数据来源（在线/本地）。
      2. 如果配置为本地模式，从本地 CSV 文件读取。
      3. 如果配置为在线模式，从 Yahoo Finance 下载（带缓存）。
      4. 清洗数据并用 stockstats 计算指标。
      5. 遍历所有行，构建日期-指标值字典。

    关键概念：
      - N+1 查询问题：
        如果逐个日期查询（N 个日期），需要发 N 次网络请求或读取 N 次文件。
        批量方法只需要 1 次请求，性能提升显著。
      - pd.isna() 函数：
        判断值是否为 NaN（Not a Number，缺失值）。
        不能用 == 判断 NaN，因为 NaN != NaN（这是 IEEE 754 浮点数标准的规定）。
      - 延迟导入（Lazy Import）：
        在函数内部 import，而不是在文件顶部。
        好处：减少模块加载时间，避免循环导入。
    """
    # 在函数内部导入，避免循环导入问题
    # 延迟导入（Lazy Import）是处理循环依赖的常用技巧
    from .config import get_config
    import pandas as pd
    from stockstats import wrap
    import os

    # 获取全局配置
    # config 是一个字典，包含数据缓存目录、数据供应商等设置
    config = get_config()
    # 判断是否使用在线模式：如果技术指标供应商不是 "local"，则为在线模式
    # config["data_vendors"]["technical_indicators"] 的值可能是 "yfinance" 或 "local"
    online = config["data_vendors"]["technical_indicators"] != "local"

    if not online:
        # ---- 本地模式：从本地 CSV 文件读取 ----
        try:
            # pd.read_csv(路径) 从 CSV 文件读取数据为 DataFrame
            # os.path.join() 拼接路径
            # on_bad_lines="skip" 跳过格式错误的行
            data = pd.read_csv(
                os.path.join(
                    config.get("data_cache_dir", "data"),  # 缓存目录，默认 "data"
                    f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
                ),
                on_bad_lines="skip",  # 跳过格式错误的行
            )
        except FileNotFoundError:
            # 如果本地文件不存在，抛出异常提示
            raise Exception("Stockstats fail: Yahoo Finance data not fetched yet!")
    else:
        # ---- 在线模式：从 Yahoo Finance 下载（带缓存） ----
        today_date = pd.Timestamp.today()  # 今天的日期
        curr_date_dt = pd.to_datetime(curr_date)  # 当前日期

        # 设置数据范围：从15年前到今天
        # pd.DateOffset(years=15) 表示 15 年偏移
        end_date = today_date
        start_date = today_date - pd.DateOffset(years=15)
        # strftime 格式化日期为字符串
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        # 确保缓存目录存在
        # exist_ok=True 表示目录已存在时不报错
        os.makedirs(config["data_cache_dir"], exist_ok=True)

        # 构建缓存文件路径
        # 文件名格式：股票代码-YFin-data-开始日期-结束日期.csv
        data_file = os.path.join(
            config["data_cache_dir"],
            f"{symbol}-YFin-data-{start_date_str}-{end_date_str}.csv",
        )

        # ==================== 缓存检查（Cache-Aside 模式）====================
        # 缓存检查：有缓存则读缓存，否则下载并缓存
        if os.path.exists(data_file):
            # 有缓存：直接从本地 CSV 读取（快，毫秒级）
            data = pd.read_csv(data_file, on_bad_lines="skip")
        else:
            # 无缓存：从 Yahoo Finance 下载
            # 带重试的 Yahoo Finance 数据下载
            # lambda 包装延迟执行，让 yf_retry 控制重试逻辑
            data = yf_retry(lambda: yf.download(
                symbol,
                start=start_date_str,
                end=end_date_str,
                multi_level_index=False,  # 不使用多级列索引，简化结构
                progress=False,           # 不显示进度条
                auto_adjust=True,         # 自动调整价格（处理拆股和分红）
            ))
            # reset_index() 将 Date 从索引变为普通列
            data = data.reset_index()  # 将索引重置为普通列
            # to_csv() 保存到缓存文件，index=False 不保存行号
            data.to_csv(data_file, index=False)  # 保存到缓存

    # 清洗数据（日期转换、缺失值处理等）
    # _clean_dataframe 是从 stockstats_utils 导入的函数
    data = _clean_dataframe(data)
    # 用 stockstats 包装 DataFrame，启用技术指标计算
    # wrap() 把普通 DataFrame 包装成 StockDataFrame
    df = wrap(data)
    # 将日期列格式化为字符串
    # .dt 是 pandas 的日期时间访问器，strftime 格式化
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    # 触发 stockstats 计算指标
    # 访问 df[indicator] 会触发 stockstats 的惰性计算机制
    # stockstats 识别出指标名，自动用历史数据计算该指标
    df[indicator]  # This triggers stockstats to calculate the indicator

    # 构建日期-指标值映射字典
    result_dict = {}
    # df.iterrows()：遍历 DataFrame 的每一行
    # 返回 (行索引, 行数据) 的元组
    # _ 是 Python 惯例，表示这个变量不会被使用（行索引我们不需要）
    for _, row in df.iterrows():  # _ 是 Python 惯例，表示这个变量不会被使用
        date_str = row["Date"]         # 获取日期字符串
        indicator_value = row[indicator]  # 获取指标值

        # 处理缺失值
        # pd.isna() 检查值是否为 NaN（缺失值）
        # 注意：不能用 == np.nan 判断，因为 NaN != NaN
        if pd.isna(indicator_value):
            result_dict[date_str] = "N/A"  # 缺失值标记为 "N/A"
        else:
            result_dict[date_str] = str(indicator_value)  # 转换为字符串

    return result_dict


# =============================================================================
# 函数：get_stockstats_indicator（单日指标查询）
# =============================================================================
def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
) -> str:
    """
    获取单个日期的技术指标值（单日查询版）。

    【调用时机】
      当批量方法 _get_stock_stats_bulk 失败时，作为降级方案被逐个日期调用。
      也可以单独使用，获取某一天的技术指标值。

    【输入是什么】
      symbol (str): 股票代码。
      indicator (str): 技术指标名称。
      curr_date (str): 目标日期，格式 "YYYY-MM-DD"。

    【输出是什么】
      str: 指标值的字符串表示。如果出错，返回空字符串。
      运行时示例："55.23" 或 "N/A: Not a trading day (weekend or holiday)" 或 ""

    【输出被谁使用】
      被 get_stock_stats_indicators_window() 在降级模式下逐个日期调用。

    注意：
      此函数只查询单个日期的指标，主要用于降级方案。
      对于多日期查询，应使用 get_stock_stats_indicators_window 函数，
      它内部会调用 _get_stock_stats_bulk 进行批量优化。
    """
    # 解析日期字符串为 datetime 对象，然后重新格式化
    # 这确保了日期格式的一致性（即使输入格式略有偏差也能标准化）
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")

    try:
        # 调用 StockstatsUtils 的静态方法获取指标值
        # 不需要创建实例，直接用类名调用
        # 运行时示例：indicator_value = 55.23
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
        )
    except Exception as e:
        # 捕获所有异常，打印错误信息并返回空字符串
        # 这种"宽进严出"的错误处理方式适合 LLM Agent 环境，
        # 避免异常导致整个 Agent 流程中断
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    # 将指标值转换为字符串返回
    # str() 把数值（如 55.23）转为字符串 "55.23"
    return str(indicator_value)


# =============================================================================
# 函数：get_fundamentals（基本面数据）
# =============================================================================
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """
    获取公司基本面数据概览。

    【调用时机】
      当系统需要了解公司的基本情况（估值、盈利能力、财务健康等）时调用。

    【输入是什么】
      ticker (str): 股票代码（如 "AAPL"）。
      curr_date (str, 可选): 当前日期（yfinance 不使用此参数，仅为接口一致性保留）。
        = None 表示这个参数有默认值 None，调用时可以不传。

    【输出是什么】
      str: 包含公司基本面关键指标的格式化文本。
      运行时输出示例：
        # Company Fundamentals for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        Name: Apple Inc.
        Sector: Technology
        Industry: Consumer Electronics
        Market Cap: 3450000000000
        PE Ratio (TTM): 32.45
        ...

    【输出被谁使用】
      被传递给 LLM 或分析模块，用于评估公司投资价值。

    获取的数据字段包括：
      - 基本信息：公司名称、行业、板块
      - 估值指标：市值、市盈率、市净率、PEG 比率
      - 盈利能力：EPS、利润率、ROE、ROA
      - 财务健康：负债权益比、流动比率
      - 市场表现：Beta、52周高低、50日/200日均线

    关键概念：
      - yf.Ticker: 创建股票对象，提供访问各种数据的方法。
      - ticker_obj.info: 返回一个字典，包含公司的各种基本面数据。
        并不是所有字段对每只股票都可用，所以使用 .get() 方法安全访问。
      - dict.get(key): 安全地从字典获取值，如果 key 不存在返回 None 而不是抛出异常。
        这是处理可能缺失字段的最佳实践。
      - 列表推导式构建格式化输出：
        遍历 fields 列表，只包含非 None 的值，构建行列表。
    """
    try:
        # 创建 Ticker 对象（股票代码转为大写）
        ticker_obj = yf.Ticker(to_yfinance_symbol(ticker))
        # 获取公司信息字典（带重试机制）
        # ticker_obj.info 返回一个字典，包含大量公司信息
        # lambda 包装延迟执行，让 yf_retry 控制重试
        # 运行时示例：info = {"longName": "Apple Inc.", "sector": "Technology", ...}
        info = yf_retry(lambda: ticker_obj.info)

        # 检查是否获取到数据
        # not info 检查字典是否为空（空字典在 Python 中为 False，not False = True）
        if not info:
            return f"No fundamentals data found for symbol '{ticker}'"

        # 定义要提取的字段列表
        # 每个元素是一个 (标签, 值) 的元组
        # info.get(key) 安全地获取值，如果 key 不存在返回 None
        # 运行时示例：("Name", "Apple Inc."), ("Sector", "Technology"), ...
        fields = [
            ("Name", info.get("longName")),                  # 公司全名
            ("Sector", info.get("sector")),                  # 行业板块
            ("Industry", info.get("industry")),              # 细分行业
            ("Market Cap", info.get("marketCap")),           # 市值
            ("PE Ratio (TTM)", info.get("trailingPE")),      # 市盈率（过去12个月）
            ("Forward PE", info.get("forwardPE")),           # 远期市盈率
            ("PEG Ratio", info.get("pegRatio")),             # PEG 比率（市盈率/增长率）
            ("Price to Book", info.get("priceToBook")),      # 市净率
            ("EPS (TTM)", info.get("trailingEps")),          # 每股收益（过去12个月）
            ("Forward EPS", info.get("forwardEps")),         # 远期每股收益
            ("Dividend Yield", info.get("dividendYield")),   # 股息率
            ("Beta", info.get("beta")),                      # Beta 系数（波动性）
            ("52 Week High", info.get("fiftyTwoWeekHigh")),  # 52周最高价
            ("52 Week Low", info.get("fiftyTwoWeekLow")),    # 52周最低价
            ("50 Day Average", info.get("fiftyDayAverage")), # 50日均线
            ("200 Day Average", info.get("twoHundredDayAverage")),  # 200日均线
            ("Revenue (TTM)", info.get("totalRevenue")),     # 总收入（过去12个月）
            ("Gross Profit", info.get("grossProfits")),      # 毛利润
            ("EBITDA", info.get("ebitda")),                  # 息税折旧摊销前利润
            ("Net Income", info.get("netIncomeToCommon")),   # 净利润
            ("Profit Margin", info.get("profitMargins")),    # 利润率
            ("Operating Margin", info.get("operatingMargins")),  # 营业利润率
            ("Return on Equity", info.get("returnOnEquity")),    # 净资产收益率
            ("Return on Assets", info.get("returnOnAssets")),    # 总资产收益率
            ("Debt to Equity", info.get("debtToEquity")),    # 负债权益比
            ("Current Ratio", info.get("currentRatio")),     # 流动比率
            ("Book Value", info.get("bookValue")),           # 每股账面价值
            ("Free Cash Flow", info.get("freeCashflow")),    # 自由现金流
        ]

        # 构建输出行列表
        # 遍历 fields 列表，只保留值不为 None 的条目
        lines = []
        for label, value in fields:
            # is not None 检查值是否为 None
            # 只添加有值的字段，跳过值为 None 的字段
            if value is not None:  # 只添加有值的字段
                # f"{label}: {value}" 格式化每行
                lines.append(f"{label}: {value}")

        # 构建输出头部
        header = f"# Company Fundamentals for {ticker.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # 返回头部 + 数据行（用换行符连接）
        # "\n".join(lines) 把列表中的字符串用换行符连接成一个字符串
        # 例如 ["a", "b", "c"] → "a\nb\nc"
        return header + "\n".join(lines)

    except Exception as e:
        # 捕获所有异常，返回错误信息而不是崩溃
        # str(e) 把异常对象转为字符串描述
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# =============================================================================
# 函数：get_balance_sheet（资产负债表）
# =============================================================================
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """
    获取公司资产负债表数据。

    【调用时机】
      当系统需要分析公司的财务状况（资产、负债、股东权益）时调用。

    【输入是什么】
      ticker (str): 股票代码。
      freq (str): 数据频率，'annual'（年度）或 'quarterly'（季度）。默认 'quarterly'。
      curr_date (str, 可选): 当前日期（yfinance 不使用）。

    【输出是什么】
      str: CSV 格式的资产负债表数据，带元数据头部。
      运行时输出示例：
        # Balance Sheet data for AAPL (quarterly)
        # Data retrieved on: 2024-07-08 15:30:00

        ,2024-03-30,2023-12-30,...
        Total Assets,350000000000,340000000000,...
        Total Liabilities,250000000000,240000000000,...
        ...

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司财务健康状况。

    关键概念：
      - ticker_obj.quarterly_balance_sheet: 季度资产负债表。
      - ticker_obj.balance_sheet: 年度资产负债表。
      - 这两个属性返回的是 pandas DataFrame，行是财务科目，列是日期。
      - .to_csv(): 将 DataFrame 转换为 CSV 格式字符串。
      - .empty: DataFrame 的属性，判断是否为空（没有数据）。

    Python 语法要点：
      - if freq.lower() == "quarterly" 判断使用哪个频率的数据。
      - .lower() 将字符串转为小写，实现不区分大小写的比较。
        这样 'Quarterly'、'QUARTERLY' 都能正确匹配。
      - 三元表达式：value_if_true if condition else value_if_false
    """
    try:
        # 创建 Ticker 对象
        ticker_obj = yf.Ticker(to_yfinance_symbol(ticker))

        # 根据频率参数选择季度或年度数据
        # .lower() 实现不区分大小写：'Quarterly'、'QUARTERLY' 都能正确匹配
        # 运行时示例：freq="quarterly" → freq.lower()="quarterly" → 条件为 True
        if freq.lower() == "quarterly":
            # ticker_obj.quarterly_balance_sheet 返回季度资产负债表 DataFrame
            # lambda 包装延迟执行
            data = yf_retry(lambda: ticker_obj.quarterly_balance_sheet)
        else:
            # ticker_obj.balance_sheet 返回年度资产负债表 DataFrame
            data = yf_retry(lambda: ticker_obj.balance_sheet)

        # 检查数据是否为空
        # .empty 是 DataFrame 的属性，没有行时返回 True
        if data.empty:
            return f"No balance sheet data found for symbol '{ticker}'"

        # 转换为 CSV 字符串
        # .to_csv() 不带参数返回 CSV 格式字符串
        csv_string = data.to_csv()

        # 添加元数据头部
        header = f"# Balance Sheet data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


# =============================================================================
# 函数：get_cashflow（现金流量表）
# =============================================================================
def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """
    获取公司现金流量表数据。

    【调用时机】
      当系统需要分析公司的现金流状况时调用。
      现金流是评估公司真实财务状况的关键指标（比利润更真实）。

    【输入是什么】
      ticker (str): 股票代码。
      freq (str): 数据频率，'annual'（年度）或 'quarterly'（季度）。默认 'quarterly'。
      curr_date (str, 可选): 当前日期（yfinance 不使用）。

    【输出是什么】
      str: CSV 格式的现金流量表数据，带元数据头部。

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司现金流健康状况。

    关键概念：
      - ticker_obj.quarterly_cashflow: 季度现金流量表。
      - ticker_obj.cashflow: 年度现金流量表。
      - 现金流量表反映公司现金的流入和流出，分为经营活动、投资活动和筹资活动三部分。
    """
    try:
        ticker_obj = yf.Ticker(to_yfinance_symbol(ticker))

        # 根据频率参数选择季度或年度数据
        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_cashflow)
        else:
            data = yf_retry(lambda: ticker_obj.cashflow)

        if data.empty:
            return f"No cash flow data found for symbol '{ticker}'"

        # 转换为 CSV 字符串
        csv_string = data.to_csv()

        # 添加元数据头部
        header = f"# Cash Flow data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


# =============================================================================
# 函数：get_income_statement（利润表）
# =============================================================================
def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """
    获取公司利润表（损益表）数据。

    【调用时机】
      当系统需要分析公司的盈利能力时调用。

    【输入是什么】
      ticker (str): 股票代码。
      freq (str): 数据频率，'annual'（年度）或 'quarterly'（季度）。默认 'quarterly'。
      curr_date (str, 可选): 当前日期（yfinance 不使用）。

    【输出是什么】
      str: CSV 格式的利润表数据，带元数据头部。

    【输出被谁使用】
      被 LLM 或分析模块用于评估公司盈利能力。

    关键概念：
      - ticker_obj.quarterly_income_stmt: 季度利润表。
      - ticker_obj.income_stmt: 年度利润表。
      - 利润表反映公司在一定时期内的经营成果，包括收入、成本、费用和利润。
    """
    try:
        ticker_obj = yf.Ticker(to_yfinance_symbol(ticker))

        # 根据频率参数选择季度或年度数据
        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_income_stmt)
        else:
            data = yf_retry(lambda: ticker_obj.income_stmt)

        if data.empty:
            return f"No income statement data found for symbol '{ticker}'"

        # 转换为 CSV 字符串
        csv_string = data.to_csv()

        # 添加元数据头部
        header = f"# Income Statement data for {ticker.upper()} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


# =============================================================================
# 函数：get_insider_transactions（内部交易）
# =============================================================================
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"]
):
    """
    获取公司内部交易数据。

    【调用时机】
      当系统需要分析公司内部人（高管、董事）的买卖行为时调用。
      内部人大量买入通常被视为看涨信号，大量卖出可能被视为看跌信号。

    【输入是什么】
      ticker (str): 股票代码。

    【输出是什么】
      str: CSV 格式的内部交易数据，带元数据头部。
      运行时输出示例：
        # Insider Transactions data for AAPL
        # Data retrieved on: 2024-07-08 15:30:00

        Date,Transaction,Shares,Value,Insider,Title
        2024-06-15,SELL,10000,1856400,Tim Cook,CEO
        ...

    【输出被谁使用】
      被 LLM 或分析模块用于辅助交易决策。

    关键概念：
      - 内部交易（Insider Transactions）：公司高管、董事、大股东等内部人员
        买卖本公司股票的交易记录。大量内部人士买入通常被视为看涨信号，
        大量卖出可能被视为看跌信号。
      - ticker_obj.insider_transactions: 返回内部交易数据 DataFrame。
        可能为空（某些股票没有内部交易数据或数据不可用）。
      - 与之前的函数不同，此函数不需要 freq 参数（内部交易数据没有年度/季度之分）。

    Python 语法要点：
      - is None 和 .empty 是两种不同的空值检查：
        - is None: 对象本身为 None（没有返回数据）
        - .empty: 对象存在但 DataFrame 中没有行
      - 或运算符 or：两边只要有一个为 True 就返回 True
    """
    try:
        ticker_obj = yf.Ticker(to_yfinance_symbol(ticker))
        # 获取内部交易数据（带重试机制）
        data = yf_retry(lambda: ticker_obj.insider_transactions)

        # 检查数据是否存在（None 或空 DataFrame）
        # is None 和 .empty 是两种不同的空值检查：
        #   - is None: 对象本身为 None（没有返回数据）
        #   - .empty: 对象存在但 DataFrame 中没有行
        # or 运算符：两边只要有一个为 True 就为 True
        if data is None or data.empty:
            return f"No insider transactions data found for symbol '{ticker}'"

        # 转换为 CSV 字符串
        csv_string = data.to_csv()

        # 添加元数据头部
        header = f"# Insider Transactions data for {ticker.upper()}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"
