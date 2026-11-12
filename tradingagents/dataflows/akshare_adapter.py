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
import random
import threading
import time
from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta

from .ashare_rules import (
    contains_cjk,
    exchange_of,
    is_a_share_code,
    normalize_code,
    report_available_date,
    risk_warnings,
)

# AkShare 是重量级可选依赖：缺失时模块仍可导入，
# 便于在非 A 股场景、单元测试与文档生成环境中使用纯函数能力。
# 实际发起行情请求前会检查 ak 是否为 None 并抛出明确错误。
try:
    import akshare as ak
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    ak = None


# ============================================================================
# AkShare 自定义异常类
# ============================================================================

class AkShareRateLimitError(Exception):
    """
    AkShare API 速率限制超限异常。

    继承自 Exception 基类，表示东方财富 API 请求频率超过限制。
    当触发此异常时，应该等待一段时间后重试，或者切换到其他数据源。

    使用示例：
        try:
            get_akshare_stock(...)
        except AkShareRateLimitError:
            print("东方财富 API 请求太频繁，请稍后再试")
    """
    pass


class AkShareTimeoutError(Exception):
    """
    AkShare API 请求超时异常。

    继承自 Exception 基类，表示东方财富 API 请求超时。
    可能是网络问题或东方财富服务器响应缓慢。

    使用示例：
        try:
            get_akshare_stock(...)
        except AkShareTimeoutError:
            print("东方财富 API 请求超时，请稍后再试")
    """
    pass


# ============================================================================
# AkShare 配置常量
# ============================================================================
# 这些常量可以通过环境变量覆盖，方便运行时配置

# AkShare API 超时时间（秒）
# 东方财富 API 响应较慢，建议设置较长超时
AKSHARE_TIMEOUT = int(os.getenv("AKSHARE_TIMEOUT", "30"))

# 默认重试次数
AKSHARE_MAX_RETRIES = int(os.getenv("AKSHARE_MAX_RETRIES", "3"))

# 重试基础延迟（秒）
AKSHARE_BASE_DELAY = float(os.getenv("AKSHARE_BASE_DELAY", "2.0"))

# 最大延迟时间（秒）
AKSHARE_MAX_DELAY = float(os.getenv("AKSHARE_MAX_DELAY", "30.0"))

# 请求间隔时间（秒），避免快速连续请求触发限流
AKSHARE_REQUEST_INTERVAL = float(os.getenv("AKSHARE_REQUEST_INTERVAL", "0.5"))

# 上次请求时间戳（模块级，用于请求间隔控制）
_last_request_time = 0.0


def _require_akshare():
    """确保 AkShare 可用，否则抛出明确错误。

    AkShare 是可选的重量级依赖。缺失时模块仍可导入，
    这样纯函数（代码归一化、日期转换等）与单元测试不受影响，
    但任何真正发起行情请求的入口都会立刻给出可操作的错误提示，
    而不是抛出难以理解的 AttributeError: 'NoneType' has no attribute ...
    """
    if ak is None:
        raise ImportError(
            "未安装 akshare，无法使用 A 股数据源。请执行: pip install akshare\n"
            "如仅需代码归一化等纯函数功能，可忽略此依赖。"
        )
    return ak


def _throttle_request():
    """
    请求节流控制，避免快速连续请求触发东方财富 API 限流。

    根据 AKSHARE_REQUEST_INTERVAL 配置，在连续请求之间添加最小间隔。
    """
    global _last_request_time
    current_time = time.time()
    elapsed = current_time - _last_request_time

    if elapsed < AKSHARE_REQUEST_INTERVAL:
        sleep_time = AKSHARE_REQUEST_INTERVAL - elapsed
        time.sleep(sleep_time)

    _last_request_time = time.time()


def _run_with_timeout(func, timeout: int = None):
    """
    在指定超时时间内执行函数。

    使用线程实现超时机制，适用于Windows平台。

    参数：
      func: 要执行的无参函数
      timeout: 超时时间（秒），默认使用 AKSHARE_TIMEOUT

    返回：
      函数的返回值

    异常：
      AkShareTimeoutError: 如果函数执行超时
    """
    if timeout is None:
        timeout = AKSHARE_TIMEOUT

    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = func()
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        # 线程仍在运行，说明超时了
        raise AkShareTimeoutError(
            f"AkShare API 请求超时（{timeout}秒）。"
            f"可能是网络问题或东方财富服务器响应缓慢。"
        )

    if exception[0] is not None:
        raise exception[0]

    return result[0]


# ============================================================================
# 通用重试工具
# ============================================================================

def retry_with_backoff(
    func,
    max_retries: int = None,
    base_delay: float = None,
    max_delay: float = None,
    timeout: int = None,
    use_timeout: bool = False,
):
    """
    带指数退避的重试包装器。

    用于处理 AkShare API 的限流和网络波动问题。

    参数：
      func: 要重试的无参函数
      max_retries: 最大重试次数（默认使用 AKSHARE_MAX_RETRIES）
      base_delay: 初始等待秒数（默认使用 AKSHARE_BASE_DELAY）
      max_delay: 最大等待秒数（默认使用 AKSHARE_MAX_DELAY）
      timeout: 请求超时时间（秒），默认使用 AKSHARE_TIMEOUT
      use_timeout: 是否启用超时机制（默认 False）

    返回：
      函数的返回值，或最后一次异常

    退避策略：
      第1次重试: 等待 base_delay + 随机抖动
      第2次重试: 等待 base_delay*2 + 随机抖动
      第3次重试: 等待 base_delay*4 + 随机抖动
    """
    if max_retries is None:
        max_retries = AKSHARE_MAX_RETRIES
    if base_delay is None:
        base_delay = AKSHARE_BASE_DELAY
    if max_delay is None:
        max_delay = AKSHARE_MAX_DELAY
    if timeout is None:
        timeout = AKSHARE_TIMEOUT

    def execute_with_timeout():
        if use_timeout:
            return _run_with_timeout(func, timeout)
        else:
            return func()

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            # 在每次请求前进行节流控制
            _throttle_request()
            return execute_with_timeout()
        except AkShareTimeoutError:
            # 超时异常，直接重试，不增加额外等待
            last_exception = AkShareTimeoutError(
                f"AkShare API 请求超时（{timeout}秒），"
                f"第 {attempt + 1}/{max_retries + 1} 次尝试"
            )
            if attempt < max_retries:
                # 超时后等待较短时间再重试
                delay = min(base_delay * (2 ** attempt), max_delay)
                delay += random.uniform(0, 1.0)
                time.sleep(delay)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                # 添加随机抖动，避免多个请求同时重试
                delay += random.uniform(0, 1.0)
                time.sleep(delay)
    raise last_exception


# ============================================================================
# 股票代码标准化
# ============================================================================

