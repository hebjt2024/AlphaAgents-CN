# =====================================================================
# 模块：cli/utils.py
# 功能：CLI 层的用户交互工具函数
# 说明：提供所有与用户交互的终端界面功能。
#       使用 questionary 库实现交互式问答（选择、输入、复选框等），
#       使用 Rich 库实现彩色终端输出。
#
# 核心概念：
#   - questionary：一个 Python 库，用于在终端中创建交互式问答界面。
#     支持多种问题类型：text（文本输入）、select（单选）、checkbox（多选）等。
#   - questionary.Style：用于自定义问答界面的颜色和样式。
#   - Rich Console：用于在终端中输出彩色文本。
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件提供了 CLI 中所有与用户交互的函数。以下是需要了解的核心概念：
#
# 1. questionary 库（交互式命令行问答）：
#    - questionary 是一个 Python 库，用于在终端中创建交互式问答界面。
#    - 安装方式：pip install questionary
#    - 支持的问题类型：
#      * questionary.text("问题") → 文本输入（用户输入一行文字）
#      * questionary.select("问题", choices=[...]) → 单选列表（用方向键选择一项）
#      * questionary.checkbox("问题", choices=[...]) → 多选复选框（用空格勾选多项）
#      * questionary.confirm("问题") → 是/否确认（Y/N）
#    - 每个问题对象调用 .ask() 方法执行问答，返回用户的选择
#    - questionary.Choice(display, value)：
#      * display 是显示给用户的文本
#      * value 是选中后返回的实际值（可以与 display 不同）
#    - questionary.Style([(元素, 样式), ...])：自定义问答界面样式
#      * "fg:green" 表示前景色（文字颜色）为绿色
#      * "noinherit" 表示不继承父级样式
#
# 2. Rich 库（终端美化）：
#    - Rich 是 Python 的终端美化库（pip install rich）。
#    - Console 是 Rich 的核心输出类，用于向终端输出格式化内容。
#    - Rich 标记语法：[颜色]文本[/颜色]
#      例如 [red]错误信息[/red] 会显示红色文字
#    - console.print() 输出格式化文本，print() 输出普通文本
#
# 3. Python 概念：
#    - lambda 表达式：匿名函数，语法 lambda 参数: 返回值
#      例如 lambda x: len(x) > 0 表示接收 x，返回 x 长度是否大于 0
#    - 列表推导式（List Comprehension）：[表达式 for 变量 in 可迭代对象]
#      例如 [x*2 for x in range(3)] 生成 [0, 2, 4]
#    - 元组（tuple）：不可变序列，用 (a, b) 表示
#    - f-string：格式化字符串，f"Hello {name}" 中的 {name} 会被替换
#    - .strip()：去除字符串首尾空格
#    - .upper()：将字符串转为大写
#    - .lower()：将字符串转为小写
#    - exit(1)：退出程序，1 表示异常退出（0 表示正常）
#
# 4. 本文件的运行时数据流：
#    用户启动 CLI → cli/main.py 的 get_user_selections() 被调用
#    → 逐步调用本文件的各交互函数：
#      get_ticker() → 用户输入 "NVDA" → 返回 "NVDA"
#      get_analysis_date() → 用户输入 "2024-05-10" → 返回 "2024-05-10"
#      select_analysts() → 用户勾选市场+新闻 → 返回 [AnalystType.MARKET, AnalystType.NEWS]
#      select_research_depth() → 用户选择 "Deep" → 返回 5
#      select_llm_provider() → 用户选择 "OpenAI" → 返回 ("OpenAI", "https://api.openai.com/v1")
#      select_shallow_thinking_agent("openai") → 用户选择 "GPT-5 Mini" → 返回 "gpt-5-mini"
#      select_deep_thinking_agent("openai") → 用户选择 "GPT-5.2" → 返回 "gpt-5.2"
#    → 所有选择汇总成配置字典，传递给分析引擎
# =====================================================================

# questionary 库：在终端中创建交互式问答界面
# 安装方式：pip install questionary
# 支持问题类型：text（文本输入）、select（单选列表）、checkbox（多选框）、
#              confirm（确认）、password（密码输入）等
# 基本用法：
#   answer = questionary.text("你的名字是？").ask()  # 等待用户输入
#   choice = questionary.select("选一个", choices=["A", "B", "C"]).ask()
import questionary

