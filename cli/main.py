# =====================================================================
# 模块：cli/main.py
# 功能：TradingAgents CLI 主程序入口
# 说明：这是整个 CLI（命令行界面）的核心文件，包含：
#       1. 终端 UI 布局（使用 Rich 库的 Layout 系统）
#       2. 消息缓冲与管理（MessageBuffer 类）
#       3. 分析流程控制（run_analysis 函数）
#       4. 用户交互引导（get_user_selections 函数）
#       5. 报告展示与保存
#
# 核心架构：
#   本文件使用 Typer 框架创建 CLI 应用，通过 Rich 库实现实时终端 UI，
#   连接到 TradingAgentsGraph 后端引擎执行金融分析。
#
# 主要第三方库：
#   - Typer：基于 Click 的 CLI 框架，用于构建命令行应用
#           支持类型提示、自动生成帮助文档、Shell 补全
#   - Rich：终端美化库，提供彩色文本、表格、面板、实时布局等功能
#   - LangChain：LLM 应用框架，提供 Agent、消息、回调等机制
#   - dotenv：从 .env 文件加载环境变量
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件是整个 CLI（命令行界面）的核心入口。以下是需要了解的核心概念：
#
# 1. Typer（CLI 框架）：
#    - Typer 是一个用 Python 构建 CLI（命令行界面）应用的库。
#    - 安装方式：pip install typer
#    - 核心概念：
#      * app = typer.Typer()：创建一个 CLI 应用实例
#      * @app.command()：装饰器，将函数注册为 CLI 命令
#        装饰器是 Python 的一种语法，用 @ 开头，用于在不修改函数代码的情况下
#        为函数添加额外功能。@app.command() 将下面的函数注册为 CLI 命令。
#      * typer.prompt()：向用户显示提示并等待输入
#      * 用户通过 python main.py analyze 调用注册的命令
#    - 类比：Typer 就像一个"前台接待员"——接收用户的命令，分发给对应的函数处理。
#
# 2. Rich（终端美化库）：
#    - Rich 是 Python 的终端美化库（pip install rich）。
#    - 核心组件：
#      * Console：输出接口，所有 Rich 输出通过它进行
#      * Panel：面板组件，将内容包裹在带边框的框中
#      * Layout：布局组件，将终端划分为多个区域（类似 HTML div 布局）
#      * Live：实时更新组件，创建自动刷新的终端上下文
#      * Table：表格组件，显示结构化数据
#      * Spinner：旋转动画，表示"正在加载"
#      * Markdown：Markdown 渲染，将 Markdown 文本渲染为终端富文本
#      * Rule：水平分割线
#      * Text：富文本，支持颜色、样式
#      * Tree：树形结构
#      * Columns：多列布局
#      * Align：对齐控制
#    - Rich 标记语法：[颜色]文本[/颜色]
#      例如 [bold green]绿色粗体[/bold green]、[dim]灰色暗淡[/dim]
#
# 3. 装饰器模式（Decorator Pattern）：
#    - 装饰器是 Python 的一种语法糖，用 @ 符号标记。
#    - 作用：在不修改原函数代码的情况下，为函数添加额外行为。
#    - 示例：
#        def log_decorator(func):
#            def wrapper(*args, **kwargs):
#                print(f"Calling {func.__name__}")
#                result = func(*args, **kwargs)
#                print(f"Finished {func.__name__}")
#                return result
#            return wrapper
#
#        @log_decorator
#        def my_function():
#            print("Hello!")
#    - 调用 my_function() 时，实际执行的是 wrapper()，它先打印日志，再调用原函数。
#    - @wraps(func) 装饰器：保留原函数的元数据（函数名、文档字符串等）。
#    - 本文件中用于日志记录装饰器（save_message_decorator 等）。
#
# 4. 双端队列（deque）：
#    - deque（发音 "deck"）是 Python collections 模块提供的数据结构。
#    - 与普通列表（list）的区别：
#      * 列表在头部插入/删除元素是 O(n) 操作（需要移动所有元素）
#      * deque 在头部和尾部插入/删除元素都是 O(1) 操作（常数时间）
#    - 支持 maxlen 参数：设置最大长度，超过后自动从另一端删除旧元素
#    - 非常适合实现消息历史缓冲区（保留最新 N 条消息）
#    - 示例：
#        from collections import deque
#        buf = deque(maxlen=100)  # 最多保留 100 条消息
#        buf.append("msg1")  # 添加到末尾
#        buf.append("msg2")  # 添加到末尾
#        # 当超过 maxlen 时，最旧的元素自动被删除
#
# 5. 线程安全（Thread Safety）与 Lock：
#    - 当多个线程同时访问共享数据时，可能产生竞态条件。
#    - Lock（互斥锁）确保同一时间只有一个线程能修改数据。
#    - 本文件中 MessageBuffer 的数据被 UI 线程和回调线程同时访问，
#      因此需要注意线程安全（虽然 Python 的 GIL 提供了一定保护）。
#
# 6. LangChain 回调处理器（Callback Handler）：
#    - LangChain 提供回调机制，在 LLM 调用和工具调用的关键节点触发自定义函数。
#    - StatsCallbackHandler（在 cli/stats_handler.py 中定义）：
#      * on_llm_start/on_chat_model_start：LLM 开始时触发，计数 +1
#      * on_llm_end：LLM 结束时触发，提取 token 使用量
#      * on_tool_start：工具开始时触发，计数 +1
#    - 回调处理器通过 callbacks 参数传递给 LLM 和 Agent。
#
# 7. LangGraph 图流式处理（Graph Streaming）：
#    - TradingAgentsGraph 基于 LangGraph 构建，定义了智能体之间的执行流程图。
#    - graph.graph.stream() 是 LangGraph 的流式接口：
#      逐步返回分析过程中的中间状态（chunk），每个 chunk 包含某个节点的输出。
#    - 通过处理每个 chunk，可以实现实时进度显示。
#    - 类比：就像水管里的水流——数据一段一段地流过来，你可以在每一段到达时处理它。
#
# 8. LangChain 消息类型：
#    - LangChain 中对话由消息组成，主要类型：
#      * HumanMessage：用户发送的消息
#      * AIMessage：AI 助手的回复（可能包含 tool_calls 工具调用信息）
#      * ToolMessage：工具执行后返回的结果
#    - AIMessage.content 可能是字符串或结构化块列表（需要规范化）。
#
# 9. "猴子补丁"（Monkey Patching）：
#    - 在运行时动态替换对象的方法或属性。
#    - 本文件中用装饰后的方法替换 message_buffer 的原始方法。
#    - 示例：
#        obj.method = decorator(obj, "method")  # 用装饰版本替换原始方法
#    - 优点：不需要修改 MessageBuffer 类的代码即可添加日志功能。
#    - 注意：猴子补丁可能使代码难以调试，应谨慎使用。
#
# 10. 本文件的运行时数据流：
#     用户运行 python main.py analyze
#     → Typer 调用 analyze() 函数
#     → analyze() 调用 run_analysis()
#     → run_analysis() 执行完整流程：
#       1. get_user_selections()：引导用户完成 7 步配置
#          → 获取股票代码、日期、分析师选择、模型选择等
#       2. 创建配置字典 config = DEFAULT_CONFIG.copy()
#       3. 创建 StatsCallbackHandler() 统计回调
#       4. 创建 TradingAgentsGraph(analysts, config, callbacks=[stats_handler])
#       5. message_buffer.init_for_analysis() 初始化消息缓冲
#       6. 创建结果目录和日志文件
#       7. 设置日志装饰器（猴子补丁）
#       8. 创建 Live(layout) 实时显示上下文
#       9. 流式处理：
#          for chunk in graph.graph.stream(init_state, **args):
#            → 处理消息：classify_message_type → add_message
#            → 处理工具调用：add_tool_call
#            → 更新分析师状态：update_analyst_statuses
#            → 处理研究团队辩论状态
#            → 处理交易团队计划
#            → 处理风险管理辩论状态
#            → update_display() 刷新终端 UI
#       10. 分析完成：保存报告 + 显示完整报告
# =====================================================================

# typing 模块：Python 标准库，提供类型注解
# Optional 表示值可以是 None，常用于可选参数
# datetime 模块：Python 标准库，处理日期和时间
# 用于记录时间戳、生成文件名、日期比较等
import datetime

# functools 模块：Python 标准库，提供高阶函数工具
# wraps 装饰器：用于创建装饰器时保留原函数的元数据（函数名、文档字符串等）
from functools import wraps

# pathlib 模块：Python 标准库，面向对象的文件路径操作
# Path 类提供了跨平台的文件路径操作方法
# 比传统的 os.path 更直观、更易读
from pathlib import Path

# typer 库：现代化的 CLI 框架
# 安装方式：pip install typer
# 特点：
#   - 基于 Python 类型提示自动生成 CLI 参数和选项
#   - 自动生成帮助文档和 Shell 补全脚本
#   - 支持子命令（类似 git 的 commit、push 等）
import typer

# dotenv 库：从 .env 文件加载环境变量
# 安装方式：pip install python-dotenv
# load_dotenv() 会读取项目根目录下的 .env 文件，
# 将其中的键值对加载到操作系统的环境变量中（os.environ）
# 常用于存储 API 密钥等敏感配置，避免硬编码
from dotenv import load_dotenv

# rich.console.Console：Rich 库的核心输出类
# 所有终端输出（文本、表格、面板等）都通过 Console 实例进行
from rich.console import Console

# 在导入其他模块之前加载环境变量
# 这样后续模块中 os.getenv() 调用就能读取到 .env 中的配置
load_dotenv()

# Rich 库的各种组件导入：

# Panel：面板组件，将内容包裹在带边框的框中
# 可以设置标题、边框颜色、内边距等
# time 模块：Python 标准库，提供时间相关函数
# time.time() 返回当前时间戳（秒），用于计算耗时
import time

# deque：双端队列，Python 标准库 collections 中的数据结构
# 支持从两端高效地添加和删除元素
# 可以设置 maxlen（最大长度），超过后自动从另一端删除旧数据
# 非常适合实现消息历史缓冲区
from collections import deque