# 常见 A 股中文名称 → 6 位代码映射表
# 用于快速查询，避免每次调用 AkShare 实时 API
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
    "兴业银行": "601166",
    "美的集团": "000333",
    "格力电器": "000651",
    "中信证券": "600030",
    "伊利股份": "600887",
    "恒瑞医药": "600276",
    "迈瑞医疗": "300760",
    "海康威视": "002415",
    "药明康德": "603259",
    "隆基绿能": "601012",
    "长江电力": "600900",
    "中国神华": "601088",
    "中国石油": "601857",
    "中国石化": "600028",
    "工商银行": "601398",
    "建设银行": "601939",
    "农业银行": "601288",
    "中国银行": "601988",
    "交通银行": "601328",
    "浦发银行": "600000",
    "中信银行": "601998",
    "民生银行": "600016",
    "光大银行": "601818",
    "中国太保": "601601",
    "中国人寿": "601628",
    "保利发展": "600048",
    "中国建筑": "601668",
    "中国中铁": "601390",
    "中国铁建": "601186",
    "中国交建": "601800",
    "中国中车": "601766",
    "中国联通": "600050",
    "中国电信": "601728",
    "中国移动": "600941",
    "京东方A": "000725",
    "京东方": "000725",
    "中兴通讯": "000063",
    "立讯精密": "002475",
    "工业富联": "601138",
    "中芯国际": "688981",
    "紫金矿业": "601899",
    "洛阳钼业": "603993",
    "北方稀土": "600111",
    "天齐锂业": "002466",
    "赣锋锂业": "002460",
    "阳光电源": "300274",
    "通威股份": "600438",
    "中科创达": "300496",
    "科大讯飞": "002230",
    "三六零": "601360",
    "金山办公": "688111",
    "东方财富": "300059",
    "同花顺": "300033",
    "恒生电子": "600570",
    "爱尔眼科": "300015",
    "片仔癀": "600436",
    "云南白药": "000538",
    "华润三九": "000999",
    "上汽集团": "600104",
    "长城汽车": "601633",
    "长安汽车": "000625",
    "广汽集团": "601238",
    "赛力斯": "601127",
    "中航沈飞": "600760",
    "中航西飞": "000768",
    "航发动力": "600893",
    "中国船舶": "600150",
    "中国重工": "601989",
    "海螺水泥": "600585",
    "福耀玻璃": "600660",
    "万华化学": "600309",
    "宝钢股份": "600019",
    "三一重工": "600031",
    "潍柴动力": "000338",
    "中联重科": "000157",
    "徐工机械": "000425",
    "海尔智家": "600690",
    "海信家电": "000921",
    "青岛啤酒": "600600",
    "泸州老窖": "000568",
    "山西汾酒": "600809",
    "洋河股份": "002304",
    "古井贡酒": "000596",
    "海天味业": "603288",
    "牧原股份": "002714",
    "双汇发展": "000895",
    "新希望": "000876",
    "温氏股份": "300498",
    "中远海控": "601919",
    "顺丰控股": "002352",
    "中通快递": "002057",
    "圆通速递": "600233",
    "韵达股份": "002120",
    "中国国航": "601111",
    "南方航空": "600029",
    "中国东航": "600115",
    "春秋航空": "601021",
    "上海机场": "600009",
    "白云机场": "600004",
    "深圳机场": "000089",
    "万科企业": "000002",
    "华侨城A": "000069",
    "招商蛇口": "001979",
    "金地集团": "600383",
    "绿地控股": "600606",
    "华夏幸福": "600340",
    "新城控股": "601155",
    "杭州银行": "600926",
    "宁波银行": "002142",
    "南京银行": "601009",
    "北京银行": "601169",
    "上海银行": "601229",
    "江苏银行": "600919",
    "浙商银行": "601916",
    "邮储银行": "601658",
    "华夏银行": "600015",
    # —— 有色金属与矿业（与 baostock_adapter 保持同步）——
    "云南铜业": "000878",
    "江西铜业": "600362",
    "铜陵有色": "000630",
    "山东黄金": "600547",
    "中金黄金": "600489",
    "华友钴业": "603799",
    "中国铝业": "601600",
    "云铝股份": "000807",
    # —— 钢铁 ——
    "鞍钢股份": "000898",
    "包钢股份": "600010",
    # —— 医药 ——
    "同仁堂": "600085",
    # —— 消费 ——
    "永辉超市": "601933",
    # —— 科技 ——
    "中科曙光": "603019",
    "浪潮信息": "000977",
    # —— 券商 ——
    "华泰证券": "601688",
    "国泰君安": "601211",
    "海通证券": "600837",
    # —— 保险 ——
    "新华保险": "601336",
}

# AkShare 实时查询缓存（模块级，避免重复调用）
_name_cache: dict = {}
_name_cache_loaded = False


def _load_akshare_names() -> dict:
    """
    从 AkShare 实时加载所有 A 股名称和代码映射。
    结果缓存到模块级变量，仅首次成功加载后缓存。

    数据源优先级：
      1. stock_info_a_code_name()：轻量级接口，只返回代码+名称两列，
         不依赖实时行情，调用快、稳定性高。约 5500 条记录。
      2. stock_zh_a_spot_em()：实时行情接口（包含全部股票代码+名称+行情），
         数据更全但调用更慢、更易限流。作为兜底。

    Returns:
        {股票名称: 6位代码} 字典；加载失败时返回空字典（继续使用内置映射表）
    """
    _require_akshare()

    global _name_cache, _name_cache_loaded
    if _name_cache_loaded:
        return _name_cache

    # 尝试轻量级接口 stock_info_a_code_name（优先，更稳定）
    try:
        df = retry_with_backoff(
            lambda: ak.stock_info_a_code_name(),
            max_retries=AKSHARE_MAX_RETRIES,
            base_delay=AKSHARE_BASE_DELAY,
        )

        if df is not None and not df.empty and {"code", "name"}.issubset(df.columns):
            for _, row in df.iterrows():
                name = str(row["name"]).strip()
                code = str(row["code"]).strip()
                if name and code.isdigit() and len(code) == 6:
                    _name_cache[name] = code
    except Exception:
        pass

    # 兜底：尝试 stock_zh_a_spot_em（覆盖范围更广，但易限流）
    if not _name_cache:
        try:
            df = retry_with_backoff(
                lambda: ak.stock_zh_a_spot_em(),
                max_retries=AKSHARE_MAX_RETRIES,
                base_delay=AKSHARE_BASE_DELAY,
            )

            if df is not None and not df.empty:
                name_col = None
                code_col = None
                for col in df.columns:
                    if "名称" in col or col == "name":
                        name_col = col
                    if "代码" in col or col == "code":
                        code_col = col

                if name_col and code_col:
                    for _, row in df.iterrows():
                        name = str(row[name_col]).strip()
                        code = str(row[code_col]).strip()
                        if name and code.isdigit() and len(code) == 6:
                            _name_cache[name] = code
        except Exception:
            pass

    # 仅在成功加载且拿到数据时才标记为已加载，
    # 否则下次调用会重试（避免网络抖动导致永久缓存空结果）
    if _name_cache:
        _name_cache_loaded = True

    return _name_cache


