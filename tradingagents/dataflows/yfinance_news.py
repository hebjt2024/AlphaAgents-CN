# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 yfinance？
#    yfinance 是一个免费的开源 Python 库，用于从"雅虎财经"（Yahoo Finance）
#    网站获取股票数据（价格、新闻、财务报表等）。
#    它不是雅虎官方的 API，而是社区维护的第三方工具。
#
# 2. 什么是 dict（字典）？
#    Python 的键值对容器，用花括号 {} 定义。
#    例如 {"title": "苹果发布新iPhone", "publisher": "Reuters"}
#    通过 dict["title"] 或 dict.get("title", "默认值") 访问值。
#    - dict["title"]：如果 key 不存在会报错（KeyError）
#    - dict.get("title", "默认值")：如果 key 不存在返回默认值，不报错
#
# 3. 什么是 list（列表）？
#    Python 的有序容器，用方括号 [] 定义。
#    例如 ["苹果新闻1", "苹果新闻2", "苹果新闻3"]
#    可以用 append() 添加元素，用 len() 获取长度，用 [:n] 取前 n 个。
#
# 4. 什么是 set（集合）？
#    Python 的无序不重复容器，用花括号 {} 或 set() 定义。
#    特点：元素不重复（自动去重），查找速度快。
#    本文件用 set 来记录已见过的新闻标题，避免重复。
#
# 5. 什么是 datetime？
#    Python 标准库的日期时间处理类。
#    - strptime(字符串, 格式)：把字符串解析为日期对象
#    - strftime(格式)：把日期对象格式化为字符串
#    - fromisoformat(字符串)：解析 ISO 8601 格式的日期字符串
#    格式码：%Y=年, %m=月, %d=日, %H=时, %M=分
#
# 6. 什么是 relativedelta？
#    dateutil 库提供的日期差计算工具。
#    relativedelta(days=7) 表示 7 天的偏移量。
#    可以做日期的加减：datetime(2024,1,15) - relativedelta(days=7) → datetime(2024,1,8)
#
# 7. 什么是 try-except 语句？
#    Python 的异常处理机制：
#    try:
#        可能出错的代码
#    except 异常类型 as 变量:
#        出错后的处理代码（变量 保存了异常信息）
#    捕获异常后程序不会崩溃，而是执行 except 块中的处理逻辑。
#    可以捕获多种异常：(ValueError, AttributeError)
#
# 8. 什么是 or 短路求值？
#    Python 的 or 运算符有"短路"特性：
#    A or B 先计算 A，如果 A 为"真值"（非 None、非空、非 0），直接返回 A，不计算 B。
#    如果 A 为"假值"（None、空字符串、空字典等），则计算并返回 B。
#    本文件用这个特性来选择 URL：canonicalUrl or clickThroughUrl or {}
#
# 9. 什么是 f-string？
#    Python 的格式化字符串语法。在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Hello {name}" 中，{name} 会被替换为变量 name 的值。
#    比 "Hello " + str(name) 更简洁可读。
#
# 10. 什么是 UTC 和时区？
#     UTC = Coordinated Universal Time（协调世界时），是全球标准时间。
#     "Z" 是 UTC 的简写标记（如 "2024-01-15T10:30:00Z"）。
#     Python 的 fromisoformat() 不认识 "Z"，需要替换为 "+00:00"。
#     带时区的 datetime 和不带时区的 datetime 不能直接比较（会报错）。
#
# 11. 什么是 Markdown 格式？
#     Markdown 是一种轻量级的文本格式标记语言。
#     ### 表示三级标题，\n 表示换行。
#     本文件用 Markdown 格式输出新闻内容，便于阅读。
#
# 12. 什么是 None 和空值检查？
#     None 是 Python 中的"空值"（表示没有值）。
#     检查是否为 None：用 "is None" 而不是 "== None"
#     检查列表是否为空：用 "not 列表" 或 "len(列表) == 0"
#     空列表 []、空字符串 ""、空字典 {}、数字 0 在布尔判断中都是 False。
#
# 13. 什么是 for 循环和 continue？
#     for 变量 in 序列:  遍历序列中的每个元素
#     continue 跳过当前循环的剩余代码，直接进入下一次循环
#     break 完全跳出循环
#
# 14. 什么是列表切片？
#     列表[start:stop] 取从 start 到 stop-1 的元素
#     列表[:n] 取前 n 个元素（start 默认为 0）
#     列表[n:] 取从第 n 个到最后的元素
#     例如 ["a","b","c","d"][:2] → ["a","b"]
#
# 15. 什么是类型注解（Type Hints）？
#     在函数参数后加 :类型 表示参数类型，如 ticker: str
#     在函数定义后加 -> 类型表示返回值类型，如 -> str
#     类型注解不强制类型检查，但帮助 IDE 和阅读者理解代码。
#
# 16. 什么是默认参数？
#     函数参数可以设置默认值，如 look_back_days: int = 7
#     调用时如果不传这个参数，就使用默认值 7。
# =============================================================================
#
# 模块名称：yfinance_news.py
# 模块功能：使用 yfinance 库获取股票新闻和全球市场新闻数据
# =============================================================================
# 本模块提供两个核心功能：
#   1. get_news_yfinance()     - 获取指定股票代码的新闻
#   2. get_global_news_yfinance() - 获取全球宏观经济新闻
#
# 设计模式说明：
#   - 辅助函数 _extract_article_data() 用于处理 yfinance 返回的嵌套文章数据结构
#     这种"提取函数"模式避免了在主函数中重复处理复杂的数据结构
# =============================================================================

