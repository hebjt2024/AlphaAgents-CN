# =====================================================================
# 模块：cli/announcements.py
# 功能：公告获取与显示模块
# 说明：从远程 API 获取最新公告，并在终端中显示给用户。
#       使用 requests 库进行 HTTP 请求，
#       使用 Rich 库的 Panel 组件美化公告显示。
#
# 核心概念：
#   - requests.get()：发送 HTTP GET 请求获取数据
#   - timeout：设置请求超时时间，防止程序卡死
#   - Rich Panel：在终端中显示带边框的面板
#   - getpass：安全地获取用户输入（不显示输入内容）
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件负责从远程服务器获取公告并显示在终端中。以下是需要了解的核心概念：
#
# 1. HTTP 请求（requests 库）：
#    - requests 是 Python 最流行的 HTTP 客户端库。
#    - 安装方式：pip install requests
#    - HTTP GET 请求：从服务器获取数据，类似于浏览器打开网页。
#    - requests.get(url, timeout=1.0) 发送 GET 请求，最多等待 1 秒。
#    - response.raise_for_status() 检查状态码：200-299 正常，400+ 表示错误。
#    - response.json() 将 JSON 格式的响应体解析为 Python 字典。
#    - 示例：
#        response = requests.get("https://api.example.com/data", timeout=1.0)
#        data = response.json()  # {"key": "value"}
#
# 2. Rich 库的 Panel 组件：
#    - Rich 是终端美化库（pip install rich），Panel 是其组件之一。
#    - Panel 将文本包裹在带边框的面板中，类似于终端中的"对话框"。
#    - 可设置标题（title）、边框颜色（border_style）、内边距（padding）。
#    - 显示效果：
#        ┌─ Announcements ──────────────┐
#        │                              │
#        │   公告内容...                 │
#        │                              │
#        └──────────────────────────────┘
#
# 3. 异常处理（try/except）：
#    - try 块中的代码可能出错，except 块捕获错误并处理。
#    - 容错设计（Graceful Degradation）：即使出错也不崩溃，返回备用结果。
#    - 示例：
#        try:
#            data = requests.get(url)  # 可能失败
#        except Exception:
#            data = "备用数据"  # 失败时使用
#
# 4. getpass 模块（按 Enter 继续）：
#    - Python 标准库 getpass，本意是安全获取密码（不显示输入）。
#    - 这里巧妙利用它实现"按 Enter 继续"的暂停效果：
#      程序暂停等待，用户按 Enter 后继续，输入内容不显示在屏幕上。
#
# 5. 本文件的运行时数据流：
#    程序启动
#    → fetch_announcements() 从 CLI_CONFIG 读取 URL 和超时时间
#    → requests.get(url, timeout=1.0) 向服务器发送 GET 请求
#    → 服务器返回 JSON：{"announcements": ["新版本发布！", "请更新"], "require_attention": true}
#    → response.json() 解析为 Python 字典
#    → 返回 {"announcements": ["新版本发布！", "请更新"], "require_attention": true}
#    → display_announcements(console, data) 在终端显示带边框的公告面板
#    → 如果 require_attention 为 True，等待用户按 Enter 键
#    → 如果请求失败，返回备用消息，程序继续运行
#
#    容错数据流示例：
#    fetch_announcements() → requests.get() → 网络超时 → 捕获 Exception
#    → 返回 {"announcements": [fallback_msg], "require_attention": False}
#    → display_announcements() 显示备用消息，不等待用户按 Enter
# =====================================================================

# getpass 模块：Python 标准库，用于安全地获取密码等敏感输入
# 在这里用来实现"按 Enter 继续"的等待效果，不显示用户输入
# 工作原理：getpass.getpass("提示文字") 会暂停程序，等待用户输入，
# 用户输入的内容不会显示在屏幕上（就像输入密码一样），按 Enter 后返回
import getpass

# requests 库：Python 最流行的 HTTP 客户端库
# 安装方式：pip install requests
# 用于发送 HTTP 请求（GET、POST 等），获取网页/API 数据
# GET 请求用于获取数据，POST 请求用于提交数据
import requests

# rich.console.Console：Rich 库的输出接口
# 在终端中输出格式化的彩色文本
# 使用方式：console = Console()，然后 console.print("文本")
# Rich 会自动解析标记语法如 [red]红色文字[/red]
from rich.console import Console