# box：边框样式模块，提供多种表格边框风格
# 如 SIMPLE_HEAD、MINIMAL、ROUNDED 等
from rich import box

# Align：对齐组件，控制内容在终端中的水平对齐方式
# 支持 left、center、right
from rich.align import Align

# Columns：多列布局组件，将内容排列成多列
# Layout：布局组件，是 Rich 终端 UI 布局系统的核心
# 支持将终端划分为多个区域（类似 HTML 的 div 布局）
# 支持 split_column（垂直分割）和 split_row（水平分割）
# 每个子布局可以设置 size（固定大小）或 ratio（比例）
from rich.layout import Layout

# Live：实时更新组件，是 Rich 实现动态终端 UI 的核心
# 它会创建一个可自动刷新的上下文，在上下文内更新布局即可实时显示
# 类似于游戏引擎的渲染循环概念
from rich.live import Live

# Markdown：Markdown 渲染组件
# 将 Markdown 格式文本渲染为终端中的富文本
# 支持标题、列表、代码块、粗体、斜体等
from rich.markdown import Markdown
from rich.panel import Panel

# Rule：水平分割线组件
# 用于在终端中显示一条横线，可以带标题
from rich.rule import Rule

# Spinner：旋转动画组件，用于显示"正在加载"效果
# 支持多种动画风格（dots、line、bouncingBar 等）
from rich.spinner import Spinner

# Table：表格组件，用于显示结构化数据
# 支持表头、列样式、边框样式等
from rich.table import Table

# Text：富文本组件，支持样式化的文本
# 可以设置颜色、粗体、斜体等属性
from rich.text import Text

# Tree：树形组件，用于显示层级结构数据
# 公告系统：获取和显示公告
from cli.announcements import display_announcements, fetch_announcements

# AnalystType：分析师类型枚举（market、social、news、fundamentals）
# 统计回调处理器：跟踪 LLM 调用和 token 使用统计
from cli.stats_handler import StatsCallbackHandler

# 从 cli/utils.py 导入所有公共函数
# 包括交互式问答函数（get_ticker、select_analysts 等）
# 注意：这里的 * 导入会导入 utils.py 中定义的所有非私有名称
from cli.utils import *

# 显式再导入一次关键函数，避免本文件后续的同名定义（或其它星号导入的
# 名字）把它们悄悄覆盖掉 —— 之前正是这个覆盖导致 CLI 实际使用了
# 不做规范化的 get_ticker 版本。
from cli.utils import get_analysis_date, get_ticker  # noqa: F811

# DEFAULT_CONFIG：默认配置字典
# 包含 LLM 模型、参数、路径等默认设置
from tradingagents.default_config import DEFAULT_CONFIG

# 从项目内部模块导入：
# TradingAgentsGraph：交易分析图，是整个分析引擎的核心
# 它基于 LangGraph 构建，定义了 Agent 之间的协作流程
# 包含分析师、研究团队、交易员、风险管理、投资组合管理等角色
from tradingagents.graph.trading_graph import TradingAgentsGraph

# 创建全局 Console 实例
# Console 是 Rich 库的核心输出对象
# 所有通过 Rich 的终端输出都使用这个实例
console = Console()


# =====================================================================
# Typer 应用初始化
# =====================================================================
# typer.Typer() 创建一个 CLI 应用实例
# 参数说明：
#   name：应用名称，显示在帮助信息中
#   help：应用简短描述，显示在帮助信息中
#   add_completion：是否启用 Shell 自动补全功能
#     True 表示允许用户通过 --install-completion 安装补全脚本
#     支持 bash、zsh、fish、powershell 等 Shell
app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # 启用 Shell 补全
)


