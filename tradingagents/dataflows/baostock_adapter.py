"""
BaoStock 数据供应商适配器
基于 BaoStock 库提供中国 A 股市场数据

BaoStock 特点：
  - 免费、无需注册、无需 API Key
  - 数据来源于证券宝（www.baostock.com）
  - 提供完整的历史日线、周线、月线数据
  - 提供公司基本面、财务报表数据
  - 相比 AkShare 更稳定，不依赖东方财富 API

与 AkShare 适配器互备：
  - AkShare 优先：数据更全面（新闻、实时行情等）
  - BaoStock 备选：更稳定，提供准确的财务报表数据

功能覆盖：
  1. get_stock_data()       - 股票历史日线 OHLCV 数据
  2. get_fundamentals()     - 公司基本面数据
  3. get_balance_sheet()    - 资产负债表
  4. get_cashflow()         - 现金流量表
  5. get_income_statement() - 利润表

支持的股票代码格式：
  - "sh.601118" 或 "sz.000001" 标准格式
  - "601118" 或 "000001" 纯数字格式（自动补充前缀）
  - "海南橡胶" 等中文名（通过内置映射表）

依赖：
  - baostock: A 股数据接口库
  - pandas: 数据处理
"""

import os
import random
import threading
import time
from datetime import datetime
from typing import Annotated, Optional

import pandas as pd

# ============================================================================
# BaoStock 全局会话锁
# ============================================================================
# BaoStock 的 bs.login()/bs.logout() 操作的是模块级全局套接字，
# 多个工具函数连续调用（如 get_fundamentals → get_balance_sheet → get_cashflow
# → get_income_statement）会反复 login/logout，套接字状态容易错乱，
# 抛出 WinError 10057（套接字未连接）。
#
# 用全局锁串行化所有 BaoStock 操作，并在每次 login 前先强制 logout 清理旧状态。
_bs_lock = threading.Lock()


def _bs_safe_login():
    """
    安全登录 BaoStock：先强制 logout 清理可能残留的旧套接字，再 login。
    必须在 _bs_lock 持有后调用。
    """
    import baostock as bs
    # 先尝试 logout 清理旧状态（logout 是幂等的，未登录时调用也安全）
    try:
        bs.logout()
    except Exception:
        pass  # 忽略 logout 异常，继续 login
    lg = bs.login()
    return lg


# ============================================================================
# 简易股票代码标准化（不依赖外部模块，与 cache_provider 保持一致）
# ============================================================================

_CHINESE_NAME_MAP = {
    "海南橡胶": "601118",
    "平安银行": "000001",
    "万科A": "000002",
    "万科": "000002",
    "中国平安": "601318",
    "贵州茅台": "600519",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "招商银行": "600036",
    "五粮液": "000858",
    "京东方A": "000725",
    "京东方": "000725",
    "中兴通讯": "000063",
    "东方财富": "300059",
    "中芯国际": "688981",
    "工商银行": "601398",
    "农业银行": "601288",
    "中国石油": "601857",
    "中国石化": "600028",
    "长江电力": "600900",
    "中国神华": "601088",
    "隆基绿能": "601012",
    "海尔智家": "600690",
    "格力电器": "000651",
    "美的集团": "000333",
    "海康威视": "002415",
    "伊利股份": "600887",
    "恒瑞医药": "600276",
    "药明康德": "603259",
    "中信证券": "600030",
    "中国中车": "601766",
    "中国建筑": "601668",
    "中国联通": "600050",
    "紫金矿业": "601899",
    "万华化学": "600309",
    "三一重工": "600031",
    "福耀玻璃": "600660",
    "海螺水泥": "600585",
    "上汽集团": "600104",
    "保利发展": "600048",
    "通威股份": "600438",
    "阳光电源": "300274",
    "科大讯飞": "002230",
    "立讯精密": "002475",
    "工业富联": "601138",
    # —— 有色金属与矿业 ——
    "云南铜业": "000878",
    "江西铜业": "600362",
    "铜陵有色": "000630",
    "洛阳钼业": "603993",
    "北方稀土": "600111",
    "山东黄金": "600547",
    "中金黄金": "600489",
    "天齐锂业": "002466",
    "赣锋锂业": "002460",
    "华友钴业": "603799",
    "中国铝业": "601600",
    "云铝股份": "000807",
    # —— 钢铁 ——
    "宝钢股份": "600019",
    "鞍钢股份": "000898",
    "包钢股份": "600010",
    # —— 医药 ——
    "云南白药": "000538",
    "同仁堂": "600085",
    "片仔癀": "600436",
    # —— 军工 ——
    "中航沈飞": "600760",
    "航发动力": "600893",
    "中国船舶": "600150",
    # —— 消费 ——
    "双汇发展": "000895",
    "永辉超市": "601933",
    # —— 科技 ——
    "京东方": "000725",
    "京东方A": "000725",
    "中科曙光": "603019",
    "浪潮信息": "000977",
    # —— 券商 ——
    "华泰证券": "601688",
    "国泰君安": "601211",
    "海通证券": "600837",
    # —— 保险 ——
    "中国人寿": "601628",
    "新华保险": "601336",
    "中国太保": "601601",
}