def resolve_stock_name(symbol: str) -> str:
    """
    将股票名称（中文名、代码、带前缀代码）解析为 6 位纯数字代码。

    支持格式：
      - "601118"       → "601118" (6位纯数字)
      - "sh600000"     → "600000" (带交易所前缀)
      - "海南橡胶"      → "601118" (中文名，查内置映射表)
      - "NVDA"         → "NVDA"   (美股，原样返回)

    参数：
      symbol: 原始输入

    返回：
      6 位数字代码（A 股）或原始字符串（美股等）
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return ""

    # 1. 纯代码 / 带前缀 / 带后缀：交给纯函数处理，不触网
    #    覆盖 600519、sh600519、sh.600519、600519.SH、600519.SS、600519.XSHG 等写法
    code = normalize_code(symbol)
    if code:
        return code

    # 2. 中文名称 → 先查内置映射表
    if symbol in _CHINESE_NAME_MAP:
        return _CHINESE_NAME_MAP[symbol]

    # 3. 仅当输入看起来是中文名称时，才查 AkShare 实时名称表。
    #    这样 is_a_share("NVDA") 之类的判断不会因为查询名称表而发起网络请求。
    if contains_cjk(symbol):
        try:
            names = _load_akshare_names()
        except Exception:
            names = {}
        if symbol in names:
            return names[symbol]

    # 4. 无法识别，返回原始字符串（可能是美股等）
    return symbol


def normalize_symbol(symbol: str) -> str:
    """
    将股票代码标准化为 AkShare 要求的 6 位纯数字格式。

    这是 resolve_stock_name 的别名，保持向后兼容。

    支持的输入格式：
      - "000001"     → "000001" (深圳主板)
      - "600000"     → "600000" (上海主板)
      - "sh600000"   → "600000" (带前缀)
      - "sz000001"   → "000001" (带前缀)
      - "NVDA"       → "NVDA"   (美股，不做转换)
      - "海南橡胶"    → "601118" (中文名，自动查找)

    参数：
      symbol: 原始股票代码

    返回：
      标准化的 6 位数字代码（或原始代码如果不是 A 股格式）
    """
    return resolve_stock_name(symbol)


def _fast_code(symbol: str) -> str:
    """尽最大努力把输入转成 A 股代码，且对非中文输入绝不触网。"""
    code = normalize_code(symbol)
    if code:
        return code
    if contains_cjk(symbol):
        return resolve_stock_name(symbol)
    return ""


def is_a_share(symbol: str) -> bool:
    """判断是否为 A 股代码（排除沪深 B 股与港美股）。"""
    return is_a_share_code(_fast_code(symbol))


def is_shanghai(symbol: str) -> bool:
    """判断是否为上海交易所股票（60/601/603/605/688/689 开头）。"""
    return exchange_of(_fast_code(symbol)) == "sh"


def is_shenzhen(symbol: str) -> bool:
    """判断是否为深圳交易所股票（00/001/002/003/300/301 开头）。"""
    return exchange_of(_fast_code(symbol)) == "sz"


def is_beijing(symbol: str) -> bool:
    """判断是否为北京交易所股票（4/8 开头）。"""
    return exchange_of(_fast_code(symbol)) == "bj"


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
# 股票历史行情获取（多数据源容错）
# ============================================================================

def _to_sina_symbol(code: str) -> str:
    """
    将 6 位纯数字代码转换为新浪 / 腾讯接口所需的带前缀格式。

      - 上海交易所（60xxxx / 68xxxx）→ sh + 代码，如 sh601118
      - 深圳交易所（00xxxx / 30xxxx）→ sz + 代码，如 sz000001
      - 北京交易所（4xxxxx / 8xxxxx）→ bj + 代码，如 bj830799
    """
    exchange = exchange_of(code)
    if exchange in ("sh", "sz", "bj"):
        return exchange + code
    # 非 A 股代码原样返回，交由调用方处理
    return code


def _normalize_columns(df: "pd.DataFrame", column_map: dict) -> "pd.DataFrame":
    """
    将任意数据源返回的 DataFrame 统一为中文列名。

    参数：
      df: 原始 DataFrame
      column_map: 源列名 → 目标中文列名 的映射
    返回：
      只保留存在的源列、并重命名后的副本
    """
    keep = [c for c in column_map if c in df.columns]
    return df[keep].rename(columns=column_map).copy()


_HIST_PRICE_COLUMNS = ("开盘", "最高", "最低", "收盘", "成交量")


def _finalize_hist(df: "pd.DataFrame") -> "pd.DataFrame":
    """统一历史行情：日期解析、数值化、升序、去重、丢弃无效行。

    这是 A 股行情链路的关键清洗步骤：
      - 不同数据源（东财/新浪/腾讯）返回的日期类型和顺序不一致；
      - 成交量可能以字符串返回；
      - 停牌或异常行可能出现空收盘价。
    不统一清洗会直接导致技术指标与回测结果错误。
    """
    if df is None or df.empty or "日期" not in df.columns or "收盘" not in df.columns:
        return pd.DataFrame()

    out = df.copy()
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    for column in _HIST_PRICE_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.dropna(subset=["日期", "收盘"])
    out = out[out["收盘"] > 0]
    out = out.sort_values("日期").drop_duplicates(subset=["日期"], keep="last")
    if out.empty:
        return pd.DataFrame()

    out["日期"] = out["日期"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def get_stock_hist_with_fallback(
    code: str,
    start_ak: str,
    end_ak: str,
    adjust: str = "qfq",
) -> "pd.DataFrame":
    """
    获取 A 股历史日线 OHLCV 数据，带三级数据源容错。

    数据源优先级（任一被限流 / 网络阻断 / 超时 / 返回空，自动尝试下一个）：
      1. 东方财富（ak.stock_zh_a_hist）—— 主数据源，数据最全
      2. 新浪财经（ak.stock_zh_a_daily）—— 备选源 1
      3. 腾讯财经（ak.stock_zh_a_hist_tx）—— 备选源 2

    三个数据源返回列格式各不相同，本函数统一转换为中文列：
      [日期, 开盘, 最高, 最低, 收盘, 成交量]
    以便上层函数无需关心实际来自哪个数据源。

    注意：
      - 腾讯接口要求 adjust 传空字符串 ''（传 'qfq' 会解析报错），
        因此调用腾讯源时强制忽略传入的 adjust 参数。
      - 腾讯源返回的是 amount（成交额）而非 volume（成交量），
        统一映射到"成交量"列（兜底数据，仅供连续性展示）。
      - 新浪 / 腾讯接口代码需带交易所前缀（sh/sz + 6 位）。

    参数：
      code: 6 位纯数字股票代码
      start_ak: 开始日期，格式 YYYYMMDD
      end_ak: 结束日期，格式 YYYYMMDD
      adjust: 复权方式，默认 "qfq"（前复权），腾讯源忽略此参数
    """
    _require_akshare()

    # ------------------------------------------------------------------
    # 第 1 级：东方财富（主数据源）
    # ------------------------------------------------------------------
    df = None
    try:
        df = retry_with_backoff(
            lambda: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_ak,
                end_date=end_ak,
                adjust=adjust,
            ),
            max_retries=2,
            base_delay=2.0,
        )
    except Exception:
        # 东方财富被限流 / 网络阻断 / 超时：捕获异常，降级到备选源
        df = None

    if df is not None and not df.empty:
        em_map = {
            "日期": "日期",
            "开盘": "开盘",
            "最高": "最高",
            "最低": "最低",
            "收盘": "收盘",
            "成交量": "成交量",
        }
        normalized = _finalize_hist(_normalize_columns(df, em_map))
        if not normalized.empty:
            return normalized

    # 北交所：新浪 / 腾讯历史接口目前不提供北交所数据，
    # 直接返回空，避免两次无谓的网络请求与等待。
    if is_beijing(code):
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 第 2 级：新浪财经（备选源 1）
    # ------------------------------------------------------------------
    sina_symbol = _to_sina_symbol(code)
    df_sina = None
    try:
        df_sina = retry_with_backoff(
            lambda: ak.stock_zh_a_daily(
                symbol=sina_symbol,
                start_date=start_ak,
                end_date=end_ak,
                adjust=adjust,
            ),
            max_retries=2,
            base_delay=2.0,
        )
    except Exception:
        df_sina = None

    if df_sina is not None and not df_sina.empty:
        sina_map = {
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
        }
        normalized = _finalize_hist(_normalize_columns(df_sina, sina_map))
        if not normalized.empty:
            return normalized

    # ------------------------------------------------------------------
    # 第 3 级：腾讯财经（备选源 2）
    # 腾讯接口代码需带前缀，且 adjust 必须为空字符串（不能传 qfq）
    # ------------------------------------------------------------------
    tencent_symbol = _to_sina_symbol(code)  # 腾讯同样使用 sh/sz 前缀
    df_tx = None
    try:
        df_tx = retry_with_backoff(
            lambda: ak.stock_zh_a_hist_tx(
                symbol=tencent_symbol,
                start_date=start_ak,
                end_date=end_ak,
                adjust="",  # 腾讯源不支持 qfq 参数，必须留空
            ),
            max_retries=2,
            base_delay=2.0,
        )
    except Exception:
        df_tx = None

    if df_tx is not None and not df_tx.empty:
        # 腾讯返回列：date/open/close/high/low/amount
        # amount 为成交额，映射到"成交量"列以保证下游字段完整
        tx_map = {
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "amount": "成交量",
        }
        normalized = _finalize_hist(_normalize_columns(df_tx, tx_map))
        if not normalized.empty:
            return normalized

    # 三个数据源全部失败，返回空 DataFrame，由调用方处理
    return pd.DataFrame()


# ============================================================================
# 1. 股票历史数据
# ============================================================================

def _missing_trading_days_note(
    start_date: str, end_date: str, dates: list
) -> str:
    """对比交易日历与实际行情日期，提示停牌/无成交/数据缺失。

    只在用户请求的行情区间内做检查，帮助 LLM 意识到“少了的交易日”
    可能意味着停牌，而不是直接当作连续行情分析。
    """
    try:
        from datetime import timedelta as _timedelta

        from .trade_calendar import is_cn_trading_day

        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except Exception:
        return ""

    if end < start:
        return ""

    expected = 0
    cursor = start
    while cursor <= end:
        try:
            if is_cn_trading_day(cursor.isoformat()):
                expected += 1
        except Exception:
            return ""
        cursor += _timedelta(days=1)

    actual = len({str(d) for d in dates})
    missing = expected - actual
    if expected > 0 and missing > 0:
        return (
            f"# 注意: 区间内 {missing}/{expected} 个交易日无数据"
            "（可能停牌、无成交或数据源缺失）\n"
        )
    return ""


def get_akshare_stock(
    symbol: Annotated[str, "股票代码，如 000001, 600000"],
    start_date: Annotated[str, "开始日期，格式 YYYY-MM-DD"],
    end_date: Annotated[str, "结束日期，格式 YYYY-MM-DD"],
) -> str:
    """
    从 AkShare 获取 A 股历史日线 OHLCV 数据。

    数据源三级容错：东方财富（stock_zh_a_hist）→ 新浪财经
    （stock_zh_a_daily）→ 腾讯财经（stock_zh_a_hist_tx）。
    当上一级被限流或网络阻断时，自动降级到下一级。
    返回格式与 yfinance 适配器一致：CSV 字符串 + 元数据头部。

    返回示例：
      # Stock data for 000001 from 2024-01-01 to 2024-01-31
      # Total records: 22
      # Data retrieved on: 2024-07-16 15:30:00

      Date,Open,High,Low,Close,Volume
      2024-01-02,7.47,7.50,7.29,7.29,1158366.0
      ...
    """
    _require_akshare()

    try:
        code = normalize_symbol(symbol)
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")

        start_ak = to_ak_date(start_date)
        end_ak = to_ak_date(end_date)

        # 获取 A 股历史日线数据（前复权），带多数据源容错
        df = get_stock_hist_with_fallback(code, start_ak, end_ak, adjust="qfq")

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
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        header += _missing_trading_days_note(
            start_date, end_date, df_out["Date"].tolist()
        )
        header += "\n"

        return header + csv_string

    except Exception:
        # 重新抛出异常，让 route_to_vendor 的降级机制触发
        # 这样 AkShare 失败时自动 fallback 到 yfinance
        raise


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
    _require_akshare()

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

    # LLM 可能传入简写指标名（sma/ema），映射为 stockstats 支持的完整名称
    _INDICATOR_ALIASES = {
        "sma": "close_50_sma",
        "ema": "close_10_ema",
        "sma50": "close_50_sma",
        "sma200": "close_200_sma",
        "ema10": "close_10_ema",
    }
    indicator = _INDICATOR_ALIASES.get(indicator.strip().lower(), indicator)

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

        # 获取足够长的历史数据用于指标计算。
        # 长周期指标（如 close_200_sma）需要至少 200 个交易日，约 280 个自然日；
        # 默认 look_back_days=30 时，原逻辑只多取 90 天，远不足以计算 200 SMA。
        # 这里至少多取 400 个自然日，保证常见长周期指标都能得到有效值。
        history_extra_days = max(look_back_days * 3, 400)
        data_start = (before - relativedelta(days=history_extra_days)).strftime("%Y%m%d")
        data_end = to_ak_date(curr_date)

        # 获取历史行情（带多数据源容错：东方财富 → 新浪）
        df = get_stock_hist_with_fallback(code, data_start, data_end, adjust="qfq")

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

        # stockstats 在 wrap 时按日期排序会更稳定，这里保留 datetime 类型，
        # 与 yfinance 链路（stockstats_utils._clean_dataframe）保持一致。
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date")

        # 用 stockstats 包装并计算指标
        stock_df = wrap(df)
        # 触发指标计算
        stock_df[indicator]

        # 构建结果
        indicator_data = {}
        for _, row in stock_df.iterrows():
            val = row[indicator]
            date_key = pd.Timestamp(row["Date"]).strftime("%Y-%m-%d")
            if pd.isna(val):
                indicator_data[date_key] = "N/A"
            else:
                indicator_data[date_key] = str(round(float(val), 4))

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

    except Exception:
        raise


# ============================================================================
# 3. 基本面数据
# ============================================================================

# 全市场快照缓存：stock_zh_a_spot_em() 一次返回所有股票，
# 但调用较慢且容易触发限流，因此做 5 分钟进程内缓存。
_SPOT_SNAPSHOT_CACHE = {"ts": 0.0, "df": None}
_SPOT_SNAPSHOT_TTL = 300.0


def _get_spot_snapshot():
    """获取东方财富 A 股全市场快照（带短时缓存）。"""
    _require_akshare()
    now = time.time()
    cached = _SPOT_SNAPSHOT_CACHE.get("df")
    if cached is not None and (now - _SPOT_SNAPSHOT_CACHE.get("ts", 0)) < _SPOT_SNAPSHOT_TTL:
        return cached

    df = retry_with_backoff(
        lambda: ak.stock_zh_a_spot_em(),
        max_retries=2,
        base_delay=AKSHARE_BASE_DELAY,
    )
    _SPOT_SNAPSHOT_CACHE["df"] = df
    _SPOT_SNAPSHOT_CACHE["ts"] = now
    return df


def _find_code_column(df) -> str:
    """在 AkShare DataFrame 中寻找股票代码列。"""
    for col in ("代码", "code", "symbol"):
        if col in df.columns:
            return col
    return None


def _format_amount(value):
    """把金额格式化为亿元/万元，便于 LLM 理解。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿元"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万元"
    return f"{v:.2f}"