# =====================================================================
# 类：MessageBuffer
# 功能：消息缓冲与管理
# 说明：存储和管理分析过程中的所有消息、工具调用、Agent 状态和报告。
#       是整个 CLI 实时显示的数据源。
#
# 设计模式：
#   - 使用 deque（双端队列）存储消息，自动限制最大长度
#   - 使用字典存储 Agent 状态和报告内容
#   - 提供统一的数据更新接口
#
# 核心概念 - Agent 工作流：
#   1. 分析师团队（Analyst Team）：可选的四个分析师（市场、社交媒体、新闻、基本面）
#   2. 研究团队（Research Team）：Bull/Bear 研究员 + 研究经理
#   3. 交易团队（Trading Team）：交易员
#   4. 风险管理（Risk Management）：激进/中立/保守分析师
#   5. 投资组合管理（Portfolio Management）：投资组合经理
# =====================================================================
class MessageBuffer:
    """
    消息缓冲区，管理分析过程中的所有数据

    职责：
    - 存储消息历史（限制最大长度）
    - 跟踪每个 Agent 的状态（pending/in_progress/completed/error）
    - 管理报告章节内容
    - 生成最终报告的汇总视图
    """

    # ------------------------------------------------------------------
    # 类属性：固定团队定义
    # ------------------------------------------------------------------
    # FIXED_AGENTS：始终运行的固定团队（不由用户选择）
    # 字典结构：{团队名称: [成员名称列表]}
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # ANALYST_MAPPING：分析师类型键到显示名称的映射
    # 用于将用户选择的类型（如 "market"）转换为显示名称（如 "Market Analyst"）
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    }

    # ------------------------------------------------------------------
    # 类属性：报告章节定义
    # ------------------------------------------------------------------
    # REPORT_SECTIONS：报告章节与分析师和完成 Agent 的映射
    # 字典结构：{章节名: (分析师键, 完成该章节的 Agent)}
    #   - 分析师键为 None 表示该章节不受用户选择影响（始终包含）
    #   - 完成 Agent 是该章节"最终完成"的标记
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),               # 市场报告
        "sentiment_report": ("social", "Social Analyst"),            # 情绪报告
        "news_report": ("news", "News Analyst"),                     # 新闻报告
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),  # 基本面报告
        "investment_plan": (None, "Research Manager"),               # 投资计划（始终包含）
        "trader_investment_plan": (None, "Trader"),                  # 交易计划（始终包含）
        "final_trade_decision": (None, "Portfolio Manager"),         # 最终决策（始终包含）
    }

    # ==================================================================
    # 构造函数
    # ==================================================================
    def __init__(self, max_length=100):
        """
        初始化消息缓冲区

        参数：
            max_length：消息和工具调用历史的最大长度
                       超过此长度时，最旧的数据会自动被删除
        """
        # deque(maxlen=max_length)：双端队列，限制最大长度
        # 当添加新元素超出 maxlen 时，最旧的元素会被自动弹出（FIFO 行为）
        # 每条消息格式：(时间戳, 消息类型, 消息内容)
        self.messages = deque(maxlen=max_length)

        # 工具调用历史队列
        # 每条工具调用格式：(时间戳, 工具名称, 参数)
        self.tool_calls = deque(maxlen=max_length)

        # current_report：当前正在显示的报告片段（最近更新的章节）
        self.current_report = None

        # final_report：完整的最终报告（所有章节的汇总）
        self.final_report = None

        # agent_status：Agent 状态字典
        # 键是 Agent 名称，值是状态字符串（pending/in_progress/completed/error）
        self.agent_status = {}

        # current_agent：当前正在执行的 Agent 名称
        self.current_agent = None

        # report_sections：报告章节内容字典
        # 键是章节名，值是报告内容
        self.report_sections = {}

        # selected_analysts：用户选择的分析师类型列表
        self.selected_analysts = []

        # _last_message_id：用于去重的最后消息 ID
        # 前缀 _ 表示私有属性（Python 命名约定）
        self._last_message_id = None

    # ==================================================================
    # 方法：init_for_analysis()
    # 功能：为新一次分析初始化所有状态
    # 参数：selected_analysts - 用户选择的分析师类型列表
    #
    # 调用时机：每次开始新的分析时调用
    # ==================================================================
    def init_for_analysis(self, selected_analysts):
        """根据选择的分析师初始化 Agent 状态和报告章节

        参数：
            selected_analysts：分析师类型字符串列表（如 ["market", "news"]）
        """
        # 将分析师类型转为小写，确保后续比较一致
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # 重置 agent_status 字典
        self.agent_status = {}

        # 添加用户选择的分析师到状态字典
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                # 所有 Agent 初始状态为 "pending"（等待中）
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # 添加固定团队的 Agent 到状态字典
        # FIXED_AGENTS.values() 遍历所有团队
        # team_agents 是每个团队的成员列表
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # 动态构建报告章节
        # 只包含被选择的分析师相关的章节和固定章节
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            # 如果 analyst_key 为 None（固定章节），或已被用户选择
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None  # 初始化为 None，表示尚未有内容

        # 重置所有状态变量为初始值
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()       # 清空消息历史
        self.tool_calls.clear()     # 清空工具调用历史
        self._last_message_id = None  # 重置消息 ID（用于去重）

    # ==================================================================
    # 方法：get_completed_reports_count()
    # 功能：计算已完成的报告章节数量
    #
    # 报告"完成"的定义：
    #   1. 报告章节有内容（不是 None），且
    #   2. 负责该章节的 Agent 状态为 "completed"
    #
    # 这种双重检查防止中间结果（如辩论轮次）被误认为完成。
    # ==================================================================
    def get_completed_reports_count(self):
        """计算已完成的报告数量（Agent 已完成且报告有内容）"""
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]

            # 报告有内容
            has_content = self.report_sections.get(section) is not None
            # 负责的 Agent 已标记为完成
            agent_done = self.agent_status.get(finalizing_agent) == "completed"

            if has_content and agent_done:
                count += 1
        return count

    # ==================================================================
    # 方法：add_message()
    # 功能：向消息队列添加一条消息
    # 参数：
    #   message_type - 消息类型（如 "System"、"Agent"、"User"、"Tool" 等）
    #   content - 消息内容
    # ==================================================================
    def add_message(self, message_type, content):
        """添加消息到历史队列"""
        # datetime.datetime.now().strftime() 格式化当前时间为字符串
        # %H:%M:%S 格式：小时:分钟:秒（24小时制）
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        # 将 (时间戳, 类型, 内容) 元组添加到队列末尾
        self.messages.append((timestamp, message_type, content))

    # ==================================================================
    # 方法：add_tool_call()
    # 功能：向工具调用队列添加一条记录
    # 参数：
    #   tool_name - 被调用的工具名称
    #   args - 传递给工具的参数
    # ==================================================================
    def add_tool_call(self, tool_name, args):
        """添加工具调用记录到历史队列"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    # ==================================================================
    # 方法：update_agent_status()
    # 功能：更新指定 Agent 的状态
    # 参数：
    #   agent - Agent 名称
    #   status - 新状态（pending/in_progress/completed/error）
    #
    # 同时更新 current_agent 为当前正在处理的 Agent
    # ==================================================================
    def update_agent_status(self, agent, status):
        """更新 Agent 的执行状态"""
        # 只更新在 agent_status 字典中存在的 Agent
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    # ==================================================================
    # 方法：update_report_section()
    # 功能：更新指定报告章节的内容
    # 参数：
    #   section_name - 章节名称（如 "market_report"）
    #   content - 章节内容
    #
    # 更新后会自动触发 current_report 和 final_report 的更新
    # ==================================================================
    def update_report_section(self, section_name, content):
        """更新报告章节并触发视图刷新"""
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    # ==================================================================
    # 私有方法：_update_current_report()
    # 功能：更新当前显示的报告（最近更新的章节）和完整报告
    #
    # 设计思路：
    #   在实时显示中，只显示最近更新的单个章节（避免信息过载），
    #   同时后台维护完整的报告汇总。
    # ==================================================================
    def _update_current_report(self):
        """更新当前报告视图（最近更新的章节）"""
        latest_section = None
        latest_content = None

        # 遍历报告章节，找到最近更新的那个
        # 由于字典按插入顺序迭代，最后更新的章节会在最后
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content

        if latest_section and latest_content:
            # 章节标题映射：将内部键名转换为可读的标题
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
            }
            # 使用 Markdown 格式：### 表示三级标题
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # 同时更新完整报告
        self._update_final_report()

    # ==================================================================
    # 私有方法：_update_final_report()
    # 功能：构建完整的最终报告（所有章节的汇总）
    #
    # 报告结构：
    #   ## Analyst Team Reports（分析师团队报告）
    #     ### Market Analysis（市场分析）
    #     ### Social Sentiment（社交媒体情绪）
    #     ### News Analysis（新闻分析）
    #     ### Fundamentals Analysis（基本面分析）
    #   ## Research Team Decision（研究团队决策）
    #   ## Trading Team Plan（交易团队计划）
    #   ## Portfolio Management Decision（投资组合管理决策）
    # ==================================================================
    def _update_final_report(self):
        """构建完整的最终报告汇总"""
        report_parts = []

        # ---- 分析师团队报告 ----
        # 只有用户选择的分析师才会在 report_sections 中存在
        analyst_sections = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
        # any() 检查是否有任何一个分析师报告不为 None
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # ---- 研究团队决策 ----
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # ---- 交易团队计划 ----
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # ---- 投资组合管理决策 ----
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        # "\n\n".join() 用两个换行符连接各部分（段落间距）
        self.final_report = "\n\n".join(report_parts) if report_parts else None


# 创建全局的 MessageBuffer 实例
# 所有函数都通过这个实例共享消息数据
message_buffer = MessageBuffer()


# =====================================================================
# 函数：create_layout()
# 功能：创建 Rich 终端 UI 布局结构
# 返回值：Layout 对象
#
# 布局结构（从上到下）：
#   ┌──────────────────────────────────────────────┐
#   │  header（固定 3 行）                          │
#   ├──────────────────────┬───────────────────────┤
#   │  upper（比例 3）      │                       │
#   │  ├─ progress（比例 2）│  analysis（比例 5）    │
#   │  └─ messages（比例 3）│                       │
#   ├──────────────────────┴───────────────────────┤
#   │  footer（固定 3 行）                          │
#   └──────────────────────────────────────────────┘
#
# Rich Layout 概念：
#   - split_column()：垂直分割，将布局分成上下两部分
#   - split_row()：水平分割，将布局分成左右两部分
#   - size：固定大小（行数），如 size=3 表示固定 3 行
#   - ratio：比例大小，如 ratio=3 表示占 3 份
#   - 总比例 = 3 + 5 = 8，所以 upper 占 3/8，analysis 占 5/8
# =====================================================================
def create_layout():
    """
    创建终端 UI 布局

    布局说明：
    - header：欢迎信息
    - upper：上方区域（进度 + 消息）
      - progress：Agent 执行进度
      - messages：消息和工具调用日志
    - analysis：分析报告（中间大区域）
    - footer：底部统计信息
    """
    # 创建根布局
    layout = Layout()
    # 垂直分割：header（固定 3 行）→ main（自适应）→ footer（固定 3 行）
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    # main 区域垂直分割：upper（比例 3）→ analysis（比例 5）
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    # upper 区域水平分割：progress（比例 2）→ messages（比例 3）
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


# =====================================================================
# 函数：format_tokens(n)
# 功能：格式化 token 数量为可读字符串
# 参数：n - token 数量（整数）
# 返回值：格式化后的字符串
#
# 示例：
#   format_tokens(500) → "500"
#   format_tokens(1500) → "1.5k"
#   format_tokens(1000000) → "1000.0k"
# =====================================================================
def format_tokens(n):
    """将 token 数量格式化为可读字符串（如 1.5k）"""
    if n >= 1000:
        # n/1000 计算千位数，:.1f 保留一位小数
        return f"{n/1000:.1f}k"
    return str(n)


# =====================================================================
# 函数：update_display(layout, spinner_text, stats_handler, start_time)
# 功能：更新终端 UI 的所有区域
# 参数：
#   layout - Rich Layout 对象
#   spinner_text - 旋转动画的文本（可选）
#   stats_handler - 统计回调处理器（可选）
#   start_time - 分析开始时间（可选，用于计算耗时）
#
# 这是整个终端 UI 的核心渲染函数，每次调用都会刷新所有区域。
# 被 Live 上下文管理器周期性调用。
# =====================================================================
def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    """
    更新终端 UI 的所有区域

    渲染区域：
    1. header：欢迎面板
    2. progress：Agent 执行进度表格
    3. messages：消息和工具调用日志
    4. analysis：当前报告内容
    5. footer：统计信息（Agent 进度、LLM 调用、Token 使用、耗时）
    """

    # ------------------------------------------------------------------
    # 1. 更新头部（Header）
    # ------------------------------------------------------------------
    # Panel 组件：Rich 库的面板
    # [bold green]：绿色粗体文本
    # [dim]：暗淡文本（灰色）
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim](c) [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,  # 扩展填充可用空间
        )
    )

    # ------------------------------------------------------------------
    # 2. 更新进度面板（Progress）
    # ------------------------------------------------------------------
    # Table 组件：Rich 库的表格
    progress_table = Table(
        show_header=True,           # 显示表头
        header_style="bold magenta", # 表头样式：洋红色粗体
        show_footer=False,           # 不显示表尾
        box=box.SIMPLE_HEAD,         # 边框样式：简单表头（只有表头下方有横线）
        title=None,                  # 不设置表格标题
        padding=(0, 2),             # 单元格内边距：上下 0，左右 2
        expand=True,                 # 扩展填充可用空间
    )
    # add_column() 添加列
    # justify 参数：文本对齐方式（center/left/right）
    # width 参数：列宽（字符数）
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # 所有可能的团队及其成员
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # 过滤：只显示在当前 agent_status 中存在的 Agent
    # 这样用户未选择的分析师不会出现在界面中
    teams = {}
    for team, agents in all_teams.items():
        # 列表推导式：过滤出在 agent_status 中的 Agent
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    # 遍历每个团队，添加表格行
    for team, agents in teams.items():
        # 添加团队的第一个成员（带团队名称）
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")

        # 根据状态决定如何显示
        if status == "in_progress":
            # Spinner 组件：旋转动画，表示正在执行
            # "dots" 是动画风格（点状动画）
            # text 参数：动画旁边的文字
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            # 状态颜色映射
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            # Rich 标记语法：[颜色]文本[/颜色]
            status_cell = f"[{status_color}]{status}[/{status_color}]"

        progress_table.add_row(team, first_agent, status_cell)

        # 添加团队的其余成员（团队名称列留空）
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)  # 团队名称为空

        # 每个团队后面添加分隔线
        # "─" * 20 生成 20 个横线字符
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    # 将进度表格放入面板
    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # ------------------------------------------------------------------
    # 3. 更新消息面板（Messages）
    # ------------------------------------------------------------------
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,
        box=box.MINIMAL,    # 最小边框样式
        show_lines=True,    # 显示行之间的分隔线
        padding=(0, 1),
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    # no_wrap=False 允许内容自动换行
    # ratio=1 表示该列占用剩余的所有空间
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )

    # 合并工具调用和消息到一个列表
    all_messages = []

    # 添加工具调用记录
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # 添加常规消息
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        # 如果内容过长，截断并添加省略号
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # 按时间戳降序排列（最新的在前）
    # sort() 的 key 参数指定排序依据
    # lambda x: x[0] 取每个元组的第一个元素（时间戳）作为排序键
    # reverse=True 表示降序
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # 最大显示消息数
    max_messages = 12

    # 取最新的 N 条消息
    recent_messages = all_messages[:max_messages]

    # 添加消息到表格
    for timestamp, msg_type, content in recent_messages:
        # Text 组件：Rich 的富文本
        # overflow="fold" 表示文本过长时自动折叠（换行）
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # ------------------------------------------------------------------
    # 4. 更新分析面板（Analysis）
    # ------------------------------------------------------------------
    if message_buffer.current_report:
        # Markdown 组件：将 Markdown 文本渲染为终端富文本
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        # 尚无报告时显示等待提示
        # [italic]：斜体文本
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # ------------------------------------------------------------------
    # 5. 更新底部统计信息（Footer）
    # ------------------------------------------------------------------
    # Agent 进度统计
    # sum() + 生成器表达式：计算状态为 "completed" 的 Agent 数量
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # 报告进度统计
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # 构建统计信息各部分
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM 和工具统计（来自回调处理器）
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token 显示（带优雅降级）
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            # \u2191 是 ↑，\u2193 是 ↓ 的 Unicode 编码
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # 耗时统计
    if start_time:
        # time.time() 返回 Unix 时间戳（秒）
        elapsed = time.time() - start_time
        # // 整除，% 取余
        # :02d 格式化为至少两位数字，不足补零
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    # 创建统计表格
    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    # " | ".join() 用竖线连接各部分
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


# =====================================================================
# 函数：get_user_selections()
# 功能：引导用户完成所有配置选择
# 返回值：字典，包含所有用户选择的配置
#
# 流程（7 个步骤）：
#   1. 股票代码输入
#   2. 分析日期输入
#   3. 分析师团队选择
#   4. 研究深度选择
#   5. LLM 提供商选择
#   6. 思考模型选择（浅层 + 深层）
#   7. 提供商特定配置（思考模式/推理力度/努力程度）
#
# 设计模式：向导式（Wizard）界面，引导用户逐步完成配置
# =====================================================================
def get_user_selections():
    """获取所有用户配置选择（在分析开始前完成）"""

    # ------------------------------------------------------------------
    # 显示 ASCII 艺术欢迎界面
    # ------------------------------------------------------------------
    # Path(__file__).parent 获取当前文件所在目录
    # / "static" / "welcome.txt" 拼接路径
    # with open() as f: 使用上下文管理器安全打开文件
    with open(Path(__file__).parent / "static" / "welcome.txt") as f:
        welcome_ascii = f.read()

    # 构建欢迎内容
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team (r) II. Research Team (r) III. Trader (r) IV. Risk Management (r) V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Align.center() 将内容水平居中
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # 额外的垂直间距

    # 获取并显示公告
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # ------------------------------------------------------------------
    # 辅助函数：create_question_box()
    # 功能：创建统一的问答提示框
    # 参数：
    #   title - 步骤标题
    #   prompt - 提示文本
    #   default - 默认值（可选）
    # ------------------------------------------------------------------
    def create_question_box(title, prompt, default=None):
        """创建问答步骤的提示框"""
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # ------------------------------------------------------------------
    # 步骤 1：股票代码
    # ------------------------------------------------------------------
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the exact ticker symbol to analyze, including exchange suffix when needed (examples: SPY, CNC.TO, 7203.T, 0700.HK)",
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # ------------------------------------------------------------------
    # 步骤 2：分析日期
    # ------------------------------------------------------------------
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # A 股交易日校验：周末 / 节假日自动回退到最近交易日，避免取不到行情。
    # 非 A 股标的（如美股 NVDA）不做限制，保持原有行为。
    try:
        from tradingagents.dataflows.trade_calendar import (
            validate_analysis_date,
        )

        ok, msg, adjusted = validate_analysis_date(
            selected_ticker, analysis_date
        )
        if not ok:
            if adjusted and adjusted != analysis_date:
                console.print(f"[yellow]{msg}[/yellow]")
                console.print(
                    f"[yellow]已自动改用最近交易日 {adjusted}[/yellow]"
                )
                analysis_date = adjusted
            else:
                console.print(f"[red]{msg}[/red]")
    except Exception:
        # 交易日历不可用时不影响主流程
        pass

    # ------------------------------------------------------------------
    # 步骤 3：选择分析师
    # ------------------------------------------------------------------
    console.print(
        create_question_box(
            "Step 3: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # ------------------------------------------------------------------
    # 步骤 4：研究深度
    # ------------------------------------------------------------------
    console.print(
        create_question_box(
            "Step 4: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # ------------------------------------------------------------------
    # 步骤 5：LLM 提供商
    # ------------------------------------------------------------------
    console.print(
        create_question_box(
            "Step 5: OpenAI backend", "Select which service to talk to"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()

    # ------------------------------------------------------------------
    # 步骤 6：思考模型选择
    # ------------------------------------------------------------------
    console.print(
        create_question_box(
            "Step 6: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # ------------------------------------------------------------------
    # 步骤 7：提供商特定配置
    # ------------------------------------------------------------------
    # 根据不同的 LLM 提供商，询问不同的配置参数
    thinking_level = None         # Gemini 思考模式
    reasoning_effort = None       # OpenAI 推理力度
    anthropic_effort = None       # Anthropic 努力程度

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        console.print(
            create_question_box(
                "Step 7: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print(
            create_question_box(
                "Step 7: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print(
            create_question_box(
                "Step 7: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    # 返回所有用户选择的配置
    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
    }


# =====================================================================
# 函数：get_ticker()
# 功能：获取股票代码
#
# 修复说明：此前本文件在第 259 行执行 ``from cli.utils import *`` 之后，
# 又在下方重新定义了同名函数 —— 后定义者会**覆盖**星号导入进来的版本
# （原注释写的"被 utils.py 中的同名函数覆盖"方向恰好相反）。结果是
# CLI 实际使用的是下面这个不做任何规范化的 typer.prompt 版本，
# " 600519 "、"" 这类输入不会被清洗，而 history / view / WebUI 路径
# 都假设代码已经规范化。
#
# 现在直接复用 cli.utils 中的实现（带规范化与非空校验）。
# =====================================================================


# =====================================================================
# 函数：get_analysis_date()
# 功能：获取分析日期，验证格式和范围
# 返回值：格式为 YYYY-MM-DD 的日期字符串
#
# 验证规则：
#   1. 必须是有效的日期格式
#   2. 不能是未来的日期（分析历史数据）
#
# 同上：原先本文件的同名定义覆盖了 cli.utils 的版本，这里一并复用。
# =====================================================================


# =====================================================================
# 函数：save_report_to_disk(final_state, ticker, save_path)
# 功能：将完整的分析报告保存到磁盘
# 参数：
#   final_state - 最终状态字典（包含所有报告内容）
#   ticker - 股票代码
#   save_path - 保存目录的路径
# 返回值：完整报告文件的路径
#
# 文件结构：
#   save_path/
#   ├── 1_analysts/
#   │   ├── market.md        # 市场分析报告
#   │   ├── sentiment.md     # 社交媒体情绪报告
#   │   ├── news.md          # 新闻分析报告
#   │   └── fundamentals.md  # 基本面分析报告
#   ├── 2_research/
#   │   ├── bull.md          # 多方研究员观点
#   │   ├── bear.md          # 空方研究员观点
#   │   └── manager.md       # 研究经理决策
#   ├── 3_trading/
#   │   └── trader.md        # 交易员计划
#   ├── 4_risk/
#   │   ├── aggressive.md    # 激进分析师
#   │   ├── conservative.md  # 保守分析师
#   │   └── neutral.md       # 中立分析师
#   ├── 5_portfolio/
#   │   └── decision.md      # 投资组合经理决策
#   └── complete_report.md   # 完整汇总报告
# =====================================================================
def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """将完整分析报告保存到磁盘，按子目录组织"""

    # mkdir(parents=True, exist_ok=True)：创建目录
    # parents=True 会创建所有必需的父目录
    # exist_ok=True 如果目录已存在不会报错
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # ------------------------------------------------------------------
    # 1. 分析师报告
    # ------------------------------------------------------------------
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []

    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        # write_text() 将文本写入文件
        (analysts_dir / "market.md").write_text(final_state["market_report"])
        analyst_parts.append(("Market Analyst", final_state["market_report"]))

    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"])
        analyst_parts.append(("Social Analyst", final_state["sentiment_report"]))

    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"])
        analyst_parts.append(("News Analyst", final_state["news_report"]))

    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"])
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))

    if analyst_parts:
        # 生成器表达式 + join：连接所有分析师报告
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # ------------------------------------------------------------------
    # 2. 研究团队报告（投资辩论）
    # ------------------------------------------------------------------
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []

        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"])
            research_parts.append(("Bull Researcher", debate["bull_history"]))

        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"])
            research_parts.append(("Bear Researcher", debate["bear_history"]))

        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"])
            research_parts.append(("Research Manager", debate["judge_decision"]))

        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # ------------------------------------------------------------------
    # 3. 交易团队计划
    # ------------------------------------------------------------------
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"])
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # ------------------------------------------------------------------
    # 4. 风险管理团队报告（风险辩论）
    # ------------------------------------------------------------------
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []

        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"])
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))

        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"])
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))

        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"])
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))

        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # ------------------------------------------------------------------
        # 5. 投资组合经理决策
        # ------------------------------------------------------------------
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"])
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # 写入完整汇总报告
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections))
    return save_path / "complete_report.md"


# =====================================================================
# 函数：parse_final_decision(decision_text)
# 功能：从 final_trade_decision markdown 中提取结构化数据
# 参数：decision_text - markdown 格式的决策文本
# 返回：包含关键字段的字典
# =====================================================================
def parse_final_decision(decision_text: str) -> dict:
    """从 markdown 决策报告中提取关键结构化数据"""
    import re
    result = {
        "rating": "",
        "entry": "",
        "position": "",
        "stop_loss": "",
        "resistance": "",
        "timeframe": "",
        "core_action": "",
        "full_text": decision_text,
    }
    if not decision_text:
        return result

    lines = decision_text.split("\n")

    # 提取评级：查找包含 低配/高配/持有/Underweight/Overweight/Hold 等关键词的行
    rating_patterns = [
        r"[低高]配[（(].+[)）]", r"持有", r"买入", r"卖出",
        r"Underweight", r"Overweight", r"Hold", r"Buy", r"Sell",
    ]
    for _i, line in enumerate(lines):
        for pat in rating_patterns:
            if re.search(pat, line, re.IGNORECASE):
                # 清理 markdown 标记
                clean = line.replace("#", "").replace("**", "").replace(">", "").strip()
                if clean:
                    result["rating"] = clean
                    break
        if result["rating"]:
            break

    # 提取执行摘要表格中的字段
    in_summary = False
    for line in lines:
        if "执行摘要" in line or "Executive Summary" in line:
            in_summary = True
            continue
        if in_summary and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                key = parts[0].replace("**", "").strip()
                val = parts[1].replace("**", "").strip()
                if "入场" in key or "entry" in key.lower():
                    result["entry"] = val
                elif "仓位" in key or "position" in key.lower():
                    result["position"] = val
                elif "止损" in key or "stop" in key.lower():
                    result["stop_loss"] = val
                elif "阻力" in key or "resistance" in key.lower():
                    result["resistance"] = val
                elif "时间" in key or "time" in key.lower():
                    result["timeframe"] = val
        elif in_summary and not line.startswith("|") and line.strip():
            # 表格结束，查找核心行动
            if "核心行动" in line or "核心" in line:
                result["core_action"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                # 也可能是下一行
            elif result["entry"] and not result["core_action"]:
                # 可能是核心行动的延续
                pass

    # 如果核心行动未找到，尝试从段落中提取
    if not result["core_action"]:
        for line in lines:
            if "核心行动" in line:
                result["core_action"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                break
            elif "核心" in line and "：" in line:
                result["core_action"] = line.split("：", 1)[-1].strip()
                break

    return result


# =====================================================================
# 函数：sanitize_for_console(text)
# 功能：清理文本中的特殊Unicode字符，避免Windows GBK编码错误
# 参数：text - 原始文本
# 返回：清理后的安全文本
# =====================================================================
def sanitize_for_console(text: str) -> str:
    """清理文本中的特殊Unicode字符，避免Windows GBK编码错误"""
    if not text:
        return text
    # 替换常见的特殊字符
    replacements = {
        "\u2022": "-",   # bullet
        "\u2023": "-",   # triangular bullet
        "\u25cf": "-",   # black circle
        "\u25cb": "-",   # white circle
        "\u2705": "[OK]",  # check mark
        "\u274c": "[X]",   # cross mark
        "\u26a0": "[!]",   # warning
        "\u2139": "[i]",   # info
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


# =====================================================================
# 函数：display_summary_report(record, ticker, date)
# 功能：以层级分明的方式展示分析摘要
# 参数：record - 分析记录字典; ticker - 股票代码; date - 分析日期
# =====================================================================
def display_summary_report(record: dict, ticker: str, date: str):
    """以摘要优先模式展示分析结果"""

    company = record.get("company_of_interest", ticker)
    final_decision = record.get("final_trade_decision", "")

    # 解析决策数据
    parsed = parse_final_decision(final_decision)

    console.print()

    # ---- 标题栏 ----
    console.print(Panel(
        f"[bold]{company}[/bold] ({ticker}) - {date}",
        title="Analysis Report",
        border_style="blue",
        padding=(0, 2),
    ))

    # ---- Level 1: 评级（最醒目）----
    rating = parsed.get("rating", "未知")
    # 根据评级选择颜色
    if any(w in rating for w in ["低配", "卖出", "Underweight", "Sell"]):
        rating_color = "bold red"
        rating_icon = "!!"
    elif any(w in rating for w in ["高配", "买入", "Overweight", "Buy"]):
        rating_color = "bold green"
        rating_icon = "+"
    else:
        rating_color = "bold yellow"
        rating_icon = "~"

    # 评级面板
    console.print(Panel(
        f"[{rating_color}] {rating_icon} {rating}[/{rating_color}]",
        border_style="red" if "red" in rating_color else ("green" if "green" in rating_color else "yellow"),
        padding=(1, 2),
    ))

    # ---- Level 2: 关键指标表格 ----
    # 构建指标表格
    metrics_table = Table(show_header=False, box=None, padding=(0, 2))
    metrics_table.add_column("Key", style="bold cyan", width=12)
    metrics_table.add_column("Value")

    if parsed["stop_loss"]:
        metrics_table.add_row("Stop Loss", parsed["stop_loss"])
    if parsed["resistance"]:
        metrics_table.add_row("Resistance", parsed["resistance"])
    if parsed["position"]:
        metrics_table.add_row("Position", parsed["position"])
    if parsed["timeframe"]:
        metrics_table.add_row("Timeframe", parsed["timeframe"])

    if metrics_table.row_count > 0:
        console.print(Panel(
            metrics_table,
            title="Key Metrics",
            border_style="cyan",
            padding=(1, 0),
        ))

    # ---- Level 3: 核心逻辑 ----
    core_action = parsed.get("core_action", "")
    if core_action:
        console.print(Panel(
            core_action,
            title="Core Logic",
            border_style="green",
            padding=(1, 2),
        ))

    # ---- 详细报告选项 ----
    console.print()
    show_detail = typer.prompt("Show detailed reports?", default="n").strip().upper()
    if show_detail in ("Y", "YES"):
        display_complete_report(record)


# =====================================================================
# 函数：display_complete_report(final_state)
# 功能：在终端中按顺序显示完整的分析报告
# 参数：final_state - 最终状态字典
#
# 使用 Rule 组件创建分隔线，使用 Panel 组件展示每个章节
# =====================================================================
def display_complete_report(final_state):
    """在终端中按顺序显示完整分析报告（避免截断）"""
    console.print()
    # Rule 组件：水平分割线
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # ---- I. 分析师团队报告 ----
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            content = sanitize_for_console(content[:1000] + "..." if len(content) > 1000 else content)
            console.print(Panel(content, title=title, border_style="blue", padding=(1, 2)))

    # ---- II. 研究团队决策 ----
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                content = sanitize_for_console(content[:1000] + "..." if len(content) > 1000 else content)
                console.print(Panel(content, title=title, border_style="blue", padding=(1, 2)))

    # ---- III. 交易团队计划 ----
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        content = sanitize_for_console(final_state["trader_investment_plan"][:1000] + "..." if len(final_state["trader_investment_plan"]) > 1000 else final_state["trader_investment_plan"])
        console.print(Panel(content, title="Trader", border_style="blue", padding=(1, 2)))

    # ---- IV. 风险管理团队决策 ----
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                content = sanitize_for_console(content[:1000] + "..." if len(content) > 1000 else content)
                console.print(Panel(content, title=title, border_style="blue", padding=(1, 2)))

        # ---- V. 投资组合经理决策 ----
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            content = sanitize_for_console(risk["judge_decision"][:1000] + "..." if len(risk["judge_decision"]) > 1000 else risk["judge_decision"])
            console.print(Panel(content, title="Portfolio Manager", border_style="blue", padding=(1, 2)))


# =====================================================================
# 函数：update_research_team_status(status)
# 功能：批量更新研究团队所有成员的状态
# 参数：status - 新状态（pending/in_progress/completed/error）
#
# 研究团队包括：Bull Researcher、Bear Researcher、Research Manager
# 注意：不包括 Trader（交易员属于交易团队）
# =====================================================================
def update_research_team_status(status):
    """更新研究团队成员的状态（不包括交易员）"""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# =====================================================================
# 分析师相关常量定义
# =====================================================================

# ANALYST_ORDER：分析师的执行顺序
# 决定了状态转换的顺序：market → social → news → fundamentals
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]

# ANALYST_AGENT_NAMES：分析师键到 Agent 显示名称的映射
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

# ANALYST_REPORT_MAP：分析师键到报告章节键的映射
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


# =====================================================================
# 函数：update_analyst_statuses(message_buffer, chunk)
# 功能：根据分析图返回的当前数据块更新分析师状态
# 参数：
#   message_buffer - MessageBuffer 实例
#   chunk - 分析图流式返回的数据块
#
# 状态转换逻辑：
#   - 有报告内容的分析师 → completed
#   - 第一个没有报告的分析师 → in_progress
#   - 其余没有报告的分析师 → pending
#   - 所有分析师完成后 → 将 Bull Researcher 设为 in_progress
#
# 设计要点：
#   1. 状态判断基于累积的 report_sections（而非仅当前数据块）
#   2. 顺序执行：按 ANALYST_ORDER 定义的顺序逐个处理
#   3. 自动过渡：所有分析师完成后自动启动研究团队
# =====================================================================
def update_analyst_statuses(message_buffer, chunk):
    """根据累积的报告状态更新分析师状态

    逻辑：
    - 存储当前数据块中的新报告内容
    - 基于累积的 report_sections（而非仅当前数据块）检查状态
    - 有报告的分析师 = completed
    - 第一个没有报告的分析师 = in_progress
    - 其余没有报告的分析师 = pending
    - 所有分析师完成后，将 Bull Researcher 设为 in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False  # 标记是否已找到当前正在执行的分析师

    for analyst_key in ANALYST_ORDER:
        # 跳过用户未选择的分析师
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # 从当前数据块中捕获新的报告内容
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # 基于累积的报告章节判断状态（而非仅当前数据块）
        # bool() 将 None 转为 False，有内容转为 True
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            # 该分析师已完成报告
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            # 第一个还没有报告的分析师 = 正在执行
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            # 后面还没有报告的分析师 = 等待中
            message_buffer.update_agent_status(agent_name, "pending")

    # 当所有分析师完成时，过渡到研究团队
    # found_active 为 False 意味着没有分析师处于 in_progress 状态
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")


