# ============================================================================
# 数据获取测试脚本
# 用于测试 yfinance 数据源的性能和功能
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件是一个测试脚本，用于验证 yfinance 数据获取功能的正确性和性能。
# 以下是需要了解的核心概念：
#
# 1. 测试脚本的作用：
#    - 测试脚本是独立的小程序，用于验证某个功能是否正常工作。
#    - 与主程序不同，测试脚本不参与生产流程，只在开发/调试时运行。
#    - 本脚本测试：数据获取函数能否正确获取技术指标数据，以及执行速度如何。
#
# 2. time 模块（时间测量）：
#    - Python 标准库，用于测量时间。
#    - time.time() 返回当前时间的时间戳（从 1970-01-01 开始的秒数，浮点数）。
#    - 测量代码执行时间的模式：
#        start = time.time()   # 记录开始时间
#        # ... 要测量的代码 ...
#        end = time.time()     # 记录结束时间
#        print(f"耗时: {end - start:.2f} 秒")  # 计算差值
#
# 3. yfinance 库：
#    - 一个 Python 库（pip install yfinance），用于从 Yahoo Finance 获取股票数据。
#    - 免费，不需要 API 密钥。
#    - 支持获取：股票价格、技术指标、财务报表、公司信息等。
#    - 本项目的 dataflows/y_finance.py 模块封装了 yfinance 的调用。
#
# 4. 技术指标（Technical Indicators）：
#    - 技术指标是通过对股票价格和成交量进行数学计算得出的数值。
#    - 常见指标：
#      * MACD（移动平均线趋同/背离）：判断趋势方向和动量
#      * RSI（相对强弱指数）：判断超买/超卖
#      * 布林带（Bollinger Bands）：判断价格波动范围
#    - 本脚本测试获取 MACD 指标。
#
# 5. 模块导入与别名（as 关键字）：
#    - 使用 as 可以给导入的名称起别名，使代码更简洁。
#    - 示例：from module import long_function_name as short_name
#    - 本脚本中大量使用 as 来简化函数名。
#
# 6. 本文件的运行时数据流：
#    用户运行 python test.py
#    → 导入 yfinance 数据获取函数
#    → start_time = time.time() 记录开始时间
#    → get_stock_stats_indicators_window("AAPL", "macd", "2024-11-01", 30)
#      → yfinance 从 Yahoo Finance 获取 AAPL 的历史价格
#      → 计算 MACD 指标（基于收盘价）
#      → 返回基准日期前 30 天的 MACD 数据（字符串格式）
#    → end_time = time.time() 记录结束时间
#    → 打印执行耗时、结果长度和具体内容
# ============================================================================

# time 模块：Python 标准库，用于测量代码执行时间
# time.time() 返回当前时间的 Unix 时间戳（从 1970-01-01 00:00:00 开始的秒数）
import time

from tradingagents.dataflows.y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,  # 获取资产负债表（别名简化）
)
from tradingagents.dataflows.y_finance import (
    get_cashflow as get_yfinance_cashflow,  # 获取现金流量表（别名简化）
)
from tradingagents.dataflows.y_finance import (
    get_income_statement as get_yfinance_income_statement,  # 获取利润表（别名简化）
)
from tradingagents.dataflows.y_finance import (
    get_insider_transactions as get_yfinance_insider_transactions,  # 获取内部交易（别名简化）
)

# 从 dataflows 层导入多个数据获取函数
# 这些函数封装了对 Yahoo Finance 数据的访问
# 使用 as 关键字给函数起别名，使代码更简洁易读
from tradingagents.dataflows.y_finance import (
    get_stock_stats_indicators_window,  # 获取技术指标（带时间窗口）
    get_YFin_data_online,  # 获取在线股票数据（日线等）
)

# ----------------------------------------------------------------------------
# 测试技术指标获取性能
# ----------------------------------------------------------------------------

# print() 是 Python 内置函数，向终端输出文本
# 这行输出测试标题，提示用户正在测试什么功能
print("Testing optimized implementation with 30-day lookback:")

# 记录开始时间
# time.time() 返回当前时间的浮点秒数
# 例如 1700000000.123（表示 2023-11-14 22:13:20.123）
start_time = time.time()

# 调用 get_stock_stats_indicators_window 获取苹果股票（AAPL）的 MACD 指标
# 参数说明：
#   "AAPL"：苹果公司的股票代码
#   "macd"：要获取的技术指标名称（MACD = 移动平均线趋同/背离）
#   "2024-11-01"：基准日期，获取该日期之前的数据
#   30：回溯天数，即获取基准日期前 30 天的数据
# 返回值：字符串格式的 MACD 指标数据
# MACD 指标包含三部分：
#   - MACD 线（快线）：12日EMA - 26日EMA
#   - 信号线（慢线）：MACD 线的 9日EMA
#   - 柱状图：MACD 线与信号线的差值
result = get_stock_stats_indicators_window("AAPL", "macd", "2024-11-01", 30)

# 记录结束时间
end_time = time.time()

# 输出执行耗时（保留两位小数）
# f"..." 是 f-string（格式化字符串），{表达式} 中的表达式会被计算并替换
# :.2f 表示格式化为浮点数，保留两位小数
# 例如 end_time - start_time = 1.234567 → "1.23 seconds"
print(f"Execution time: {end_time - start_time:.2f} seconds")

# 输出结果的长度（字符数），用于判断数据量是否合理
# len() 是 Python 内置函数，返回字符串/列表等的长度
# 这有助于验证：如果返回 0 个字符，说明获取数据失败
print(f"Result length: {len(result)} characters")

# 输出获取到的 MACD 指标数据
# result 是字符串格式，包含日期和对应的 MACD 值
print(result)