# 关键指标缓存：stock_financial_abstract 需要逐年份抓取，耗时可达 1 分钟，
# 因此按股票代码缓存 1 小时，避免同一分析中反复请求。
_ABSTRACT_CACHE = {}
_ABSTRACT_TTL = 3600.0


def _fetch_abstract_metrics(code: str, curr_date: str = None) -> dict:
    """
    从新浪《关键指标》接口提取最近一期已披露的财务指标。

    stock_financial_abstract 返回的列是报告期（如 20250930），
    这里只选择 <= curr_date 的最新一期，避免使用未来报告。
    """
    cache_key = (code, str(curr_date or ""))
    now = time.time()
    cached = _ABSTRACT_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _ABSTRACT_TTL:
        return dict(cached[1])

    df = retry_with_backoff(
        lambda: ak.stock_financial_abstract(symbol=code),
        max_retries=1,
        base_delay=AKSHARE_BASE_DELAY,
    )
    if df is None or df.empty or "指标" not in df.columns:
        _ABSTRACT_CACHE[cache_key] = (now, {})
        return {}

    # 按“报告期 + 法定披露截止日”过滤，避免使用分析日尚未披露的财报。
    cutoff_iso = str(curr_date)[:10] if curr_date else None
    date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    eligible = []
    for column in date_cols:
        if cutoff_iso:
            available = report_available_date(column)
            if not available or available > cutoff_iso:
                continue
        eligible.append(column)
    if not eligible:
        _ABSTRACT_CACHE[cache_key] = (now, {})
        return {}

    latest_col = max(eligible, key=lambda c: str(c))
    metrics = {}
    for _, row in df.iterrows():
        name = str(row.get("指标", "")).strip()
        if not name:
            continue
        value = row.get(latest_col)
        if value is None or pd.isna(value):
            continue
        # 同名指标可能出现在多个“选项”分组中，优先保留“常用指标”
        if name not in metrics or str(row.get("选项", "")) == "常用指标":
            metrics[name] = value
    metrics["_report_date"] = str(latest_col)
    _ABSTRACT_CACHE[cache_key] = (now, dict(metrics))
    return metrics


# 历史估值缓存：乐咕估值接口一次返回全部历史，按 (code, curr_date) 缓存 1 小时
_VALUATION_CACHE = {}
_VALUATION_TTL = 3600.0