# =====================================================================
# 函数：extract_content_string(content)
# 功能：从各种消息格式中提取文本内容
# 参数：content - 消息内容（可能是字符串、字典或列表）
# 返回值：提取的文本字符串，如果没有有效内容则返回 None
#
# LangChain 消息内容格式多样：
#   - 字符串："Hello World"
#   - 字典：{"text": "Hello World", "type": "text"}
#   - 列表：[{"text": "Hello", "type": "text"}, {"text": "World", "type": "text"}]
# =====================================================================
def extract_content_string(content):
    """从各种消息格式中提取字符串内容。

    如果没有找到有意义的文本内容，返回 None。
    """
    import ast  # ast 模块：Python 标准库，用于解析 Python 字面量

    # 内部函数：检查值是否为空
    def is_empty(val):
        """使用 Python 的真值判断检查值是否为空"""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                # ast.literal_eval() 安全地解析 Python 字面量
                # 例如 "[]" → []，空列表为空
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                # 无法解析 = 真实文本，不是空
                return False
        return not bool(val)

    if is_empty(content):
        return None

    # 处理字符串类型
    if isinstance(content, str):
        return content.strip()

    # 处理字典类型（如 {"text": "...", "type": "text"}）
    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    # 处理列表类型（多段文本组合）
    if isinstance(content, list):
        text_parts = [
            # 列表推导式 + 条件表达式
            # 如果是字典且 type 为 "text"，取 text 字段
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            # 如果是字符串，直接使用
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        # ' '.join() 用空格连接各部分
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    # 回退处理：转为字符串
    return str(content).strip() if not is_empty(content) else None


# =====================================================================
# 函数：classify_message_type(message)
# 功能：将 LangChain 消息分类为显示类型
# 参数：message - LangChain 消息对象
# 返回值：元组 (类型, 内容)
#
# 消息类型映射：
#   - HumanMessage → "User" 或 "Control"（如果是 "Continue"）
#   - ToolMessage → "Data"
#   - AIMessage → "Agent"
#   - 其他 → "System"
# =====================================================================
def classify_message_type(message) -> tuple[str, str | None]:
    """将 LangChain 消息分类为显示类型并提取内容

    返回：
        (type, content) - type 是以下之一：User, Agent, Data, Control
                        - content 是提取的字符串或 None
    """
    # 在函数内部导入，避免循环导入问题
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    # getattr(obj, 'attr', default) 安全获取属性
    content = extract_content_string(getattr(message, 'content', None))

    # isinstance() 检查对象类型

    if isinstance(message, HumanMessage):
        # 特殊处理："Continue" 消息表示控制流（继续执行）
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        # 模拟数据必须醒目区分，避免用户把伪造行情当成真实数据
        if content and (
            "[SIMULATED DATA]" in content
            or "simulated data for testing" in content
            or "(generated)" in content
            or "(cached)" in content
            or "Cached indicator data" in content
        ):
            return ("Simulated", content)
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # 未知类型的默认处理
    return ("System", content)


# =====================================================================
# 函数：format_tool_args(args, max_length=80)
# 功能：格式化工具参数为适合终端显示的字符串
# 参数：
#   args - 工具参数（字典或字符串）
#   max_length - 最大显示长度，超过则截断
# 返回值：格式化后的字符串
# =====================================================================
def format_tool_args(args, max_length=80) -> str:
    """格式化工具参数供终端显示，超长时截断"""
    result = str(args)
    if len(result) > max_length:
        # 切片截断：保留前 max_length-3 个字符，添加 "..."
        return result[:max_length - 3] + "..."
    return result


# =====================================================================
# 函数：run_analysis()
# 功能：执行完整的分析流程
#
# 流程概述：
#   1. 获取用户配置（get_user_selections）
#   2. 创建配置字典
#   3. 创建统计回调处理器
#   4. 初始化 TradingAgentsGraph（分析引擎）
#   5. 初始化消息缓冲区
#   6. 创建结果目录
#   7. 设置日志装饰器（将消息/工具调用写入文件）
#   8. 创建 Live 实时显示布局
#   9. 流式处理分析图输出
#   10. 更新终端 UI
#   11. 分析完成后，提示保存和显示报告
#
# 核心概念 - Live 实时显示：
#   Rich 的 Live 组件创建一个自动刷新的终端上下文。
#   在 Live 上下文中，每次调用 update_display() 后，
#   终端画面会自动刷新，实现实时更新效果。
#   refresh_per_second=4 表示每秒最多刷新 4 次。
#
# 核心概念 - 图流式处理（Graph Streaming）：
#   graph.graph.stream() 是 LangGraph 的流式接口，
#   它会逐步返回分析过程中的中间状态。
#   每个 chunk 包含分析图在某个节点的输出。
#   通过处理每个 chunk，可以实现实时进度显示。
# =====================================================================
def run_analysis():
    """
    执行完整的分析流程

    步骤：
    1. 获取用户配置
    2. 初始化后端引擎和 UI
    3. 流式执行分析
    4. 实时更新终端显示
    5. 保存和展示结果
    """

    # ==================================================================
    # 步骤 1：获取用户配置
    # ==================================================================
    selections = get_user_selections()

    # 校验分析日期：A 股非交易日（周末/节假日）给出提示并允许自动调整
    try:
        from tradingagents.dataflows.trade_calendar import validate_analysis_date

        date_ok, date_msg, adjusted_date = validate_analysis_date(
            selections["ticker"], selections["analysis_date"]
        )
        if not date_ok:
            console.print(f"[yellow]Warning: {date_msg}[/yellow]")
            if adjusted_date and typer.confirm(
                f"是否改用最近的交易日 {adjusted_date}？", default=True
            ):
                selections["analysis_date"] = adjusted_date
    except Exception:
        # 交易日历不可用时不影响主流程
        pass

    # ==================================================================
    # 步骤 2：创建配置字典
    # ==================================================================
    # DEFAULT_CONFIG.copy() 创建默认配置的浅拷贝
    # 避免修改原始默认配置
    config = DEFAULT_CONFIG.copy()

    # 用用户选择的参数覆盖默认配置
    config["max_debate_rounds"] = selections["research_depth"]        # 辩论轮数
    config["max_risk_discuss_rounds"] = selections["research_depth"]  # 风险讨论轮数
    config["quick_think_llm"] = selections["shallow_thinker"]         # 浅层思考模型
    config["deep_think_llm"] = selections["deep_thinker"]             # 深层思考模型
    config["backend_url"] = selections["backend_url"]                 # API 基础 URL
    config["llm_provider"] = selections["llm_provider"].lower()       # LLM 提供商

    # 提供商特定的思考配置
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")

    # ==================================================================
    # 步骤 3：创建统计回调处理器
    # ==================================================================
    # StatsCallbackHandler 会在每次 LLM 调用和工具调用时自动收集统计数据
    stats_handler = StatsCallbackHandler()

    # ==================================================================
    # 步骤 4：初始化分析引擎
    # ==================================================================
    # 将分析师选择规范化为固定顺序
    # 集合（set）用于去重和快速查找，但顺序不固定
    selected_set = {analyst.value for analyst in selections["analysts"]}
    # 按 ANALYST_ORDER 定义的顺序排列
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    # 创建 TradingAgentsGraph 实例
    # 参数说明：
    #   selected_analyst_keys：用户选择的分析师类型列表
    #   config：配置字典
    #   debug：是否启用调试模式
    #   callbacks：回调处理器列表，用于跟踪 LLM 和工具调用
    try:
        graph = TradingAgentsGraph(
            selected_analyst_keys,
            config=config,
            debug=True,
            callbacks=[stats_handler],
        )
    except RuntimeError as exc:
        # 缺少 API Key 等配置错误：给出可操作提示，而不是打印长 traceback
        console.print(f"\n[red]{exc}[/red]")
        console.print(
            "[yellow]请编辑项目根目录的 .env 并重启程序后重试。[/yellow]"
        )
        return

    # ==================================================================
    # 步骤 5：初始化消息缓冲区
    # ==================================================================
    message_buffer.init_for_analysis(selected_analyst_keys)

    # 记录分析开始时间（用于计算耗时）
    start_time = time.time()

    # ==================================================================
    # 步骤 6：创建结果目录
    # ==================================================================
    # Path 对象可以通过 / 运算符拼接路径（Python 3.6+）
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)

    # 报告子目录
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # 日志文件
    log_file = results_dir / "message_tool.log"
    # touch() 创建空文件（如果不存在）
    log_file.touch(exist_ok=True)

    # ==================================================================
    # 步骤 7：设置日志装饰器
    # ==================================================================
    # 装饰器模式（Decorator Pattern）：在不修改原函数的情况下，
    # 为函数添加额外的行为（如日志记录）。
    #
    # 下面的装饰器会在 message_buffer 的方法被调用后，
    # 自动将数据写入日志文件。

    def save_message_decorator(obj, func_name):
        """
        创建消息日志装饰器

        参数：
            obj：被装饰的对象（message_buffer）
            func_name：被装饰的方法名（如 "add_message"）

        工作原理：
        1. getattr(obj, func_name) 获取原始方法
        2. @wraps(func) 保留原方法的元数据
        3. wrapper 先调用原方法，再记录到日志文件
        """
        func = getattr(obj, func_name)

        @wraps(func)  # 保留原函数名、文档字符串等元数据
        def wrapper(*args, **kwargs):
            # 调用原始方法
            func(*args, **kwargs)
            # 获取刚刚添加的消息（最后一条）
            timestamp, message_type, content = obj.messages[-1]
            # 将换行符替换为空格（日志文件一行一条记录）
            content = content.replace("\n", " ")
            # 追加写入日志文件
            with open(log_file, "a") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper

    def save_tool_call_decorator(obj, func_name):
        """
        创建工具调用日志装饰器

        与 save_message_decorator 类似，但专门记录工具调用
        """
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            # 格式化参数字符串：key=value, key=value
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        """
        创建报告章节日志装饰器

        将每个报告章节的内容保存为单独的 Markdown 文件
        """
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            # 检查章节是否存在且有内容
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    # 如果内容是列表，转为字符串
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w") as f:
                        f.write(text)
        return wrapper

    # 用装饰后的方法替换原始方法
    # 这里使用了"猴子补丁"（Monkey Patching）技术：
    # 在运行时动态替换对象的方法
    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # ==================================================================
    # 步骤 8：创建 Live 实时显示布局
    # ==================================================================
    layout = create_layout()

    # Live 上下文管理器：创建自动刷新的终端 UI
    # refresh_per_second=4：每秒刷新 4 次（终端刷新率）
    # 在 Live 上下文中，对 layout 的修改会自动反映到终端
    with Live(layout, refresh_per_second=4):
        # 初始显示
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 添加初始系统消息
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        message_buffer.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 将第一个分析师的状态设为 in_progress
        # capitalize() 将首字母大写
        first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # 创建旋转动画文本
        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        # ==============================================================
        # 步骤 9：流式执行分析
        # ==============================================================
        # 初始化 Agent 状态
        # propagator 是图的传播器，负责创建初始状态
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"], selections["analysis_date"]
        )

        # 获取图运行参数，传入回调处理器
        # 回调处理器用于跟踪工具调用（LLM 跟踪由 LLM 构造函数单独处理）
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # graph.graph.stream() 流式执行分析图
        # 每次返回一个数据块（chunk），包含分析图的中间状态
        trace = []  # 存储所有数据块，用于最终分析
        for chunk in graph.graph.stream(init_agent_state, **args):
            # ==========================================================
            # 处理消息
            # ==========================================================
            if len(chunk["messages"]) > 0:
                last_message = chunk["messages"][-1]  # 取最后一条消息
                msg_id = getattr(last_message, "id", None)

                # 去重：跳过已经处理过的消息
                if msg_id != message_buffer._last_message_id:
                    message_buffer._last_message_id = msg_id

                    # 分类消息并添加到缓冲区
                    msg_type, content = classify_message_type(last_message)
                    if content and content.strip():
                        message_buffer.add_message(msg_type, content)

                    # 处理工具调用（AIMessage 可能包含 tool_calls）
                    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                        for tool_call in last_message.tool_calls:
                            # tool_call 可能是字典或对象，需要兼容处理
                            if isinstance(tool_call, dict):
                                message_buffer.add_tool_call(
                                    tool_call["name"], tool_call["args"]
                                )
                            else:
                                message_buffer.add_tool_call(tool_call.name, tool_call.args)

            # ==========================================================
            # 更新分析师状态
            # ==========================================================
            update_analyst_statuses(message_buffer, chunk)

            # ==========================================================
            # 处理研究团队（Investment Debate State）
            # ==========================================================
            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                # 当有内容时，更新研究团队状态
                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    # 研究经理决策完成后，研究团队完成，启动交易员
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            # ==========================================================
            # 处理交易团队
            # ==========================================================
            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    # 交易员完成后，启动风险管理团队
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            # ==========================================================
            # 处理风险管理团队（Risk Debate State）
            # ==========================================================
            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                    )
                if judge:
                    if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                        # 投资组合经理做最终决策
                        message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                        )
                        # 所有风险管理分析师和投资组合经理都标记为完成
                        message_buffer.update_agent_status("Aggressive Analyst", "completed")
                        message_buffer.update_agent_status("Conservative Analyst", "completed")
                        message_buffer.update_agent_status("Neutral Analyst", "completed")
                        message_buffer.update_agent_status("Portfolio Manager", "completed")

            # 每次迭代后更新终端显示
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            # 记录数据块到追踪列表
            trace.append(chunk)

        # ==============================================================
        # 步骤 10：分析完成后的处理
        # ==============================================================
        # 获取最终状态
        final_state = trace[-1]
        # 处理最终交易信号
        decision = graph.process_signal(final_state["final_trade_decision"])

        # 将所有 Agent 标记为完成
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )

        # 更新最终报告章节
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    # 持久化到本地 SQLite，供 `history` / `view` 和 WebUI 报告中心使用
    try:
        from tradingagents.storage import save_report

        report_id = save_report(
            ticker=selections["ticker"],
            trade_date=selections["analysis_date"],
            decision=decision,
            decision_text=final_state.get("final_trade_decision", ""),
            reports={
                "market_report": final_state.get("market_report", ""),
                "sentiment_report": final_state.get("sentiment_report", ""),
                "news_report": final_state.get("news_report", ""),
                "fundamentals_report": final_state.get("fundamentals_report", ""),
                "investment_plan": final_state.get("investment_plan", ""),
                "trader_investment_plan": final_state.get(
                    "trader_investment_plan", ""
                ),
                "final_trade_decision": final_state.get(
                    "final_trade_decision", ""
                ),
            },
            source="cli",
            meta={
                "llm_provider": selections.get("llm_provider"),
                "deep_think_llm": selections.get("deep_thinker"),
                "quick_think_llm": selections.get("shallow_thinker"),
            },
        )
        console.print(f"[dim]报告已保存到本地数据库 (id={report_id})[/dim]")
    except Exception as exc:
        console.print(f"[yellow]报告持久化失败: {exc}[/yellow]")

    # ==================================================================
    # 步骤 11：分析后交互（在 Live 上下文之外）
    # ==================================================================
    # 在 Live 上下文之外，使用普通的终端交互方式
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # 提示保存报告
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        # 生成时间戳用于文件名
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Path.cwd() 获取当前工作目录
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green](check) Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # 使用摘要优先模式展示结果
    display_summary_report(final_state, selections["ticker"], selections["analysis_date"])