# ---------------------------------------------------------------------------
# 导入 yfinance 库：一个开源的 Yahoo Finance API 封装库
# 功能：获取股票价格、历史数据、新闻、财务报表等金融市场数据
# 注意：yfinance 不是官方 Yahoo Finance API，而是社区维护的第三方库
# 用法：
#   - yf.Ticker("AAPL")：创建股票对象
#   - stock.get_news()：获取该股票的新闻
#   - yf.Search(query="关键词")：按关键词搜索新闻
# ---------------------------------------------------------------------------
import yfinance as yf
from datetime import datetime
from .y_finance import to_yfinance_symbol
from dateutil.relativedelta import relativedelta


def _extract_article_data(article: dict) -> dict:
    """
    【辅助函数】从 yfinance 新闻文章中提取标准化的文章数据。

    函数名以 _ 开头是 Python 的命名约定，表示这是一个"内部/私有"函数，
    不应该被本模块外的代码直接调用。

    yfinance 返回的新闻数据有两种结构：
    1. 嵌套结构：文章信息包裹在 "content" 键内（新格式）
    2. 扁平结构：文章信息直接放在顶层（旧格式/回退格式）
    本函数同时处理这两种情况。

    【调用时机】
      被 get_news_yfinance() 和 get_global_news_yfinance() 调用，
      在遍历新闻列表时，对每篇文章调用此函数提取标准化数据。

    【输入是什么】
      article (dict): yfinance 返回的原始文章字典（dict 类型）
      dict 是 Python 内置的键值对容器类型
      运行时示例（嵌套结构）：
        {
          "content": {
            "title": "Apple announces new iPhone",
            "summary": "Apple today unveiled...",
            "provider": {"displayName": "Reuters"},
            "canonicalUrl": {"url": "https://..."},
            "pubDate": "2024-01-15T10:30:00Z"
          }
        }

    【输出是什么】
      dict: 标准化后的文章数据字典，包含以下固定键：
            - title     : 文章标题
            - summary   : 文章摘要
            - publisher : 发布者名称
            - link      : 文章链接
            - pub_date  : 发布日期（datetime 对象或 None）
      运行时示例：
        {
          "title": "Apple announces new iPhone",
          "summary": "Apple today unveiled...",
          "publisher": "Reuters",
          "link": "https://...",
          "pub_date": datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
        }

    【输出被谁使用】
      被调用方用于构建格式化的新闻字符串和按日期范围过滤新闻。
    """
    # 检查文章数据是否为嵌套结构（即是否包含 "content" 键）
    # "if 'content' in article" 是检查字典中是否存在某个键的常用写法
    # in 运算符检查键是否在字典中，返回 True 或 False
    if "content" in article:
        # 提取嵌套的 content 子字典
        # article["content"] 用方括号访问字典的值
        content = article["content"]

        # 使用 .get() 方法安全地获取字典中的值
        # .get(key, default) 与直接使用 [] 的区别：
        #   - content.get("title", "No title") 如果键不存在，返回默认值 "No title"
        #   - content["title"] 如果键不存在，会抛出 KeyError 异常，导致程序崩溃
        title = content.get("title", "No title")
        summary = content.get("summary", "")

        # provider 本身也是一个字典，包含发布者的详细信息
        # 运行时示例：provider = {"displayName": "Reuters", "id": "reuters"}
        # 如果 provider 不存在，返回空字典 {} 作为默认值
        provider = content.get("provider", {})
        # 从 provider 字典中提取发布者显示名称
        publisher = provider.get("displayName", "Unknown")

        # 获取文章链接：优先使用 canonicalUrl（标准链接），其次使用 clickThroughUrl（点击链接）
        # 逻辑运算符 or 的短路求值机制：
        #   A or B 先计算 A，如果 A 为"真值"（非 None、非空等），直接返回 A，不再计算 B
        #   如果 A 为"假值"（None、空字符串、空字典等），则计算并返回 B
        # 这里的意思是：先取 canonicalUrl，如果为 None 或空，则取 clickThroughUrl
        # 最后如果都是 None，则使用空字典 {} 作为默认值
        # 运行时示例：
        #   content.get("canonicalUrl") → {"url": "https://reuters.com/..."} （非空，直接返回）
        #   或 None → 尝试 clickThroughUrl → {"url": "https://..."}
        #   或 None → 返回 {}
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        # 从 url_obj 字典中提取实际的 URL 字符串
        link = url_obj.get("url", "")

        # 获取发布日期字符串
        # 运行时示例：pub_date_str = "2024-01-15T10:30:00Z"
        pub_date_str = content.get("pubDate", "")
        pub_date = None  # 初始化为 None（Python 中的空值/空对象）

        # 如果存在发布日期字符串，尝试将其解析为 datetime 对象
        # 空字符串在 Python 中为 False，非空字符串为 True
        if pub_date_str:
            try:
                # .replace("Z", "+00:00") 将 UTC 时区标记 "Z" 替换为偏移量格式
                # "Z" 表示 UTC 时间（Coordinated Universal Time，协调世界时）
                # "+00:00" 是 Python 可识别的 UTC 偏移量表示
                # fromisoformat 是 Python 3.7+ 支持的 ISO 8601 日期解析方法
                # ISO 8601 格式示例："2024-01-15T10:30:00+00:00"
                # 运行时示例：
                #   输入："2024-01-15T10:30:00Z"
                #   替换后："2024-01-15T10:30:00+00:00"
                #   解析后：datetime(2024, 1, 15, 10, 30, tzinfo=timezone(utc))
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                # 捕获两种可能的异常：
                #   ValueError  : 日期字符串格式不正确，无法解析
                #   AttributeError : 日期字符串不是有效的字符串（如为 None，没有 .replace 方法）
                # 括号内多个异常类型用逗号分隔，表示捕获其中任意一种
                # pass 语句表示"什么都不做"，继续执行后面的代码
                # 这里如果解析失败，pub_date 保持为 None
                pass

        # 返回标准化后的字典，使用 {} 字典字面量语法创建
        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "link": link,
            "pub_date": pub_date,
        }
    else:
        # 回退处理：扁平结构（旧格式）的文章数据
        # 直接使用 .get() 方法提取各字段，提供合理的默认值
        return {
            "title": article.get("title", "No title"),
            "summary": article.get("summary", ""),
            "publisher": article.get("publisher", "Unknown"),
            "link": article.get("link", ""),
            "pub_date": None,  # 扁平结构中没有可靠的日期信息
        }


