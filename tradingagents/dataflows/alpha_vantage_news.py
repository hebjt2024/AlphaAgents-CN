# =============================================================================
# ============================================================================
# 【前置知识】—— 零基础读者必读
# ============================================================================
#
# 本文件涉及的所有核心概念，请先阅读本区块，再看后面的代码。
#
# 1. 什么是 Alpha Vantage API？
#    一个金融数据 API 服务。通过 HTTP 请求获取股票数据和新闻。
#    本文件使用 NEWS_SENTIMENT 端点获取新闻和情绪分析数据。
#    需要 API 密钥（API Key），免费版有频率限制。
#
# 2. 什么是新闻情绪分析（Sentiment Analysis）？
#    Alpha Vantage 的新闻 API 会对每篇新闻进行情绪分析：
#    - overall_sentiment_label: 整体情绪标签（"bullish"=看涨,
#      "bearish"=看跌, "neutral"=中性）
#    - overall_sentiment_score: 情绪分数（-1 到 1，负=看跌，正=看涨）
#    - ticker_sentiment: 针对特定股票的情绪分析
#    这帮助投资者快速了解新闻对股价的影响方向。
#
# 3. 什么是 NEWS_SENTIMENT 端点？
#    Alpha Vantage 的新闻 API 函数名。功能包括：
#    - 按股票代码过滤新闻（ticker 参数）
#    - 按日期范围过滤（time_from, time_to 参数）
#    - 按主题过滤（topics 参数）
#    - 限制返回数量（limit 参数）
#    返回 JSON 格式的新闻列表，每条新闻包含标题、摘要、来源、日期、情绪等。
#
# 4. 什么是 JSON 格式？
#    轻量级数据交换格式，用 {} 表示对象，[] 表示数组。
#    例如：
#    {
#      "feed": [
#        {
#          "title": "Apple announces new iPhone",
#          "summary": "Apple today unveiled...",
#          "source": "Reuters",
#          "overall_sentiment_label": "bullish",
#          "overall_sentiment_score": 0.35
#        }
#      ]
#    }
#    json.loads(str) 把 JSON 字符串解析为 Python 字典/列表。
#
# 5.什么是 dict（字典）？
#   Python 的键值对容器，用 {} 定义。
#   例如 {"title": "Apple news", "source": "Reuters"}
#   通过 dict["title"] 或 dict.get("title", "默认值") 访问值。
#   .get(key, default) 在键不存在时返回默认值，不报错。
#
# 6. 什么是 list（列表）？
#    Python 的有序容器，用 [] 定义。
#    例如 [新闻1, 新闻2, 新闻3]
#    可以用 append() 添加元素，len() 获取长度，[:n] 取前 n 个。
#
# 7. 什么是 datetime 和 strftime？
#    datetime 是 Python 标准库的日期时间处理类。
#    strftime(格式) 把日期对象格式化为字符串。
#    strptime(字符串, 格式) 把字符串解析为日期对象。
#    格式码：%Y=年, %m=月, %d=日, %H=时, %M=分
#
# 8. 什么是 relativedelta？
#    dateutil 库的日期差计算工具。
#    relativedelta(days=7) 表示 7 天的偏移量。
#    datetime(2024,1,15) - relativedelta(days=7) → datetime(2024,1,8)
#
# 9. 什么是 Annotated 类型注解？
#    Annotated[str, "说明文字"] 给参数附加描述信息。
#    LangChain 框架读取"说明文字"作为 LLM 工具参数描述。
#
# 10.什么是 try-except 语句？
#    Python 异常处理机制，捕获异常后程序不崩溃。
#    可以有多个 except 块捕获不同类型的异常。
#
# 11.什么是 f-string？
#    在字符串前加 f，用 {} 嵌入变量。
#    例如 f"Hello {name}" 中 {name} 被替换为变量值。
#
# 12.什么是 API 限流（Rate Limit）？
#    服务器限制请求频率，超限时返回错误。
#    本文件通过捕获 AlphaVantageRateLimitError 处理。
#
# 13.什么是 format_datetime_for_api()？
#    alpha_vantage_common 模块中的日期格式转换函数。
#    把 "2024-01-15" 转为 Alpha Vantage API 要求的 "20240115T0000" 格式。
#
# 14.什么是 None 和空值检查？
#    None 是 Python 的空值。空列表 []、空字符串 "" 都视为 False。
#    not data 检查 data 是否为 None 或空。
#
# 15.什么是 for 循环和 break？
#    for 变量 in 序列: 遍历序列中的每个元素
#    break 完全跳出循环
#    enumerate(列表) 同时获取索引和元素
#
# 16.什么是 Markdown 格式？
#    轻量级文本格式标记语言。### 三级标题，\n 换行。
#    本文件用 Markdown 格式输出新闻，便于阅读。
# =============================================================================
#
# 模块名称：alpha_vantage_news.py
# 模块功能：使用 Alpha Vantage API 获取股票新闻和情绪分析数据
# =============================================================================
# 本模块提供以下功能：
#   1. get_news()         - 获取指定股票代码的新闻
#   2. get_global_news()  - 获取全球市场新闻
#
# 依赖说明：
#   - alpha_vantage_common: 提供 _make_api_request()、format_datetime_for_api()、
#     AlphaVantageRateLimitError
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------