# =====================================================================
# Typer 命令定义
# =====================================================================

@app.command()
def history(
    ticker: str | None = typer.Argument(None, help="股票代码，不指定则显示所有记录"),
):
    """
    查看分析历史记录

    示例：
      tradingagents history           # 显示所有历史记录
      tradingagents history 海南橡胶   # 显示海南橡胶的历史记录
      tradingagents history 601118    # 显示601118的历史记录
    """
    # 优先读取 SQLite 报告中心（CLI 与 WebUI 共用）
    try:
        from tradingagents.storage import list_reports

        db_reports = list_reports(ticker=ticker, limit=200)
        if db_reports:
            table = Table(title="分析历史记录（数据库）", box=box.ROUNDED)
            table.add_column("ID", style="dim", width=6)
            table.add_column("股票", style="cyan", no_wrap=True)
            table.add_column("日期", style="green")
            table.add_column("决策", style="yellow")
            table.add_column("来源", style="magenta")
            table.add_column("创建时间", style="dim")
            for item in db_reports:
                table.add_row(
                    str(item["id"]),
                    item["ticker"],
                    item["trade_date"],
                    item["decision"] or "未知",
                    item.get("source", ""),
                    item.get("created_at", ""),
                )
            console.print(table)
            console.print(
                f"\n[dim]共 {len(db_reports)} 条记录，使用 "
                "tradingagents view <股票> <日期> 查看详情[/dim]"
            )
            return
    except Exception:
        # 数据库不可用时回退到旧版 eval_results JSON
        pass

    results_dir = Path("eval_results")

    if not results_dir.exists():
        console.print("[yellow]暂无分析历史记录[/yellow]")
        return

    # 收集所有历史记录
    records = []
    for stock_dir in results_dir.iterdir():
        if not stock_dir.is_dir():
            continue

        log_dir = stock_dir / "TradingAgentsStrategy_logs"
        if not log_dir.exists():
            continue

        stock_name = stock_dir.name

        # 如果指定了ticker，过滤
        if ticker and ticker != stock_name:
            # 也检查代码是否匹配
            json_files = list(log_dir.glob("full_states_log_*.json"))
            if not json_files:
                continue
            # 检查文件内容中的代码
            try:
                import json
                with open(json_files[0], encoding="utf-8") as f:
                    data = json.load(f)
                first_key = list(data.keys())[0] if data else None
                if first_key:
                    company = data[first_key].get("company_of_interest", "")
                    if ticker != stock_name and ticker not in company:
                        continue
            except Exception:
                continue

        for json_file in sorted(log_dir.glob("full_states_log_*.json"), reverse=True):
            try:
                import json
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                for date_key, record in data.items():
                    decision = record.get("final_trade_decision", "")
                    # 提取评级（第一行）
                    rating_line = ""
                    for line in decision.split("\n"):
                        line = line.strip()
                        if line and ("低配" in line or "高配" in line or "持有" in line or
                                     "Underweight" in line or "Overweight" in line or
                                     "Hold" in line or "Buy" in line or "Sell" in line or
                                     "买入" in line or "卖出" in line):
                            rating_line = line.replace("#", "").replace("**", "").strip()
                            break

                    records.append({
                        "stock": stock_name,
                        "date": date_key,
                        "rating": rating_line or "未知",
                        "file": str(json_file),
                    })
            except Exception:
                continue

    if not records:
        console.print("[yellow]暂无分析历史记录[/yellow]")
        return

    # 按日期倒序排列
    records.sort(key=lambda x: x["date"], reverse=True)

    # 显示表格
    table = Table(title="分析历史记录", box=box.ROUNDED)
    table.add_column("股票", style="cyan", no_wrap=True)
    table.add_column("日期", style="green")
    table.add_column("评级", style="yellow")
    table.add_column("文件路径", style="dim")

    for record in records:
        # 截断文件路径，只显示相对路径
        file_path = record["file"]
        if "eval_results" in file_path:
            file_path = file_path.split("eval_results/")[-1] if "eval_results/" in file_path else file_path

        table.add_row(
            record["stock"],
            record["date"],
            record["rating"],
            file_path,
        )

    console.print(table)
    console.print(f"\n[dim]共 {len(records)} 条记录[/dim]")
    console.print("[dim]使用 tradingagents view <股票名> <日期> 查看详细报告[/dim]")