def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    【公开函数】使用 yfinance 获取指定股票代码的新闻。

    函数签名中的类型注解（如 ticker: str）是 Python 的类型提示（Type Hints）：
    - 它们不强制类型检查，但能帮助 IDE 和代码阅读者理解参数类型
    - -> str 表示函数返回值的类型是字符串

    【调用时机】
      当系统需要获取某支股票在指定日期范围内的新闻时调用。
      通常被 LangChain Agent 作为工具调用。

    【输入是什么】
      ticker (str): 股票代码符号，如 "AAPL" 表示苹果公司
      start_date (str): 开始日期，格式为 yyyy-mm-dd（如 "2024-01-01"）
      end_date (str): 结束日期，格式为 yyyy-mm-dd

    【输出是什么】
      str: 格式化后的新闻文章字符串，包含标题、摘要、来源和链接
      运行时输出示例：
        ## AAPL News, from 2024-01-01 to 2024-01-31:

        ### Apple announces new iPhone (source: Reuters)
        Apple today unveiled the new iPhone 16...
        Link: https://reuters.com/article/...

        ### Apple Q1 earnings beat estimates (source: Bloomberg)
        Apple reported record quarterly revenue...
        Link: https://bloomberg.com/news/...

    【输出被谁使用】
      被 LLM 或分析模块用于了解该股票的最新新闻动态。

    Python 语法要点：
      - f"..." 是 f-string 格式化字符串，可以在字符串中直接嵌入变量
      - for article in news: 遍历列表中的每个元素
      - continue 跳过当前循环的剩余代码，进入下一次循环
      - += 是追加赋值运算符，news_str += "..." 等价于 news_str = news_str + "..."
    """
    try:
        # yf.Ticker(symbol) 创建一个 Ticker 对象，代表一支股票
        # Ticker 对象可以调用各种方法获取股票数据
        stock = yf.Ticker(to_yfinance_symbol(ticker))

        # .get_news(count=20) 获取最近 20 条新闻
        # 返回一个列表（list），每个元素是一个文章字典
        # 运行时示例：news = [{"content": {...}}, {"content": {...}}, ...]
        news = stock.get_news(count=20)

        # 如果新闻列表为空（空列表在 Python 中视为 False）
        # not news 等价于 len(news) == 0
        if not news:
            return f"No news found for {ticker}"
            # f"..." 是 Python 的 f-string（格式化字符串），可以在字符串中直接嵌入变量
            # 与 "..." + str(ticker) 相比，f-string 更简洁、可读性更好

        # 解析日期范围，用于后续过滤
        # datetime.strptime() 将字符串按指定格式解析为 datetime 对象
        # "%Y-%m-%d" 是日期格式码：
        #   %Y = 四位年份（如 2024）
        #   %m = 两位月份（01-12）
        #   %d = 两位日期（01-31）
        # 运行时示例：start_date="2024-01-01" → start_dt=datetime(2024, 1, 1)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        news_str = ""  # 初始化空字符串，用于累积新闻内容
        filtered_count = 0  # 计数器，记录符合条件的新闻数

        # 遍历新闻列表，article 是列表中的每个元素
        for article in news:
            # 调用辅助函数提取标准化文章数据
            # 运行时示例：data = {"title": "Apple...", "summary": "...", ...}
            data = _extract_article_data(article)

            # 如果文章有发布日期，按日期范围过滤
            # None 在 Python 中为 False，非 None 的 datetime 对象为 True
            if data["pub_date"]:
                # .replace(tzinfo=None) 移除时区信息
                # 这是因为 start_dt 和 end_dt 没有时区信息，需要统一类型才能比较
                # 带时区和不带时区的 datetime 对象不能直接比较（会抛出 TypeError）
                pub_date_naive = data["pub_date"].replace(tzinfo=None)

                # 日期范围检查：文章日期必须在 [start_dt, end_dt + 1天] 之间
                # relativedelta(days=1) 表示 1 天，用于扩展 end_dt 以包含当天全部时间
                # 链式比较：start_dt <= pub_date <= end_dt+1day
                # Python 支持链式比较，等价于：
                #   pub_date >= start_dt and pub_date <= end_dt + relativedelta(days=1)
                # not (...) 取反，如果不在范围内则执行 continue
                # continue 跳过当前循环的剩余代码，直接进入下一次循环
                if not (start_dt <= pub_date_naive <= end_dt + relativedelta(days=1)):
                    continue  # 跳过不满足日期条件的文章，进入下一次循环

            # 构建 Markdown 格式的新闻条目
            # ### 表示 Markdown 中的三级标题
            # \n 是换行符（escape character，转义字符）
            # += 是追加赋值运算符，news_str += "..." 等价于 news_str = news_str + "..."
            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            # 如果有摘要，添加摘要
            # 空字符串 "" 为 False，非空字符串为 True
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            # 如果有链接，添加链接
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"  # 文章之间添加空行分隔
            filtered_count += 1  # 计数器加 1（+= 1 等价于 filtered_count = filtered_count + 1）

        # 如果没有符合条件的文章，返回提示信息
        # == 是等于比较运算符
        if filtered_count == 0:
            return f"No news found for {ticker} between {start_date} and {end_date}"

        # 返回带标题的格式化新闻汇总
        # f"## {ticker} News..." 中 {ticker} 等变量会被替换为实际值
        return f"## {ticker} News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        # 捕获所有异常，返回错误信息而非让程序崩溃
        # Exception 是所有异常的基类，捕获它能确保函数不会因未预料的错误而崩溃
        # str(e) 将异常对象转换为可读的字符串描述
        return f"Error fetching news for {ticker}: {str(e)}"


def get_global_news_yfinance(
    curr_date: str,
    look_back_days: int = 7,   # 默认参数值：如果调用时不传此参数，默认使用 7
    limit: int = 10,           # 默认参数值：最多返回 10 篇文章
) -> str:
    """
    【公开函数】使用 yfinance 的 Search 功能获取全球宏观经济新闻。

    与 get_news_yfinance() 的区别：
    - 前者针对特定股票代码获取新闻
    - 本函数通过关键词搜索获取广泛的市场新闻，更适合宏观分析

    【调用时机】
      当系统需要获取全球宏观经济新闻（而非特定股票新闻）时调用。

    【输入是什么】
      curr_date (str): 当前日期，格式为 yyyy-mm-dd
      look_back_days (int): 向前回溯的天数，默认 7 天
      limit (int): 最多返回的文章数量，默认 10 篇

    【输出是什么】
      str: 格式化后的全球新闻文章字符串
      运行时输出示例：
        ## Global Market News, from 2024-01-08 to 2024-01-15:

        ### Fed signals possible rate cut (source: Reuters)
        The Federal Reserve indicated...
        Link: https://...

        ### Inflation cools to 3.2% (source: Bloomberg)
        Consumer prices rose less than expected...
        Link: https://...

    【输出被谁使用】
      被 LLM 或分析模块用于了解宏观经济动态。

    Python 语法要点：
      - 默认参数：look_back_days: int = 7 表示不传参时默认值为 7
      - 集合 set：用 set() 创建空集合，add() 添加元素，in 检查是否存在
      - break：完全跳出 for 循环
      - 列表切片：all_news[:limit] 取前 limit 个元素
    """
    # 定义搜索关键词列表
    # 列表（list）是 Python 中最常用的容器类型，用方括号 [] 定义
    # 这些关键词覆盖了主要的宏观经济和市场主题
    search_queries = [
        "stock market economy",          # 股市经济
        "Federal Reserve interest rates", # 美联储利率
        "inflation economic outlook",     # 通胀经济展望
        "global markets trading",         # 全球市场交易
    ]

    all_news = []  # 空列表，用于存储收集到的所有新闻文章
    seen_titles = set()  # 空集合（set），用于去重
    # set 是 Python 中的集合类型，特点：
    #   - 元素不重复（自动去重）
    #   - 查找速度快（O(1) 时间复杂度）
    # 这里用 seen_titles 记录已见过的标题，避免重复添加同一篇文章
    # 用法：seen_titles.add(标题) 添加，标题 in seen_titles 检查是否存在

    try:
        # 遍历每个搜索关键词
        # for 变量 in 列表: 遍历列表中的每个元素
        for query in search_queries:
            # yf.Search() 执行新闻搜索
            # 参数说明：
            #   query              : 搜索关键词
            #   news_count=limit   : 每个查询返回的新闻数量上限
            #   enable_fuzzy_query : 启用模糊搜索（可以匹配相近的关键词）
            search = yf.Search(
                query=query,
                news_count=limit,
                enable_fuzzy_query=True,
            )

            # 如果搜索结果中有新闻
            # search.news 是一个列表，如果为空列表则为 False
            if search.news:
                # 遍历搜索到的每篇新闻
                for article in search.news:
                    # 处理嵌套和扁平两种数据结构
                    # "content" in article 检查字典中是否有 "content" 键
                    if "content" in article:
                        # 嵌套结构：调用辅助函数提取数据
                        data = _extract_article_data(article)
                        title = data["title"]
                    else:
                        # 扁平结构：直接获取标题
                        title = article.get("title", "")

                    # 去重逻辑：如果标题非空且未出现过
                    # 检查 title 是否为真值（非空字符串），避免把空标题加入集合
                    # "title not in seen_titles" 检查标题是否不在集合中
                    # and 运算符：两边都为 True 才为 True
                    if title and title not in seen_titles:
                        seen_titles.add(title)  # 将标题加入集合，标记为已见
                        all_news.append(article)  # 将文章添加到结果列表

            # 如果已经收集到足够多的文章，提前终止搜索循环
            # 这是一个性能优化：不需要搜索所有关键词
            # len(all_news) 获取列表长度，>= 是大于等于比较
            if len(all_news) >= limit:
                break  # 跳出 for 循环（完全终止循环，不继续搜索剩余关键词）

        # 如果没有找到任何新闻
        # not all_news 等价于 len(all_news) == 0
        if not all_news:
            return f"No global news found for {curr_date}"

        # 计算搜索的起始日期
        # strptime 把字符串解析为日期对象
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        # 从当前日期减去 look_back_days 天
        # relativedelta(days=look_back_days) 表示 look_back_days 天的时间差
        # 运行时示例：curr_dt=datetime(2024,1,15), look_back_days=7
        #             → start_dt=datetime(2024,1,8)
        start_dt = curr_dt - relativedelta(days=look_back_days)
        # .strftime() 将 datetime 对象格式化回字符串
        # 运行时示例：start_dt.strftime("%Y-%m-%d") → "2024-01-08"
        start_date = start_dt.strftime("%Y-%m-%d")

        news_str = ""
        # 列表切片 [:] 语法：all_news[:limit] 表示取前 limit 个元素
        # 列表切片的完整语法是 [start:stop:step]，其中 start 默认 0，step 默认 1
        # 例如 ["a","b","c","d"][:2] → ["a","b"]
        # 这里 [:limit] 限制只处理前 limit 篇文章
        for article in all_news[:limit]:
            # 处理嵌套和扁平两种数据结构
            if "content" in article:
                data = _extract_article_data(article)
                title = data["title"]
                publisher = data["publisher"]
                link = data["link"]
                summary = data["summary"]
            else:
                title = article.get("title", "No title")
                publisher = article.get("publisher", "Unknown")
                link = article.get("link", "")
                summary = ""

            # 构建 Markdown 格式的新闻条目
            news_str += f"### {title} (source: {publisher})\n"
            if summary:
                news_str += f"{summary}\n"
            if link:
                news_str += f"Link: {link}\n"
            news_str += "\n"

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching global news: {str(e)}"
