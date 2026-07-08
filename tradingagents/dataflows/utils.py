# =============================================================================
# 模块名称：utils.py
# 模块功能：通用工具函数模块
# =============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 经验，请先阅读本节）
# ============================================================================
#
# 【什么是 pandas（pd）？】
#   pandas 是 Python 最流行的数据分析库。
#   它的核心数据结构是 DataFrame（数据表），类似 Excel 表格：
#   - 每列是一个字段（如"开盘价"、"收盘价"）
#   - 每行是一条记录（如某一天的行情）
#   你可以用 pandas 读取 CSV、筛选数据、计算指标、导出文件等。
#   代码中通常用 `import pandas as pd` 引入，pd 是约定俗成的缩写。
#
# 【什么是 datetime？】
#   Python 内置的日期时间处理模块。
#   - date：表示日期（年-月-日），如 date(2026, 7, 7)
#   - datetime：表示日期+时间，如 datetime(2026, 7, 7, 14, 30, 0)
#   - timedelta：表示时间差，如 timedelta(days=7) 表示 7 天
#   股票分析中经常需要处理日期（如"获取过去30天的数据"）。
#
# 【什么是 f-string？】
#   f"..." 是 Python 3.6+ 的字符串格式化语法。
#   在字符串前加 f，就可以用 {变量名} 直接嵌入变量值：
#     name = "AAPL"
#     f"Stock: {name}"  → "Stock: AAPL"
#
# 【什么是 Annotated 类型别名？】
#   Annotated 是 Python typing 模块提供的高级类型工具。
#   语法：Annotated[基础类型, 元数据1, 元数据2, ...]
#   它在基础类型上附加额外说明，运行时仍然是基础类型，但：
#   - 让代码更易读（知道这个参数代表什么）
#   - 让工具（如 LangChain）可以读取元数据生成文档或参数 schema
#
#   例如：Annotated[str, "用户姓名"] 表示"这是个字符串，代表用户姓名"。
#   运行时它就是普通 str，但 IDE 和 LangChain 能利用这段说明。
#
# 【什么是装饰器（Decorator）？】
#   装饰器是 Python 的高级特性，本质是"接收函数、返回函数"的函数。
#   用 @ 语法使用，放在函数/类定义上方：
#     @my_decorator
#     def hello(): ...
#   等价于：hello = my_decorator(hello)
#   装饰器可以在不修改原函数代码的前提下，给它增加额外功能。
#   LangChain 的 @tool 就是一个装饰器，它把普通函数变成"智能体可调用的工具"。
#
# 【什么是"装饰器工厂"（Decorator Factory）？】
#   装饰器工厂是"返回装饰器的函数"——比普通装饰器多一层。
#   普通装饰器：@decorator → decorator(被装饰对象)
#   装饰器工厂：@decorator_factory(参数) → 先调用 factory(参数) 得到装饰器，
#              再用装饰器装饰对象。
#   decorate_all_methods 就是装饰器工厂：你可以传给它不同的装饰器，
#   它返回一个能把该装饰器应用到类所有方法上的类装饰器。
#
# 【什么是闭包（Closure）？】
#   闭包是"定义在函数内部的函数"，它能记住外部函数的变量。
#   即使外部函数已经执行完毕返回了，内部函数仍然能访问外部函数的变量。
#   在 decorate_all_methods 中，内部的 class_decorator 就是一个闭包，
#   它记住了外部传入的 decorator 参数。
#
# 【什么是 callable()？】
#   callable(obj) 检查 obj 是否"可调用"——即是否能加括号调用。
#   函数、方法、类都是可调用的：callable(print) → True
#   数字、字符串不可调用：callable(42) → False
#
# 【什么是 setattr()？】
#   setattr(对象, 属性名字符串, 值) 动态设置对象的属性。
#   等价于 对象.属性名 = 值，但 setattr 可以用字符串指定属性名，
#   所以能在循环中动态修改不同属性。
# ============================================================================
#
# 设计模式说明：
#   - 装饰器模式（Decorator Pattern）：decorate_all_methods 函数是一个
#     "装饰器工厂"（Decorator Factory），它接收一个装饰器并返回一个类装饰器，
#     用于批量为类的所有方法应用同一个装饰器。
#   - 类型别名（Type Alias）：SavePathType 使用 Annotated 创建了一个带有
#     语义说明的类型别名，提高代码可读性。
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：技术分析智能体获取 RSI 指标后保存结果
#
#   第 1 步：获取当前日期
#     today = get_current_date()  → "2026-07-07"
#
#   第 2 步：分析完后保存数据
#     df = pd.DataFrame(...)  # 假设这是获取到的指标数据
#     save_output(df, "RSI指标数据", "/tmp/rsi_aapl.csv")
#     → 内部调用 df.to_csv("/tmp/rsi_aapl.csv")
#     → 打印 "RSI指标数据 saved to /tmp/rsi_aapl.csv"
#
#   第 3 步（装饰器工厂的使用场景）
#     @decorate_all_methods(log_decorator)
#     class DataFetcher:
#         def get_a(self): ...
#         def get_b(self): ...
#     → 等价于给 get_a 和 get_b 都加上了 @log_decorator
#     → 每次调用这些方法都会自动记录日志
#
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------