# datetime 类：Python 标准库的日期时间处理类
# 用于日期解析、格式化和当前时间获取
# 常用方法：
#   - datetime.strptime(字符串, 格式)：字符串 → 日期对象
#   - datetime.now()：获取当前时间
#   - dt.strftime(格式)：日期对象 → 字符串
# json 模块：用于解析 JSON 格式的 API 响应
# Alpha Vantage 的新闻 API 返回 JSON 格式
# json.loads(str) 把 JSON 字符串解析为 Python 字典/列表
import json
from datetime import datetime

# Annotated: 用于为类型注解添加元数据说明
# LangChain 框架读取"说明文字"作为 LLM 工具参数描述
from typing import Annotated

# relativedelta 类（来自 dateutil 库）：基于日历的日期差计算工具
# 与标准库 timedelta 的区别：
#   - timedelta 只能做绝对天数/秒数计算
#   - relativedelta 可以做"减 N 天"等基于日历的加减
# 示例：datetime(2024,1,15) - relativedelta(days=7) → datetime(2024,1,8)
from dateutil.relativedelta import relativedelta

# 从同目录的 alpha_vantage_common 模块导入公共工具
# . 表示相对导入——从当前包（dataflows 目录）导入
# 导入内容：
#   - _make_api_request: 统一的 API 请求函数
#   - format_datetime_for_api: 日期格式转换函数
#     把 "2024-01-15" 转为 Alpha Vantage 要求的 "20240115T0000" 格式
#   - AlphaVantageRateLimitError: API 限流异常类
from .alpha_vantage_common import (
    AlphaVantageRateLimitError,  # API 速率限制超限异常
    _make_api_request,  # 统一的 API 请求函数
    format_datetime_for_api,  # 日期格式转换函数
)