@app.command()
def view(
    ticker: str = typer.Argument(..., help="股票代码或名称"),
    date: str | None = typer.Argument(None, help="分析日期 (YYYY-MM-DD)，不指定则显示最新"),
):
    """
    查看特定股票的分析报告

    示例：
      tradingagents view 海南橡胶 2026-07-15
      tradingagents view 601118              # 显示最新报告
    """
    # 优先读取 SQLite 报告中心
    try:
        from tradingagents.storage import list_reports

        db_reports = list_reports(ticker=ticker, limit=200)
        if not db_reports:
            # 允许用中文名/代码互相查找
            all_reports = list_reports(limit=200)
            db_reports = [
                r for r in all_reports if ticker in r["ticker"]
            ]
        target = None
        if date:
            target = next(
                (r for r in db_reports if r["trade_date"] == date), None
            )
        elif db_reports:
            target = db_reports[0]

        if target:
            flat = dict(target.get("reports") or {})
            flat.update(
                {
                    "company_of_interest": target["ticker"],
                    "trade_date": target["trade_date"],
                    "final_trade_decision": flat.get(
                        "final_trade_decision", target.get("decision_text", "")
                    ),
                }
            )
            display_summary_report(flat, target["ticker"], target["trade_date"])
            console.print(
                f"\n[dim]数据库记录 id={target['id']}，创建于 {target['created_at']}[/dim]"
            )
            return
    except Exception:
        pass

    results_dir = Path("eval_results")
    stock_dir = results_dir / ticker

    if not results_dir.exists():
        console.print(f"[red]未找到股票 {ticker} 的分析记录[/red]")
        return

    if not stock_dir.exists():
        # 尝试查找包含该ticker的目录
        for d in results_dir.iterdir():
            if d.is_dir():
                log_dir = d / "TradingAgentsStrategy_logs"
                if log_dir.exists():
                    for json_file in log_dir.glob("full_states_log_*.json"):
                        try:
                            import json
                            with open(json_file, encoding="utf-8") as f:
                                data = json.load(f)
                            for _k, v in data.items():
                                if ticker in v.get("company_of_interest", ""):
                                    stock_dir = d
                                    break
                        except Exception:
                            pass
                if stock_dir.exists():
                    break

    if not stock_dir.exists():
        console.print(f"[red]未找到股票 {ticker} 的分析记录[/red]")
        return

    log_dir = stock_dir / "TradingAgentsStrategy_logs"
    if not log_dir.exists():
        console.print(f"[red]未找到 {ticker} 的日志目录[/red]")
        return

    # 查找指定日期或最新的日志
    json_files = sorted(log_dir.glob("full_states_log_*.json"), reverse=True)
    if not json_files:
        console.print(f"[red]未找到 {ticker} 的日志文件[/red]")
        return

    target_file = None
    if date:
        for f in json_files:
            if date in f.name:
                target_file = f
                break
        if not target_file:
            console.print(f"[red]未找到 {ticker} 在 {date} 的分析记录[/red]")
            console.print("[yellow]可用日期:[/yellow]")
            for f in json_files[:5]:
                console.print(f"  - {f.name.replace('full_states_log_', '').replace('.json', '')}")
            return
    else:
        target_file = json_files[0]

    try:
        import json
        with open(target_file, encoding="utf-8") as f:
            data = json.load(f)

        # 获取第一个（也是唯一的）日期记录
        date_key = list(data.keys())[0]
        record = data[date_key]

        # 使用新的摘要展示模式
        display_summary_report(record, ticker, date_key)

        console.print()
        console.print(f"[dim]Full log: {target_file}[/dim]")

    except Exception as e:
        error_msg = str(e).replace("\u2022", "-").replace("\u2023", "-").replace("\u25cf", "-").replace("\u25cb", "-")
        console.print(f"[red]Failed to read log file: {error_msg}[/red]")