# typing 模块：Python 标准库，提供类型注解支持
# List 用于标注列表类型，Optional 表示值可以为 None
# Tuple 用于标注元组类型（固定长度、不同类型的序列），Dict 用于标注字典类型
from typing import List, Optional, Tuple, Dict

# rich.console.Console：Rich 库的核心输出类
# Rich 是一个终端格式化库，可以在终端中输出彩色文本、表格、面板等
# Console 是 Rich 的主要接口，用于向终端输出格式化内容
# 安装方式：pip install rich
from rich.console import Console

# 从本地模块导入 AnalystType 枚举类型
# from ... import ... 是 Python 的相对导入语法
# 这里导入的是 cli/models.py 中定义的 AnalystType 枚举
# AnalystType.MARKET.value == "market"，AnalystType.NEWS.value == "news" 等
from cli.models import AnalystType

# 创建全局 Console 实例
# Console 是 Rich 库的核心输出对象，所有终端输出都通过它进行
# 创建后可以重复使用，不需要每次创建新的实例
# 使用方式：console.print("文本") 或 console.print("[red]红色文本[/red]")
console = Console()

# TICKER_INPUT_EXAMPLES：股票代码输入示例文本
# 用于在提示用户输入时显示格式参考
# 不同类型的股票代码：
#   - SPY：美国股票（纽约证券交易所）
#   - CNC.TO：加拿大股票（多伦多证券交易所，.TO 后缀）
#   - 7203.T：日本股票（东京证券交易所，.T 后缀）
#   - 0700.HK：香港股票（香港证券交易所，.HK 后缀）
# 这些后缀帮助系统识别股票在哪个交易所交易
TICKER_INPUT_EXAMPLES = "Examples: SPY, CNC.TO, 7203.T, 0700.HK"

# ANALYST_ORDER：分析师选项的有序列表
# 每个元素是一个元组 (显示名称, 枚举值)
# 元组（tuple）是 Python 中不可变的序列类型，用圆括号 () 定义
# 列表中的顺序决定了用户在界面中看到的顺序
# 这个顺序也决定了分析师的执行顺序（市场 → 社交 → 新闻 → 基本面）
ANALYST_ORDER = [
    ("Market Analyst", AnalystType.MARKET),           # 市场分析师
    ("Social Media Analyst", AnalystType.SOCIAL),     # 社交媒体分析师
    ("News Analyst", AnalystType.NEWS),               # 新闻分析师
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),  # 基本面分析师
]


