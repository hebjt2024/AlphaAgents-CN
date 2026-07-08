# =====================================================================
# 模块：cli/config.py
# 功能：CLI 层的配置常量
# 说明：定义命令行界面中使用的全局配置参数。
#       这些参数在程序启动时被加载，全局可用。
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件定义了 CLI 层的全局配置常量。以下是需要了解的核心概念：
#
# 1. 字典（dict）：
#    - Python 中存储"键值对"的数据结构，用花括号 {} 定义。
#    - 示例：config = {"name": "Alice", "age": 30}
#    - 通过键访问值：config["name"] 返回 "Alice"
#    - 安全访问（键不存在时返回默认值）：config.get("name", "unknown")
#
# 2. 全局常量模式：
#    - 将配置集中在一个文件中，其他模块通过 import 引用。
#    - 优点：修改配置只需改一处，所有引用处自动生效。
#    - 本文件的 CLI_CONFIG 字典就是这种模式。
#
# 3. Rich 库的标记语法（Markup）：
#    - Rich 是一个终端美化库，支持用类似 HTML 的标记语法来设置文字样式。
#    - 语法：[颜色名]文本[/颜色名]
#    - 示例：[cyan]青色文字[/cyan]、[bold]粗体[/bold]、[red]红色[/red]
#    - [link=URL]文本[/link]：在支持的终端中创建可点击的链接
#    - 注意：这些标记只在通过 Rich 的 console.print() 输出时生效，
#      普通的 print() 不会解析这些标记。
#
# 4. HTTP 请求与超时（timeout）：
#    - 当程序向远程服务器发送 HTTP 请求时，可能因为网络问题导致请求卡住。
#    - timeout（超时时间）设置一个上限，超过后自动放弃请求。
#    - 本文件设置 1.0 秒超时，确保公告服务不可用时不会让用户等太久。
#
# 5. 本文件的运行时数据流：
#    程序启动 → cli/main.py 导入 CLI_CONFIG
#    → cli/announcements.py 的 fetch_announcements() 读取 URL 和超时配置
#    → 向远程 API 发送 GET 请求获取公告
#    → 如果请求失败，使用 announcements_fallback 作为备用消息显示给用户
#
#    数据流示例：
#    CLI_CONFIG["announcements_url"] = "https://api.tauric.ai/v1/announcements"
#    → requests.get(url, timeout=1.0)
#    → 如果 1 秒内无响应 → 捕获异常 → 返回 CLI_CONFIG["announcements_fallback"]
#    → 在终端显示青色提示文字和可点击链接
# =====================================================================

# CLI_CONFIG：CLI 全局配置字典
# 字典（dict）是 Python 中存储键值对的数据结构，用花括号 {} 定义
# 这里的配置项控制公告系统的行为
# 其他模块通过 from cli.config import CLI_CONFIG 引用此字典
CLI_CONFIG = {
    # ------------------------------------------------------------------
    # 公告相关配置
    # ------------------------------------------------------------------

    # announcements_url：公告 API 的地址
    # 程序启动时会从这个 URL 获取最新的公告信息
    # 这是一个 RESTful API 端点，返回 JSON 格式的数据
    # 返回格式示例：{"announcements": ["公告1", "公告2"], "require_attention": false}
    "announcements_url": "https://api.tauric.ai/v1/announcements",

    # announcements_timeout：HTTP 请求超时时间（秒）
    # 1.0 秒表示如果服务器在 1 秒内没有响应，就会放弃请求
    # 设置短超时可以避免用户等待太久（如果服务器不可用）
    # 这是一个浮点数（float），1.0 表示 1 秒
    "announcements_timeout": 1.0,

    # announcements_fallback：当无法获取公告时的备用消息
    # [cyan]...[/cyan] 是 Rich 库的标记语法，用于设置文字颜色为青色
    # [link=...] 是 Rich 库的链接标记，在支持的终端中可点击
    # 这个备用消息只在 fetch_announcements() 请求失败时使用
    # 显示效果：青色文字 + 可点击的 GitHub 链接
    "announcements_fallback": "[cyan]For more information, please visit[/cyan] [link=https://github.com/TauricResearch]https://github.com/TauricResearch[/link]",
}