@app.command()
def backtest(
    ticker: str = typer.Argument(..., help="股票代码或名称，如 600519 / 贵州茅台"),
    start: str | None = typer.Option(None, "--start", help="开始日期 YYYY-MM-DD，默认两年前"),
    end: str | None = typer.Option(None, "--end", help="结束日期 YYYY-MM-DD，默认今天"),
    strategy: str = typer.Option("ma_cross", "--strategy", help="策略：ma_cross 或 buy_hold"),
    fast: int = typer.Option(5, "--fast", help="快均线周期"),
    slow: int = typer.Option(20, "--slow", help="慢均线周期"),
    initial_cash: float = typer.Option(100000.0, "--cash", help="初始资金"),
):
    """轻量回测（内置 A 股交易成本：佣金 + 印花税）。

    示例：
      alphaagents backtest 600519
      alphaagents backtest 贵州茅台 --start 2023-01-01 --end 2024-12-31
      alphaagents backtest 000001 --strategy buy_hold
    """
    from tradingagents.services.backtest import run_backtest

    end_date = end or datetime.datetime.now().strftime("%Y-%m-%d")
    if start:
        start_date = start
    else:
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (end_dt - datetime.timedelta(days=730)).strftime("%Y-%m-%d")

    console.print(
        f"[dim]回测 {ticker}：{start_date} ~ {end_date}，策略 {strategy}[/dim]"
    )
    try:
        result = run_backtest(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            strategy=strategy,
            fast=fast,
            slow=slow,
            initial_cash=initial_cash,
        )
    except Exception as exc:
        console.print(f"[red]回测失败: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"{result['ticker']} 回测结果", box=box.ROUNDED)
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    rows = [
        ("区间", f"{result['start_date']} ~ {result['end_date']}"),
        ("策略", result["strategy"]),
        ("K线数量", str(result["bars"])),
        ("初始资金", f"{result['initial_cash']:,.0f}"),
        ("期末权益", f"{result['final_equity']:,.2f}"),
        ("总收益", f"{result['total_return'] * 100:.2f}%"),
        ("毛收益(不含成本)", f"{result.get('gross_return', 0) * 100:.2f}%"),
        ("年化收益", f"{result['annualized_return'] * 100:.2f}%"),
        ("最大回撤", f"{result['max_drawdown'] * 100:.2f}%"),
        ("夏普比率", f"{result['sharpe']:.3f}"),
        ("交易次数", str(result["trade_count"])),
        ("胜率", f"{result['win_rate'] * 100:.1f}%"),
    ]
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)
    console.print(
        "[dim]已计入佣金与卖出印花税；结果为历史回测，不构成投资建议。[/dim]"
    )