# rich.panel.Panel：Rich 库的面板组件
# 将文本包裹在带边框的面板中，支持标题、边框颜色、内边距等自定义
# 示例：Panel("内容", title="标题", border_style="cyan", padding=(1, 2))
# padding=(1, 2) 表示上下各 1 行间距，左右各 2 个字符间距
from rich.panel import Panel

# 从本地配置模块导入 CLI_CONFIG 字典
# CLI_CONFIG 包含了公告系统的 URL、超时时间和备用消息
# 这是模块间引用的标准方式：from 模块名 import 变量名
from cli.config import CLI_CONFIG


# =====================================================================
# 函数：fetch_announcements(url, timeout)
# 功能：从远程 API 获取公告数据
# 参数：
#   url - 公告 API 的地址（可选，默认使用 CLI_CONFIG 中的配置）
#   timeout - 请求超时时间（可选，默认使用 CLI_CONFIG 中的配置）
# 返回值：字典，包含两个键：
#   - "announcements"：公告文本列表
#   - "require_attention"：是否需要用户确认（按 Enter）
#
# 设计模式：容错设计（Graceful Degradation）
#   如果 API 请求失败（网络问题、服务器宕机等），
#   不会崩溃，而是返回备用消息，保证程序正常运行
#
# 运行时数据流示例：
#   场景1（成功）：
#     fetch_announcements()
#     → endpoint = "https://api.tauric.ai/v1/announcements"
#     → timeout = 1.0
#     → requests.get(endpoint, timeout=1.0)  # 0.3秒后返回
#     → response.raise_for_status()  # 状态码 200，通过
#     → data = response.json()  # {"announcements": ["v2.0已发布"], "require_attention": True}
#     → 返回 {"announcements": ["v2.0已发布"], "require_attention": True}
#
#   场景2（网络超时）：
#     fetch_announcements()
#     → requests.get(endpoint, timeout=1.0)  # 1秒后超时
#     → 抛出 requests.exceptions.Timeout 异常
#     → 被 except Exception 捕获
#     → 返回 {"announcements": [fallback], "require_attention": False}
#     # 程序不崩溃，继续运行，显示备用消息
# =====================================================================
def fetch_announcements(url: str = None, timeout: float = None) -> dict:
    """从远程端点获取公告，返回包含 announcements 和 settings 的字典"""

    # 使用 or 运算符实现默认值：如果参数为 None，则使用配置中的默认值
    # Python 的 or 运算符：如果左边的值为"假"（None、空字符串、0 等），返回右边的值
    # 如果 url 参数为 None（调用时未传参），则使用 CLI_CONFIG["announcements_url"]
    endpoint = url or CLI_CONFIG["announcements_url"]
    # 同理，如果 timeout 参数为 None，使用配置中的 1.0 秒
    timeout = timeout or CLI_CONFIG["announcements_timeout"]
    # 获取备用消息，当请求失败时使用
    fallback = CLI_CONFIG["announcements_fallback"]

    try:
        # requests.get() 发送 HTTP GET 请求
        # 参数说明：
        #   endpoint：请求的 URL 地址
        #   timeout 参数设置请求超时时间（秒），防止无限等待
        # 如果服务器在 timeout 秒内没有响应，会抛出 requests.exceptions.Timeout 异常
        response = requests.get(endpoint, timeout=timeout)

        # raise_for_status() 检查 HTTP 状态码
        # HTTP 状态码：
        #   200-299：成功（如 200 OK）
        #   300-399：重定向
        #   400-499：客户端错误（如 404 Not Found）
        #   500-599：服务器错误（如 500 Internal Server Error）
        # 如果状态码是 4xx 或 5xx（表示错误），抛出 HTTPError 异常
        response.raise_for_status()

        # response.json() 将 JSON 格式的响应体解析为 Python 字典
        # JSON 是一种轻量级数据交换格式，类似于 Python 的字典/列表嵌套
        # 例如 JSON 字符串 '{"announcements": ["msg1"], "require_attention": false}'
        # 会被解析为 Python 字典 {"announcements": ["msg1"], "require_attention": False}
        data = response.json()

        # 返回格式化的公告数据
        # data.get(key, default) 安全地从字典中获取值
        # 与 data[key] 的区别：如果 key 不存在，data[key] 会抛出 KeyError 异常，
        # 而 data.get(key, default) 返回 default 值，避免异常
        # 这里使用 .get() 确保即使 API 返回的数据缺少某些字段，也不会崩溃
        return {
            # 如果 API 返回了 announcements 字段，使用它；否则使用 fallback 备用消息
            "announcements": data.get("announcements", [fallback]),
            # 如果 API 返回了 require_attention 字段，使用它；否则默认为 False
            "require_attention": data.get("require_attention", False),
        }
    except Exception:
        # 捕获所有异常（网络错误、解析错误、超时等）
        # Exception 是所有非系统退出异常的基类
        # 这里不区分具体异常类型，统一返回备用消息
        # 这种设计确保：无论发生什么错误，程序都不会崩溃

        # 返回备用消息，保证程序不会崩溃
        # announcements 使用 fallback（配置中的备用消息）
        # require_attention 设为 False，表示不需要用户确认（因为只是备用消息）
        return {
            "announcements": [fallback],
            "require_attention": False,
        }