def _fetch_historical_valuation(code: str, curr_date: str = None) -> dict:
    """获取分析日及之前的估值指标（PE-TTM / PB）。

    使用百度股市通历史估值接口（ak.stock_zh_valuation_baidu）。
    相比实时快照，历史估值能避免把"今天"的 PE/PB 用到历史时点分析中。
    接口不可用时返回空字典，由调用方退回实时快照。
    """
    cache_key = (code, str(curr_date or ""))
    now = time.time()
    cached = _VALUATION_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _VALUATION_TTL:
        return dict(cached[1])

    fetch = getattr(ak, "stock_zh_valuation_baidu", None)
    if fetch is None:
        _VALUATION_CACHE[cache_key] = (now, {})
        return {}

    cutoff = None
    period = "近一年"
    if curr_date:
        try:
            cutoff = pd.to_datetime(curr_date, errors="coerce")
        except Exception:
            cutoff = None
        if cutoff is not None and pd.notna(cutoff):
            # 按分析日距今天数选择最小可用区间，减少分页请求与日志噪音
            days = (pd.Timestamp.now().normalize() - cutoff.normalize()).days
            if days > 365 * 5:
                period = "全部"
            elif days > 365 * 3:
                period = "近五年"
            elif days > 365:
                period = "近三年"

    result: dict = {}
    for indicator, label in (("市盈率(TTM)", "市盈率-TTM"), ("市净率", "市净率")):
        try:
            df = retry_with_backoff(
                lambda ind=indicator, p=period: fetch(
                    symbol=code, indicator=ind, period=p
                ),
                max_retries=1,
                base_delay=AKSHARE_BASE_DELAY,
            )
        except Exception:
            continue
        if df is None or df.empty or "date" not in df.columns:
            continue

        work = pd.DataFrame(
            {
                "date": pd.to_datetime(df["date"], errors="coerce"),
                "value": pd.to_numeric(df["value"], errors="coerce"),
            }
        ).dropna()
        if cutoff is not None and pd.notna(cutoff):
            work = work[work["date"] <= cutoff]
        if work.empty:
            continue

        row = work.sort_values("date").iloc[-1]
        result[label] = round(float(row["value"]), 4)
        result.setdefault(
            "_valuation_date",
            pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        )

    _VALUATION_CACHE[cache_key] = (now, dict(result))
    return result


def get_akshare_fundamentals(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股公司基本面数据概览。

    数据来源（多接口合并，任一失败不影响其他部分）：
      1. stock_individual_info_em：股票简称/行业/上市时间/股本
      2. stock_zh_a_spot_em：市盈率/市净率/总市值/流通市值/换手率/量比
      3. stock_financial_abstract：ROE/毛利率/净利率/资产负债率/EPS 等

    注意：旧实现只调用 stock_individual_info_em，但该接口并不返回
    市盈率、市净率、营收、净利润、ROE 等字段，导致基本面报告大面积缺失。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)

        # ---- 1. 公司概况 ----
        info_dict = {}
        try:
            df = retry_with_backoff(
                lambda: ak.stock_individual_info_em(symbol=code),
                max_retries=AKSHARE_MAX_RETRIES,
                base_delay=AKSHARE_BASE_DELAY,
            )
            if df is not None and not df.empty and {"item", "value"}.issubset(df.columns):
                for _, row in df.iterrows():
                    info_dict[str(row["item"])] = row["value"]
        except Exception:
            info_dict = {}

        # ---- 2. 实时估值快照 ----
        spot = {}
        try:
            spot_df = _get_spot_snapshot()
            code_col = _find_code_column(spot_df)
            if spot_df is not None and not spot_df.empty and code_col:
                matched = spot_df[
                    spot_df[code_col].astype(str).str.zfill(6) == code
                ]
                if not matched.empty:
                    spot = matched.iloc[0].to_dict()
        except Exception:
            spot = {}

        # ---- 3. 最近一期财务关键指标 ----
        abstract = {}
        try:
            abstract = _fetch_abstract_metrics(code, curr_date)
        except Exception:
            abstract = {}

        # ---- 4. 分析日历史估值（避免把实时 PE/PB 用到历史时点）----
        # 仅当分析日早于今天时才请求历史估值：当天/无日期时实时快照即可，
        # 从而避免每次分析都多打一次重量级接口。
        valuation = {}
        _today = datetime.now().strftime("%Y-%m-%d")
        if curr_date and str(curr_date)[:10] < _today:
            try:
                valuation = _fetch_historical_valuation(code, curr_date)
            except Exception:
                valuation = {}

        if not info_dict and not spot and not abstract and not valuation:
            return f"No fundamentals data found for '{ticker}'"

        def pick(*names):
            """按 valuation -> spot -> info -> abstract 的优先级取第一个有效值。"""
            for name in names:
                for source in (valuation, spot, info_dict, abstract):
                    if name in source:
                        value = source[name]
                        if value is not None and value != "" and value != "-":
                            return value
            return None

        field_map = [
            ("股票名称", pick("股票简称", "名称")),
            ("行业", pick("行业")),
            ("报告期", abstract.get("_report_date")),
            ("估值日期", valuation.get("_valuation_date")),
            ("总市值", _format_amount(pick("总市值"))),
            ("流通市值", _format_amount(pick("流通市值"))),
            ("市盈率(TTM)", pick("市盈率-TTM", "市盈率-动态", "市盈率")),
            ("市净率", pick("市净率")),
            ("市销率(TTM)", pick("市销率-TTM")),
            ("股息率(TTM)", pick("股息率-TTM")),
            ("换手率", pick("换手率")),
            ("量比", pick("量比")),
            ("60日涨跌幅", pick("60日涨跌幅")),
            ("基本每股收益", pick("基本每股收益", "摊薄每股收益_最新股数")),
            ("每股净资产", pick("每股净资产", "每股净资产_最新股数")),
            ("营业总收入", _format_amount(pick("营业总收入"))),
            ("归母净利润", _format_amount(pick("归母净利润", "净利润"))),
            ("营业总收入增长率", pick("营业总收入增长率")),
            ("归母净利润增长率", pick("归属母公司净利润增长率")),
            ("毛利率", pick("毛利率")),
            ("销售净利率", pick("销售净利率", "净利率")),
            ("净资产收益率(ROE)", pick("净资产收益率(ROE)", "净资产收益率")),
            ("资产负债率", pick("资产负债率")),
            ("每股经营现金流", pick("每股经营现金流", "每股现金流")),
            ("上市时间", pick("上市时间")),
            ("总股本", pick("总股本")),
            ("流通股", pick("流通股")),
        ]

        lines = []
        for label, value in field_map:
            if value is not None:
                lines.append(f"{label}: {value}")

        # A 股特有风险提示（ST/退市/板块涨跌幅限制）
        stock_name = pick("股票简称", "名称")
        for warning in risk_warnings(code, str(stock_name) if stock_name else ""):
            lines.append(f"风险提示: {warning}")

        # 历史分析时明确提示哪些字段是实时快照，避免被误当作历史值
        today = datetime.now().strftime("%Y-%m-%d")
        if curr_date and str(curr_date)[:10] < today:
            if valuation:
                lines.append(
                    "数据说明: PE/PB/市销率/股息率已使用分析日及之前的历史估值；"
                    "市值/换手率/量比等仍为当前实时快照。"
                )
            else:
                lines.append(
                    "数据说明: 市值/PE/PB/换手率等来自当前实时快照，"
                    "并非分析日历史值，历史时点判断请谨慎使用。"
                )

        header = f"# Company Fundamentals for {code}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        header += (
            "# Sources: AkShare (stock_individual_info_em / stock_zh_a_spot_em / "
            "stock_financial_abstract / stock_zh_valuation_baidu)\n\n"
        )

        return header + "\n".join(lines)

    except Exception:
        raise


# ============================================================================
# 4. 资产负债表
# ============================================================================