@app.command("strategies")
def strategy_scan(
    ticker: str = typer.Argument(..., help="股票代码或名称，如 600519 / 贵州茅台"),
    date: str | None = typer.Option(None, "--date", help="分析日期 YYYY-MM-DD，默认最新"),
    strategies: str | None = typer.Option(
        None, "--strategies", help="逗号分隔的策略 key，如 bull_trend,volume_breakout"
    ),
    run_all: bool = typer.Option(False, "--all", help="运行全部 15 个策略"),
    json_output: bool = typer.Option(False, "--json", help="输出完整 JSON"),
):
    """运行选中的经典策略（默认核心组合）。

    示例：
      alphaagents strategies 600519
      alphaagents strategies 600519 --strategies bull_trend,volume_breakout
      alphaagents strategies 600519 --all
      alphaagents strategies 贵州茅台 --date 2026-01-02 --json
    """
    import json

    from tradingagents.strategies import STRATEGY_META, scan_all_strategies

    keys = None
    if run_all:
        keys = [item["key"] for item in STRATEGY_META]
    elif strategies:
        keys = [item.strip() for item in strategies.split(",") if item.strip()]

    try:
        result = scan_all_strategies(ticker, date, keys)
    except Exception as exc:
        console.print(f"[red]策略扫描失败: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    summary = result["summary"]
    price = result["price"]
    console.print(
        f"[bold]{result['ticker']}[/bold] · {result['as_of']} · "
        f"共识 [cyan]{summary['consensus']}[/cyan]（强度 {summary['strength']}%）· "
        f"触发 {summary['triggered']} 个（多 {summary['bullish']} / 空 {summary['bearish']}）"
    )
    console.print(
        f"[dim]收盘 {price['close']}（{price['change_pct']}%） · "
        f"MA20 {price['ma20']} · RSI {price['rsi']} · 量比 {price['volume_ratio']}[/dim]"
    )

    table = Table(title="触发策略", box=box.ROUNDED)
    table.add_column("策略", style="cyan")
    table.add_column("方向", style="yellow")
    table.add_column("评分", justify="right")
    table.add_column("说明")
    triggered = [s for s in result["signals"] if s["triggered"]]
    if not triggered:
        console.print("[yellow]本次没有触发策略[/yellow]")
        return
    for signal in triggered:
        table.add_row(
            signal["name"],
            "偏多" if signal["direction"] == "bullish" else "偏空",
            str(signal["score"]),
            signal["summary"],
        )
    console.print(table)
    console.print("[dim]策略结果为规则化近似，仅供研究参考。[/dim]")


watch_app = typer.Typer(help="自选股管理")
app.add_typer(watch_app, name="watch")


@watch_app.command("add")
def watch_add(
    ticker: str = typer.Argument(..., help="股票代码或名称"),
    name: str = typer.Option("", "--name", help="备注名称"),
):
    """添加自选股。"""
    from tradingagents.storage import add_watch

    item = add_watch(ticker, name)
    console.print(f"[green]已添加自选股:[/green] {item.get('ticker')} {item.get('name', '')}")


@watch_app.command("list")
def watch_list():
    """列出自选股。"""
    from tradingagents.storage import list_watchlist

    items = list_watchlist()
    if not items:
        console.print("[yellow]暂无自选股[/yellow]")
        return
    table = Table(title="自选股", box=box.ROUNDED)
    table.add_column("代码", style="cyan")
    table.add_column("名称")
    table.add_column("添加时间", style="dim")
    for item in items:
        table.add_row(item["ticker"], item.get("name", ""), item.get("created_at", ""))
    console.print(table)


@watch_app.command("remove")
def watch_remove(ticker: str = typer.Argument(..., help="股票代码")):
    """删除自选股。"""
    from tradingagents.storage import remove_watch

    if remove_watch(ticker):
        console.print(f"[green]已删除:[/green] {ticker}")
    else:
        console.print(f"[yellow]未找到:[/yellow] {ticker}")


@app.command()
def analyze():
    """
    主分析命令

    执行完整的交易分析流程：
    1. 引导用户完成配置
    2. 运行多 Agent 分析
    3. 实时显示进度
    4. 保存和展示结果
    """
    run_analysis()


# =====================================================================
# 程序入口点
# =====================================================================
# if __name__ == "__main__"：Python 的标准入口点检查
# 当文件作为脚本直接运行时（python main.py），__name__ 为 "__main__"
# 当文件作为模块导入时（import main），__name__ 为模块名
# 这确保 app() 只在直接运行时执行，导入时不会执行
if __name__ == "__main__":
    app()