def _is_valid_a_share_code(code: str) -> bool:
    """
    判断字符串是否为合法的 A 股 6 位数字代码。
    A 股代码规则：
      - 60xxxx / 68xxxx：上海证券交易所
      - 00xxxx / 30xxxx：深圳证券交易所
      - 8xxxxx / 4xxxxx：北京证券交易所
    """
    if not code or not code.isdigit() or len(code) != 6:
        return False
    if code[0] in ("6", "0", "3", "8", "4"):
        return True
    return False


def _normalize(symbol: str) -> str:
    """
    标准化为 6 位纯数字代码。

    支持格式：
      - 中文名 → 6 位代码（如 "海南橡胶" → "601118"）
      - sh.XXXXXX / sz.XXXXXX → 6 位代码
      - XXXXXX.SZ / XXXXXX.SS → 6 位代码（yfinance 格式）
      - 纯 6 位数字 → 原样返回
    """
    s = symbol.strip()
    if s in _CHINESE_NAME_MAP:
        return _CHINESE_NAME_MAP[s]
    # 处理 sh.XXXXXX / sz.XXXXXX / bj.XXXXXX 格式
    if s.lower().startswith(("sh", "sz", "bj")) and len(s) == 8:
        return s[2:]
    # 处理 XXXXXX.SZ / XXXXXX.SS / XXXXXX.SH 格式（yfinance 后缀）
    if "." in s:
        parts = s.split(".")
        if parts[0].isdigit() and len(parts[0]) == 6:
            return parts[0]
    return s


def _to_baostock_code(symbol: str) -> str:
    """
    将 6 位数字代码转换为 BaoStock 格式 (sh.XXXXXX 或 sz.XXXXXX)。

    上海交易所：60xxxx, 68xxxx → sh.xxxxxx
    深圳交易所：00xxxx, 30xxxx → sz.xxxxxx
    """
    code = _normalize(symbol)
    if code.startswith("6"):
        return f"sh.{code}"
    elif code.startswith(("0", "3")):
        return f"sz.{code}"
    return code


def _to_baostock_date(date_str: str) -> str:
    """将 'YYYY-MM-DD' 转为 BaoStock 的 'YYYY-MM-DD' 格式（相同）"""
    return date_str


# ============================================================================
# 重试工具
# ============================================================================

def bs_retry(func, max_retries=2, base_delay=1.0):
    """带指数退避的重试包装器"""
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 0.5))
    raise last_exception


# ============================================================================
# 1. 股票历史数据
# ============================================================================

def get_baostock_stock(
    symbol: Annotated[str, "股票代码"],
    start_date: Annotated[str, "开始日期，格式 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD"],
) -> str:
    """
    从 BaoStock 获取 A 股历史日线 OHLCV 数据。

    返回格式与 yfinance/AkShare 适配器一致：CSV 字符串 + 元数据头部。
    """
    try:
        code = _normalize(symbol)
        # 若 normalize 后仍非合法 A 股代码（如中文名无映射），直接返回错误，
        # 不去登录 BaoStock —— 避免无效查询污染全局套接字状态
        if not _is_valid_a_share_code(code):
            return f"No data: '{symbol}' is not a valid A-share code"

        bs_code = _to_baostock_code(code)

        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        import baostock as bs

        def _query():
            # 用全局锁串行化 BaoStock 操作，避免并发 login/logout 导致套接字错乱
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")

                try:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume",
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",
                        adjustflag="2",  # 前复权
                    )
                    if rs.error_code != "0":
                        raise RuntimeError(f"BaoStock query failed: {rs.error_msg}")

                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())

                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass  # logout 失败不阻塞流程

        rows = bs_retry(_query, max_retries=2)

        if not rows:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

        # 构建 CSV 输出
        header = f"# Stock data for {code} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(rows)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        header += f"# Source: BaoStock\n\n"
        header += "Date,Open,High,Low,Close,Volume\n"

        csv_lines = []
        for row in rows:
            date, open_p, high, low, close, volume = row
            csv_lines.append(f"{date},{float(open_p):.2f},{float(high):.2f},{float(low):.2f},{float(close):.2f},{int(float(volume))}")

        return header + "\n".join(csv_lines)

    except Exception:
        # 重新抛出异常，让 route_to_vendor 的降级机制触发
        raise


# ============================================================================
# 2. 公司基本面数据
# ============================================================================