def _fetch_financial_statement_ths(
    code: str,
    ths_func,
    statement_name: str,
    freq: str,
    curr_date: str,
    max_periods: int = 8,
) -> str:
    """
    通用财务报表获取函数：从同花顺数据源（通过 akshare 的 stock_financial_*_ths
    系列接口）拉取三大报表数据。

    背景：
      akshare 原先的 stock_balance_sheet_by_report_em / stock_cash_flow_sheet_by_report_em
      / stock_profit_sheet_by_report_em 三个接口依赖东方财富网页爬虫，
      东方财富页面结构变更后全部失效（TypeError: 'NoneType' object is not subscriptable）。
      改用同花顺数据源接口，数据更稳定，覆盖范围与字段完整度与原接口相当。

    参数：
      code: 已标准化的 6 位股票代码（如 "000878"）
      ths_func: akshare 的财务报表函数（如 ak.stock_financial_debt_ths）
      statement_name: 报表名称（用于返回头信息，如 "Balance Sheet"）
      freq: "annual"（年度）或 "quarterly"（季度，按报告期累计）
      curr_date: 当前日期字符串，格式 "YYYY-MM-DD"；仅返回此日期之前已发布的报告
      max_periods: 最多返回的报告期数（从最新开始倒序）

    返回：
      CSV 格式字符串，带元信息头。报告期按从新到旧排列。
    """
    _require_akshare()

    # 根据频率选择同花顺的 indicator 参数
    # - "按年度"：只返回年报，报告期列为整数年份（如 2024）
    # - "按报告期"：返回所有季报，报告期列为日期字符串（如 "2025-12-31"）
    indicator = "按年度" if freq.lower() == "annual" else "按报告期"

    df = retry_with_backoff(
        lambda: ths_func(symbol=code, indicator=indicator),
        max_retries=AKSHARE_MAX_RETRIES,
        base_delay=AKSHARE_BASE_DELAY,
    )

    if df is None or df.empty:
        return f"No {statement_name.lower()} data found for '{code}'"

    # 按“报告期 + 法定披露截止日”过滤，避免使用分析日尚未披露的报告。
    # 同花顺数据中"报告期"列在"按年度"模式下是 int，在"按报告期"模式下是 str。
    if curr_date:
        cutoff_iso = str(curr_date)[:10]
        try:
            mask = []
            for value in df["报告期"].tolist():
                available = report_available_date(value)
                mask.append(bool(available) and available <= cutoff_iso)
            df = df[mask]
        except Exception:
            # 过滤失败时不影响主流程，返回全部数据
            pass

    # 报告期按从新到旧排序，再限制数量，避免输出过大且保证顺序稳定
    try:
        df = df.sort_values("报告期", ascending=False)
    except Exception:
        pass
    if len(df) > max_periods:
        df = df.head(max_periods)

    if df.empty:
        return f"No {statement_name.lower()} data found for '{code}' before {curr_date}"

    csv_string = df.to_csv(index=False)

    header = f"# {statement_name} data for {code} ({freq})\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# Source: AkShare (同花顺数据源)\n"
    header += f"# Reports: {len(df)} periods\n\n"

    return header + csv_string


def get_akshare_balance_sheet(
    ticker: Annotated[str, "股票代码"],
    freq: Annotated[str, "数据频率：'annual' 或 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "当前日期"] = None,
) -> str:
    """
    获取 A 股资产负债表数据。

    数据源：同花顺（通过 AkShare 的 stock_financial_debt_ths 接口）。
    返回 CSV 格式字符串，每行一个报告期，每列一个会计科目。

    注意：原先使用的 stock_balance_sheet_by_report_em 接口因东方财富页面
    结构变更而失效，已切换到同花顺数据源。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)
        return _fetch_financial_statement_ths(
            code=code,
            ths_func=ak.stock_financial_debt_ths,
            statement_name="Balance Sheet",
            freq=freq,
            curr_date=curr_date,
        )
    except Exception:
        raise


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

    数据源：同花顺（通过 AkShare 的 stock_financial_cash_ths 接口）。
    返回 CSV 格式字符串，包含经营活动、投资活动、筹资活动三大现金流明细。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)
        return _fetch_financial_statement_ths(
            code=code,
            ths_func=ak.stock_financial_cash_ths,
            statement_name="Cash Flow",
            freq=freq,
            curr_date=curr_date,
        )
    except Exception:
        raise


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

    数据源：同花顺（通过 AkShare 的 stock_financial_benefit_ths 接口）。
    返回 CSV 格式字符串，包含营业收入、营业成本、净利润、每股收益等明细。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)
        return _fetch_financial_statement_ths(
            code=code,
            ths_func=ak.stock_financial_benefit_ths,
            statement_name="Income Statement",
            freq=freq,
            curr_date=curr_date,
        )
    except Exception:
        raise


# ============================================================================
# 7. 股东持股变动（内部交易替代）
# ============================================================================

def get_akshare_insider_transactions(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"] = None,
) -> str:
    """
    获取 A 股重要股东持股变动数据。

    中国 A 股没有"内部交易"概念，此函数提供大股东持股变动作为替代。
    调用 AkShare 的 stock_gdfx_holding_change_em() 接口。

    返回 CSV 格式，包含股东名称、变动方向、变动数量等信息。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)

        # 确定查询日期：使用传入日期的季度末，或当前日期的上一个季度末
        if curr_date:
            curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        else:
            curr_dt = datetime.now()

        # 股东持股变动按季度披露，取最近一个已披露的季度末日期
        # 季度末：3-31, 6-30, 9-30, 12-31
        year = curr_dt.year
        month = curr_dt.month
        if month >= 11:
            # 11-12月：三季报(9-30)已披露
            query_date = f"{year}0930"
        elif month >= 8:
            # 8月：中报(6-30)已披露
            query_date = f"{year}0630"
        elif month >= 5:
            # 5-7月：一季报(3-31)已披露
            query_date = f"{year}0331"
        else:
            # 1-4月：上一年年报(12-31)已披露
            query_date = f"{year-1}1231"

        # 使用重试机制获取股东持股变动数据
        df = retry_with_backoff(
            lambda: ak.stock_gdfx_holding_change_em(date=query_date),
            max_retries=AKSHARE_MAX_RETRIES,
            base_delay=AKSHARE_BASE_DELAY,
        )

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

    except Exception:
        raise


# ============================================================================
# 7.5 资金流向（主力资金）
# ============================================================================

