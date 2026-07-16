"""
AkShare 数据供应商适配器
基于 AkShare 库提供中国 A 股市场数据

功能覆盖：
  1. get_stock_data()       - 股票历史 OHLCV 数据
  2. get_indicators()       - 技术指标计算
  3. get_fundamentals()     - 公司基本面数据
  4. get_balance_sheet()    - 资产负债表
  5. get_cashflow()         - 现金流量表
  6. get_income_statement() - 利润表
  7. get_insider_transactions() - 股东持股变动
  8. get_news()             - 个股新闻
  9. get_global_news()      - 全球/宏观新闻

设计说明：
  - 所有函数返回字符串格式，与 yfinance 适配器保持一致
  - 股票代码自动标准化：支持 "000001"、"sh600000"、"sz000001" 等格式
  - 日期格式内部转换为 AkShare 要求的 "YYYYMMDD"

依赖：
  - akshare: 中国金融市场数据接口库
  - stockstats: 技术指标计算库
  - pandas: 数据处理
"""

import os
from datetime import datetime
from typing import Annotated, Optional

import akshare as ak
import pandas as pd
from dateutil.relativedelta import relativedelta


# ============================================================================
# 股票代码标准化
# ============================================================================

def normalize_symbol(symbol: str) -> str:
    """
    将股票代码标准化为 AkShare 要求的 6 位纯数字格式。

    支持的输入格式：
      - "000001"     → "000001" (深圳主板)
      - "600000"     → "600000" (上海主板)
      - "sh600000"   → "600000" (带前缀)
      - "sz000001"   → "000001" (带前缀)
      - "bj430047"   → "430047" (北交所)
      - "NVDA"       → "NVDA"   (美股，不做转换)
      - "海南橡胶"    → "601118" (中文名，尝试查找)

    参数：
      symbol: 原始股票代码

    返回：
      标准化的 6 位数字代码（或原始代码如果不是 A 股格式）
    """
    symbol = symbol.strip()

    # 去掉常见前缀 sh/sz/bj
    if symbol.lower().startswith(("sh", "sz", "bj")) and len(symbol) == 8:
        return symbol[2:]

    # 已经是 6 位纯数字
    if symbol.isdigit() and len(symbol) == 6:
        return symbol

    # 美股代码或其他，保持原样
    return symbol


def is_a_share(symbol: str) -> bool:
    """判断是否为 A 股代码"""
    normalized = normalize_symbol(symbol)
    return normalized.isdigit() and len(normalized) == 6


def is_shanghai(symbol: str) -> bool:
    """判断是否为上海交易所股票 (60xxxx, 68xxxx)"""
    normalized = normalize_symbol(symbol)
    return normalized.startswith("60") or normalized.startswith("68")


def is_shenzhen(symbol: str) -> bool:
    """判断是否为深圳交易所股票 (00xxxx, 30xxxx)"""
    normalized = normalize_symbol(symbol)
    return normalized.startswith("00") or normalized.startswith("30")


# ============================================================================
# 日期格式转换
# ============================================================================

def to_ak_date(date_str: str) -> str:
    """将 'YYYY-MM-DD' 转为 AkShare 的 'YYYYMMDD' 格式"""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m%d")


def to_iso_date(date_str: str) -> str:
    """将 'YYYYMMDD' 转为 'YYYY-MM-DD' 格式"""
    return datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")


# ============================================================================
# 1. 股票历史数据
# ============================================================================

