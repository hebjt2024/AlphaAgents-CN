# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 Python 模块（Module）？
#    一个 .py 文件就是一个"模块"。模块中可以包含变量、函数、类等。
#    其他 Python 文件可以通过 import 语句导入并使用本模块中的内容。
#    可以把"模块"想象成一个"工具箱"，里面放了很多"工具"（函数和类）。
#
# 2. 什么是 pandas？
#    pandas 是 Python 最流行的数据分析库。它提供了两种核心数据结构：
#    - DataFrame：二维表格，就像一个 Excel 工作表。
#      有行（row）和列（column），每列有一个列名（如 "Open"、"Close"）。
#      示例：
#           Date        Open    High    Low     Close   Volume
#      0    2024-01-02  187.15  188.44  183.89  185.64  82488700
#      1    2024-01-03  184.22  185.88  183.43  184.25  58414500
#    - Series：一维序列，就像 Excel 中的一列。DataFrame 的每一列就是一个 Series。
#
# 3. 什么是 yfinance？
#    yfinance 是一个免费的开源 Python 库，用于从"雅虎财经"（Yahoo Finance）
#    网站获取股票数据（价格、新闻、财务报表等）。它不是雅虎官方的 API，
#    而是社区维护的第三方工具。虽然有时会被限流，但胜在免费、好用。
#
# 4. 什么是 stockstats？
#    stockstats 是一个基于 pandas 的技术指标计算库。
#    "技术指标"是用数学公式从价格和成交量数据中算出来的数值，
#    用来帮助判断股票的趋势和买卖时机（如 RSI、MACD、布林带等）。
#    stockstats 的巧妙之处：你只需要把普通 DataFrame "包装"一下，
#    然后像访问普通列一样写 df['rsi']，它就会自动帮你算好 RSI 指标。
#
# 5. 什么是 API 限流（Rate Limit）？
#    当你频繁请求某个服务器（如 Yahoo Finance）时，服务器为了保护自己
#    不会被压垮，会限制你的请求频率。比如"每分钟最多 30 次请求"。
#    如果你超过了限制，服务器会返回 HTTP 429 错误（Too Many Requests），
#    yfinance 库会把它包装成 YFRateLimitError 异常。
#
# 6. 什么是指数退避重试（Exponential Backoff）？
#    当请求被限流时，不能立刻重试（那样只会再次被拒绝），而要"等一会儿"再试。
#    "指数退避"的意思是：每次等待时间翻倍。
#    举例：第 1 次失败后等 2 秒，第 2 次失败后等 4 秒，第 3 次失败后等 8 秒。
#    这样既给了服务器恢复的时间，又不会一直傻等。
#
# 7. 什么是缓存机制（Cache-Aside 模式）？
#    每次从网络下载股票数据都很慢（需要几秒钟），而且可能被限流。
#    "缓存"就是把下载好的数据保存到本地文件（CSV 文件）中。
#    Cache-Aside 模式的流程：
#    a) 先检查本地有没有缓存文件
#    b) 有 → 直接读取本地文件（快，毫秒级）
#    c) 没有 → 从网络下载 → 保存到本地文件 → 返回数据
#    就像你去图书馆借书：先看书架上有没有，有就直接拿；没有就去采购入库。
#
# 8. 什么是装饰器（Decorator）？
#    装饰器是 Python 的一种语法，用 @ 符号标记，放在函数定义上方。
#    它可以在不修改函数代码的前提下，给函数"附加"额外功能。
#    本文件中 @staticmethod 是一个内置装饰器，表示"这个方法不需要创建
#    对象实例就能直接调用"。
#
# 9. 什么是 lambda 表达式？
#    lambda 是 Python 中创建"小型匿名函数"的语法。
#    格式：lambda 参数: 返回值
#    例如 lambda: ticker.history() 表示"一个无参数的函数，调用时执行 ticker.history()"。
#    之所以用 lambda 而不是直接传值，是为了"延迟执行"——
#    只有在重试函数内部调用 func() 时，API 请求才真正发生。
#
# 10. 什么是 try-except 语句？
#     try-except 是 Python 的异常处理机制。
#     try 块中的代码如果出错（抛出异常），程序不会崩溃，
#     而是跳到 except 块中执行错误处理代码。
#     格式：
#       try:
#           # 可能出错的代码
#       except SomeError:
#           # 出错后的处理代码
#
# 11. 什么是列表推导式（List Comprehension）？
#     这是 Python 中用一行代码创建列表的简洁写法。
#     格式：[表达式 for 变量 in 可迭代对象 if 条件]
#     例如：[c for c in ["Open","Close"] if c in data.columns]
#     等价于：
#       result = []
#       for c in ["Open","Close"]:
#           if c in data.columns:
#               result.append(c)
#
# 12. 什么是 NaN 和 NaT？
#     NaN = Not a Number（不是数字），表示数值类型的缺失值。
#     NaT = Not a Time（不是时间），表示日期类型的缺失值。
#     重要：NaN 有一个特殊性质——NaN != NaN（它不等于自己）。
#     所以不能用 == 判断，必须用 pd.isna() 函数。
#
# 13. 什么是 ffill 和 bfill？
#     ffill = forward fill（前向填充）：用上一行的有效值来填充当前行的缺失值。
#     bfill = backward fill（后向填充）：用下一行的有效值来填充当前行的缺失值。
#     两者配合使用，可以确保数据中的"空洞"都被填上。
#     示例：[10, NaN, NaN, 40] → ffill → [10, 10, 10, 40] → bfill → [10, 10, 10, 40]
#
# 14. 什么是 iloc 和 loc？
#     - df.iloc[行号]：按"整数位置"取行（从 0 开始计数）。
#       如 df.iloc[0] 取第一行，df.iloc[-1] 取最后一行。
#     - df.loc[标签]：按"标签"（行索引名或列名）取数据。
#       如 df.loc[:, 'Close'] 取 Close 列的所有行。
#     在本文件中主要用 df["列名"] 来访问列，这等价于 df.loc[:, "列名"]。
#
# 15. 什么是 datetime 和 Timestamp？
#     datetime 是 Python 标准库的日期时间类。
#     Timestamp 是 pandas 的日期时间类（功能更强大）。
#     两者都可以用 strftime("%Y-%m-%d") 格式化为字符串如 "2024-01-15"。
#
# 16. 什么是 logging（日志）？
#     logging 是 Python 标准的日志库。比 print() 更专业：
#     - 可以设置日志级别（DEBUG < INFO < WARNING < ERROR）
#     - 自动附带时间戳和来源模块名
#     - 可以输出到文件而不仅仅是屏幕
#     本文件用 logger.warning() 记录"被限流了"这种警告信息。
#
# 17. 什么是 Annotated 类型注解？
#     Annotated[str, "说明文字"] 是 Python 的类型提示增强工具。
#     在类型检查器眼中，它仍然是 str 类型；
#     但"说明文字"可以告诉代码阅读者这个参数的含义。
#     LangChain 框架会读取这些说明文字，用作 LLM（大语言模型）的工具描述。
#
# 18. 什么是 N+1 查询问题？
#     如果你要查 N 个日期的指标，每个日期都单独发一次网络请求，
#     就需要发 N 次请求。这叫"N+1 问题"，非常慢。
#     解决方案：一次性获取所有数据（1 次请求），然后在本地筛选。
#     本文件的 _get_stock_stats_bulk 和 get_stock_stats 都采用了批量获取策略。
# =============================================================================
#
# 模块名称：stockstats_utils.py
# 模块功能：股票统计指标工具模块
# 描述：
#   本模块提供了基于 Yahoo Finance 和 stockstats 库的股票技术指标计算功能。
#   它封装了数据获取、缓存、清洗和指标计算的全流程，并实现了针对 Yahoo Finance
#   API 频率限制的指数退避重试机制。
#
# 核心依赖库说明：
#   - yfinance: Yahoo Finance 的 Python 封装库，用于获取股票历史价格数据。
#   - stockstats: 基于 pandas DataFrame 的股票技术指标计算库，可以直接在
#     DataFrame 上添加各种技术指标列（如 RSI、MACD、布林带等）。
#     使用方式：wrap(data) 将普通 DataFrame 包装为 StockDataFrame，
#     然后通过 df['indicator_name'] 即可触发指标计算。
#
# 设计模式说明：
#   - 静态方法模式（Static Method Pattern）：StockstatsUtils 类使用
#     @staticmethod 装饰器，表示方法不依赖类实例，可以直接通过类名调用。
#   - 重试模式（Retry Pattern）：yf_retry 函数实现了指数退避重试，
#     当 API 请求被限流时自动等待并重试。
#   - 缓存模式（Cache-Aside Pattern）：将获取的股票数据缓存到本地 CSV 文件，
#     下次请求时先从缓存读取，避免重复的网络请求。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# 每一行 import 都会在程序启动时执行，把外部库的功能加载进来。
# ---------------------------------------------------------------------------