# os 模块：提供与操作系统交互的功能，如文件路径操作、环境变量读取等。
# 本模块中用于文件路径拼接（但当前代码中未直接使用，可能是为扩展预留）。
import os

# json 模块：用于 JSON 数据的编码和解码。
# 本模块中未直接使用，可能是为扩展预留。
import json

# pandas（别名 pd）：Python 数据分析的核心库。
# 提供 DataFrame（数据表）和 Series（数据列）等数据结构，用于高效处理表格数据。
# 本模块中用于 save_output 函数的类型注解。
import pandas as pd

# datetime 模块：提供日期和时间处理的类和函数。
# date: 表示日期（年、月、日），不包含时间信息。
# timedelta: 表示两个日期/时间之间的差值，用于日期计算。
# datetime: 表示完整的日期时间（年、月、日、时、分、秒）。
from datetime import date, timedelta, datetime

# typing.Annotated: Python 类型注解的高级特性。
# Annotated 允许在类型注解上附加额外的元数据（metadata），
# 语法：Annotated[基础类型, 元数据1, 元数据2, ...]
# 元数据不会影响运行时行为，但可以被类型检查工具或文档生成工具使用。
# 例如：Annotated[str, "用户姓名"] 表示这是一个字符串类型，且代表"用户姓名"。
from typing import Annotated

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

# SavePathType: 自定义类型别名，用于标注文件保存路径参数。
# 语法解析：
#   Annotated[str, "描述文本"] 创建了一个带有元数据说明的字符串类型。
#   这个类型在运行时仍然是普通的 str 类型，但 IDE 和类型检查工具
#   可以利用附加的元数据提供更好的代码提示和文档。
# 用途：在函数签名中使用此类型别名，可以清晰地表达参数的含义。
#
# 【通俗理解】就像给变量贴标签：
#   普通 str："这是个字符串"
#   Annotated[str, "文件保存路径"]："这是个字符串，而且它是文件保存路径"
#   两者在运行时完全一样，但后者让人一眼就知道这个参数是干嘛的。
SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]