def get_baostock_fundamentals(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"],
) -> str:
    """
    从 BaoStock 获取 A 股公司基本面数据。

    包括：公司名称、行业、上市日期、总股本、流通股本等。
    """
    try:
        code = _normalize(ticker)
        # 中文名无映射时直接返回错误，不登录 BaoStock
        if not _is_valid_a_share_code(code):
            return f"No data: '{ticker}' is not a valid A-share code"

        bs_code = _to_baostock_code(code)

        import baostock as bs

        def _query():
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")

                try:
                    # 获取股票基本信息
                    rs = bs.query_stock_basic(code=bs_code)
                    if rs.error_code != "0":
                        return None

                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())

                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass

        rows = bs_retry(_query, max_retries=2)

        if not rows:
            return f"No fundamentals data found for {ticker}"

        # 解析基本信息
        row = rows[0]
        # baoStock 返回格式: [code, code_name, ipoDate, outDate, type, status]
        code_name = row[1] if len(row) > 1 else code
        ipo_date = row[2] if len(row) > 2 else "N/A"

        # 获取行业分类
        def _query_industry():
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    return None
                try:
                    rs = bs.query_stock_industry(code=bs_code)
                    if rs.error_code != "0":
                        return None
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass

        industry_rows = bs_retry(_query_industry, max_retries=1)
        industry = industry_rows[0][-1] if industry_rows and len(industry_rows[0]) >= 4 else "N/A"

        result = f"# Company Fundamentals for {code} (BaoStock)\n"
        result += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        result += f"股票名称: {code_name}\n"
        result += f"股票代码: {code}\n"
        result += f"行业: {industry}\n"
        result += f"上市日期: {ipo_date}\n"

        return result

    except Exception:
        raise


# ============================================================================
# 3. 资产负债表
# ============================================================================

def get_baostock_balance_sheet(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "频率: annual 或 quarterly"],
    curr_date: Annotated[str, "当前日期"],
) -> str:
    """从 BaoStock 获取资产负债表数据"""
    try:
        code = _normalize(ticker)
        if not _is_valid_a_share_code(code):
            return f"No data: '{ticker}' is not a valid A-share code"

        bs_code = _to_baostock_code(code)

        import baostock as bs

        def _query():
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")

                try:
                    year = int(curr_date[:4])
                    rs = bs.query_balance_data(code=bs_code, year=year, quarter=4)
                    if rs.error_code != "0":
                        return None

                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())

                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass

        rows = bs_retry(_query, max_retries=2)

        if not rows:
            return f"No balance sheet data found for {ticker}"

        # 返回原始 CSV 格式，与各适配器保持一致
        result = f"# Balance Sheet data for {code} ({freq}) (BaoStock)\n"
        result += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        if rows:
            # 构建字段名列表
            fields = rows[0].__class__.__name__ if hasattr(rows[0], '__class__') else ""
            for row in rows:
                result += " | ".join(str(f) for f in row) + "\n"

        return result

    except Exception:
        raise


# ============================================================================
# 4. 现金流量表
# ============================================================================

def get_baostock_cashflow(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "频率: annual 或 quarterly"],
    curr_date: Annotated[str, "当前日期"],
) -> str:
    """从 BaoStock 获取现金流量表数据"""
    try:
        code = _normalize(ticker)
        if not _is_valid_a_share_code(code):
            return f"No data: '{ticker}' is not a valid A-share code"

        bs_code = _to_baostock_code(code)

        import baostock as bs

        def _query():
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")

                try:
                    year = int(curr_date[:4])
                    rs = bs.query_cash_flow_data(code=bs_code, year=year, quarter=4)
                    if rs.error_code != "0":
                        return None

                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())

                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass

        rows = bs_retry(_query, max_retries=2)

        if not rows:
            return f"No cashflow data found for {ticker}"

        result = f"# Cash Flow data for {code} ({freq}) (BaoStock)\n"
        result += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        for row in rows:
            result += " | ".join(str(f) for f in row) + "\n"

        return result

    except Exception:
        raise


# ============================================================================
# 5. 利润表
# ============================================================================

def get_baostock_income_statement(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "频率: annual 或 quarterly"],
    curr_date: Annotated[str, "当前日期"],
) -> str:
    """从 BaoStock 获取利润表数据"""
    try:
        code = _normalize(ticker)
        if not _is_valid_a_share_code(code):
            return f"No data: '{ticker}' is not a valid A-share code"

        bs_code = _to_baostock_code(code)

        import baostock as bs

        def _query():
            with _bs_lock:
                lg = _bs_safe_login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")

                try:
                    year = int(curr_date[:4])
                    rs = bs.query_profit_data(code=bs_code, year=year, quarter=4)
                    if rs.error_code != "0":
                        return None

                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())

                    return rows
                finally:
                    try:
                        bs.logout()
                    except Exception:
                        pass

        rows = bs_retry(_query, max_retries=2)

        if not rows:
            return f"No income statement data found for {ticker}"

        result = f"# Income Statement data for {code} ({freq}) (BaoStock)\n"
        result += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        for row in rows:
            result += " | ".join(str(f) for f in row) + "\n"

        return result

    except Exception:
        raise