# =============================================================================
# 函数：get_news
# =============================================================================
def get_news(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "end date in yyyy-mm-dd format"],
) -> str:
    """
    【公开函数】使用 Alpha Vantage 获取指定股票代码的新闻和情绪分析数据。

    【调用时机】
      当系统需要获取某支股票在指定日期范围内的新闻时调用。
      通常被 LangChain Agent 作为工具调用。

    【输入是什么】
      symbol (str): 股票代码（如 "AAPL"）。
      start_date (str): 开始日期，格式 "YYYY-MM-DD"。
      end_date (str): 结束日期，格式 "YYYY-MM-DD"。

    【输出是什么】
      str: 格式化的新闻文章字符串，包含标题、摘要、来源、情绪标签等。
      运行时输出示例：
        ## Alpha Vantage News for AAPL, from 2024-01-01 to 2024-01-31:

        ### Apple announces new iPhone (source: Reuters)
        Sentiment: bullish (0.35)
        Apple today unveiled the new iPhone 16...
        Published: 2024-01-15T10:30:00
        URL: https://reuters.com/article/...

        ### Apple Q1 earnings beat estimates (source: Bloomberg)
        Sentiment: bullish (0.52)
        Apple reported record quarterly revenue...
        Published: 2024-01-20T14:00:00
        URL: https://bloomberg.com/news/...

    【输出被谁使用】
      被 LLM 或分析模块用于了解该股票的新闻动态和情绪倾向。

    工作流程：
      1. 将日期转换为 Alpha Vantage API 要求的格式（YYYYMMDDTHHMM）。
      2. 构建 API 请求参数。
      3. 调用 _make_api_request 发送请求。
      4. 解析 JSON 响应。
      5. 遍历新闻列表，格式化输出每条新闻。

    关键概念：
      - NEWS_SENTIMENT: Alpha Vantage 的新闻情绪 API 函数名。
        返回包含新闻文章和情绪分析的 JSON 数据。
      - format_datetime_for_api: 把 "2024-01-15" 转为 "20240115T0000"。
        Alpha Vantage 的 time_from 和 time_to 参数要求这种特殊格式。
      - JSON 响应结构：{"feed": [{新闻1}, {新闻2}, ...]}
        feed 是一个列表，每个元素是一条新闻的字典。
      - 情绪标签：bullish（看涨）、bearish（看跌）、neutral（中性）。
      - limit=1000: 限制返回最多 1000 条新闻。

    Python 语法要点：
      - json.loads(str) 把 JSON 字符串解析为 Python 字典
      - dict.get(key, default) 安全获取值
      - for item in list 遍历列表
      - enumerate(list) 同时获取索引和元素
      - f"..." 格式化字符串
      - break 跳出循环
    """
    try:
        # 将日期字符串转换为 Alpha Vantage API 要求的格式
        # format_datetime_for_api() 把 "2024-01-15" 转为 "20240115T0000"
        # 运行时示例：
        #   format_datetime_for_api("2024-01-01") → "20240101T0000"
        #   format_datetime_for_api("2024-01-31") → "20240131T0000"
        time_from = format_datetime_for_api(start_date)
        time_to = format_datetime_for_api(end_date)

        # 构建 API 请求参数
        # 字典用 {} 定义
        # 运行时示例：
        #   params = {"tickers": "AAPL", "time_from": "20240101T0000",
        #             "time_to": "20240131T0000", "limit": "1000", "sort": "RELEVANCE"}
        params = {
            "tickers": symbol,                # 按股票代码过滤新闻
            "time_from": time_from,           # 新闻起始时间（API 特殊格式）
            "time_to": time_to,               # 新闻结束时间（API 特殊格式）
            "limit": "1000",                  # 最多返回 1000 条新闻
            "sort": "RELEVANCE",              # 按相关性排序（也可按时间 LATEST）
        }

        # 调用 Alpha Vantage API 获取新闻数据
        # "NEWS_SENTIMENT" 是 API 函数名，返回 JSON 格式的新闻列表
        # 运行时示例：data = '{"feed": [{"title": "...", "summary": "...", ...}, ...]}'
        data = _make_api_request("NEWS_SENTIMENT", params)

        # 检查响应是否为有效数据
        # not data 检查 None 和空字符串
        # .strip() 去除首尾空白
        if not data or not data.strip():
            return f"No news found for {symbol} between {start_date} and {end_date}"

        # 解析 JSON 响应为 Python 字典
        # json.loads() 把 JSON 字符串转为 Python 字典
        # 运行时示例：
        #   parsed = {"feed": [{"title": "Apple...", "summary": "...", "source": "Reuters", ...}]}
        parsed = json.loads(data)

        # 从解析结果中获取新闻列表
        # .get(key, default) 安全获取值，如果 "feed" 不存在返回空列表 []
        # feed 是 Alpha Vantage 返回的新闻数组
        # 运行时示例：feed = [{"title": "...", "summary": "...", ...}, ...]
        feed = parsed.get("feed", [])

        # 检查是否获取到新闻
        # not feed 检查列表是否为空（空列表为 False，not False = True）
        if not feed:
            return f"No news found for {symbol} between {start_date} and {end_date}"

        # 构建格式化的新闻输出
        news_str = ""
        # enumerate(feed) 同时获取索引 i 和元素 article
        # i 是当前新闻的序号（从 0 开始）
        # article 是一条新闻的字典
        for _i, article in enumerate(feed):
            # 从新闻字典中提取各字段
            # .get(key, default) 安全获取值，键不存在时返回默认值
            title = article.get("title", "No title")             # 新闻标题
            summary = article.get("summary", "")                  # 新闻摘要
            source = article.get("source", "Unknown")             # 新闻来源
            url = article.get("url", "")                           # 新闻链接
            time_published = article.get("time_published", "")     # 发布时间
            # 情绪分析数据
            sentiment_label = article.get("overall_sentiment_label", "N/A")  # 情绪标签
            sentiment_score = article.get("overall_sentiment_score", "N/A")  # 情绪分数

            # 构建 Markdown 格式的新闻条目
            # f"..." 是 f-string 格式化字符串，{变量} 被替换为实际值
            # ### 表示 Markdown 三级标题
            # \n 是换行符
            # += 是追加赋值运算符
            news_str += f"### {title} (source: {source})\n"
            # 添加情绪分析信息
            news_str += f"Sentiment: {sentiment_label} ({sentiment_score})\n"
            # 如果有摘要，添加摘要
            # 空字符串 "" 为 False，非空字符串为 True
            if summary:
                news_str += f"{summary}\n"
            # 如果有发布时间，添加发布时间
            if time_published:
                news_str += f"Published: {time_published}\n"
            # 如果有链接，添加链接
            if url:
                news_str += f"URL: {url}\n"
            news_str += "\n"  # 文章之间添加空行分隔

        # 返回带标题的格式化新闻汇总
        # f"## ..." 是 Markdown 二级标题
        return f"## Alpha Vantage News for {symbol.upper()}, from {start_date} to {end_date}:\n\n{news_str}"

    except AlphaVantageRateLimitError:
        # 捕获 API 速率限制异常
        # 当请求频率超过 Alpha Vantage 的限制时抛出
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        # 捕获所有其他异常（网络错误、JSON 解析错误等）
        # str(e) 把异常对象转为字符串描述
        return f"Error fetching news for {symbol}: {str(e)}"