def get_akshare_capital_flow(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"] = None,
    look_back_days: Annotated[int, "回溯交易日数量"] = 20,
) -> str:
    """获取 A 股个股资金流向（主力 / 超大单 / 大单等）。

    数据源：东方财富（ak.stock_individual_fund_flow）。
    当个股接口不可用时，回退到全市场资金流排行（stock_individual_fund_flow_rank）
    并按代码过滤，保证北交所等特殊标的也能尽量返回数据。
    """
    _require_akshare()

    code = normalize_symbol(ticker)
    if not is_a_share_code(code):
        return f"'{ticker}' 不是 A 股标的，无资金流向数据"

    exchange = exchange_of(code)
    df = None

    # 主数据源：个股资金流（沪深）
    if exchange in ("sh", "sz"):
        try:
            df = retry_with_backoff(
                lambda: ak.stock_individual_fund_flow(stock=code, market=exchange),
                max_retries=2,
                base_delay=AKSHARE_BASE_DELAY,
            )
        except Exception:
            df = None

    # 兜底：全市场资金流排行（覆盖北交所 / 个股接口异常）
    if df is None or df.empty:
        try:
            rank = retry_with_backoff(
                lambda: ak.stock_individual_fund_flow_rank(indicator="今日"),
                max_retries=1,
                base_delay=AKSHARE_BASE_DELAY,
            )
            if rank is not None and not rank.empty:
                code_col = _find_code_column(rank)
                if code_col:
                    matched = rank[
                        rank[code_col].astype(str).str.zfill(6) == code
                    ]
                    if not matched.empty:
                        df = matched
        except Exception:
            df = None

    if df is None or df.empty:
        return f"No capital flow data found for '{ticker}'"

    df = df.copy()

    # 按日期过滤并按时间倒序（最新的在前）
    date_col = next((c for c in ("日期", "date") if c in df.columns), None)
    if not date_col and curr_date:
        # 兜底的资金流排行没有日期列，只能代表“当前快照”。
        # 历史分析时禁止使用，避免未来数据泄漏。
        try:
            cutoff = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            cutoff = None
        if cutoff is not None and cutoff < datetime.now().date():
            return (
                f"No capital flow data found for '{ticker}' "
                f"before {curr_date} (source only provides current snapshot)"
            )
    if date_col:
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=["_date"]).sort_values("_date", ascending=False)
        if curr_date:
            cutoff = pd.to_datetime(curr_date, errors="coerce")
            if pd.notna(cutoff):
                # 严格只保留分析日及之前的数据：
                # 资金流接口通常只返回最近约 100 个交易日，
                # 若请求的是更早的历史日期，必须返回“无数据”，
                # 绝不能把当前/未来数据交给 LLM（会造成未来函数与误导）。
                filtered = df[df["_date"] <= cutoff]
                if filtered.empty:
                    return (
                        f"No capital flow data found for '{ticker}' "
                        f"before {curr_date} (source only keeps recent data)"
                    )
                df = filtered

    df = df.head(max(1, int(look_back_days)))
    if df.empty:
        return f"No capital flow data found for '{ticker}' before {curr_date}"

    # 汇总主力资金净流入
    summary_lines = []
    main_col = next(
        (
            c
            for c in df.columns
            if "主力净流入" in str(c) and "净额" in str(c)
        ),
        None,
    )
    if main_col is None:
        main_col = next((c for c in df.columns if "主力净流入" in str(c)), None)
    if main_col:
        values = pd.to_numeric(df[main_col], errors="coerce").dropna()
        if not values.empty:
            summary_lines.append(
                f"最近{len(values)}个交易日主力净流入合计: {_format_amount(values.sum())}"
            )
            summary_lines.append(
                f"最新交易日主力净流入: {_format_amount(values.iloc[0])}"
            )

    # 控制输出列数量，优先保留资金流关键字段
    preferred = [
        "日期",
        "收盘价",
        "涨跌幅",
        "主力净流入-净额",
        "主力净流入-净占比",
        "超大单净流入-净额",
        "大单净流入-净额",
        "中单净流入-净额",
        "小单净流入-净额",
    ]
    available = [c for c in df.columns if c != "_date"]
    ordered = [c for c in preferred if c in available]
    ordered += [c for c in available if c not in ordered]
    df_out = df[ordered]

    header = f"# Capital Flow data for {code} (A-share main capital flow)\n"
    header += (
        f"# Data retrieved on: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    if summary_lines:
        header += "# " + "\n# ".join(summary_lines) + "\n"
    header += "# Unit: CNY. 正值=净流入，负值=净流出。\n\n"

    return header + df_out.to_csv(index=False)


# ============================================================================
# 7.6 北向资金
# ============================================================================

# 北向资金历史接口一次返回全部历史，进程内缓存 1 小时
_NORTHBOUND_CACHE = {"ts": 0.0, "df": None}
_NORTHBOUND_TTL = 3600.0


def _load_northbound_history() -> "pd.DataFrame | None":
    """加载北向资金历史数据（带缓存与日期归一化）。"""
    fetch = getattr(ak, "stock_hsgt_hist_em", None)
    if fetch is None:
        return None

    now = time.time()
    cached = _NORTHBOUND_CACHE.get("df")
    if (
        cached is not None
        and (now - _NORTHBOUND_CACHE.get("ts", 0)) < _NORTHBOUND_TTL
    ):
        return cached

    df = retry_with_backoff(
        lambda: fetch(symbol="北向资金"),
        max_retries=2,
        base_delay=AKSHARE_BASE_DELAY,
    )
    if df is None or df.empty:
        return None

    df = df.copy()
    date_col = next((c for c in ("日期", "date") if c in df.columns), None)
    if date_col:
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=["_date"]).sort_values("_date", ascending=False)
    _NORTHBOUND_CACHE["df"] = df
    _NORTHBOUND_CACHE["ts"] = now
    return df