def get_akshare_stock(
    symbol: Annotated[str, "股票代码，如 000001, 600000"],
    start_date: Annotated[str, "开始日期，格式 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD"],
) -> str:
    """
    从 AkShare 获取 A 股历史日线 OHLCV 数据。

    调用 AkShare 的 stock_zh_a_hist() 接口，数据来源为东方财富。
    返回格式与 yfinance 适配器一致：CSV 字符串 + 元数据头部。

    返回示例：
      # Stock data for 000001 from 2024-01-01 to 2024-01-31
      # Total records: 22
      # Data retrieved on: 2024-07-16 15:30:00

      Date,Open,High,Low,Close,Volume
      2024-01-02,7.47,7.50,7.29,7.29,1158366.0
      ...
    """
    try:
        code = normalize_symbol(symbol)
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        start_ak = to_ak_date(start_date)
        end_ak = to_ak_date(end_date)

        # 获取 A 股历史日线数据（前复权）
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_ak,
            end_date=end_ak,
            adjust="qfq",
        )

        if df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

        # 重命名列以匹配 yfinance 的 OHLCV 格式
        column_map = {
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
        }
        df_out = df[list(column_map.keys())].rename(columns=column_map).copy()

        # 数值列四舍五入
        for col in ["Open", "High", "Low", "Close"]:
            df_out[col] = df_out[col].round(2)

        csv_string = df_out.to_csv(index=False)

        header = f"# Stock data for {code} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(df_out)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error fetching stock data for {symbol}: {str(e)}"


# ============================================================================
# 2. 技术指标
# ============================================================================

def get_akshare_indicators(
    symbol: Annotated[str, "股票代码"],
    indicator: Annotated[str, "技术指标名称，如 rsi, macd, close_50_sma"],
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"],
    look_back_days: Annotated[int, "回溯天数"],
) -> str:
    """
    计算 A 股技术指标。

    先从 AkShare 获取历史数据，再用 stockstats 计算指标。
    与 yfinance 的 get_stock_stats_indicators_window 返回格式一致。

    支持的技术指标与 yfinance 的 best_ind_params 字典一致：
      rsi, macd, macds, macdh, close_50_sma, close_200_sma,
      close_10_ema, boll, boll_ub, boll_lb, atr, vwma, mfi
    """
    # 指标说明（与 yfinance 保持一致）
    best_ind_params = {
        "close_50_sma": "50 SMA: 中期趋势指标。用于识别趋势方向和动态支撑/阻力位。",
        "close_200_sma": "200 SMA: 长期趋势基准。用于确认整体市场趋势和识别金叉/死叉。",
        "close_10_ema": "10 EMA: 短期灵敏均线。用于捕捉快速动量变化和入场点。",
        "macd": "MACD: 通过 EMA 差异计算动量。观察交叉和背离作为趋势变化信号。",
        "macds": "MACD Signal: MACD 线的 EMA 平滑。与 MACD 线交叉触发交易信号。",
        "macdh": "MACD Histogram: MACD 线与信号线的差值。可视化动量强度和早期背离。",
        "rsi": "RSI: 衡量动量以标记超买/超卖条件。使用 70/30 阈值，观察背离以判断反转。",
        "boll": "Bollinger Middle: 20 SMA 作为布林带基准。作为动态价格基准。",
        "boll_ub": "Bollinger Upper Band: 通常为中线 +2 标准差。信号潜在超买和突破区域。",
        "boll_lb": "Bollinger Lower Band: 通常为中线 -2 标准差。指示潜在超卖条件。",
        "atr": "ATR: 平均真实波幅衡量波动性。用于设置止损位和调整仓位大小。",
        "vwma": "VWMA: 成交量加权移动平均。整合价格与成交量数据确认趋势。",
        "mfi": "MFI: 资金流量指数，使用价格和成交量衡量买卖压力。超买 >80，超卖 <20。",
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. "
            f"Choose from: {list(best_ind_params.keys())}"
        )

    try:
        from stockstats import wrap

        code = normalize_symbol(symbol)
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before = curr_date_dt - relativedelta(days=look_back_days)

        # 获取足够长的历史数据用于指标计算
        # 需要回溯天数的 2 倍（确保指标有足够的数据点）
        data_start = (before - relativedelta(days=look_back_days * 3)).strftime("%Y%m%d")
        data_end = to_ak_date(curr_date)

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=data_start,
            end_date=data_end,
            adjust="qfq",
        )

        if df.empty:
            return f"No data found for indicator calculation of {symbol}"

        # 重命名列以匹配 stockstats 的要求
        df = df.rename(columns={
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
        })

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date")
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        # 用 stockstats 包装并计算指标
        stock_df = wrap(df)
        # 触发指标计算
        stock_df[indicator]

        # 构建结果
        indicator_data = {}
        for _, row in stock_df.iterrows():
            val = row[indicator]
            if pd.isna(val):
                indicator_data[str(row["Date"])] = "N/A"
            else:
                indicator_data[str(row["Date"])] = str(round(float(val), 4))

        # 生成日期范围内的结果
        current_dt = curr_date_dt
        ind_string = ""
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            if date_str in indicator_data:
                ind_string += f"{date_str}: {indicator_data[date_str]}\n"
            else:
                ind_string += f"{date_str}: N/A: Not a trading day (weekend or holiday)\n"
            current_dt = current_dt - relativedelta(days=1)

        result_str = (
            f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
            + ind_string
            + "\n\n"
            + best_ind_params.get(indicator, "No description available.")
        )

        return result_str

    except Exception as e:
        return f"Error calculating indicator {indicator} for {symbol}: {str(e)}"