# =============================================================================
# 函数：get_global_news
# =============================================================================
def get_global_news(
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "number of days to look back"] = 7,
    limit: int = 50,  # 默认参数：最多返回 50 篇文章
) -> str:
    """
    【公开函数】使用 Alpha Vantage 获取全球宏观经济新闻。

    与 get_news() 的区别：
    - get_news() 针对特定股票代码获取新闻
    - 本函数不带股票代码过滤，获取广泛的市场新闻，更适合宏观分析

    【调用时机】
      当系统需要获取全球宏观经济新闻（而非特定股票新闻）时调用。

    【输入是什么】
      curr_date (str): 当前日期，格式 "YYYY-MM-DD"。
      look_back_days (int): 向前回溯的天数，默认 7 天。
      limit (int): 最多返回的文章数量，默认 50 篇。

    【输出是什么】
      str: 格式化的全球新闻文章字符串，包含情绪分析。
      运行时输出示例：
        ## Alpha Vantage Global News, from 2024-01-08 to 2024-01-15:

        ### Fed signals possible rate cut (source: Reuters)
        Sentiment: bullish (0.45)
        The Federal Reserve indicated...
        Published: 2024-01-14T10:00:00
        URL: https://...

        ### Inflation cools to 3.2% (source: Bloomberg)
        Sentiment: neutral (0.10)
        Consumer prices rose less than expected...
        Published: 2024-01-12T14:30:00
        URL: https://...

    【输出被谁使用】
      被 LLM 或分析模块用于了解宏观经济动态。

    工作流程：
      1. 计算日期范围（curr_date 往前推 look_back_days 天）。
      2. 转换日期格式为 API 要求的格式。
      3. 构建 API 请求参数（不带 tickers 过滤，获取所有新闻）。
      4. 调用 API 获取数据。
      5. 解析 JSON 并格式化输出。

    关键概念：
      - 不带 tickers 参数：获取所有新闻而非特定股票的新闻。
      - sort="LATEST": 按最新时间排序，优先获取最近的新闻。
      - limit: 限制返回的新闻数量，避免数据过多。
    """
    try:
        # 计算日期范围
        # strptime 把字符串解析为日期对象
        # 运行时示例：curr_date="2024-01-15" → curr_dt=datetime(2024,1,15)
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        # 从当前日期减去回溯天数
        # relativedelta(days=look_back_days) 表示 look_back_days 天的偏移量
        # 运行时示例：datetime(2024,1,15) - relativedelta(days=7) → datetime(2024,1,8)
        start_dt = curr_dt - relativedelta(days=look_back_days)
        # strftime 把日期对象格式化为字符串
        # 运行时示例：start_dt.strftime("%Y-%m-%d") → "2024-01-08"
        start_date = start_dt.strftime("%Y-%m-%d")

        # 转换日期格式为 Alpha Vantage API 要求的格式
        # format_datetime_for_api() 把 "2024-01-08" 转为 "20240108T0000"
        time_from = format_datetime_for_api(start_date)
        time_to = format_datetime_for_api(curr_date)

        # 构建 API 请求参数
        # 注意：不带 "tickers" 参数，获取所有新闻而非特定股票的新闻
        # 运行时示例：
        #   params = {"time_from": "20240108T0000", "time_to": "20240115T0000",
        #             "limit": "50", "sort": "LATEST"}
        params = {
            "time_from": time_from,           # 新闻起始时间
            "time_to": time_to,               # 新闻结束时间
            "limit": str(limit),               # 最多返回 limit 条（str() 把整数转字符串）
            "sort": "LATEST",                  # 按最新时间排序
        }

        # 调用 Alpha Vantage API 获取新闻数据
        # 返回 JSON 格式的新闻列表
        data = _make_api_request("NEWS_SENTIMENT", params)

        # 检查响应是否为有效数据
        if not data or not data.strip():
            return f"No global news found for {curr_date}"

        # 解析 JSON 响应为 Python 字典
        parsed = json.loads(data)
        # 获取新闻列表
        feed = parsed.get("feed", [])

        # 检查是否获取到新闻
        if not feed:
            return f"No global news found for {curr_date}"

        # 构建格式化的新闻输出
        news_str = ""
        # enumerate(feed) 同时获取索引 i 和元素 article
        for _i, article in enumerate(feed):
            # 从新闻字典中提取各字段
            title = article.get("title", "No title")
            summary = article.get("summary", "")
            source = article.get("source", "Unknown")
            url = article.get("url", "")
            time_published = article.get("time_published", "")
            sentiment_label = article.get("overall_sentiment_label", "N/A")
            sentiment_score = article.get("overall_sentiment_score", "N/A")

            # 构建 Markdown 格式的新闻条目
            news_str += f"### {title} (source: {source})\n"
            news_str += f"Sentiment: {sentiment_label} ({sentiment_score})\n"
            if summary:
                news_str += f"{summary}\n"
            if time_published:
                news_str += f"Published: {time_published}\n"
            if url:
                news_str += f"URL: {url}\n"
            news_str += "\n"

        # 返回带标题的格式化新闻汇总
        return f"## Alpha Vantage Global News, from {start_date} to {curr_date}:\n\n{news_str}"

    except AlphaVantageRateLimitError:
        return (
            "Alpha Vantage API rate limit exceeded. "
            "Please wait a minute and try again."
        )

    except Exception as e:
        return f"Error fetching global news: {str(e)}"