def get_akshare_northbound_flow(
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"] = None,
    look_back_days: Annotated[int, "回溯交易日数量"] = 10,
) -> str:
    """获取北向资金（沪深股通）历史净流入数据。

    注意：自 2024-08-19 起交易所不再每日披露北向资金净买入额，
    接口数据会停留在该日期附近。若分析日距今较远或数据明显滞后，
    输出中会给出明确提示，避免把旧数据当作当前资金动向。
    """
    _require_akshare()

    df = _load_northbound_history()
    if df is None or df.empty or "_date" not in df.columns:
        return "No northbound capital flow data available"

    reference = pd.Timestamp.now().normalize()
    if curr_date:
        cutoff = pd.to_datetime(curr_date, errors="coerce")
        if pd.notna(cutoff):
            reference = cutoff.normalize()
            filtered = df[df["_date"] <= cutoff]
            if filtered.empty:
                return (
                    f"No northbound capital flow data found before {curr_date} "
                    "(source data starts later or is unavailable)"
                )
            df = filtered

    df = df.head(max(1, int(look_back_days)))
    if df.empty:
        return f"No northbound capital flow data found before {curr_date}"

    latest_date = pd.Timestamp(df["_date"].iloc[0])
    stale_days = (reference - latest_date.normalize()).days

    summary_lines = []
    net_col = next(
        (c for c in ("当日成交净买额", "当日资金流入") if c in df.columns),
        None,
    )
    if net_col:
        values = pd.to_numeric(df[net_col], errors="coerce").dropna()
        if not values.empty:
            summary_lines.append(
                f"最近{len(values)}个交易日北向净流入合计: {values.sum():.2f} 亿元"
            )
            summary_lines.append(f"最新交易日北向净流入: {values.iloc[0]:.2f} 亿元")

    preferred = [
        "日期",
        "当日成交净买额",
        "当日资金流入",
        "历史累计净买额",
        "沪深300",
        "沪深300-涨跌幅",
    ]
    available = [c for c in df.columns if c != "_date"]
    ordered = [c for c in preferred if c in available]
    ordered += [c for c in available if c not in ordered]
    df_out = df[ordered]

    header = "# Northbound Capital Flow (沪深股通)\n"
    header += (
        f"# Data retrieved on: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    if summary_lines:
        header += "# " + "\n# ".join(summary_lines) + "\n"
    if not summary_lines:
        header += (
            "# 注意: 所选区间北向资金净买入额无有效数据"
            "（交易所已停止每日披露或数据缺失），请勿据此判断资金动向。\n"
        )
    elif stale_days > 30:
        header += (
            f"# 注意: 北向资金每日净买入数据最新仅到 "
            f"{latest_date.strftime('%Y-%m-%d')}（交易所已停止每日披露），"
            "请勿据此判断当前资金动向，仅可作为历史参考。\n"
        )
    header += "# 金额单位: 亿元。正值=净流入，负值=净流出。\n\n"

    return header + df_out.to_csv(index=False)


# ============================================================================
# 7.7 龙虎榜
# ============================================================================

_LHB_CACHE: dict = {}
_LHB_TTL = 600.0


def get_akshare_dragon_tiger(
    ticker: Annotated[str, "股票代码"],
    curr_date: Annotated[str, "当前日期，格式 YYYY-MM-DD"] = None,
    look_back_days: Annotated[int, "回溯自然日数量"] = 60,
) -> str:
    """获取 A 股个股龙虎榜数据（上榜原因、买卖金额、席位解读等）。"""
    _require_akshare()

    code = normalize_symbol(ticker)
    if not is_a_share_code(code):
        return f"'{ticker}' 不是 A 股标的，无龙虎榜数据"

    fetch = getattr(ak, "stock_lhb_detail_em", None)
    if fetch is None:
        return "龙虎榜接口不可用（当前 AkShare 版本缺少 stock_lhb_detail_em）"

    if curr_date:
        try:
            end_dt = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d")
        except ValueError:
            end_dt = datetime.now()
    else:
        end_dt = datetime.now()
    start_dt = end_dt - relativedelta(days=max(1, int(look_back_days)))
    start = start_dt.strftime("%Y%m%d")
    end = end_dt.strftime("%Y%m%d")

    cache_key = (start, end)
    now = time.time()
    cached = _LHB_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _LHB_TTL:
        raw = cached[1]
    else:
        raw = retry_with_backoff(
            lambda: fetch(start_date=start, end_date=end),
            max_retries=2,
            base_delay=AKSHARE_BASE_DELAY,
        )
        if raw is not None and not raw.empty:
            if len(_LHB_CACHE) > 8:
                _LHB_CACHE.clear()
            _LHB_CACHE[cache_key] = (now, raw)

    if raw is None or raw.empty:
        return f"No dragon-tiger (LHB) data found for '{ticker}' in {start}~{end}"

    df = raw.copy()
    code_col = _find_code_column(df)
    if not code_col:
        return "龙虎榜数据格式不符合预期（缺少代码列）"

    matched = df[df[code_col].astype(str).str.zfill(6) == code]
    if matched.empty:
        return (
            f"No dragon-tiger (LHB) data found for '{code}' in "
            f"{start_dt.strftime('%Y-%m-%d')}~{end_dt.strftime('%Y-%m-%d')}"
        )

    matched = matched.copy()
    date_col = next(
        (c for c in ("上榜日", "日期", "date") if c in matched.columns),
        None,
    )
    if date_col:
        matched["_date"] = pd.to_datetime(matched[date_col], errors="coerce")
        matched = matched.dropna(subset=["_date"])
        # 防御式过滤：即使上游返回了区间外/未来数据，也绝不能交给 LLM
        matched = matched[matched["_date"] <= pd.Timestamp(end_dt)]
        matched = matched.sort_values("_date", ascending=False)

    matched = matched.head(20)

    summary_lines = []
    net_col = next(
        (c for c in matched.columns if "龙虎榜净买额" in str(c)), None
    )
    if net_col:
        values = pd.to_numeric(matched[net_col], errors="coerce").dropna()
        if not values.empty:
            summary_lines.append(f"最近上榜次数: {len(matched)} 次")
            summary_lines.append(
                f"区间龙虎榜净买额合计: {_format_amount(values.sum())}"
            )

    preferred = [
        "上榜日",
        "代码",
        "名称",
        "收盘价",
        "涨跌幅",
        "龙虎榜净买额",
        "龙虎榜买入额",
        "龙虎榜卖出额",
        "换手率",
        "上榜原因",
        "上榜后1日",
        "上榜后5日",
        "上榜后10日",
    ]
    available = [c for c in matched.columns if c != "_date"]
    ordered = [c for c in preferred if c in available]
    ordered += [c for c in available if c not in ordered]
    df_out = matched[ordered]

    header = f"# Dragon-Tiger (LHB) data for {code}\n"
    header += (
        f"# Data retrieved on: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    if summary_lines:
        header += "# " + "\n# ".join(summary_lines) + "\n"
    header += "# 金额单位: 元。正值=净买入，负值=净卖出。\n\n"

    return header + df_out.to_csv(index=False)


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
    内置指数退避重试机制，应对东方财富 API 的限流问题。
    """
    _require_akshare()

    try:
        code = normalize_symbol(ticker)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # 使用重试机制获取新闻（东方财富 API 可能限流）
        df = retry_with_backoff(
            lambda: ak.stock_news_em(symbol=code),
            max_retries=2,
            base_delay=3.0,
        )

        if df.empty:
            return (
                f"No recent news found for {ticker} ({code}) in the specified period. "
                f"This may be due to limited news coverage or API restrictions."
            )

        # 按日期范围过滤
        if "发布时间" in df.columns:
            df["发布时间"] = pd.to_datetime(df["发布时间"])
            df = df[(df["发布时间"] >= start_dt) & (df["发布时间"] <= end_dt + relativedelta(days=1))]
        elif "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])
            df = df[(df["日期"] >= start_dt) & (df["日期"] <= end_dt + relativedelta(days=1))]

        if df.empty:
            return (
                f"No news found for {ticker} ({code}) between {start_date} and {end_date}. "
                f"The stock may have no significant news in this period."
            )

        # 构建 Markdown 格式新闻
        # 注意：AkShare stock_news_em 实际返回列名为
        #   关键词 / 新闻标题 / 新闻内容 / 发布时间 / 文章来源 / 新闻链接
        # 旧代码读取的是 "标题" / "内容"，导致所有新闻标题都变成 No title、正文为空。
        news_str = ""
        count = 0
        for _, row in df.iterrows():
            title = row.get("新闻标题", row.get("标题", row.get("title", "No title")))
            content = row.get("新闻内容", row.get("内容", row.get("content", "")))
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

        return f"## {code} ({ticker}) News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception:
        raise


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
    _require_akshare()

    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - relativedelta(days=look_back_days)
        start_date = start_dt.strftime("%Y-%m-%d")

        news_str = ""

        try:
            # 使用重试机制获取央视新闻联播（宏观政策新闻的重要来源）
            # 启用超时机制，因为news_cctv可能需要较长时间下载数据
            cctv_news = retry_with_backoff(
                lambda: ak.news_cctv(),
                max_retries=2,  # 减少重试次数，避免长时间等待
                base_delay=AKSHARE_BASE_DELAY,
                timeout=60,  # 设置较长的超时时间（60秒）
                use_timeout=True,
            )
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
        except (AkShareTimeoutError, Exception):
            # 超时或其他异常，继续尝试下一个数据源
            pass

        if not news_str:
            # 如果央视新闻获取失败，尝试获取财联社电报
            try:
                # 使用重试机制获取财联社电报
                # 启用超时机制
                cls_telegraph = retry_with_backoff(
                    lambda: ak.stock_info_global_cls(),
                    max_retries=2,  # 减少重试次数
                    base_delay=AKSHARE_BASE_DELAY,
                    timeout=30,  # 设置超时时间（30秒）
                    use_timeout=True,
                )
                if cls_telegraph is not None and not cls_telegraph.empty:
                    cls_df = cls_telegraph.copy()
                    # 财联社电报只返回最近数据。历史分析时必须按日期过滤，
                    # 否则会把当前/未来新闻注入历史时点分析（未来函数）。
                    date_col = next(
                        (
                            c
                            for c in ("发布日期", "日期", "date")
                            if c in cls_df.columns
                        ),
                        None,
                    )
                    time_col = next(
                        (
                            c
                            for c in ("发布时间", "时间", "time")
                            if c in cls_df.columns
                        ),
                        None,
                    )
                    if date_col:
                        raw = cls_df[date_col].astype(str)
                        if time_col:
                            raw = raw + " " + cls_df[time_col].astype(str)
                        cls_df["_dt"] = pd.to_datetime(raw, errors="coerce")
                        cls_df = cls_df.dropna(subset=["_dt"])
                        cls_df = cls_df[
                            (cls_df["_dt"] >= start_dt)
                            & (cls_df["_dt"] <= curr_dt + relativedelta(days=1))
                        ]
                    else:
                        # 无法判断时间时不返回，宁可缺失也不泄漏未来数据
                        cls_df = cls_df.iloc[0:0]

                    title_col = next(
                        (c for c in ("标题", "title") if c in cls_df.columns),
                        None,
                    )
                    for _, row in cls_df.head(limit).iterrows():
                        if title_col:
                            title = str(row.get(title_col, "No title"))
                        else:
                            title = str(row.iloc[0]) if len(row) > 0 else "No title"
                        news_str += f"### {title} (source: CLS Telegraph)\n\n"
            except (AkShareTimeoutError, Exception):
                # 超时或其他异常，继续
                pass

        if not news_str:
            return (
                f"## Global Market News (AkShare), from {start_date} to {curr_date}:\n\n"
                f"No macro news available for this period. "
                f"Note: AkShare provides China-focused data. "
                f"For global market news, consider using yfinance as a fallback."
            )

        return f"## Global Market News (AkShare), from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception:
        raise