# time 模块：Python 标准库，提供时间相关的函数。
# 这里主要用于 time.sleep(秒数) 函数——让程序暂停执行指定的秒数。
# 在"指数退避重试"中，我们需要在重试前暂停一会儿，等服务器恢复。
# logging 模块：Python 标准的日志记录库。
# 相比 print()，logging 提供了日志级别（DEBUG/INFO/WARNING/ERROR）、
# 时间戳、模块名等更丰富的日志信息，更适合生产环境使用。
# 用法：logger = logging.getLogger(__name__) 创建日志记录器，
#       然后用 logger.warning("消息") 记录警告级别的日志。
import logging

# os 模块：Python 标准库，用于操作系统相关的操作。
# 这里主要用于：
#   - os.path.exists(路径)：检查文件或目录是否存在
#   - os.path.join(目录, 文件名)：跨平台拼接路径（Windows 用 \，Linux 用 /）
#   - os.makedirs(目录, exist_ok=True)：创建目录（已存在不报错）
import os
import random
import time

# typing.Annotated：Python 的类型注解增强工具。
# 用法：Annotated[基础类型, "说明文字"]
# 例如 Annotated[str, "ticker symbol"] 在类型检查器看来还是 str，
# 但"ticker symbol"这个说明文字会被 LangChain 框架读取，
# 作为传递给 LLM（大语言模型）的工具描述信息。
from typing import Annotated

