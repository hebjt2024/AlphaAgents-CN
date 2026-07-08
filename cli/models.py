# =====================================================================
# 模块：cli/models.py
# 功能：CLI 层的数据模型定义
# 说明：定义命令行界面中使用到的枚举类型和数据模型。
#       使用 Pydantic 的 BaseModel 来进行数据验证，
#       使用 Python 的 Enum 来定义固定的选项集合。
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件为 CLI（命令行界面）层定义数据模型。以下是需要了解的核心概念：
#
# 1. Enum（枚举类型）：
#    - Python 标准库 enum 模块提供的类型，用于创建"一组有名字的常量"。
#    - 作用：防止拼写错误、提高代码可读性、集中管理可选值。
#    - 示例：一周七天可以定义为
#        class Weekday(Enum):
#            MON = 1
#            TUE = 2
#            ...
#      之后使用 Weekday.MON 比直接用数字 1 更清晰。
#    - 本文件中 AnalystType 枚举继承 str 和 Enum，
#      这意味着每个枚举成员既是字符串也是枚举实例，
#      可以直接与字符串比较：AnalystType.MARKET == "market" 返回 True。
#
# 2. Pydantic（数据验证库）：
#    - Pydantic 是 Python 最流行的数据验证库。
#    - 核心类 BaseModel：继承它即可自动获得类型校验、序列化等功能。
#    - 示例：
#        class User(BaseModel):
#            name: str
#            age: int = 0
#      创建 User(name="Alice") 会自动校验 name 是字符串、age 是整数。
#    - 在本文件中 BaseModel 被导入但未使用，为后续扩展预留。
#
# 3. typing 模块（类型注解）：
#    - Python 标准库，提供 List、Optional、Dict 等类型注解工具。
#    - 类型注解（Type Hint）不会影响运行，但能让代码更清晰、IDE 更智能。
#    - List 表示列表类型，Optional 表示值可以为 None，Dict 表示字典类型。
#
# 4. 本文件的运行时数据流：
#    用户在 CLI 中选择分析师 → select_analysts() 返回 [AnalystType.MARKET, ...]
#    → 传递给 TradingAgentsGraph 作为参数 → 分析引擎据此决定运行哪些分析师 Agent。
#
#    数据流示例：
#    用户勾选 "Market Analyst" 和 "News Analyst"
#    → select_analysts() 返回 [AnalystType.MARKET, AnalystType.NEWS]
#    → AnalystType.MARKET.value == "market"（字符串值）
#    → TradingAgentsGraph 接收 ["market", "news"] 作为参数
#    → 后端引擎只运行市场分析师和新闻分析师，跳过社交媒体和基本面分析师
# =====================================================================

# enum 模块：Python 标准库，用于创建枚举类型（一组有名字的常量）
# 枚举可以防止拼写错误，提高代码可读性
# 用法：继承 Enum 类，在类体中定义常量成员
from enum import Enum

# typing 模块：Python 标准库，用于类型注解（Type Hints）
# 类型注解是 Python 3.5+ 的特性，不会影响运行时行为，但能提高代码可读性
# List 表示列表类型，Optional 表示可选值（可以是 None），Dict 表示字典类型
# 示例：def foo(items: List[str]) -> Optional[str]: 表示函数接受字符串列表，返回可选字符串
from typing import List, Optional, Dict

# pydantic 库：一个数据验证库，BaseModel 是其核心类
# 使用 Pydantic 可以自动验证数据类型、提供默认值、序列化/反序列化数据
# 安装方式：pip install pydantic
# BaseModel 基本用法：
#   class MyModel(BaseModel):
#       name: str          # 必填字段，必须是字符串
#       age: int = 0        # 可选字段，默认值为 0
#   obj = MyModel(name="Alice")  # 自动验证类型
# 在本文件中声明但未使用，为后续扩展预留（如将来添加 CLI 参数验证模型）
from pydantic import BaseModel


# =====================================================================
# AnalystType 枚举类：定义分析师类型
# =====================================================================
# 继承 str 和 Enum：
#   - str 继承使其值可以像字符串一样使用（如拼接、比较）
#     这意味着 AnalystType.MARKET == "market" 返回 True
#     也可以直接用于字符串上下文：f"Type: {AnalystType.MARKET}" 输出 "Type: market"
#   - Enum 使其成为枚举类型，每个成员都是该类的单例
#     即 AnalystType.MARKET 是唯一的，不能被实例化覆盖
#
# 运行时示例：
#   selected = [AnalystType.MARKET, AnalystType.NEWS]
#   for analyst in selected:
#       print(analyst.value)  # 输出 "market" 和 "news"
#   print(AnalystType.MARKET == "market")  # 输出 True（因为继承了 str）
# =====================================================================
class AnalystType(str, Enum):
    """
    分析师类型枚举

    定义了四种分析师类型，分别负责不同领域的分析：
    - MARKET: 市场分析（技术面分析，如K线图、趋势线等）
    - SOCIAL: 社交媒体分析（分析社交媒体上的情绪和讨论）
    - NEWS: 新闻分析（分析相关新闻报道的影响）
    - FUNDAMENTALS: 基本面分析（分析公司财务数据、行业地位等）

    使用方式：
        # 在 utils.py 的 select_analysts() 中作为 checkbox 选项的值
        # 在 main.py 中传递给 TradingAgentsGraph 作为参数
        # 在 MessageBuffer.init_for_analysis() 中用于初始化 Agent 状态

    数据流示例：
        用户在 CLI 复选框中勾选 "Market Analyst" 和 "News Analyst"
        → select_analysts() 返回 [AnalystType.MARKET, AnalystType.NEWS]
        → AnalystType.MARKET.value 返回 "market"
        → AnalystType.NEWS.value 返回 "news"
        → 传递给 TradingAgentsGraph(["market", "news"], ...)
        → 分析引擎只运行市场分析师和新闻分析师
    """
    MARKET = "market"          # 市场分析师：分析价格走势、技术指标（MACD、RSI等）
    SOCIAL = "social"          # 社交媒体分析师：分析 Twitter/Reddit 等平台上的舆论情绪
    NEWS = "news"              # 新闻分析师：分析公司新闻、行业新闻对股价的影响
    FUNDAMENTALS = "fundamentals"  # 基本面分析师：分析财务报表、估值指标、行业地位