# =====================================================================
# 函数：get_ticker()
# 功能：提示用户输入股票代码
# 返回值：归一化后的股票代码字符串（大写、去空格）
#
# 运行时数据流示例：
#   用户看到提示："Enter the exact ticker symbol to analyze (Examples: SPY, CNC.TO, 7203.T, 0700.HK):"
#   → 用户输入 "  nvda  "（有小写和空格）
#   → questionary.text(...).ask() 返回 "  nvda  "
#   → normalize_ticker_symbol("  nvda  ") 被调用
#   → strip() 去空格 → "nvda"，upper() 转大写 → "NVDA"
#   → 返回 "NVDA"
# =====================================================================
def get_ticker() -> str:
    """提示用户输入要分析的股票代码"""

    # questionary.text() 创建一个文本输入问题
    # 参数说明：
    #   第一个参数是提示文本（显示给用户的问题）
    #     f"..." 是 f-string（格式化字符串），{TICKER_INPUT_EXAMPLES} 会被替换为变量值
    #   validate 参数：输入验证函数，接收用户输入，返回 True 或错误消息字符串
    #     lambda x: ... 是一个匿名函数（lambda 表达式）
    #       lambda 是 Python 创建小函数的语法，不需要 def 关键字
    #       x 是参数名，代表用户输入的字符串
    #       len(x.strip()) > 0 检查输入去掉首尾空格后是否非空
    #       or "..." 如果左侧条件为 False（输入为空），返回右侧的错误消息
    #       Python 的 or 运算符：如果左边为"真"返回左边，否则返回右边
    #       True or "error" → True（验证通过）
    #       False or "error" → "error"（验证失败，显示错误消息）
    #   style 参数：自定义界面样式
    ticker = questionary.text(
        f"Enter the exact ticker symbol to analyze ({TICKER_INPUT_EXAMPLES}):",
        validate=lambda x: len(x.strip()) > 0 or "Please enter a valid ticker symbol.",
        # questionary.Style：定义问答界面的颜色方案
        # 每个样式条目是一个元组：(目标元素, 样式规则)
        # "fg:green" 中 fg 表示 foreground（前景色/文字颜色），green 是绿色
        style=questionary.Style(
            [
                # 设置文本输入框的颜色为绿色
                ("text", "fg:green"),
                # noinherit 表示不高亮，不继承父级样式
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()  # .ask() 方法执行问答，等待用户输入后返回结果
    # .ask() 会阻塞程序，等待用户输入并按 Enter，然后返回输入的字符串

    # 如果用户按了 Ctrl+C 或 Escape 取消输入，ask() 返回 None
    # not ticker 为 True 的情况：ticker 为 None 或空字符串 ""
    if not ticker:
        # console.print() 使用 Rich 库输出格式化文本
        # [red]...[/red] 是 Rich 的标记语法，设置文字颜色为红色
        # \n 是换行符，在输出前先换一行（留出间距）
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        # exit(1) 退出程序，返回码 1 表示异常退出（0 表示正常退出）
        # 返回码可以被操作系统或调用脚本检测
        exit(1)

    # 调用 normalize_ticker_symbol 处理用户输入
    # 将输入的股票代码标准化（去空格 + 转大写）
    return normalize_ticker_symbol(ticker)


# =====================================================================
# 函数：normalize_ticker_symbol()
# 功能：规范化股票代码输入
# 参数：ticker - 原始输入的股票代码字符串
# 返回值：规范化后的字符串（去首尾空格 + 转大写）
# 注意：保留交易所后缀（如 .TO、.HK 等），因为大小写可能影响匹配
#
# 运行时数据流示例：
#   输入 "  nvda  " → strip() → "nvda" → upper() → "NVDA"
#   输入 "cnc.to" → strip() → "cnc.to" → upper() → "CNC.TO"
# =====================================================================
def normalize_ticker_symbol(ticker: str) -> str:
    """规范化股票代码输入，同时保留交易所后缀"""
    # strip() 去除首尾空格（包括换行符、制表符等空白字符）
    # upper() 将所有字母转为大写（股票代码通常用大写）
    # 链式调用：ticker.strip() 先执行，返回结果再调用 .upper()
    return ticker.strip().upper()


# =====================================================================
# 函数：get_analysis_date()
# 功能：提示用户输入分析日期
# 返回值：日期字符串，格式为 YYYY-MM-DD
#
# 运行时数据流示例：
#   用户看到提示："Enter the analysis date (YYYY-MM-DD):"
#   → 用户输入 "2024-05-10"
#   → validate_date("2024-05-10") 被调用
#   → re.match 检查格式：2024-05-10 匹配 \d{4}-\d{2}-\d{2} ✓
#   → datetime.strptime("2024-05-10", "%Y-%m-%d") 成功解析 ✓
#   → 返回 True（验证通过）
#   → 返回 "2024-05-10"
#
#   用户输入 "2024-13-45"（无效日期）
#   → re.match 检查格式：匹配 ✓
#   → datetime.strptime("2024-13-45", "%Y-%m-%d") 抛出 ValueError（13月不存在）
#   → 返回 False（验证失败）
#   → 显示错误消息 "Please enter a valid date in YYYY-MM-DD format."
#   → 用户需要重新输入
# =====================================================================
def get_analysis_date() -> str:
    """提示用户输入分析日期（格式：YYYY-MM-DD）"""

    # re 模块：Python 标准库，用于正则表达式操作
    # 正则表达式是一种模式匹配语言，用于字符串的搜索和验证
    import re
    # datetime 模块：Python 标准库，用于日期和时间处理
    from datetime import datetime

    # 内部函数：验证日期格式是否有效
    # 内部函数（嵌套函数）定义在另一个函数内部，只能在外部函数中使用
    def validate_date(date_str: str) -> bool:
        """验证日期字符串是否符合 YYYY-MM-DD 格式且是有效日期"""
        # re.match() 检查字符串是否从开头匹配正则表达式
        # 正则表达式 r"^\d{4}-\d{2}-\d{2}$" 的含义：
        #   ^     - 字符串开头锚点
        #   \d{4} - 恰好 4 个数字（年份，如 2024）
        #   -     - 连字符（日期分隔符）
        #   \d{2} - 恰好 2 个数字（月份，如 05）
        #   -     - 连字符
        #   \d{2} - 恰好 2 个数字（日期，如 10）
        #   $     - 字符串结尾锚点
        # r"..." 中的 r 表示原始字符串（raw string），\ 不被转义
        # 如果格式不匹配，re.match() 返回 None（假值），直接返回 False
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            # datetime.strptime() 尝试将字符串解析为日期对象
            # "%Y-%m-%d" 是格式代码：
            #   %Y = 四位数年份（如 2024）
            #   %m = 两位数月份（如 05）
            #   %d = 两位数日期（如 10）
            # 如果日期不存在（如 2024-02-30），会抛出 ValueError
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            # ValueError：日期无效（如 2024-13-45 月份不存在）
            return False

    # questionary.text() 创建文本输入问题
    # validate 参数使用 lambda 表达式调用上面定义的 validate_date 函数
    # x.strip() 先去除输入的空格，再传给 validate_date 验证
    # or "..." 如果验证失败（返回 False），显示错误消息
    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: validate_date(x.strip())
        or "Please enter a valid date in YYYY-MM-DD format.",
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    # 如果用户取消输入（按 Ctrl+C 或 Escape）
    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    # 返回去除空格的日期字符串
    return date.strip()


# =====================================================================
# 函数：select_analysts()
# 功能：使用交互式复选框让用户选择分析师团队
# 返回值：选中的 AnalystType 枚举值列表
#
# 运行时数据流示例：
#   用户看到复选框列表：
#     ◯ Market Analyst
#     ◯ Social Media Analyst
#     ◯ News Analyst
#     ◯ Fundamentals Analyst
#   → 用户用方向键移动，按空格勾选 Market Analyst 和 News Analyst
#     ☑ Market Analyst
#     ◯ Social Media Analyst
#     ☑ News Analyst
#     ◯ Fundamentals Analyst
#   → 按 Enter 确认
#   → choices = [AnalystType.MARKET, AnalystType.NEWS]
#   → 返回 [AnalystType.MARKET, AnalystType.NEWS]
# =====================================================================
def select_analysts() -> List[AnalystType]:
    """使用交互式复选框选择分析师团队"""

    # questionary.checkbox() 创建一个多选复选框问题
    # 与 select 不同，checkbox 允许用户选择多个选项
    # 参数说明：
    #   choices 参数：选项列表，每个选项是一个 questionary.Choice 对象
    #     questionary.Choice(display, value)：
    #       display 是显示给用户的文本（如 "Market Analyst"）
    #       value 是选中后返回的实际值（如 AnalystType.MARKET）
    #   instruction 参数：显示给用户的操作提示文本
    #     \n 是换行符，用于多行提示
    #   validate 参数：验证函数，确保至少选择了一个分析师
    #     lambda x: len(x) > 0 检查返回列表的长度是否大于 0
    #     如果用户没有选中任何选项就按 Enter，会显示错误消息
    choices = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=[
            # 列表推导式：遍历 ANALYST_ORDER 生成 Choice 对象列表
            # 语法：[表达式 for 变量 in 可迭代对象]
            # ANALYST_ORDER = [("Market Analyst", AnalystType.MARKET), ...]
            # for display, value in ANALYST_ORDER 解包元组
            # 每次循环 display = "Market Analyst"，value = AnalystType.MARKET
            # 生成 questionary.Choice("Market Analyst", AnalystType.MARKET)
            questionary.Choice(display, value=value) for display, value in ANALYST_ORDER
        ],
        instruction="\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                # checkbox-selected：已选中的复选框样式，绿色
                ("checkbox-selected", "fg:green"),
                # selected：光标所在选项的样式，绿色
                ("selected", "fg:green noinherit"),
                # highlighted：高亮样式
                ("highlighted", "noinherit"),
                # pointer：指针样式（当前光标位置的指示器）
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    # 如果用户取消选择
    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    # 返回选中的 AnalystType 枚举值列表
    return choices


# =====================================================================
# 函数：select_research_depth()
# 功能：让用户选择研究深度
# 返回值：整数，表示研究深度级别（1=浅层，3=中等，5=深度）
#
# 研究深度影响：
#   1 (Shallow)：辩论和策略讨论轮数少，速度快
#   3 (Medium)：中等辩论和策略讨论轮数
#   5 (Deep)：充分辩论和策略讨论，最深入但最慢
#
# 运行时数据流示例：
#   用户看到选项列表：
#     > Shallow - Quick research, few debate and strategy discussion rounds
#       Medium - Middle ground, moderate debate rounds and strategy discussion
#       Deep - Comprehensive research, in depth debate and strategy discussion
#   → 用户用方向键选择 "Deep"，按 Enter
#   → choice = 5
#   → 返回 5
#   → 后续 config["max_debate_rounds"] = 5, config["max_risk_discuss_rounds"] = 5
# =====================================================================
def select_research_depth() -> int:
    """使用交互式选择器选择研究深度"""

    # DEPTH_OPTIONS：研究深度选项列表
    # 每个元组包含 (显示文本, 实际值)
    # 值 1、3、5 分别对应浅层、中等、深度研究
    # 这个值会被用来设置辩论轮数（max_debate_rounds）
    DEPTH_OPTIONS = [
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
    ]

    # questionary.select() 创建一个单选列表问题
    # 与 checkbox 不同，用户只能选择一个选项
    # 用户用上下方向键浏览选项，按 Enter 确认选择
    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=[
            # 列表推导式生成 Choice 对象
            questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                # selected：被选中项的颜色，黄色
                # fg 表示 foreground（前景色/文字颜色）
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    # 如果用户取消选择（按 Escape 或 Ctrl+C）
    if choice is None:
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)

    # 返回选中的研究深度值（1、3 或 5）
    return choice


# =====================================================================
# 函数：select_shallow_thinking_agent(provider)
# 功能：根据用户选择的 LLM 提供商，选择浅层思考模型
# 参数：provider - LLM 提供商名称（如 "openai"、"anthropic" 等）
# 返回值：选中的模型名称字符串
#
# 浅层思考模型（Quick-Thinking）：用于快速、简单的任务
# 这些模型通常更便宜、更快，但推理能力不如深层思考模型
# 排序原则：中等 → 轻量 → 重量（平衡优先，适合快速任务）
# 同级别内，新模型排在前面
#
# 运行时数据流示例：
#   provider = "openai"
#   → 用户看到选项：
#     > GPT-5 Mini - Balanced speed, cost, and capability
#       GPT-5 Nano - High-throughput, simple tasks
#       GPT-5.4 - Latest frontier, 1M context
#       GPT-4.1 - Smartest non-reasoning model
#   → 用户选择 "GPT-5 Mini"
#   → choice = "gpt-5-mini"
#   → 返回 "gpt-5-mini"
#   → 后续 config["quick_think_llm"] = "gpt-5-mini"
# =====================================================================
def select_shallow_thinking_agent(provider) -> str:
    """使用交互式选择器选择浅层思考 LLM 引擎"""

    # SHALLOW_AGENT_OPTIONS：按提供商分类的浅层思考模型选项
    # 字典的键是提供商名称（小写），值是模型选项列表
    # 每个选项是一个元组 (显示文本, 模型名称)
    # 排序原则：中等 → 轻量 → 重量（平衡优先，适合快速任务）
    # 同级别内，新模型排在前面
    SHALLOW_AGENT_OPTIONS = {
        "openai": [
            ("GPT-5 Mini - Balanced speed, cost, and capability", "gpt-5-mini"),
            ("GPT-5 Nano - High-throughput, simple tasks", "gpt-5-nano"),
            ("GPT-5.4 - Latest frontier, 1M context", "gpt-5.4"),
            ("GPT-4.1 - Smartest non-reasoning model", "gpt-4.1"),
        ],
        "anthropic": [
            ("Claude Sonnet 4.6 - Best speed and intelligence balance", "claude-sonnet-4-6"),
            ("Claude Haiku 4.5 - Fast, near-instant responses", "claude-haiku-4-5"),
            ("Claude Sonnet 4.5 - Agents and coding", "claude-sonnet-4-5"),
        ],
        "google": [
            ("Gemini 3 Flash - Next-gen fast", "gemini-3-flash-preview"),
            ("Gemini 2.5 Flash - Balanced, stable", "gemini-2.5-flash"),
            ("Gemini 3.1 Flash Lite - Most cost-efficient", "gemini-3.1-flash-lite-preview"),
            ("Gemini 2.5 Flash Lite - Fast, low-cost", "gemini-2.5-flash-lite"),
        ],
        "xai": [
            ("Grok 4.1 Fast (Non-Reasoning) - Speed optimized, 2M ctx", "grok-4-1-fast-non-reasoning"),
            ("Grok 4 Fast (Non-Reasoning) - Speed optimized", "grok-4-fast-non-reasoning"),
            ("Grok 4.1 Fast (Reasoning) - High-performance, 2M ctx", "grok-4-1-fast-reasoning"),
        ],
        "openrouter": [
            ("NVIDIA Nemotron 3 Nano 30B (free)", "nvidia/nemotron-3-nano-30b-a3b:free"),
            ("Z.AI GLM 4.5 Air (free)", "z-ai/glm-4.5-air:free"),
        ],
        "ollama": [
            ("Qwen3:latest (8B, local)", "qwen3:latest"),
            ("GPT-OSS:latest (20B, local)", "gpt-oss:latest"),
            ("GLM-4.7-Flash:latest (30B, local)", "glm-4.7-flash:latest"),
        ],
    }

    # provider.lower() 将提供商名称转为小写，确保字典查找不区分大小写
    # 例如 "OpenAI" → "openai"，与字典键匹配
    # SHALLOW_AGENT_OPTIONS[provider.lower()] 获取该提供商的模型选项列表
    choice = questionary.select(
        "Select Your [Quick-Thinking LLM Engine]:",
        choices=[
            # 列表推导式：为该提供商的每个模型选项创建 Choice 对象
            questionary.Choice(display, value=value)
            for display, value in SHALLOW_AGENT_OPTIONS[provider.lower()]
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                # 洋红色（magenta）主题
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print(
            "\n[red]No shallow thinking llm engine selected. Exiting...[/red]"
        )
        exit(1)

    # 返回选中的模型名称字符串（如 "gpt-5-mini"）
    return choice


# =====================================================================
# 函数：select_deep_thinking_agent(provider)
# 功能：根据用户选择的 LLM 提供商，选择深层思考模型
# 参数：provider - LLM 提供商名称
# 返回值：选中的模型名称字符串
#
# 深层思考模型（Deep-Thinking）：用于复杂、需要深度推理的任务
# 这些模型通常更贵、更慢，但推理能力更强
# 排序原则：重量 → 中等 → 轻量（能力优先，适合深度分析任务）
#
# 运行时数据流示例：
#   provider = "openai"
#   → 用户选择 "GPT-5.2"
#   → choice = "gpt-5.2"
#   → 返回 "gpt-5.2"
#   → 后续 config["deep_think_llm"] = "gpt-5.2"
# =====================================================================
def select_deep_thinking_agent(provider) -> str:
    """使用交互式选择器选择深层思考 LLM 引擎"""

    # DEEP_AGENT_OPTIONS：按提供商分类的深层思考模型选项
    # 与浅层思考模型选项结构相同，但排序不同（重量优先）
    # 深层模型通常参数更多、推理能力更强，但速度更慢、成本更高
    DEEP_AGENT_OPTIONS = {
        "openai": [
            ("GPT-5.4 - Latest frontier, 1M context", "gpt-5.4"),
            ("GPT-5.2 - Strong reasoning, cost-effective", "gpt-5.2"),
            ("GPT-5 Mini - Balanced speed, cost, and capability", "gpt-5-mini"),
            ("GPT-5.4 Pro - Most capable, expensive ($30/$180 per 1M tokens)", "gpt-5.4-pro"),
        ],
        "anthropic": [
            ("Claude Opus 4.6 - Most intelligent, agents and coding", "claude-opus-4-6"),
            ("Claude Opus 4.5 - Premium, max intelligence", "claude-opus-4-5"),
            ("Claude Sonnet 4.6 - Best speed and intelligence balance", "claude-sonnet-4-6"),
            ("Claude Sonnet 4.5 - Agents and coding", "claude-sonnet-4-5"),
        ],
        "google": [
            ("Gemini 3.1 Pro - Reasoning-first, complex workflows", "gemini-3.1-pro-preview"),
            ("Gemini 3 Flash - Next-gen fast", "gemini-3-flash-preview"),
            ("Gemini 2.5 Pro - Stable pro model", "gemini-2.5-pro"),
            ("Gemini 2.5 Flash - Balanced, stable", "gemini-2.5-flash"),
        ],
        "xai": [
            ("Grok 4 - Flagship model", "grok-4-0709"),
            ("Grok 4.1 Fast (Reasoning) - High-performance, 2M ctx", "grok-4-1-fast-reasoning"),
            ("Grok 4 Fast (Reasoning) - High-performance", "grok-4-fast-reasoning"),
            ("Grok 4.1 Fast (Non-Reasoning) - Speed optimized, 2M ctx", "grok-4-1-fast-non-reasoning"),
        ],
        "openrouter": [
            ("Z.AI GLM 4.5 Air (free)", "z-ai/glm-4.5-air:free"),
            ("NVIDIA Nemotron 3 Nano 30B (free)", "nvidia/nemotron-3-nano-30b-a3b:free"),
        ],
        "ollama": [
            ("GLM-4.7-Flash:latest (30B, local)", "glm-4.7-flash:latest"),
            ("GPT-OSS:latest (20B, local)", "gpt-oss:latest"),
            ("Qwen3:latest (8B, local)", "qwen3:latest"),
        ],
    }

    choice = questionary.select(
        "Select Your [Deep-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in DEEP_AGENT_OPTIONS[provider.lower()]
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No deep thinking llm engine selected. Exiting...[/red]")
        exit(1)

    return choice


# =====================================================================
# 函数：select_llm_provider()
# 功能：让用户选择 LLM 提供商（后端 API 服务）
# 返回值：元组 (提供商名称, API 基础 URL)
#
# 每个提供商有自己的 API 端点（Endpoint）：
#   - OpenAI: https://api.openai.com/v1
#   - Google: https://generativelanguage.googleapis.com/v1
#   - Anthropic: https://api.anthropic.com/
#   - xAI: https://api.x.ai/v1
#   - Openrouter: https://openrouter.ai/api/v1（聚合多个 LLM 提供商）
#   - Ollama: http://localhost:11434/v1（本地部署的 LLM 服务）
#
# 运行时数据流示例：
#   用户看到选项：
#     > OpenAI
#       Google
#       Anthropic
#       xAI
#       Openrouter
#       Ollama
#   → 用户选择 "OpenAI"
#   → choice = ("OpenAI", "https://api.openai.com/v1")
#   → display_name, url = choice  # 元组解包
#   → display_name = "OpenAI", url = "https://api.openai.com/v1"
#   → print("You selected: OpenAI\tURL: https://api.openai.com/v1")
#   → 返回 ("OpenAI", "https://api.openai.com/v1")
#   → 后续 config["backend_url"] = url, config["llm_provider"] = display_name.lower()
# =====================================================================
def select_llm_provider() -> tuple[str, str]:
    """使用交互式选择器选择 LLM 提供商"""

    # BASE_URLS：提供商选项列表
    # 每个元组包含 (显示名称, API 基础 URL)
    # API 基础 URL 是 LLM 服务的 API 端点地址
    # 所有 API 请求都以此为前缀
    BASE_URLS = [
        ("OpenAI", "https://api.openai.com/v1"),
        ("Google", "https://generativelanguage.googleapis.com/v1"),
        ("Anthropic", "https://api.anthropic.com/"),
        ("xAI", "https://api.x.ai/v1"),
        ("Openrouter", "https://openrouter.ai/api/v1"),
        ("Ollama", "http://localhost:11434/v1"),
    ]

    # 注意：这里 choice 的 value 是一个元组 (display, value)
    # 即 questionary.Choice 的 value 参数是整个元组
    # 例如 questionary.Choice("OpenAI", value=("OpenAI", "https://api.openai.com/v1"))
    # 用户选择后，ask() 返回的是 value，即元组 ("OpenAI", "https://api.openai.com/v1")
    choice = questionary.select(
        "Select your LLM Provider:",
        choices=[
            questionary.Choice(display, value=(display, value))
            for display, value in BASE_URLS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]no OpenAI backend selected. Exiting...[/red]")
        exit(1)

    # 元组解包：将 choice 元组的两个元素分别赋值给 display_name 和 url
    # 例如 choice = ("OpenAI", "https://api.openai.com/v1")
    # 解包后 display_name = "OpenAI"，url = "https://api.openai.com/v1"
    display_name, url = choice
    # print() 是 Python 内置函数，输出普通文本到终端
    # 注意：这里用的是内置 print() 而非 Rich 的 console.print()
    # \t 是制表符（Tab），用于对齐文本
    # f"..." 是 f-string，{display_name} 和 {url} 会被替换为变量值
    print(f"You selected: {display_name}\tURL: {url}")

    # 返回元组 (提供商名称, API URL)
    return display_name, url


# =====================================================================
# 函数：ask_openai_reasoning_effort()
# 功能：让用户选择 OpenAI 推理努力程度
# 返回值：字符串 "medium"、"high" 或 "low"
#
# OpenAI 推理努力程度（reasoning_effort）：
# 控制模型在推理时投入的"思考"量
#   - low：更快但可能不够深入
#   - medium：默认，平衡速度和质量
#   - high：最深入但最慢
#
# 运行时数据流示例：
#   用户选择 "Medium (Default)"
#   → 返回 "medium"
#   → 后续 config["openai_reasoning_effort"] = "medium"
# =====================================================================
def ask_openai_reasoning_effort() -> str:
    """询问 OpenAI 推理努力程度级别"""

    # 定义三个选项，显示名称和实际值
    # questionary.Choice(显示文本, 值)
    choices = [
        questionary.Choice("Medium (Default)", "medium"),
        questionary.Choice("High (More thorough)", "high"),
        questionary.Choice("Low (Faster)", "low"),
    ]
    return questionary.select(
        "Select Reasoning Effort:",
        choices=choices,
        style=questionary.Style([
            # 青色主题
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()


# =====================================================================
# 函数：ask_anthropic_effort()
# 功能：让用户选择 Anthropic（Claude）的努力程度
# 返回值：字符串 "high"、"medium" 或 "low" 或 None
#
# Anthropic 努力程度（effort）：
# 控制 Claude 4.5+ 和 4.6 模型的 token 使用量和响应详尽程度
#   - high：推荐，最详细的回答
#   - medium：平衡
#   - low：更快、更便宜
#
# 运行时数据流示例：
#   用户选择 "High (recommended)"
#   → 返回 "high"
#   → 后续 config["anthropic_effort"] = "high"
# =====================================================================
def ask_anthropic_effort() -> str | None:
    """询问 Anthropic 努力程度级别

    控制 Claude 4.5+ 和 4.6 模型的 token 使用量和响应详尽程度
    """
    return questionary.select(
        "Select Effort Level:",
        choices=[
            questionary.Choice("High (recommended)", "high"),
            questionary.Choice("Medium (balanced)", "medium"),
            questionary.Choice("Low (faster, cheaper)", "low"),
        ],
        style=questionary.Style([
            ("selected", "fg:cyan noinherit"),
            ("highlighted", "fg:cyan noinherit"),
            ("pointer", "fg:cyan noinherit"),
        ]),
    ).ask()


# =====================================================================
# 函数：ask_gemini_thinking_config()
# 功能：让用户选择 Gemini 的思考模式
# 返回值：字符串 "high"（启用思考）或 "minimal"（禁用/最小化思考）
#
# Gemini 思考模式（thinking_level）：
#   - high：启用思考模式，模型会进行更深入的推理
#   - minimal：最小化或禁用思考模式，响应更快
# 客户端会根据模型系列自动映射到适当的 API 参数
#
# 运行时数据流示例：
#   用户选择 "Enable Thinking (recommended)"
#   → 返回 "high"
#   → 后续 config["google_thinking_level"] = "high"
#   → google_client.py 中：
#     如果模型是 "gemini-3-flash-preview" → 设置 thinking_level="high"
#     如果模型是 "gemini-2.5-pro" → 设置 thinking_budget=-1（动态思考）
# =====================================================================
def ask_gemini_thinking_config() -> str | None:
    """询问 Gemini 思考配置

    返回 thinking_level："high" 或 "minimal"
    客户端会根据模型系列映射到适当的 API 参数
    """
    return questionary.select(
        "Select Thinking Mode:",
        choices=[
            questionary.Choice("Enable Thinking (recommended)", "high"),
            questionary.Choice("Minimal/Disable Thinking", "minimal"),
        ],
        style=questionary.Style([
            ("selected", "fg:green noinherit"),
            ("highlighted", "fg:green noinherit"),
            ("pointer", "fg:green noinherit"),
        ]),
    ).ask()
