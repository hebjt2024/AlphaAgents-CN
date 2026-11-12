"""A 股交易规则与风险提示工具。

本模块只包含纯函数，不发起任何网络请求，便于被数据层、Agent 层与测试复用。

提供能力：
  - normalize_code(symbol)：把各种写法归一化为 6 位纯数字代码
  - exchange_of(code)：返回 sh / sz / bj / ""
  - is_a_share_code(code)：判断是否为合法的 A 股代码（排除 B 股）
  - is_beijing(code)：判断是否为北交所标的
  - price_limit_pct(code, name)：按板块 / ST 状态返回涨跌幅限制
  - is_st(name)：判断是否为 ST / *ST 标的
  - risk_warnings(code, name)：返回该标的的风险提示列表
  - build_ashare_context(ticker)：生成注入给 LLM 的 A 股交易规则说明
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# 各交易所代码前缀（A 股）
_SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SHENZHEN_PREFIXES = ("000", "001", "002", "003", "300", "301")
# 北交所代码段：43xxxx / 83xxxx / 87xxxx / 88xxxx，以及 2024 年新代码段 92xxxx
_BEIJING_PREFIXES = ("43", "83", "87", "88", "92")
# 沪市 B 股（900xxx）与深市 B 股（200xxx）不属于 A 股
_B_SHARE_PREFIXES = ("200", "900")
# A 股风险警示股名称前缀：ST / *ST / SST / S*ST
_ST_PREFIXES = ("*ST", "SST", "S*ST", "ST")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def contains_cjk(text: str) -> bool:
    """判断字符串是否包含中文字符。"""
    return bool(_CJK_RE.search(text or ""))


def normalize_code(symbol: str) -> str:
    """把常见 A 股代码写法归一化为 6 位纯数字。

    支持：
      - "600519" / "  600519  "
      - "sh600519" / "SZ000001" / "bj830799"
      - "sh.600519" / "sz.000001" / "bj.830799"
      - "600519.SH" / "000001.SZ" / "600519.SS" / "830799.BJ"
      - "600519.XSHG" / "000001.XSHE"（聚宽格式）

    非 A 股代码（如 NVDA、0700.HK）返回空字符串。
    """
    s = (symbol or "").strip()
    if not s:
        return ""

    # 带交易所前缀：sh.600519 / sz.000001 / bj.830799
    if "." in s:
        head, _, tail = s.partition(".")
        if head.lower() in ("sh", "sz", "bj") and tail.isdigit() and len(tail) == 6:
            return tail
        # 后缀格式：600519.SH / 000001.SZ / 600519.SS / 830799.BJ
        # 聚宽格式：600519.XSHG / 000001.XSHE
        if head.isdigit() and len(head) == 6:
            if tail.upper() in ("SH", "SZ", "SS", "BJ", "XSHG", "XSHE"):
                return head
        return ""

    # 前缀紧贴代码：sh600519 / bj830799
    low = s.lower()
    if low.startswith(("sh", "sz", "bj")) and len(s) == 8 and s[2:].isdigit():
        return s[2:]

    # 纯 6 位数字
    if s.isdigit() and len(s) == 6:
        return s

    return ""


def exchange_of(code: str) -> str:
    """返回交易所前缀：sh / sz / bj；非 A 股返回空字符串。"""
    c = normalize_code(code)
    if not c:
        return ""
    if c.startswith(_SHANGHAI_PREFIXES):
        return "sh"
    if c.startswith(_SHENZHEN_PREFIXES):
        return "sz"
    if c.startswith(_BEIJING_PREFIXES):
        return "bj"
    return ""


def is_a_share_code(code: str) -> bool:
    """判断是否为合法 A 股代码。

    仅接受沪市主板/科创板、深市主板/创业板、北交所代码。
    沪深 B 股（200xxx / 900xxx）以及港股、美股均返回 False。
    """
    c = normalize_code(code)
    if not c:
        return False
    if c.startswith(_B_SHARE_PREFIXES):
        return False
    return exchange_of(c) != ""


def is_beijing(code: str) -> bool:
    """判断是否为北交所标的。"""
    return exchange_of(code) == "bj"


def is_st(name: str) -> bool:
    """判断股票名称是否为 ST / *ST 标的。

    A 股 ST 标记固定出现在名称**前缀**（``ST`` / ``*ST`` / ``SST`` /
    ``S*ST``），因此按前缀匹配，避免把名称中间的偶发 "ST" 误判为风险警示股。
    """
    if not name:
        return False
    text = str(name).strip().upper().replace(" ", "")
    return text.startswith(_ST_PREFIXES)


def price_limit_pct(code: str, name: str = "") -> float:
    """返回该股票的涨跌幅限制（小数，如 0.10 表示 10%）。

    规则：
      - 科创板（688/689）、创业板（300/301）：20%（**含 ST 标的**）
      - 北交所（4/8 开头）：30%（含 ST 标的）
      - 主板 ST / *ST：5%
      - 主板：10%

    注意：2020 年注册制改革后，创业板/科创板的 ST 标的涨跌幅限制仍为
    20%，不再跟随主板降为 5%。此前实现对所有 ST 标的统一返回 5%，
    会让 LLM 对创业板 ST 股给出错误的涨跌停预期。
    """
    c = normalize_code(code)
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if c and c.startswith(_BEIJING_PREFIXES):
        return 0.30
    if is_st(name):
        return 0.05
    return 0.10


def risk_warnings(code: str, name: str = "") -> list[str]:
    """返回该标的的 A 股风险提示列表（可能为空）。"""
    warnings: list[str] = []
    c = normalize_code(code)
    label = str(name or "")

    if is_st(label):
        limit = int(round(price_limit_pct(c, label) * 100))
        warnings.append(
            f"ST/*ST 标的：涨跌幅限制为 {limit}%，存在退市风险，仓位与止损需更保守。"
        )
    if "退" in label:
        warnings.append("该标的可能处于退市整理期，流动性风险极高。")
    if c.startswith(("688", "689")):
        warnings.append("科创板标的：涨跌幅 20%，需开通科创板交易权限。")
    elif c.startswith(("300", "301")):
        warnings.append("创业板标的：涨跌幅 20%，需开通创业板交易权限。")
    elif c and c.startswith(_BEIJING_PREFIXES):
        warnings.append("北交所标的：涨跌幅 30%，流动性通常弱于沪深主板。")
    return warnings


def report_available_date(period) -> str:
    """根据报告期推断该报告的保守可用日期（法定披露截止日）。

    A 股定期报告披露截止日：
      - 年报（12-31）：次年 4 月 30 日
      - 一季报（03-31）：当年 4 月 30 日
      - 中报（06-30）：当年 8 月 31 日
      - 三季报（09-30）：当年 10 月 31 日

    历史分析时用该日期过滤，避免使用尚未披露的“未来财报”。
    支持 "20231231" / "2023-12-31" / 2023（视为年报）等格式。
    无法解析时返回空字符串。
    """
    if period is None or period == "":
        return ""
    text = str(period).strip()

    parsed: date | None = None
    if text.isdigit() and len(text) == 4:
        parsed = date(int(text), 12, 31)
    elif text.isdigit() and len(text) == 8:
        try:
            parsed = datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            parsed = None
    else:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            parsed = None

    if parsed is None:
        return ""

    month = parsed.month
    if month == 12:
        available = date(parsed.year + 1, 4, 30)
    elif month == 3:
        available = date(parsed.year, 4, 30)
    elif month == 6:
        available = date(parsed.year, 8, 31)
    elif month == 9:
        available = date(parsed.year, 10, 31)
    else:
        available = parsed + timedelta(days=120)
    return available.isoformat()


def build_ashare_context(ticker: str, name: str = "") -> str:
    """生成注入给 LLM 的 A 股交易规则说明；非 A 股返回空字符串。

    ``name`` 为标的真实名称（如 ``*ST某某``）。此前该参数缺失，导致
    ``price_limit_pct`` / ``risk_warnings`` 永远拿不到名称 —— ST 标的
    会被当成普通主板股告知 10% 涨跌幅，且不会收到任何风险提示。
    """
    if not is_a_share_code(ticker):
        return ""

    code = normalize_code(ticker)
    display = str(name or "").strip()
    limit = int(round(price_limit_pct(code, display) * 100))
    lines = [
        "A股交易规则（务必在给出买卖方案时遵守）：",
        "- T+1 交易：当日买入的股票需到下一交易日才能卖出，不要假设当日可平仓。",
        f"- 涨跌幅限制：{limit}%（主板 ST/*ST 为 5%，科创板/创业板为 20%，北交所为 30%）。",
        "- 交易单位：买入数量须为 100 股（1 手）的整数倍。",
        "- 若标的停牌或触及涨跌停，相关价位可能无法成交，方案需给出替代处理。",
    ]
    warnings = risk_warnings(code, display)
    if warnings:
        lines.append("风险提示：" + "；".join(warnings))
    return "\n".join(lines)