# ============================================================================
# 3. 基本面数据
# ============================================================================

def get_akshare_fundamentals(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股公司基本面数据概览。

    调用 AkShare 的 stock_individual_info_em() 接口（东方财富数据源）。
    返回格式与 yfinance 的 get_fundamentals 一致。

    返回示例：
      # Company Fundamentals for 000001
      # Data retrieved on: 2024-07-16 15:30:00

      总市值: 150000000000
      流通市值: 120000000000
      行业: 银行
      市盈率: 5.23
      ...
    """
    try:
        code = normalize_symbol(ticker)

        df = ak.stock_individual_info_em(symbol=code)

        if df.empty:
            return f"No fundamentals data found for '{ticker}'"

        # stock_individual_info_em 返回 DataFrame，列 ["item", "value"]
        # 转为字典方便查询
        info_dict = {}
        if "item" in df.columns and "value" in df.columns:
            for _, row in df.iterrows():
                info_dict[str(row["item"])] = str(row["value"])

        # 构建与 yfinance 类似的字段列表
        field_map = [
            ("总市值", info_dict.get("总市值", None)),
            ("流通市值", info_dict.get("流通市值", None)),
            ("行业", info_dict.get("行业", None)),
            ("市盈率-动态", info_dict.get("市盈率-动态", None)),
            ("市净率", info_dict.get("市净率", None)),
            ("每股收益", info_dict.get("每股收益", None)),
            ("每股净资产", info_dict.get("每股净资产", None)),
            ("营业收入", info_dict.get("营业收入", None)),
            ("净利润", info_dict.get("净利润", None)),
            ("净利润同比增长率", info_dict.get("净利润同比增长率", None)),
            ("毛利率", info_dict.get("毛利率", None)),
            ("净利率", info_dict.get("净利率", None)),
            ("净资产收益率", info_dict.get("净资产收益率", None)),
            ("资产负债率", info_dict.get("资产负债率", None)),
            ("每股经营现金流", info_dict.get("每股经营现金流", None)),
            ("上市时间", info_dict.get("上市时间", None)),
            ("总股本", info_dict.get("总股本", None)),
            ("流通股", info_dict.get("流通股", None)),
        ]

        lines = []
        for label, value in field_map:
            if value is not None:
                lines.append(f"{label}: {value}")

        header = f"# Company Fundamentals for {code}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# ============================================================================
# 4. 资产负债表
# ============================================================================

def get_akshare_balance_sheet(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "数据频率：'annual' 或 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股资产负债表数据。

    调用 AkShare 的 stock_balance_sheet_by_report_em() 接口。
    返回 CSV 格式字符串，与 yfinance 返回格式一致。

    注意：AkShare 返回的是按报告期分类的数据，数据格式为行为科目、列为报告期。
    """
    try:
        code = normalize_symbol(ticker)

        df = ak.stock_balance_sheet_by_report_em(symbol=code)

        if df.empty:
            return f"No balance sheet data found for '{ticker}'"

        # 如果要求年度数据，过滤年报（报告期以 12-31 结尾）
        if freq.lower() == "annual":
            # 报告期列可能不是统一的，尝试找到年度列
            annual_cols = ["报告期"] + [
                c for c in df.columns if "12-31" in str(c) or "1231" in str(c)
            ]
            if len(annual_cols) > 1:
                df = df[annual_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Balance Sheet data for {code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


# ============================================================================
# 5. 现金流量表
# ============================================================================

def get_akshare_cashflow(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "数据频率：'annual' 或 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股现金流量表数据。

    调用 AkShare 的 stock_cash_flow_sheet_by_report_em() 接口。
    """
    try:
        code = normalize_symbol(ticker)

        df = ak.stock_cash_flow_sheet_by_report_em(symbol=code)

        if df.empty:
            return f"No cash flow data found for '{ticker}'"

        if freq.lower() == "annual":
            annual_cols = ["报告期"] + [
                c for c in df.columns if "12-31" in str(c) or "1231" in str(c)
            ]
            if len(annual_cols) > 1:
                df = df[annual_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Cash Flow data for {code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


# ============================================================================
# 6. 利润表
# ============================================================================

def get_akshare_income_statement(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "数据频率：'annual' 或 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股利润表数据。

    调用 AkShare 的 stock_profit_sheet_by_report_em() 接口。
    """
    try:
        code = normalize_symbol(ticker)

        df = ak.stock_profit_sheet_by_report_em(symbol=code)

        if df.empty:
            return f"No income statement data found for '{ticker}'"

        if freq.lower() == "annual":
            annual_cols = ["报告期"] + [
                c for c in df.columns if "12-31" in str(c) or "1231" in str(c)
            ]
            if len(annual_cols) > 1:
                df = df[annual_cols]

        csv_string = df.to_csv(index=False)

        header = f"# Income Statement data for {code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


# ============================================================================
# 7. 股东持股变动（内部交易替代）
# ============================================================================

def get_akshare_insider_transactions(
    ticker: Annotated[str, "股票代码"],
) -> str:
    """
    获取 A 股重要股东持股变动数据。

    中国 A 股没有"内部交易"概念，此函数提供大股东持股变动作为替代。
    调用 AkShare 的 stock_gdfx_holding_change_em() 接口。

    返回 CSV 格式，包含股东名称、变动方向、变动数量等信息。
    """
    try:
        code = normalize_symbol(ticker)

        # stock_gdfx_holding_change_em 返回所有股票的近期变动
        # 需要按日期过滤（默认获取最新日期）
        df = ak.stock_gdfx_holding_change_em()

        if df.empty:
            return f"No shareholder change data found for '{ticker}'"

        # 过滤当前股票的变动
        if "股票代码" in df.columns:
            df = df[df["股票代码"] == code]
        elif "代码" in df.columns:
            df = df[df["代码"] == code]

        if df.empty:
            return f"No shareholder change data found for '{ticker}'"

        csv_string = df.to_csv(index=False)

        header = f"# Shareholder Holding Changes for {code} (Insider Transaction Proxy)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving shareholder changes for {ticker}: {str(e)}"


# ============================================================================
# 8. 个股新闻
# ============================================================================

def get_akshare_news(
    ticker: Annotated[str, "股票代码"],
    start_date: Annotated[str, "开始日期，格式 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD"],
) -> str:
    """
    获取 A 股个股新闻。

    调用 AkShare 的 stock_news_em() 接口（东方财富新闻源）。
    返回 Markdown 格式的新闻列表，与 yfinance_news 返回格式一致。
    """
    try:
        code = normalize_symbol(ticker)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        df = ak.stock_news_em(symbol=code)

        if df.empty:
            return f"No news found for {ticker}"

        # 按日期范围过滤
        if "发布时间" in df.columns:
            df["发布时间"] = pd.to_datetime(df["发布时间"])
            df = df[(df["发布时间"] >= start_dt) & (df["发布时间"] <= end_dt + relativedelta(days=1))]
        elif "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])
            df = df[(df["日期"] >= start_dt) & (df["日期"] <= end_dt + relativedelta(days=1))]

        if df.empty:
            return f"No news found for {ticker} between {start_date} and {end_date}"

        # 构建 Markdown 格式新闻
        news_str = ""
        count = 0
        for _, row in df.iterrows():
            title = row.get("标题", row.get("title", "No title"))
            content = row.get("内容", row.get("content", ""))
            source = row.get("文章来源", row.get("source", "Unknown"))
            url = row.get("新闻链接", row.get("url", ""))

            news_str += f"### {title} (source: {source})\n"
            if content and isinstance(content, str) and len(content) > 200:
                content = content[:200] + "..."
            if content:
                news_str += f"{content}\n"
            if url:
                news_str += f"Link: {url}\n"
            news_str += "\n"
            count += 1

        return f"## {code} News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching news for {ticker}: {str(e)}"


# ============================================================================
# 9. 全球/宏观新闻
# ============================================================================

def get_akshare_global_news(
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"],
    look_back_days: Annotated[int, "回溯天数"] = 7,
    limit: Annotated[int, "最大返回数量"] = 10,
) -> str:
    """
    获取全球/宏观新闻。

    使用 AkShare 的 news_cctv() 接口获取央视新闻联播文本，
    作为宏观政策新闻的替代来源。

    注意：AkShare 没有直接对应 yfinance 的全球市场新闻接口，
    但 news_cctv() 提供了重要的宏观政策信息，对 A 股分析有参考价值。
    """
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - relativedelta(days=look_back_days)
        start_date = start_dt.strftime("%Y-%m-%d")

        news_str = ""

        try:
            # 尝试获取央视新闻联播（宏观政策新闻的重要来源）
            cctv_news = ak.news_cctv()
            if cctv_news is not None and not cctv_news.empty:
                # 按日期过滤
                if "date" in cctv_news.columns or "日期" in cctv_news.columns:
                    date_col = "date" if "date" in cctv_news.columns else "日期"
                    cctv_news[date_col] = pd.to_datetime(cctv_news[date_col])
                    cctv_news = cctv_news[
                        (cctv_news[date_col] >= start_dt) &
                        (cctv_news[date_col] <= curr_dt + relativedelta(days=1))
                    ]

                for _, row in cctv_news.head(limit).iterrows():
                    title = row.get("title", row.get("标题", "No title"))
                    content = row.get("content", row.get("内容", ""))
                    news_str += f"### {title} (source: CCTV)\n"
                    if content:
                        content_str = str(content)
                        if len(content_str) > 300:
                            content_str = content_str[:300] + "..."
                        news_str += f"{content_str}\n"
                    news_str += "\n"
        except Exception:
            pass

        if not news_str:
            # 如果央视新闻获取失败，尝试获取财联社电报
            try:
                cls_telegraph = ak.stock_info_global_cls()
                if cls_telegraph is not None and not cls_telegraph.empty:
                    for _, row in cls_telegraph.head(limit).iterrows():
                        title = str(row.iloc[0]) if len(row) > 0 else "No title"
                        news_str += f"### {title} (source: CLS Telegraph)\n\n"
            except Exception:
                pass

        if not news_str:
            return (
                f"## Global Market News (AkShare), from {start_date} to {curr_date}:\n\n"
                f"No macro news available for this period. "
                f"Note: AkShare provides China-focused data. "
                f"For global market news, consider using yfinance as a fallback."
            )

        return f"## Global Market News (AkShare), from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching global news: {str(e)}"