# ---------------------------------------------------------------------------
# 函数：save_output
# ---------------------------------------------------------------------------
def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    """
    将 DataFrame 数据保存为 CSV 文件。

    参数：
      data (pd.DataFrame): 要保存的数据（pandas 数据表）。
      tag (str): 数据标签，用于输出日志信息（描述保存的是什么数据）。
      save_path (SavePathType): CSV 文件的保存路径。如果为 None，则不保存。
        默认值为 None。
        【什么是默认值？】在函数定义中 参数=值 表示如果调用时不传这个参数，
        就用默认值。例如 save_output(df, "标签") 不传 save_path，则 save_path=None。

    返回值：
      None: 此函数不返回任何值，只产生副作用（写入文件、打印日志）。
      【什么是副作用（Side Effect）？】函数除了返回值之外对外部产生的影响，
      如修改全局变量、写文件、打印输出等。save_output 的主要作用就是副作用。

    关键概念：
      - pd.DataFrame.to_csv(): pandas 提供的将 DataFrame 导出为 CSV 文件的方法。
        CSV（Comma-Separated Values）是一种通用的表格数据交换格式。
      - f-string（格式化字符串字面量）：
        f"{变量}" 是 Python 3.6+ 引入的字符串格式化语法，比传统的
        format() 或 % 格式化更简洁直观。
        例如：f"Hello {name}" 等价于 "Hello " + str(name)
    """
    # 如果提供了保存路径（非 None、非空字符串）
    # 【Python 真值判断】if save_path: 表示 save_path 为真值时执行
    #   None、空字符串 ""、0、空列表 [] 都是"假值"，其余为"真值"
    if save_path:
        # 将 DataFrame 导出为 CSV 文件
        # to_csv() 是 pandas DataFrame 的方法，把表格数据写成 CSV 文件
        data.to_csv(save_path)
        # 输出保存确认信息（f-string 把 tag 和 save_path 的值嵌入字符串）
        print(f"{tag} saved to {save_path}")  # 输出保存确认信息


# ---------------------------------------------------------------------------
# 函数：get_current_date
# ---------------------------------------------------------------------------
def get_current_date():
    """
    获取当前日期。

    返回值：
      str: 当前日期的字符串表示，格式为 "YYYY-MM-DD"（如 "2026-07-07"）。

    关键概念：
      - date.today(): 获取今天的日期对象（date 类型）。
      - .strftime(): "string format time" 的缩写，将日期/时间对象格式化为字符串。
        格式码说明：
          %Y - 四位年份（如 2026）
          %m - 两位月份（01-12）
          %d - 两位日期（01-31）
    """
    # date.today() 获取今天的日期对象（如 date(2026, 7, 7)）
    # .strftime("%Y-%m-%d") 把日期格式化为 "2026-07-07" 这样的字符串
    return date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 函数：decorate_all_methods（装饰器工厂）
# ---------------------------------------------------------------------------
def decorate_all_methods(decorator):
    """
    装饰器工厂函数：创建一个类装饰器，用于批量为类的所有可调用方法应用装饰器。

    参数：
      decorator: 一个装饰器函数，将被应用到目标类的所有方法上。

    返回值：
      class_decorator: 一个类装饰器函数。

    这是一个"高阶函数"（Higher-Order Function）的典型应用——函数接收函数作为参数，
    并返回一个新的函数。

    调用方式：
      @decorate_all_methods(some_decorator)
      class MyClass:
          def method1(self): ...
          def method2(self): ...

    详细原理说明：
      1. decorate_all_methods(some_decorator) 被调用，返回 class_decorator 函数。
      2. class_decorator(MyClass) 被调用（因为 @ 语法），接收类对象作为参数。
      3. class_decorator 遍历 MyClass 的所有属性，将可调用的方法替换为装饰后的版本。
      4. 返回修改后的类。

    关键概念：
      - 闭包（Closure）：
        class_decorator 是定义在 decorate_all_methods 内部的函数，
        它"捕获"了外部函数的 decorator 参数。即使外部函数已经返回，
        class_decorator 仍然可以访问 decorator。这就是闭包的核心特性。

      - cls.__dict__：
        类的 __dict__ 属性是一个字典，包含了类的所有属性和方法。
        注意：这是类的属性字典，而不是实例的。它只包含直接在类中定义的属性。

      - callable() 内置函数：
        判断一个对象是否可以被调用（即是否是函数、方法、或其他可调用对象）。
        例如：callable(print) 返回 True，callable(42) 返回 False。

      - setattr() 内置函数：
        动态设置对象的属性。语法：setattr(对象, 属性名字符串, 属性值)
        等价于：对象.属性名 = 属性值
        使用 setattr 的优势是可以使用字符串变量来指定属性名，实现动态属性设置。
    """
    # 定义内部函数 class_decorator，它会"捕获"外部的 decorator 参数（闭包）
    def class_decorator(cls):
        """
        内部函数：类装饰器。

        参数：
          cls: 被装饰的类对象（不是实例，是类本身）。

        返回值：
          cls: 修改后的类对象（用装饰器包装了所有方法）。

        迭代过程：
          遍历类的 __dict__ 中的所有属性名和属性值，
          如果属性值是可调用的（即方法），则用传入的装饰器包装它，
          然后用 setattr 将包装后的方法替换回类中。
        """
        # 遍历类的所有属性
        # cls.__dict__ 返回类自身的属性字典（不包含继承来的属性）
        # .items() 返回 (属性名, 属性值) 键值对
        # 例如：[('method1', <function ...>), ('method2', <function ...>), ...]
        for attr_name, attr_value in cls.__dict__.items():
            # 判断该属性是否是可调用的（即是否是方法/函数）
            # callable() 返回 True 表示是函数/方法/类等可调用对象
            if callable(attr_value):
                # 用装饰器包装原始方法，然后重新设置到类上
                # decorator(attr_value) 调用装饰器，返回包装后的新函数
                # setattr(cls, attr_name, ...) 把新函数设置回类的同名属性
                # 等价于 cls.attr_name = decorator(attr_value)，但用 setattr 能用变量名
                setattr(cls, attr_name, decorator(attr_value))
        return cls  # 返回修改后的类

    # 返回类装饰器函数（注意：返回的是函数本身，不是调用结果）
    # 调用 decorate_all_methods(my_dec) 得到的是 class_decorator 函数
    # 然后 Python 用 class_decorator 去装饰类
    return class_decorator  # 返回类装饰器函数