# pandas（别名 pd）：Python 数据分析核心库。
# pandas 是本项目中最重要的数据处理库。
# 核心数据结构：
#   - DataFrame：二维表格（类似 Excel 表），有行有列
#   - Series：一维序列（类似 Excel 的一列）
# 常用操作：
#   - pd.read_csv("文件路径")：从 CSV 文件读取数据为 DataFrame
#   - pd.to_datetime(列)：将字符串列转为日期类型
#   - pd.to_numeric(列)：将列转为数值类型
#   - pd.Timestamp.today()：获取今天的日期
#   - pd.DateOffset(years=15)：表示 15 年的时间偏移量
# 这里用 import pandas as pd 给 pandas 起了简写别名 pd，
# 后面写 pd.read_csv() 就等于写 pandas.read_csv()。
import pandas as pd

# yfinance（别名 yf）：Yahoo Finance 的 Python 封装库。
# 用于获取股票的历史价格数据、基本面数据等。
# 主要用法：
#   - yf.download("AAPL", start="2024-01-01")：批量下载股票历史数据
#   - yf.Ticker("AAPL")：创建一个代表苹果股票的对象
# 这里起别名 yf，后面写 yf.download() 就等于写 yfinance.download()。
import yfinance as yf

# stockstats.wrap：stockstats 库的包装函数。
# 它的作用：把普通的 pandas DataFrame 包装成 StockDataFrame。
# StockDataFrame 是 stockstats 的核心类，它的特殊之处在于：
# 当你访问一个"指标列"（如 df['rsi']）时，它会自动帮你计算 RSI 指标，
# 不需要你手动调用任何计算函数。这种机制叫"惰性计算"（Lazy Evaluation）——
# 只有在你真正访问指标时才进行计算，避免浪费资源。
from stockstats import wrap

# YFRateLimitError: 这是 yfinance 库在遇到 HTTP 429（请求过多）响应时抛出的异常类。
# 当我们发送请求太快、超过 Yahoo Finance 的速率限制时，yfinance 就会抛出这个异常。
# 我们需要在重试函数中捕获这个特定异常，决定是否等待后重试。
# 语法解释：从 yfinance 包的 exceptions 子模块中导入 YFRateLimitError 类。
from yfinance.exceptions import YFRateLimitError

# 从同目录下的 config 模块导入 get_config 函数。
# 语法解释：
#   - 点号 "." 表示"相对导入"，指从"当前包"（即 dataflows 目录）中导入
#   - 从 .config 导入表示从 dataflows/config.py 文件中导入
# get_config() 函数返回一个全局配置字典，包含数据缓存目录路径等信息。
# 运行时示例：config = {"data_cache_dir": "data", "data_vendors": {...}, ...}
from .config import get_config

# ---------------------------------------------------------------------------
# 日志记录器
# ---------------------------------------------------------------------------

# 创建模块级别的日志记录器。
# __name__ 是 Python 的内置变量，表示当前模块的名称。
# 如果本模块被导入为 tradingagents.dataflows.stockstats_utils，
# 那么 __name__ 的值就是 "tradingagents.dataflows.stockstats_utils"。
# 使用 __name__ 可以方便地在日志中追踪消息来源——
# 看到日志就知道是哪个模块输出的。
# 运行时示例：logger.name 的值为 "tradingagents.dataflows.stockstats_utils"
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 函数：reset_yfinance_session（重置 yfinance 单例会话）
# ---------------------------------------------------------------------------
def reset_yfinance_session():
    """
    重置 yfinance 的全局单例会话，解决限流状态缓存问题。

    yfinance 内部使用 YfData 单例（SingletonMeta），持有全局唯一的
    HTTP 会话（session）和 cookie。当触发 Yahoo Finance 的限流后，
    这个会话会缓存限流状态，导致后续所有请求都返回限流错误。

    此函数通过清除单例实例来强制创建新的会话，从而绕过之前的限流状态。

    调用时机：
      - 分析开始前（main.py 或 run_webui.py）
      - 连续多次限流后
    """
    try:
        # 清除 YfData 单例实例
        # yfinance 的 YfData 类使用 SingletonMeta 元类，
        # _instances 字典存储了所有单例实例
        from yfinance.data import YfData
        YfData._instances.clear()
        logger.info("yfinance session reset successfully")
    except Exception as e:
        logger.debug(f"Failed to reset yfinance session: {e}")