# =====================================================================
# 函数：display_announcements(console, data)
# 功能：在终端中显示公告面板
# 参数：
#   console - Rich Console 实例，用于输出
#   data - fetch_announcements() 返回的公告数据字典
#
# 行为：
#   1. 如果没有公告内容，直接返回
#   2. 使用 Rich Panel 组件显示公告
#   3. 如果 require_attention 为 True，等待用户按 Enter 键
#   4. 否则只打印空行
#
# 运行时数据流示例：
#   场景1（需要用户确认）：
#     data = {"announcements": ["重要：请更新到最新版本！"], "require_attention": True}
#     → display_announcements(console, data)
#     → 创建 Panel 面板，标题 "Announcements"，青色边框
#     → console.print(panel) 在终端显示带边框的公告
#     → require_attention 为 True
#     → getpass.getpass("Press Enter to continue...") 暂停程序
#     → 用户看到公告后按 Enter → 程序继续
#
#   场景2（不需要确认）：
#     data = {"announcements": ["欢迎使用 TradingAgents"], "require_attention": False}
#     → 显示面板后不需要等待
#     → console.print() 输出一个空行
#     → 程序立即继续
# =====================================================================
def display_announcements(console: Console, data: dict) -> None:
    """显示公告面板。如果 require_attention 为 True，提示用户按 Enter"""

    # 从数据字典中获取公告列表，默认为空列表 []
    announcements = data.get("announcements", [])
    # 获取是否需要用户确认的标志，默认为 False
    require_attention = data.get("require_attention", False)

    # 如果没有公告内容，直接返回，不显示任何东西
    # not announcements 为 True 的情况：空列表 [] 或 None
    if not announcements:
        return

    # "\n".join() 将公告列表用换行符连接成一个字符串
    # 例如 ["公告1", "公告2"] → "公告1\n公告2"
    # join() 是字符串方法，用调用它的字符串作为分隔符连接列表元素
    content = "\n".join(announcements)

    # Panel 组件：Rich 库的面板
    # 参数说明：
    #   第一个参数（content）：面板内容，即公告文本
    #   border_style：边框颜色，"cyan" 表示青色
    #   padding：内边距，(1, 2) 表示上下各 1 行，左右各 2 个字符
    #     即面板内容和边框之间有 1 行空行和 2 个空格的间距
    #   title：面板标题，显示在顶部边框中间
    # 显示效果：
    #   ┌─── Announcements ─────────────────┐
    #   │                                    │
    #   │   公告内容...                       │
    #   │                                    │
    #   └────────────────────────────────────┘
    panel = Panel(
        content,
        border_style="cyan",
        padding=(1, 2),
        title="Announcements",
    )
    # console.print() 输出面板到终端
    # Rich 会自动处理面板的边框绘制和内容换行
    console.print(panel)

    if require_attention:
        # getpass.getpass() 提示用户输入但不显示输入内容
        # 这里用于实现"按 Enter 继续"的效果
        # 用户必须按 Enter 才能继续，确保公告被看到
        # 工作原理：
        #   1. 显示提示文字 "Press Enter to continue..."
        #   2. 程序暂停，等待用户输入
        #   3. 用户输入的内容不显示在屏幕上（像输入密码一样）
        #   4. 用户按 Enter 后，getpass 返回（返回值被忽略）
        #   5. 程序继续执行
        getpass.getpass("Press Enter to continue...")
    else:
        # 如果不需要强制关注，只输出一个空行
        # console.print() 不带参数时输出一个空行
        # 这是为了在公告和后续内容之间留出视觉间距
        console.print()