# ---------------------------------------------------------------------------
# 函数：get_next_weekday
# ---------------------------------------------------------------------------
def get_next_weekday(date):
    """
    获取下一个工作日：如果给定日期是周末（周六或周日），则返回下一个周一；
    如果是工作日，则直接返回原日期。

    参数：
      date: 可以是 datetime 对象或格式为 "YYYY-MM-DD" 的字符串。

    返回值：
      datetime: 下一个工作日对应的 datetime 对象。

    关键概念：
      - isinstance() 内置函数：
        检查一个对象是否是某个类型的实例。
        语法：isinstance(对象, 类型)
        例如：isinstance(42, int) 返回 True，isinstance("hello", int) 返回 False。

      - datetime.strptime() 类方法：
        "string parse time" 的缩写，将字符串按照指定格式解析为 datetime 对象。
        格式码与 strftime 相同。
        例如：datetime.strptime("2026-07-07", "%Y-%m-%d") 解析日期字符串。

      - .weekday() 方法：
        返回星期几，0 表示周一，6 表示周日。
        所以 >= 5 表示周六（5）或周日（6）。

      - timedelta 日期计算：
        timedelta(days=n) 表示 n 天的时间差。
        date + timedelta(days=7) 表示 7 天后的日期。
        这里用于计算从当前日期到下一个周一需要的天数。

      计算逻辑：
        如果今天是周六(5)，需要加 2 天到周一：7 - 5 = 2
        如果今天是周日(6)，需要加 1 天到周一：7 - 6 = 1
        公式：days_to_add = 7 - date.weekday()
    """
    # 如果传入的是字符串，先转换为 datetime 对象
    # isinstance(date, datetime) 检查 date 是否已经是 datetime 类型
    # 如果不是（比如是字符串 "2026-07-07"），就需要用 strptime 解析
    if not isinstance(date, datetime):
        # strptime 把字符串按 "%Y-%m-%d" 格式解析成 datetime 对象
        # 例如："2026-07-07" → datetime(2026, 7, 7, 0, 0, 0)
        date = datetime.strptime(date, "%Y-%m-%d")

    # 判断是否是周末（周六=5，周日=6）
    # .weekday() 返回 0-6：0=周一, 1=周二, ..., 5=周六, 6=周日
    if date.weekday() >= 5:
        # 计算到下一个周一需要的天数
        # 周六(5)：7 - 5 = 2 天后是周一
        # 周日(6)：7 - 6 = 1 天后是周一
        days_to_add = 7 - date.weekday()
        # 添加天数，得到下一个周一的日期
        # timedelta(days=n) 表示 n 天的时间差
        # date + timedelta(days=2) 得到 2 天后的日期
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        # 已经是工作日（周一到周五），直接返回原日期
        return date