# ---------------------------------------------------------------------------
# 函数：configure_yfinance_proxy（配置 yfinance 代理）
# ---------------------------------------------------------------------------
def configure_yfinance_proxy(proxy_url: str = None):
    """
    为 yfinance 配置 HTTP 代理。

    当在中国大陆网络环境下访问 Yahoo Finance 时，通常需要代理。
    此函数通过 yfinance 内置的 YfConfig 接口设置代理。

    参数：
      proxy_url: 代理地址，如 "http://127.0.0.1:7890"
                 如果为 None，则从环境变量 HTTPS_PROXY 或 HTTP_PROXY 读取

    调用时机：
      - 分析开始前（main.py 或 run_webui.py）
    """
    if proxy_url is None:
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("YFINANCE_PROXY")

    if proxy_url:
        try:
            from yfinance.config import YfConfig
            YfConfig.network.proxy = proxy_url
            reset_yfinance_session()  # 代理变更后需要重置会话
            logger.info(f"yfinance proxy configured: {proxy_url}")
        except Exception as e:
            logger.warning(f"Failed to configure yfinance proxy: {e}")


# ---------------------------------------------------------------------------
# 函数：yf_retry（指数退避重试包装器）
# ---------------------------------------------------------------------------
def yf_retry(func, max_retries=1, base_delay=2.0):
    """
    为 Yahoo Finance API 调用添加指数退避重试机制。

    【调用时机】
      当代码需要调用 Yahoo Finance API（如下载股价数据、获取基本面信息）时，
      不直接调用 API，而是把调用包装成一个无参数函数传给 yf_retry()。
      yf_retry 会先尝试执行，如果被限流就自动等待重试。

    【输入是什么】
      func: 一个无参数的可调用对象（通常使用 lambda 表达式包装）。
        【重要】这里使用 lambda 的原因：yfinance 的 API 调用涉及网络请求，
        如果直接传入调用结果（如 ticker.history()），会在传入前就执行一次。
        使用 lambda 可以"延迟执行"——只有在 func() 被调用时才真正执行 API 请求。
        举例：
          # 错误写法：data 会在传参时就执行一次 API 请求
          data = yf_retry(ticker.history(start="2024-01-01"))
          # 正确写法：用 lambda 包装，延迟到 yf_retry 内部才执行
          data = yf_retry(lambda: ticker.history(start="2024-01-01"))
      max_retries (int): 最大重试次数，默认 3 次。
      base_delay (float): 基础等待时间（秒），默认 2.0 秒。
        实际等待时间 = base_delay * (2^attempt)，即指数增长。

    【输出是什么】
      返回 func() 的执行结果（通常是 pandas DataFrame 或字典）。

    【输出被谁使用】
      被 y_finance.py 中的各函数调用，如 get_YFin_data_online、
      get_fundamentals、get_balance_sheet 等。

    【运行时数据流示例】
      假设调用 yf_retry(lambda: yf.download("AAPL", start="2024-01-01")):
      - 第 0 次尝试（attempt=0）：调用 func() → 可能遇到 YFRateLimitError
      - 等待 2.0 * (2^0) = 2.0 秒
      - 第 1 次尝试（attempt=1）：再次调用 func() → 仍然被限流
      - 等待 2.0 * (2^1) = 4.0 秒
      - 第 2 次尝试（attempt=2）：再次调用 func() → 成功！返回 DataFrame
      日志输出：[WARNING] Yahoo Finance rate limited, retrying in 2s (attempt 1/3)

    关键概念：
      - 指数退避（Exponential Backoff）：
        一种重试策略，每次重试的等待时间呈指数增长。
        例如：第 0 次重试等待 2 秒，第 1 次等待 4 秒，第 2 次等待 8 秒。
        这样可以在避免对服务器造成过大压力的同时，给服务器足够的时间恢复。
        公式：delay = base_delay * (2 ** attempt)

      - YFRateLimitError：
        yfinance 在收到 HTTP 429（Too Many Requests）响应时抛出此异常。
        这意味着我们发送请求太快，超过了 Yahoo Finance 的速率限制。
        其他类型的异常（如网络错误）不会触发重试，会直接向上传播。

      - range(max_retries + 1)：
        range(4) 生成 [0, 1, 2, 3]，即循环 4 次。
        其中第一次（attempt=0）是正常尝试，后面 3 次是重试。
    """
    # range(max_retries + 1) 生成从 0 到 max_retries 的整数序列
    # 例如 max_retries=3 时，range(4) 生成 [0, 1, 2, 3]
    # attempt 变量在每次循环中依次取这些值
    #
    # 退避策略：base_delay * 2^attempt + 随机抖动
    # WHY 加抖动：多个分析师线程同时收到 429 时，若都按同一个确定值等待，
    # 会在同一时刻再次同时发起请求（惊群），反而更容易继续被限流。
    # WHY 只在首次重试时重置会话：reset_yfinance_session() 会清空**整个进程**
    # 的 Yahoo cookie jar，每次重试都调用会让并发的其它请求一起失效。
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1.0)
                logger.warning(
                    "Yahoo Finance rate limited, retrying in %.1fs (attempt %s/%s)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                if attempt == 0:
                    reset_yfinance_session()
                time.sleep(delay)
            else:
                raise
        except (ConnectionError, TimeoutError, OSError) as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1.0)
                logger.warning(
                    "Yahoo Finance network error (%s), retrying in %.1fs (attempt %s/%s)",
                    type(e).__name__,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                if attempt == 0:
                    reset_yfinance_session()
                time.sleep(delay)
            else:
                raise
        except Exception as e:
            # yfinance 可能将 DNS 解析失败等错误包装为 YFRateLimitError
            # 但某些版本也会直接抛出其他异常类型
            # 这里的兜底捕获确保任何异常都触发重试和降级
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1.0)
                logger.warning(
                    "Yahoo Finance error (%s: %s), retrying in %.1fs (attempt %s/%s)",
                    type(e).__name__,
                    e,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                if attempt == 0:
                    reset_yfinance_session()
                time.sleep(delay)
            else:
                raise


# ---------------------------------------------------------------------------
# 函数：_clean_dataframe（数据清洗）
# ---------------------------------------------------------------------------
def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """
    清洗股票数据 DataFrame，使其符合 stockstats 的要求。

    【调用时机】
      在从 CSV 缓存或网络下载获取原始数据后、用 stockstats 计算指标之前调用。
      stockstats 要求 DataFrame 有标准的日期列和数值列，不能有缺失值或错误格式。

    【输入是什么】
      data (pd.DataFrame): 原始股票数据，通常来自 yfinance 下载或 CSV 缓存。
      运行时示例（data 的样子）：
           Date        Open    High    Low     Close   Volume
      0    2024-01-02  187.15  188.44  183.89  185.64  82488700
      1    2024-01-03  184.22  185.88  183.43  184.25  58414500
      注意：此时 Date 列可能是字符串类型，需要转换为日期类型。

    【输出是什么】
      pd.DataFrame: 清洗后的数据。
      运行时示例（返回值的样子，Date 已转为日期类型，缺失值已填充）：
           Date        Open    High    Low     Close   Volume
      0    2024-01-02  187.15  188.44  183.89  185.64  82488700
      1    2024-01-03  184.22  185.88  183.43  184.25  58414500

    【输出被谁使用】
      被 _get_stock_stats_bulk() 和 StockstatsUtils.get_stock_stats() 调用，
      清洗后的数据会被传给 stockstats.wrap() 进行技术指标计算。

    清洗步骤：
      1. 将 Date 列转换为 datetime 类型，无效值转为 NaT（Not a Time）。
      2. 删除 Date 为空的行（无效行）。
      3. 将价格列（Open/High/Low/Close/Volume）转换为数值类型。
      4. 删除 Close 列为空的行（没有收盘价的数据没有意义）。
      5. 使用前向填充和后向填充填补缺失的数值。

    关键概念：
      - pd.to_datetime(..., errors="coerce")：
        将数据转换为 datetime 类型。errors="coerce" 表示遇到无法转换的值时，
        将其设为 NaT（Not a Time），而不是抛出异常。
        例如：pd.to_datetime("2024-01-15") → Timestamp('2024-01-15')
              pd.to_datetime("abc", errors="coerce") → NaT

      - list comprehension（列表推导式）：
        [c for c in [...] if c in data.columns] 是 Python 的列表推导式语法。
        它等价于：
          result = []
          for c in ["Open", "High", "Low", "Close", "Volume"]:
              if c in data.columns:
                  result.append(c)
        列表推导式是 Python 中一种简洁高效的创建列表的方式。

      - pd.to_numeric(..., errors="coerce")：
        将数据转换为数值类型。errors="coerce" 表示无法转换的值设为 NaN。
        例如：pd.to_numeric("123.45") → 123.45
              pd.to_numeric("abc", errors="coerce") → NaN

      - .ffill() 和 .bfill()：
        ffill (forward fill)：用前一个有效值填充缺失值（向下填充）。
        bfill (backward fill)：用后一个有效值填充缺失值（向上填充）。
        两者结合使用可以填补数据中的空洞。
        示例：[10, NaN, NaN, 40]
              .ffill() → [10, 10, 10, 40]（用前面的值填充）
              .bfill() → [10, 10, 40, 40]（用后面的值填充）
              .ffill().bfill() → [10, 10, 10, 40]（先前后后，确保填满）

      - .apply() 方法：
        DataFrame.apply(函数) 会对每一列应用指定的函数。
        这里 data[price_cols].apply(pd.to_numeric, errors="coerce")
        表示对 price_cols 中的每一列都执行 pd.to_numeric 转换。
    """
    # 步骤1：将 Date 列转换为 datetime 类型
    # data["Date"] 选中名为 "Date" 的列（一个 Series）
    # pd.to_datetime() 把字符串转为 pandas 的 Timestamp 日期对象
    # errors="coerce" 表示无效值转为 NaT，避免转换失败导致程序崩溃
    # 运行时示例：data["Date"] 转换前可能是 "2024-01-02"（字符串）
    #             转换后变成 Timestamp('2024-01-02')（日期对象）
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    # 步骤2：删除 Date 为 NaT 的行（无效行）
    # dropna() 删除含有缺失值的行
    # subset=["Date"] 表示只检查 Date 列，如果 Date 为 NaT 就删掉该行
    data = data.dropna(subset=["Date"])

    # 步骤3：找出 DataFrame 中实际存在的价格列
    # 列表推导式：遍历 ["Open", "High", "Low", "Close", "Volume"] 这 5 个列名，
    # 只保留那些在 data.columns 中确实存在的列名。
    # 这是因为不同数据源可能列名略有不同（如有的没有 "Volume" 列）。
    # 运行时示例：如果 data 有全部 5 列，price_cols = ["Open", "High", "Low", "Close", "Volume"]
    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    # 将价格列转换为数值类型（确保没有字符串混入）
    # .apply(pd.to_numeric, errors="coerce") 对 price_cols 中每一列执行数值转换
    # errors="coerce" 把无法转换的值设为 NaN
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    # 步骤4：删除没有收盘价的行（这些行的数据不完整）
    # Close（收盘价）是最重要的价格数据，如果没有收盘价，整行数据没有意义
    data = data.dropna(subset=["Close"])
    # 步骤5：用前向填充和后向填充填补缺失值
    # 先 .ffill()：用前一行的值填充当前行的 NaN
    # 再 .bfill()：用后一行的值填充剩余的 NaN（处理数据开头没有前值的情况）
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


# ---------------------------------------------------------------------------
# 类：StockstatsUtils
# ---------------------------------------------------------------------------
class StockstatsUtils:
    """
    股票统计工具类，提供技术指标计算的静态方法。

    设计说明：
      使用 @staticmethod 而非普通实例方法，因为：
      1. 这些方法不依赖类的实例状态（不需要 self）。
      2. 可以直接通过类名调用，如 StockstatsUtils.get_stock_stats(...)，
         无需创建实例对象（不需要写 stockstats = StockstatsUtils() 再调用）。
      3. 这体现了"无状态工具类"的设计思想——方法之间没有共享状态。

    【调用时机】
      当系统需要获取某个股票在某一天的技术指标值时调用。
      通常由 y_finance.py 中的 get_stockstats_indicator() 函数调用，
      后者又被 get_stock_stats_indicators_window() 调用。

    【运行时数据流示例】
      调用 StockstatsUtils.get_stock_stats("AAPL", "rsi", "2024-01-15"):
      1. 读取配置，确定缓存目录（如 "data/"）
      2. 计算日期范围：start = 2009-01-15, end = 2024-07-08（15年范围）
      3. 检查缓存文件 "data/AAPL-YFin-data-2009-01-15-2024-07-08.csv"
      4. 有缓存 → 直接读取；无缓存 → 从 Yahoo Finance 下载并保存
      5. 清洗数据（_clean_dataframe）
      6. 用 stockstats 包装：df = wrap(data)
      7. 触发 RSI 计算：df["rsi"]
      8. 查找 2024-01-15 对应的行，返回 RSI 值（如 55.23）
    """

    # @staticmethod 是 Python 装饰器，表示这个方法不需要实例（self）。
    # 调用方式：StockstatsUtils.get_stock_stats(...)（直接用类名调用）
    # 而不是：obj = StockstatsUtils(); obj.get_stock_stats(...)
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        """
        获取指定股票在指定日期的技术指标值。

        参数：
          symbol (str): 股票代码（如 "AAPL"、"TSLA"）。
          indicator (str): 技术指标名称（如 "rsi"、"macd"、"close_50_sma"）。
          curr_date (str): 目标日期，格式 "YYYY-MM-DD"。

        返回值：
          指标值（数值）或错误信息字符串（如 "N/A: Not a trading day"）。

        工作流程：
          1. 获取配置信息。
          2. 计算数据获取的日期范围（从15年前到今天）。
          3. 检查本地缓存，如果存在则直接读取；否则从 Yahoo Finance 下载并缓存。
          4. 清洗数据。
          5. 用 stockstats 计算指标。
          6. 在目标日期中查找指标值并返回。

        关键概念：
          - pd.DateOffset(years=15)：pandas 的日期偏移量，表示 15 年的时间跨度。
            用于计算从今天往前推 15 年的日期。
          - os.makedirs(..., exist_ok=True)：创建目录，exist_ok=True 表示
            如果目录已存在也不会报错，避免重复创建导致的异常。
          - os.path.join()：跨平台路径拼接方法，自动使用正确的路径分隔符。
            在 Windows 上 os.path.join("data", "file.csv") → "data\\file.csv"
            在 Linux 上 os.path.join("data", "file.csv") → "data/file.csv"
          - stockstats 的惰性计算：df[indicator] 访问指标列时，stockstats
            会自动识别并计算该指标，无需显式调用计算方法。
          - .reset_index()：将 DataFrame 的索引（行标签）转换为普通列。
            yfinance 下载的数据默认以 Date 为索引，reset_index 后 Date 变成普通列。
          - .str.startswith()：pandas 的字符串方法，检查每个字符串是否以指定前缀开头。
            用于日期匹配，因为有时区等原因，日期字符串可能不完全匹配。
        """
        # 获取全局配置（包含数据缓存目录等设置）
        # config 是一个字典，运行时示例：
        # config = {"data_cache_dir": "data", "data_vendors": {"technical_indicators": "yfinance"}, ...}
        config = get_config()

        # 将 A 股代码转换为 Yahoo Finance 格式（如 000001 → 000001.SZ）
        # 避免循环导入，在此处内联转换逻辑
        yf_symbol = symbol.strip().upper()
        if "." not in yf_symbol and yf_symbol.isdigit() and len(yf_symbol) == 6:
            if yf_symbol.startswith("60") or yf_symbol.startswith("68"):
                yf_symbol = f"{yf_symbol}.SS"  # 上海交易所
            else:
                yf_symbol = f"{yf_symbol}.SZ"  # 深圳交易所

        # 获取今天的日期作为数据结束日期
        # pd.Timestamp.today() 返回今天的日期时间（如 Timestamp('2024-07-08 15:30:00')）
        today_date = pd.Timestamp.today()
        # 将字符串日期（如 "2024-01-15"）转换为 pandas Timestamp 对象
        # pd.to_datetime() 能自动识别多种日期格式并转换
        curr_date_dt = pd.to_datetime(curr_date)

        # 设置数据获取范围：从15年前到今天
        # pd.DateOffset(years=15) 表示 15 年的时间偏移量
        # 运行时示例：如果 today 是 2024-07-08，则 start_date 是 2009-07-08
        end_date = today_date
        start_date = today_date - pd.DateOffset(years=15)  # 往前推 15 年
        # 将日期格式化为字符串，用于文件名和 API 请求
        # .strftime("%Y-%m-%d") 把 Timestamp 格式化为 "YYYY-MM-DD" 字符串
        # 运行时示例：start_date_str = "2009-07-08", end_date_str = "2024-07-08"
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        # 确保数据缓存目录存在
        # os.makedirs(目录, exist_ok=True) 创建目录
        # exist_ok=True 表示如果目录已存在也不报错（否则会抛出 FileExistsError）
        os.makedirs(config["data_cache_dir"], exist_ok=True)

        # 构建缓存文件路径
        # 文件名格式：股票代码-YFin-data-开始日期-结束日期.csv
        # os.path.join() 跨平台拼接路径
        # 运行时示例：os.path.join("data", "AAPL-YFin-data-2009-07-08-2024-07-08.csv")
        #           → "data/AAPL-YFin-data-2009-07-08-2024-07-08.csv"（Linux）
        #           → "data\\AAPL-YFin-data-2009-07-08-2024-07-08.csv"（Windows）
        data_file = os.path.join(
            config["data_cache_dir"],
            f"{yf_symbol}-YFin-data-{start_date_str}-{end_date_str}.csv",
        )

        # ==================== 缓存检查（Cache-Aside 模式）====================
        # 缓存检查：如果本地已有缓存文件，直接读取；否则从网络下载
        if os.path.exists(data_file):
            # 从 CSV 缓存读取
            # pd.read_csv(文件路径) 将 CSV 文件读入为 DataFrame
            # on_bad_lines="skip" 表示遇到格式错误的行时跳过，而不是报错
            # 运行时示例：data 是一个 DataFrame，包含 Date/Open/High/Low/Close/Volume 列
            data = pd.read_csv(data_file, on_bad_lines="skip")
        else:
            # 从 Yahoo Finance 下载数据
            # lambda 表达式创建了一个无参数的可调用对象，用于延迟执行
            # yf.download() 的参数说明：
            #   symbol: 股票代码（如 "AAPL"）
            #   start/end: 数据起止日期（如 "2009-07-08" / "2024-07-08"）
            #   multi_level_index=False: 不使用多级列索引，简化 DataFrame 结构
            #     （yfinance 默认返回多级列名，设为 False 使列名更简洁）
            #   progress=False: 不显示下载进度条
            #   auto_adjust=True: 自动调整价格（处理拆股和分红的影响）
            # 运行时示例：yf.download 返回一个 DataFrame，包含约 3700 行（15年的交易日）
            data = yf_retry(lambda: yf.download(
                yf_symbol,
                start=start_date_str,
                end=end_date_str,
                multi_level_index=False,
                progress=False,
                auto_adjust=True,
            ))
            # 将索引重置为普通列（yfinance 下载的数据默认以 Date 为索引）
            # reset_index() 后，Date 从"索引"变成一个普通列，行号变成索引
            # 转换前：            转换后：
            #   Date        Close     Date        Close
            # 2024-01-02   185.64  0  2024-01-02  185.64
            # 2024-01-03   184.25  1  2024-01-03  184.25
            data = data.reset_index()
            # 保存到 CSV 缓存，index=False 表示不保存行索引（行号）
            # 这样下次请求相同数据时可以直接从本地读取，不用再下载
            data.to_csv(data_file, index=False)

        # 清洗数据（日期转换、缺失值处理等）
        # _clean_dataframe 是本文件中定义的私有函数，负责数据清洗
        data = _clean_dataframe(data)
        # 用 stockstats 的 wrap 函数包装 DataFrame，使其支持技术指标计算
        # wrap() 把普通 DataFrame 包装成 StockDataFrame
        # 包装后，访问 df["rsi"] 等指标列名时，stockstats 会自动计算该指标
        df = wrap(data)
        # 将 Date 列格式化为字符串，方便后续日期匹配
        # .dt.strftime("%Y-%m-%d") 是 pandas 的日期格式化方法
        # .dt 是 pandas 的日期时间访问器（datetime accessor）
        # 转换前：Timestamp('2024-01-02 00:00:00')
        # 转换后："2024-01-02"（字符串）
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        # 将目标日期也格式化为字符串
        # curr_date_dt 是 Timestamp 对象，strftime 转为字符串
        curr_date_str = curr_date_dt.strftime("%Y-%m-%d")

        # 触发 stockstats 计算指标
        # 这行代码的关键作用：访问 df[indicator] 会触发 stockstats 的
        # "惰性计算"机制——stockstats 识别出 "rsi" 是一个技术指标，
        # 自动用历史价格数据计算 RSI，结果会作为新列添加到 df 中。
        # 如果 indicator = "rsi"，这一行执行后 df 中会多出一列 "rsi"
        # 运行时示例：df["rsi"] 的值可能是 [55.23, 48.12, 62.87, ...]
        df[indicator]  # trigger stockstats to calculate the indicator

        # 在 DataFrame 中查找与目标日期匹配的行
        # df["Date"] 获取 Date 列（一个 Series）
        # .str.startswith(curr_date_str) 检查每个日期字符串是否以目标日期开头
        # 返回一个布尔 Series（True/False），用于"布尔索引"筛选行
        # 为什么用 startswith 而不是 ==？因为有时区等问题可能导致不完全匹配
        # 运行时示例：
        #   df["Date"] = ["2024-01-12", "2024-01-15", "2024-01-16"]
        #   curr_date_str = "2024-01-15"
        #   结果：[False, True, False]
        #   df[df["Date"].str.startswith("2024-01-15")] 返回第 2 行
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:  # 找到了匹配的行
            # .empty 是 DataFrame 的属性，如果 DataFrame 没有行则返回 True
            # not matching_rows.empty 表示"有数据"
            # 取第一行的指标值
            # matching_rows[indicator] 获取指标列
            # .values 返回 NumPy 数组（把 Series 转为数组）
            # [0] 取数组的第一个元素
            # 运行时示例：matching_rows["rsi"].values → array([55.23])
            #             [0] → 55.23
            indicator_value = matching_rows[indicator].values[0]
            # WHY（FIX 2）：这里必须显式检查 NaN。
            # 批量路径（y_finance._get_stock_stats_bulk）已经把 NaN 转成 "N/A"，
            # 但单日路径历史上直接把原始值返回，NaN 会被 Python 格式化成
            # 字面量字符串 "nan" 出现在给 LLM 的结果里 —— LLM 会把
            # "nan" 当成一个真实的指标数值（例如价格）来解读。
            # 这里与批量路径保持一致，返回明确的缺失标记。
            if pd.isna(indicator_value):
                return "N/A: 指标值缺失（数据不足或该日无有效行情）"
            return indicator_value
        else:
            # 目标日期不是交易日（周末或节假日，股市不开盘就没有数据）
            # 运行时示例：curr_date = "2024-01-13"（周六），没有交易数据
            return "N/A: Not a trading day (weekend or holiday)"
