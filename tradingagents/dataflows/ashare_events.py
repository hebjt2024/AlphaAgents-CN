"""A 股基本面事件数据（估值分位、业绩预告、解禁、分红、财务排雷）。

WHY 独立成模块：
  - A 股的“事件驱动”信息密度远高于海外成熟市场：业绩预告/快报是强制
    披露制度，限售解禁是特有的股本供给冲击，分红送转与预约披露日会
    直接影响短期博弈。只看财报和行情会漏掉这些最强的短期催化。
  - 这些 AkShare 接口大多按“报告期 / 交易日”返回**全市场**数据，
    必须缓存后按代码过滤，否则一次分析会重复拉取几十次全市场快照，
    既慢又容易被东方财富限流。
  - 所有公开函数统一返回 dict（success / ticker / summary / warnings），
    绝不向调用方抛异常。单个数据源失效只能降低结论置信度，不能让整个
    分析流程失败；缺失数据一律如实标注“数据不足”，绝不编造数值。

数据源：AkShare（东方财富 / 百度股市通 / 巨潮资讯 / 新浪财经）。
"""

import logging
import re
import time
from datetime import date, datetime, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta

from .akshare_adapter import (
    AKSHARE_BASE_DELAY,
    AKSHARE_MAX_RETRIES,
    normalize_symbol,
    retry_with_backoff,
)
from .ashare_rules import (
    contains_cjk,
    exchange_of,
    is_a_share_code,
    normalize_code,
    report_available_date,
)

try:
    import akshare as ak
except ImportError:  # pragma: no cover - 依赖缺失时的降级路径
    ak = None

logger = logging.getLogger(__name__)

# ============================================================================
# 缓存
# ============================================================================
# WHY 模块级 TTL 缓存：解禁/业绩预告/分红/质押接口都按报告期或交易日返回
# 全市场数据，一次“事件日历”构建可能同时触发多个接口。缓存后同一分析
# 会话内只拉一次，既降低延迟也避免触发东方财富限流。
_VALUATION_CACHE = {}
_VALUATION_TTL = 3600.0

_MARKET_CACHE = {}
_MARKET_TTL = 1800.0

_SNAPSHOT_CACHE = {}
_SNAPSHOT_TTL = 3600.0

_RESULT_CACHE = {}
_RESULT_TTL = 900.0

_MISS = object()


def clear_caches():
    """清空本模块全部 TTL 缓存（测试与手动刷新时使用）。"""
    for cache in (_VALUATION_CACHE, _MARKET_CACHE, _SNAPSHOT_CACHE, _RESULT_CACHE):
        cache.clear()


def _cache_get(cache, key, ttl):
    """读取 TTL 缓存；未命中或已过期返回 _MISS 哨兵。"""
    item = cache.get(key)
    if not item:
        return _MISS
    if time.time() - item[0] >= ttl:
        cache.pop(key, None)
        return _MISS
    return item[1]


def _cache_set(cache, key, payload):
    """写入 TTL 缓存，返回 payload 方便链式使用。"""
    cache[key] = (time.time(), payload)
    return payload


# ============================================================================
# 通用小工具
# ============================================================================

def _require_akshare():
    """确保 AkShare 可用，否则抛出明确错误（由调用方降级为 success=False）。"""
    if ak is None:
        raise ImportError(
            "未安装 akshare，无法使用 A 股事件数据。请执行: pip install akshare"
        )
    return ak


def _failure(ticker, message, warnings=None):
    """构造统一失败结果，保证调用方永远拿到 dict 而不是异常。"""
    return {
        "success": False,
        "ticker": ticker,
        "summary": message,
        "warnings": list(warnings or []),
    }


def _resolve_code(ticker):
    """把各种写法解析为 6 位 A 股代码；不会因为非中文输入而触网。"""
    text = str(ticker or "").strip()
    if not text:
        return ""
    code = normalize_code(text)
    if code:
        return code
    if contains_cjk(text):
        return normalize_symbol(text)
    return ""


def _invalid_ticker(raw):
    """非 A 股标的的统一失败结果（中文提示，便于直接展示给用户）。"""
    return _failure(
        raw,
        f"'{raw}' 不是有效的 A 股代码。仅支持 6 位 A 股代码（如 600519、"
        "sh600519、600519.SH），港股/美股/B 股暂不支持。",
        ["未发起 AkShare 请求：标的不是 A 股。"],
    )


def _as_float(value):
    """尽量把 AkShare 的字符串/数值列解析为 float；无法解析返回 None。

    AkShare 的财务接口经常把数字以字符串返回（如 "64467425981.95"，
    甚至 "nan" 字符串），直接用 float() 会污染计算或抛异常。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(number) else number

    text = str(value).strip().replace(",", "").replace("，", "")
    if text.lower() in ("", "-", "--", "nan", "none", "null", "nat"):
        return None

    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 1e8
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 1e4
        text = text[:-1]
    if text.endswith("%"):
        text = text[:-1]

    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    return None if pd.isna(number) else number


def _raw_text(value):
    """把单元格转成去空白字符串；空值/NaN 返回空字符串。"""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _number_text(value):
    """把数值转成不带科学计数法的字符串（保留原始口径）。"""
    number = _as_float(value)
    if number is None:
        return ""
    if float(number).is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _to_date(value):
    """把 date / datetime / Timestamp / 字符串统一转成 date；失败返回 None。"""
    if value is None:
        return None
    # WHY 先判空再判类型：pd.NaT 是 datetime 的子类，若先走 isinstance
    # 分支会返回 NaT 本身，后续与 date 比较会抛
    # “Cannot compare NaT with datetime.date object”。
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _iso(value):
    """转成 YYYY-MM-DD；空值返回空字符串。"""
    parsed = _to_date(value)
    return parsed.isoformat() if parsed else ""


def _parse_cutoff(curr_date):
    """解析分析日；未提供时用今天，格式非法时返回 None。"""
    if not curr_date:
        return date.today()
    return _to_date(curr_date)


def _find_column(df, candidates):
    """返回 candidates 中第一个存在于 DataFrame 的列名。"""
    if df is None:
        return None
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _digits6(value):
    """把代码单元格归一化为 6 位数字字符串；无法识别返回空字符串。"""
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return ""


def _filter_by_code(df, code):
    """按代码列过滤 DataFrame；找不到代码列时返回空表。"""
    code_col = _find_column(df, ("股票代码", "代码", "symbol", "code", "SECURITY_CODE"))
    if df is None or df.empty or not code_col:
        return pd.DataFrame()
    return df[df[code_col].map(_digits6) == code]


def _first_float(row, names):
    """从一行里按候选列名取第一个可解析数值。"""
    for name in names:
        if name in row.index:
            value = _as_float(row.get(name))
            if value is not None:
                return value
    return None


def _first_text(row, names):
    """从一行里按候选列名取第一个非空文本。"""
    for name in names:
        if name in row.index:
            text = _raw_text(row.get(name))
            if text:
                return text
    return ""


def _recent_report_periods(today, count=6):
    """生成今天之前最近的若干季度报告期（新 → 旧）。

    WHY 不写死年份：写死 "20251231" 这类列表，跨年后就会静默失效。
    """
    periods = []
    for year in range(today.year, today.year - 3, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            candidate = date(year, month, day)
            if candidate <= today:
                periods.append(candidate)
    periods.sort(reverse=True)
    return periods[:count]


def _report_period_label(period_iso):
    """把报告期转成中文标签，如 2025-12-31 → 2025 年报。"""
    parsed = _to_date(period_iso)
    if parsed is None:
        return str(period_iso or "")
    if parsed.month == 12:
        return f"{parsed.year} 年报"
    if parsed.month == 9:
        return f"{parsed.year} 三季报"
    if parsed.month == 6:
        return f"{parsed.year} 中报"
    if parsed.month == 3:
        return f"{parsed.year} 一季报"
    return parsed.isoformat()


def _valuation_level(percentile):
    """按 PE/PB 分位数给出估值水平标签。"""
    if percentile >= 80:
        return "高估"
    if percentile >= 60:
        return "偏高"
    if percentile >= 40:
        return "中性"
    if percentile >= 20:
        return "偏低"
    return "低估"


def _in_window(target_iso, today, days):
    """判断日期是否落在 [today, today+days]。"""
    target = _to_date(target_iso)
    if target is None:
        return False
    return today <= target <= today + timedelta(days=days)


# ============================================================================
# 1. 估值分位
# ============================================================================

def _valuation_period(years):
    """选择不短于请求窗口的百度估值周期参数。"""
    if years <= 1:
        return "近一年"
    if years <= 3:
        return "近三年"
    if years <= 5:
        return "近五年"
    return "全部"


def _series_stats(work):
    """对单条估值序列计算当前值、分位数与分位点。"""
    values = work["value"].astype(float)
    current = float(values.iloc[-1])
    stats = {
        "value": round(current, 2),
        "percentile": round(float((values <= current).sum()) / len(values) * 100, 1),
        "min": round(float(values.min()), 2),
        "max": round(float(values.max()), 2),
        "median": round(float(values.median()), 2),
        "p20": round(float(values.quantile(0.2)), 2),
        "p80": round(float(values.quantile(0.8)), 2),
    }
    return stats, len(values), work["date"].iloc[-1].date().isoformat()


def get_valuation_percentile(ticker, curr_date=None, years=5) -> dict:
    """计算当前 PE-TTM / PB 在近 N 年历史中的分位水平。

    WHY 不用实时快照的 PE/PB：实时快照只能说明“现在贵不贵”，无法回答
    “相对于自己的历史贵不贵”。A 股大量标的的估值中枢会随行业景气度
    长期漂移，必须放在滚动窗口内看分位，才能避免把“历史高位”误判为
    “便宜”。

    数据源：``ak.stock_zh_valuation_baidu``（百度股市通历史估值）。
    ``period`` 只支持近一年/近三年/近五年/全部，因此先选一个不短于
    请求窗口的周期，再按日期自行裁剪；``curr_date`` 给定时会剔除之后
    的样本，避免历史分析偷看未来数据。

    参数：
      ticker: A 股代码或名称
      curr_date: 分析日 YYYY-MM-DD；默认今天
      years: 滚动窗口年数，默认 5 年

    返回：
      dict，包含 success/ticker/as_of/window_years/samples/pe_ttm/pb/
      level/summary/warnings。PE 与 PB 各自包含 value/percentile/min/
      max/median/p20/p80。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    end = _parse_cutoff(curr_date)
    if end is None:
        return _failure(
            code,
            f"无法解析 curr_date='{curr_date}'，请使用 YYYY-MM-DD 格式。",
        )

    try:
        years = max(1, int(years))
    except (TypeError, ValueError):
        years = 5

    cache_key = (code, end.isoformat(), years)
    cached = _cache_get(_VALUATION_CACHE, cache_key, _VALUATION_TTL)
    if cached is not _MISS:
        return cached

    period = _valuation_period(years)
    warnings = []
    series = {}
    sample_counts = {}
    latest_dates = []

    for field, indicator, label in (
        ("pe_ttm", "市盈率(TTM)", "PE-TTM"),
        ("pb", "市净率", "PB"),
    ):
        try:
            df = retry_with_backoff(
                lambda ind=indicator, p=period: ak.stock_zh_valuation_baidu(
                    symbol=code, indicator=ind, period=p
                ),
                max_retries=AKSHARE_MAX_RETRIES,
                base_delay=AKSHARE_BASE_DELAY,
            )
        except Exception as exc:
            warnings.append(f"{label} 历史估值获取失败：{type(exc).__name__}")
            logger.warning("估值接口失败 %s %s: %s", code, label, exc)
            continue

        if df is None or df.empty or "date" not in df.columns or "value" not in df.columns:
            warnings.append(f"{label} 历史估值数据为空或缺少 date/value 列")
            continue

        work = pd.DataFrame(
            {
                "date": pd.to_datetime(df["date"], errors="coerce"),
                "value": pd.to_numeric(df["value"], errors="coerce"),
            }
        ).dropna()
        if work.empty:
            warnings.append(f"{label} 历史估值序列全部无效")
            continue

        window_start = pd.Timestamp(end) - pd.DateOffset(years=years)
        work = work[
            (work["date"] >= window_start) & (work["date"] <= pd.Timestamp(end))
        ].sort_values("date")
        if work.empty:
            warnings.append(f"{label} 在近 {years} 年窗口内没有样本")
            continue
        if len(work) < 60:
            warnings.append(
                f"{label} 历史估值样本不足（{len(work)} 条），分位数不可靠"
            )
            continue

        stats, count, latest = _series_stats(work)
        series[field] = stats
        sample_counts[field] = count
        latest_dates.append(latest)

    pe = series.get("pe_ttm")
    pb = series.get("pb")

    if not pe and not pb:
        message = (
            f"无法计算估值分位：近 {years} 年窗口内 PE/PB 历史估值样本不足或数据源不可用。"
        )
        result = {
            "success": False,
            "ticker": code,
            "as_of": end.isoformat(),
            "window_years": years,
            "samples": 0,
            "pe_ttm": None,
            "pb": None,
            "level": "",
            "summary": message,
            "warnings": warnings,
        }
        _cache_set(_VALUATION_CACHE, cache_key, result)
        return result

    as_of = max(latest_dates) if latest_dates else end.isoformat()
    samples = max(sample_counts.values()) if sample_counts else 0
    level = _valuation_level((pe or pb)["percentile"])

    if pe and pb:
        summary = (
            f"PE-TTM {pe['value']:.2f}，处于近 {years} 年 {pe['percentile']:.1f}% 分位"
            f"（{level}）；PB {pb['value']:.2f}，近 {years} 年 {pb['percentile']:.1f}% 分位。"
        )
    elif pe:
        summary = (
            f"PE-TTM {pe['value']:.2f}，处于近 {years} 年 {pe['percentile']:.1f}% 分位"
            f"（{level}）；PB 数据不可用。"
        )
    else:
        summary = (
            f"PB {pb['value']:.2f}，处于近 {years} 年 {pb['percentile']:.1f}% 分位"
            f"（{level}，PE 数据不可用，按 PB 分位判断）。"
        )

    result = {
        "success": True,
        "ticker": code,
        "as_of": as_of,
        "window_years": years,
        "samples": samples,
        "pe_ttm": pe,
        "pb": pb,
        "level": level,
        "summary": summary,
        "warnings": warnings,
    }
    _cache_set(_VALUATION_CACHE, cache_key, result)
    return result


# ============================================================================
# 2. 业绩预告 / 业绩快报
# ============================================================================

def _fetch_market_frame(name, func, **kwargs):
    """按 (接口名, 参数) 缓存全市场表，避免同一分析重复拉取。

    WHY：``stock_yjyg_em`` / ``stock_yjkb_em`` / ``stock_fhps_em`` 等接口
    一次返回全市场数千行，按报告期调用；不缓存的话“事件日历”会反复
    请求同一张表，既慢又容易触发限流。
    """
    key = (name, tuple(sorted(kwargs.items())))
    cached = _cache_get(_MARKET_CACHE, key, _MARKET_TTL)
    if cached is not _MISS:
        return cached

    df = retry_with_backoff(
        lambda: func(**kwargs),
        max_retries=AKSHARE_MAX_RETRIES,
        base_delay=AKSHARE_BASE_DELAY,
    )
    if df is None:
        df = pd.DataFrame()
    _cache_set(_MARKET_CACHE, key, df)
    return df


def _forecast_item_summary(indicator, change_pct, change_text, announce_date):
    """拼出单条业绩预告/快报的中文摘要。"""
    bits = []
    if indicator:
        bits.append(indicator)
    if change_pct is not None:
        bits.append(f"同比 {change_pct:.2f}%")
    elif change_text:
        bits.append(change_text)
    if announce_date:
        bits.append(f"公告日 {announce_date}")
    return "，".join(bits)


def _parse_forecast_rows(df, code, period, kind, cutoff_iso):
    """从全市场业绩预告/快报表中筛出目标股票并规范化字段。"""
    if df is None or df.empty:
        return [], ""

    code_col = _find_column(df, ("股票代码", "代码", "symbol"))
    if not code_col:
        return [], f"{kind}接口缺少股票代码列，无法筛选"

    matched = df[df[code_col].map(_digits6) == code]
    if matched.empty:
        return [], ""

    items = []
    for _, row in matched.iterrows():
        announce_date = _iso(row.get("公告日期"))
        if cutoff_iso and announce_date and announce_date > cutoff_iso:
            continue

        if kind == "预告":
            indicator = _raw_text(row.get("预测指标"))
            change_text = _raw_text(row.get("业绩变动"))
            change_pct = _as_float(row.get("业绩变动幅度"))
            forecast_value = _number_text(row.get("预测数值")) or _raw_text(
                row.get("预测数值")
            )
            forecast_type = _raw_text(row.get("预告类型"))
            reason = _raw_text(row.get("业绩变动原因"))
        else:
            indicator = "净利润"
            change_text = ""
            change_pct = _as_float(row.get("净利润-同比增长"))
            forecast_value = _number_text(row.get("净利润-净利润"))
            forecast_type = ""
            reason = ""

        change_pct = round(change_pct, 2) if change_pct is not None else None
        items.append(
            {
                "report_period": period.isoformat(),
                "kind": kind,
                "indicator": indicator,
                "change_pct": change_pct,
                "forecast_value": forecast_value,
                "announce_date": announce_date,
                "summary": _forecast_item_summary(
                    indicator, change_pct, change_text, announce_date
                ),
                "forecast_type": forecast_type,
                "reason": reason,
            }
        )
    return items, ""


def get_earnings_forecast(ticker, curr_date=None) -> dict:
    """获取个股最近一期的业绩预告 + 业绩快报。

    WHY 这是 A 股最强的业绩催化：业绩预告/快报是**强制披露制度**，
    业绩大幅变动（预增/预减/扭亏/首亏）必须在定期报告前提前公告，
    往往直接决定短期股价方向。错过预告窗口，只看正式财报会滞后
    1-3 个月。

    接口按**报告期**返回全市场数据（不是按股票代码），因此本函数从
    最近 6 个季度末开始逐期尝试，找到目标股票后立即停止；``curr_date``
    给定时会剔除公告日晚于分析日的记录，避免历史分析使用未来信息。

    参数：
      ticker: A 股代码或名称
      curr_date: 分析日 YYYY-MM-DD；默认今天

    返回：
      dict，包含 success/ticker/as_of/items/latest/summary/warnings。
      每条 item 含 report_period/kind/indicator/change_pct/
      forecast_value/announce_date/summary。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    cutoff = _parse_cutoff(curr_date)
    if cutoff is None:
        return _failure(
            code,
            f"无法解析 curr_date='{curr_date}'，请使用 YYYY-MM-DD 格式。",
        )

    cache_key = ("earnings", code, cutoff.isoformat())
    cached = _cache_get(_RESULT_CACHE, cache_key, _RESULT_TTL)
    if cached is not _MISS:
        return cached

    warnings = []
    items = []
    attempts = 0
    failed = 0
    periods = _recent_report_periods(cutoff, 6)

    for period in periods:
        period_ak = period.strftime("%Y%m%d")
        period_items = []
        for kind, table_name, func_name in (
            ("预告", "yjyg", "stock_yjyg_em"),
            ("快报", "yjkb", "stock_yjkb_em"),
        ):
            attempts += 1
            try:
                func = getattr(ak, func_name)
                df = _fetch_market_frame(table_name, func, date=period_ak)
            except Exception as exc:
                failed += 1
                warnings.append(
                    f"{kind}接口（{period_ak}）获取失败：{type(exc).__name__}"
                )
                logger.warning("业绩 %s 接口失败 %s: %s", kind, period_ak, exc)
                continue

            parsed, note = _parse_forecast_rows(
                df, code, period, kind, cutoff.isoformat()
            )
            if note:
                warnings.append(note)
            period_items.extend(parsed)

        if period_items:
            items = period_items
            break

    items.sort(
        key=lambda item: (item["report_period"], item["announce_date"]),
        reverse=True,
    )
    latest = items[0] if items else None

    if not items and attempts and failed == attempts:
        result = {
            "success": False,
            "ticker": code,
            "as_of": cutoff.isoformat(),
            "items": [],
            "latest": None,
            "summary": "业绩预告/快报接口全部获取失败，无法判断最近业绩情况。",
            "warnings": warnings,
        }
        return result

    if latest:
        label = _report_period_label(latest["report_period"])
        summary = (
            f"最近一期业绩{latest['kind']}（{label}）：{latest['summary']}。"
        )
    else:
        tried = "、".join(p.strftime("%Y%m%d") for p in periods)
        summary = (
            f"未找到 {code} 在最近报告期（{tried}）的业绩预告或业绩快报，"
            "公司可能未披露或数据源暂无记录。"
        )

    result = {
        "success": True,
        "ticker": code,
        "as_of": cutoff.isoformat(),
        "items": items,
        "latest": latest,
        "summary": summary,
        "warnings": warnings,
    }
    _cache_set(_RESULT_CACHE, cache_key, result)
    return result


# ============================================================================
# 3. 财报预约披露日
# ============================================================================

_DISCLOSURE_PERIOD_RE = re.compile(r"^\d{4}(一季|半年报|三季|年报)$")


def _quarter_label(parsed):
    """把报告期日期转成巨潮资讯的期间标签。"""
    if parsed.month == 3:
        return f"{parsed.year}一季"
    if parsed.month == 6:
        return f"{parsed.year}半年报"
    if parsed.month == 9:
        return f"{parsed.year}三季"
    if parsed.month == 12:
        return f"{parsed.year}年报"
    return ""


def _normalize_disclosure_period(period):
    """兼容用户/LLM 传入的多种写法，如 2026半年、2026中报、20260331。"""
    text = str(period or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{8}", text):
        parsed = _to_date(text)
        return _quarter_label(parsed) if parsed else ""
    if text.endswith("中报"):
        text = text[:-2] + "半年报"
    elif text.endswith("半年"):
        text += "报"
    return text


def _disclosure_period_candidates(today):
    """返回从当前到下一期的候选披露期间（用于向前看的事件日历）。"""
    year = today.year
    if today.month <= 4:
        return [f"{year - 1}年报", f"{year}一季", f"{year}半年报"]
    if today.month <= 8:
        return [f"{year}一季", f"{year}半年报", f"{year}三季"]
    if today.month <= 10:
        return [f"{year}半年报", f"{year}三季", f"{year}年报"]
    return [f"{year}三季", f"{year}年报", f"{year + 1}一季"]


def _fetch_disclosure_frame(period):
    """拉取某个报告期的全市场预约披露表（带缓存）。"""
    key = ("disclosure", period)
    cached = _cache_get(_MARKET_CACHE, key, _MARKET_TTL)
    if cached is not _MISS:
        return cached

    func = ak.stock_report_disclosure
    df = retry_with_backoff(
        lambda: func(market="沪深京", period=period),
        max_retries=AKSHARE_MAX_RETRIES,
        base_delay=AKSHARE_BASE_DELAY,
    )
    if df is None:
        df = pd.DataFrame()
    _cache_set(_MARKET_CACHE, key, df)
    return df


def _disclosure_items(frame, code):
    """把预约披露表转成统一 items，并按代码过滤。"""
    code_col = _find_column(frame, ("股票代码", "代码", "symbol"))
    if code:
        if not code_col:
            return [], "预约披露接口缺少股票代码列，无法按代码过滤"
        selected = frame[frame[code_col].map(_digits6) == code]
    else:
        selected = frame.head(50)

    items = []
    for _, row in selected.iterrows():
        disclose_date = _iso(row.get("首次预约"))
        actual_date = _iso(row.get("实际披露"))
        if not disclose_date and not actual_date:
            continue
        reason = _first_text(
            row, ("变更原因", "变动原因", "披露变更原因", "变更说明")
        )
        items.append(
            {
                "ticker": _digits6(row.get(code_col)) if code_col else "",
                "name": _first_text(row, ("股票简称", "名称")),
                "disclose_date": disclose_date,
                "actual_date": actual_date,
                "change_reason": reason,
            }
        )
    items.sort(key=lambda item: item["disclose_date"])
    return items, ""


def get_report_disclosure_calendar(ticker=None, period=None) -> dict:
    """获取沪深京上市公司财报**预约披露日**（巨潮资讯）。

    WHY：A 股定期报告在正式披露前会先公布预约日期，且日期可变更。
    对交易而言，“哪天出财报”本身就是风险事件：业绩暴雷、机构调仓、
    停牌核查往往集中在披露日前后，提前知道日期才能控制仓位。

    数据源：``ak.stock_report_disclosure(market="沪深京", period=...)``。
    该接口按报告期返回全市场数据，因此指定 ``ticker`` 时在本地过滤；
    不指定时最多返回 50 条样本，避免把五千多行直接塞给 LLM。

    参数：
      ticker: 可选，A 股代码；不传则返回全市场样本
      period: 报告期标签，如 "2025年报"、"2026一季"、"2026半年"；
              默认取当前/最近一期

    返回：
      dict，包含 success/ticker/period/items/summary/warnings。
      item 含 ticker/name/disclose_date/actual_date/change_reason。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw) if raw else ""
    if raw and not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        result = _failure(code, str(exc))
        result.update({"period": "", "items": []})
        return result

    today = date.today()
    warnings = []

    if period:
        label = _normalize_disclosure_period(period)
        if not _DISCLOSURE_PERIOD_RE.match(label):
            result = _failure(
                code,
                f"无法识别报告期 '{period}'，请使用如 2025年报、2026一季、"
                "2026半年、2026三季 的格式。",
                warnings,
            )
            result.update({"period": label, "items": []})
            return result
        try:
            frame = _fetch_disclosure_frame(label)
        except Exception as exc:
            warnings.append(
                f"{label} 预约披露数据获取失败：{type(exc).__name__}"
            )
            result = _failure(
                code,
                f"{label} 预约披露数据获取失败：{type(exc).__name__}",
                warnings,
            )
            result.update({"period": label, "items": []})
            return result
        resolved_period = label
    else:
        frame = None
        resolved_period = ""
        for label in _disclosure_period_candidates(today):
            try:
                candidate = _fetch_disclosure_frame(label)
            except Exception as exc:
                warnings.append(
                    f"{label} 预约披露数据获取失败：{type(exc).__name__}"
                )
                continue
            if candidate is None or candidate.empty:
                warnings.append(f"{label} 暂无预约披露数据")
                continue
            resolved_period = label
            frame = candidate
            if not code or not _filter_by_code(candidate, code).empty:
                break

        if frame is None:
            result = _failure(
                code,
                "所有候选报告期的预约披露数据都不可用，请稍后重试。",
                warnings,
            )
            result.update({"period": resolved_period, "items": []})
            return result

    items, note = _disclosure_items(frame, code)
    if note:
        warnings.append(note)
    if code and not items:
        warnings.append(f"该股在 {resolved_period} 预约披露表中暂无记录")

    if code and items:
        first = items[0]
        summary = (
            f"{code} 在 {resolved_period} 的预约披露日为 "
            f"{first['disclose_date'] or '未安排'}"
        )
        if first["actual_date"]:
            summary += f"（实际披露 {first['actual_date']}）"
        summary += "。"
    elif code:
        summary = f"未找到 {code} 在 {resolved_period} 的预约披露记录。"
    else:
        summary = (
            f"{resolved_period} 共获取 {len(frame)} 条预约披露记录，"
            f"展示前 {len(items)} 条。"
        )

    return {
        "success": True,
        "ticker": code,
        "period": resolved_period,
        "items": items,
        "summary": summary,
        "warnings": warnings,
    }


# ============================================================================
# 4. 限售解禁
# ============================================================================

def _fetch_lockup_queue(code):
    """按股票拉取解禁批次（东方财富“解禁批次”接口）。"""
    func = ak.stock_restricted_release_queue_em
    return retry_with_backoff(
        lambda: func(symbol=code),
        max_retries=AKSHARE_MAX_RETRIES,
        base_delay=AKSHARE_BASE_DELAY,
    )


def _fetch_lockup_detail(start_ak, end_ak):
    """按日期区间拉取全市场解禁明细（东方财富“解禁明细”接口）。"""
    func = ak.stock_restricted_release_detail_em
    return retry_with_backoff(
        lambda: func(start_date=start_ak, end_date=end_ak),
        max_retries=1,
        base_delay=AKSHARE_BASE_DELAY,
    )


def _lockup_ratio(row):
    """提取解禁比例并统一为小数；返回 (ratio, 原始列名)。"""
    for name in (
        "占总股本比例",
        "占总市值比例",
        "占流通市值比例",
        "占解禁前流通市值比例",
    ):
        if name not in row.index:
            continue
        value = _as_float(row.get(name))
        if value is None:
            continue
        # 东方财富的“占比”列通常是小数（0.0075），个别接口给百分数（0.75）。
        ratio = value if abs(value) <= 1 else value / 100.0
        return ratio, name
    return None, ""


def get_lockup_expiry(ticker, months=6, today=None) -> dict:
    """获取未来 N 个月的限售股解禁安排。

    WHY：限售解禁是 A 股特有的**股本供给冲击**。首发原股东、定增机构、
    股权激励等股份在解禁前后往往出现集中减持，股价在解禁前 1-4 周
    常有提前抛压。提前知道最近一次解禁的日期、数量与占比，才能判断
    是否为“供给悬崖”。

    数据源优先级：
      1. ``stock_restricted_release_queue_em(symbol=code)``：单只股票的
         解禁批次（数据最直接）。
      2. 若批次接口为空，退回 ``stock_restricted_release_detail_em``：
         按日期区间返回全市场明细，再按代码过滤。

    参数：
      ticker: A 股代码或名称
      months: 向前看的月数，默认 6
      today: 基准日（date 或 YYYY-MM-DD），默认今天

    返回：
      dict，包含 success/ticker/as_of/months/items/next/nearest_days/
      level/summary/warnings。level：<=14 天 warn，否则 info，无解禁 ok。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    base_date = _to_date(today) if today else date.today()
    if base_date is None:
        return _failure(code, f"无法解析 today='{today}'，请使用 YYYY-MM-DD。")

    try:
        months = max(1, int(months))
    except (TypeError, ValueError):
        months = 6

    cache_key = ("lockup", code, months, base_date.isoformat())
    cached = _cache_get(_RESULT_CACHE, cache_key, _RESULT_TTL)
    if cached is not _MISS:
        return cached

    window_end = base_date + relativedelta(months=months)
    warnings = []
    frames = []
    source_ok = False
    source_failed = 0

    try:
        queue_df = _fetch_lockup_queue(code)
        source_ok = True
        if queue_df is not None and not queue_df.empty:
            frames.append(queue_df)
    except Exception as exc:
        source_failed += 1
        warnings.append(f"解禁批次接口获取失败：{type(exc).__name__}")
        logger.warning("解禁批次接口失败 %s: %s", code, exc)

    if not frames:
        try:
            detail_df = _fetch_lockup_detail(
                base_date.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")
            )
            source_ok = True
            if detail_df is not None and not detail_df.empty:
                filtered = _filter_by_code(detail_df, code)
                if not filtered.empty:
                    frames.append(filtered)
        except Exception as exc:
            source_failed += 1
            warnings.append(f"解禁明细接口获取失败：{type(exc).__name__}")
            logger.warning("解禁明细接口失败 %s: %s", code, exc)

    items = []
    seen = set()
    for frame in frames:
        date_col = _find_column(frame, ("解禁时间", "解禁日期"))
        if not date_col:
            warnings.append("解禁数据缺少解禁日期列，已跳过")
            continue
        for _, row in frame.iterrows():
            release = _to_date(row.get(date_col))
            if release is None or release < base_date or release > window_end:
                continue
            shares = _first_float(
                row, ("解禁数量", "解禁股数", "实际解禁数量")
            )
            ratio, ratio_name = _lockup_ratio(row)
            value = _first_float(
                row,
                ("解禁市值", "实际解禁市值", "实际解禁数量市值", "解禁金额"),
            )
            share_type = _first_text(
                row, ("股份类型", "限售股类型", "限售股类型")
            )
            dedupe_key = (
                release.isoformat(),
                round(shares or 0.0, 2),
                share_type,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append(
                {
                    "release_date": release.isoformat(),
                    "shares": round(shares, 2) if shares is not None else None,
                    "ratio": round(ratio, 6) if ratio is not None else None,
                    "value": round(value, 2) if value is not None else None,
                    "share_type": share_type,
                    "ratio_name": ratio_name,
                }
            )

    if source_failed and not source_ok:
        result = _failure(
            code, "解禁数据接口全部获取失败，无法判断解禁压力。", warnings
        )
        result.update(
            {
                "as_of": base_date.isoformat(),
                "months": months,
                "items": [],
                "next": None,
                "nearest_days": None,
                "level": "ok",
            }
        )
        return result

    items.sort(key=lambda item: item["release_date"])
    nearest_days = None
    if items:
        first_date = _to_date(items[0]["release_date"])
        nearest_days = (first_date - base_date).days

    if not items:
        level = "ok"
        summary = f"未来 {months} 个月内无解禁记录。"
    else:
        level = "warn" if nearest_days is not None and nearest_days <= 14 else "info"
        first = items[0]
        lead = f"最近一次解禁为 {first['release_date']}"
        if nearest_days is not None:
            lead += f"（{nearest_days} 天后）"
        parts = [lead]
        if first["shares"] is not None:
            parts.append(f"解禁 {first['shares'] / 1e4:.1f} 万股")
        if first["ratio"] is not None:
            label = first["ratio_name"].removeprefix("占").removesuffix("比例")
            parts.append(f"占{label} {first['ratio'] * 100:.2f}%")
        if first["share_type"]:
            parts.append(first["share_type"])
        summary = "，".join(parts) + "。"

    result = {
        "success": True,
        "ticker": code,
        "as_of": base_date.isoformat(),
        "months": months,
        "items": items,
        "next": items[0] if items else None,
        "nearest_days": nearest_days,
        "level": level,
        "summary": summary,
        "warnings": warnings,
    }
    _cache_set(_RESULT_CACHE, cache_key, result)
    return result


# ============================================================================
# 5. 分红送转 / 除权除息
# ============================================================================

def _fetch_fhps_frame(period_ak):
    """拉取某个报告期的全市场分红送转表（带缓存）。"""
    func = ak.stock_fhps_em
    return _fetch_market_frame("fhps", func, date=period_ak)


def get_dividend_schedule(ticker, days=90, today=None) -> dict:
    """获取未来 N 天内的分红送转 / 除权除息安排。

    WHY：除权除息日会机械性压低股价（分红金额从股价中扣除），同时
    股权登记日前后存在“抢权/避税”交易行为。A 股分红方案还常伴随
    送转股，除权后股本变化会影响每股指标与估值可比性，必须提前提示。

    数据源：``ak.stock_fhps_em(date="YYYYMMDD")``，按报告期返回全市场，
    因此需要从最近 6 个报告期里逐期筛选目标股票。

    注意：AkShare 的 ``现金分红-现金分红比例`` / ``送转股份-送转总比例``
    是**每 10 股**口径，本函数保留源数据原值（字段名按调用方约定），
    摘要中使用“每 10 股派 X 元”避免歧义。

    参数：
      ticker: A 股代码或名称
      days: 向前看的天数，默认 90
      today: 基准日（date 或 YYYY-MM-DD），默认今天

    返回：
      dict，包含 success/ticker/as_of/days/items/summary/warnings。
      item 含 ex_date/record_date/cash_per_share/bonus_ratio/progress/
      report_period。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    base_date = _to_date(today) if today else date.today()
    if base_date is None:
        return _failure(code, f"无法解析 today='{today}'，请使用 YYYY-MM-DD。")

    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 90

    cache_key = ("dividend", code, days, base_date.isoformat())
    cached = _cache_get(_RESULT_CACHE, cache_key, _RESULT_TTL)
    if cached is not _MISS:
        return cached

    window_end = base_date + timedelta(days=days)
    warnings = []
    items = []
    seen = set()
    attempts = 0
    failed = 0

    for period in _recent_report_periods(base_date, 6):
        period_ak = period.strftime("%Y%m%d")
        attempts += 1
        try:
            frame = _fetch_fhps_frame(period_ak)
        except Exception as exc:
            failed += 1
            warnings.append(
                f"分红接口（{period_ak}）获取失败：{type(exc).__name__}"
            )
            logger.warning("分红接口失败 %s: %s", period_ak, exc)
            continue

        matched = _filter_by_code(frame, code)
        if matched.empty:
            continue

        for _, row in matched.iterrows():
            ex_date = _to_date(row.get("除权除息日"))
            if ex_date is None or ex_date < base_date or ex_date > window_end:
                continue
            cash = _first_float(
                row, ("现金分红-现金分红比例", "派息", "现金分红比例")
            )
            bonus = _first_float(
                row,
                (
                    "送转股份-送转总比例",
                    "送转总比例",
                    "送转股份-送转比例",
                    "送转比例",
                ),
            )
            progress = _first_text(row, ("方案进度", "分配方案进度"))
            record_date = _iso(row.get("股权登记日"))
            dedupe_key = (ex_date.isoformat(), cash, bonus)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append(
                {
                    "ex_date": ex_date.isoformat(),
                    "record_date": record_date,
                    "cash_per_share": round(cash, 4) if cash is not None else None,
                    "bonus_ratio": round(bonus, 4) if bonus is not None else None,
                    "progress": progress,
                    "report_period": period.isoformat(),
                }
            )

    if attempts and failed == attempts:
        result = _failure(
            code, "分红送转数据接口全部获取失败，无法判断除权除息安排。", warnings
        )
        result.update(
            {
                "as_of": base_date.isoformat(),
                "days": days,
                "items": [],
            }
        )
        return result

    items.sort(key=lambda item: item["ex_date"])

    if not items:
        summary = f"未来 {days} 天内无除权除息安排。"
    else:
        first = items[0]
        bits = [first["ex_date"]]
        if first["record_date"]:
            bits.append(f"股权登记日 {first['record_date']}")
        if first["cash_per_share"] is not None:
            bits.append(f"每 10 股派 {first['cash_per_share']:.2f} 元")
        if first["bonus_ratio"]:
            bits.append(f"每 10 股送转 {first['bonus_ratio']:.2f} 股")
        if first["progress"]:
            bits.append(f"方案进度：{first['progress']}")
        summary = (
            f"未来 {days} 天内有 {len(items)} 个除权除息日，"
            f"最近一次 {'，'.join(bits)}。"
        )

    result = {
        "success": True,
        "ticker": code,
        "as_of": base_date.isoformat(),
        "days": days,
        "items": items,
        "summary": summary,
        "warnings": warnings,
    }
    _cache_set(_RESULT_CACHE, cache_key, result)
    return result


# ============================================================================
# 6. 财务排雷
# ============================================================================

def _metric(metrics, *names):
    """按候选指标名取第一个可解析数值。"""
    for name in names:
        if name in metrics:
            value = _as_float(metrics[name])
            if value is not None:
                return value
    return None


def _format_yi(value):
    """把金额格式化为亿元，便于中文摘要阅读。"""
    if value is None:
        return "—"
    return f"{value / 1e8:.2f} 亿元"


def _fetch_abstract_snapshot(code, cutoff):
    """获取最近一期已披露的新浪关键财务指标（按分析日过滤）。"""
    cutoff_iso = cutoff.isoformat()
    key = ("abstract", code, cutoff_iso)
    cached = _cache_get(_SNAPSHOT_CACHE, key, _SNAPSHOT_TTL)
    if cached is not _MISS:
        return cached

    func = ak.stock_financial_abstract
    try:
        df = retry_with_backoff(
            lambda: func(symbol=code),
            max_retries=1,
            base_delay=AKSHARE_BASE_DELAY,
        )
    except Exception as exc:
        logger.warning("关键指标接口失败 %s: %s", code, exc)
        return None

    if df is None or df.empty or "指标" not in df.columns:
        return None

    date_cols = [
        column
        for column in df.columns
        if str(column).isdigit() and len(str(column)) == 8
    ]
    eligible = []
    for column in date_cols:
        available = report_available_date(column)
        if available and available <= cutoff_iso:
            eligible.append(column)
    if not eligible:
        return None

    latest_col = max(eligible, key=lambda column: str(column))
    metrics = {}
    for _, row in df.iterrows():
        name = _raw_text(row.get("指标"))
        if not name:
            continue
        value = row.get(latest_col)
        if name not in metrics or _raw_text(row.get("选项")) == "常用指标":
            metrics[name] = value

    snapshot = {"report_date": _iso(latest_col), "metrics": metrics}
    return _cache_set(_SNAPSHOT_CACHE, key, snapshot)


def _fetch_balance_snapshot(code, cutoff):
    """获取最近一期已披露的资产负债表关键项（按公告日过滤）。"""
    cutoff_iso = cutoff.isoformat()
    key = ("balance", code, cutoff_iso)
    cached = _cache_get(_SNAPSHOT_CACHE, key, _SNAPSHOT_TTL)
    if cached is not _MISS:
        return cached

    exchange = exchange_of(code)
    if not exchange:
        return None
    symbol = exchange.upper() + code

    func = ak.stock_balance_sheet_by_report_em
    try:
        df = retry_with_backoff(
            lambda: func(symbol=symbol),
            max_retries=1,
            base_delay=AKSHARE_BASE_DELAY,
        )
    except Exception as exc:
        logger.warning("资产负债表接口失败 %s: %s", symbol, exc)
        return None

    if df is None or df.empty or "REPORT_DATE" not in df.columns:
        return None

    work = df.copy()
    work["_report"] = pd.to_datetime(work["REPORT_DATE"], errors="coerce")
    work = work.dropna(subset=["_report"]).sort_values("_report", ascending=False)

    for _, row in work.iterrows():
        report_iso = _iso(row.get("REPORT_DATE"))
        if not report_iso:
            continue
        notice = _iso(row.get("NOTICE_DATE"))
        if notice:
            if notice > cutoff_iso:
                continue
        else:
            available = report_available_date(report_iso)
            if not available or available > cutoff_iso:
                continue

        snapshot = {
            "report_date": report_iso,
            "goodwill": _as_float(row.get("GOODWILL")),
            "equity": _first_float(row, ("TOTAL_EQUITY", "TOTAL_PARENT_EQUITY")),
            "accounts_rece": _as_float(row.get("ACCOUNTS_RECE")),
            "accounts_rece_yoy": _as_float(row.get("ACCOUNTS_RECE_YOY")),
        }
        return _cache_set(_SNAPSHOT_CACHE, key, snapshot)

    return None


def _fetch_pledge_snapshot(code, base_date):
    """获取最近一个交易日的股权质押比例（按日期回退，跳过周末）。"""
    key = ("pledge", code, base_date.isoformat())
    cached = _cache_get(_SNAPSHOT_CACHE, key, _SNAPSHOT_TTL)
    if cached is not _MISS:
        return cached

    func = ak.stock_gpzy_pledge_ratio_em
    failures = 0
    for offset in range(0, 7):
        current = base_date - timedelta(days=offset)
        if current.weekday() >= 5:
            continue

        date_key = ("pledge_table", current.isoformat())
        frame = _cache_get(_MARKET_CACHE, date_key, _MARKET_TTL)
        if frame is _MISS:
            try:
                frame = retry_with_backoff(
                    lambda d=current.strftime("%Y%m%d"): func(date=d),
                    max_retries=1 if offset == 0 else 0,
                    base_delay=AKSHARE_BASE_DELAY,
                )
            except Exception as exc:
                # 单日接口不可用是**预期内**的：质押数据按日发布，
                # 当天/最近一两个交易日往往还没生成。这里用 debug 记录，
                # 只有 7 天全部失败时才在末尾汇总成一条 warning ——
                # 否则每次排雷都会刷出 4~5 行噪音，淹没真正的告警。
                failures += 1
                logger.debug("股权质押接口 %s 不可用: %s", current, exc)
                frame = None
            if frame is not None:
                _cache_set(_MARKET_CACHE, date_key, frame)

        if frame is None or getattr(frame, "empty", True):
            continue

        matched = _filter_by_code(frame, code)
        if matched.empty:
            snapshot = {
                "trade_date": current.isoformat(),
                "ratio": None,
                "covered": True,
            }
            return _cache_set(_SNAPSHOT_CACHE, key, snapshot)

        snapshot = {
            "trade_date": current.isoformat(),
            "ratio": _as_float(matched.iloc[0].get("质押比例")),
            "covered": True,
        }
        return _cache_set(_SNAPSHOT_CACHE, key, snapshot)

    if failures:
        logger.warning(
            "股权质押数据连续 %s 个交易日不可用，已跳过该项检查", failures
        )
    return None


def _flag(check_code, name, level, value, message, detail):
    """构造单项排雷结果。"""
    return {
        "code": check_code,
        "name": name,
        "level": level,
        "value": value,
        "message": message,
        "detail": detail,
    }


_LEVEL_LABEL = {"ok": "正常", "warn": "关注", "danger": "危险"}


def get_financial_red_flags(ticker, curr_date=None) -> dict:
    """财务排雷四项：商誉、股权质押、应收账款、经营现金流。

    WHY：A 股财务造假与暴雷高度集中在四类信号：
      a) 商誉占净资产过高 —— 并购后业绩不达预期会一次性计提巨额减值；
      b) 大股东高比例股权质押 —— 股价下跌触发平仓，形成负反馈；
      c) 应收账款增速长期快于营收 —— 收入可能是“赊”出来的，回款质量差；
      d) 经营现金流长期低于净利润 —— 利润没有转化为现金，含金量低。

    四项检查彼此独立：任一数据源失败只会让该项返回“数据不足”，
    不会阻断其他检查；绝不用 0 或默认值冒充缺失数据。

    数据源：
      - 商誉/净资产/应收账款：``stock_balance_sheet_by_report_em``
      - 营收/净利润/经营现金流/商誉兜底：``stock_financial_abstract``
      - 股权质押：``stock_gpzy_pledge_ratio_em``（按交易日全市场）

    返回：
      dict，包含 success/ticker/as_of/checks/risk_score/level/summary/
      warnings。risk_score 为 0-100（ok=0/warn=50/danger=100 的平均）。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    cutoff = _parse_cutoff(curr_date)
    if cutoff is None:
        return _failure(
            code,
            f"无法解析 curr_date='{curr_date}'，请使用 YYYY-MM-DD 格式。",
        )

    cache_key = ("red_flags", code, cutoff.isoformat())
    cached = _cache_get(_RESULT_CACHE, cache_key, _RESULT_TTL)
    if cached is not _MISS:
        return cached

    abstract = _fetch_abstract_snapshot(code, cutoff)
    balance = _fetch_balance_snapshot(code, cutoff)
    warnings = []
    checks = []

    detail_sources = []
    if balance:
        detail_sources.append(f"资产负债表 {balance['report_date']}")
    if abstract:
        detail_sources.append(f"关键指标 {abstract['report_date']}")
    source_note = "；".join(detail_sources)

    # ------------------------------------------------------------------
    # a) 商誉 / 净资产
    # ------------------------------------------------------------------
    goodwill = balance.get("goodwill") if balance else None
    equity = balance.get("equity") if balance else None
    if goodwill is None and abstract:
        goodwill = _metric(abstract["metrics"], "商誉")
    if equity is None and abstract:
        equity = _metric(
            abstract["metrics"], "股东权益合计(净资产)", "净资产"
        )

    if goodwill is None or equity is None or equity <= 0:
        checks.append(
            _flag(
                "goodwill",
                "商誉",
                "ok",
                None,
                "数据不足，无法判断",
                "未取得商誉或净资产数据",
            )
        )
        warnings.append("商誉数据不可用，无法判断商誉占净资产比例。")
    else:
        ratio = goodwill / equity * 100
        if ratio >= 30:
            level = "danger"
            message = f"商誉占净资产 {ratio:.1f}%（危险阈值 30%）"
        elif ratio >= 10:
            level = "warn"
            message = f"商誉占净资产 {ratio:.1f}%（关注阈值 10%）"
        else:
            level = "ok"
            message = f"商誉占净资产 {ratio:.1f}%，未触发风险阈值"
        checks.append(
            _flag(
                "goodwill",
                "商誉",
                level,
                round(ratio, 2),
                message,
                f"商誉 {_format_yi(goodwill)} / 净资产 {_format_yi(equity)}"
                + (f"（{source_note}）" if source_note else ""),
            )
        )

    # ------------------------------------------------------------------
    # b) 股权质押比例
    # ------------------------------------------------------------------
    pledge = _fetch_pledge_snapshot(code, cutoff)
    if pledge is None:
        checks.append(
            _flag(
                "pledge",
                "股权质押",
                "ok",
                None,
                "数据不足，无法判断",
                "未获取到股权质押比例数据",
            )
        )
        warnings.append("股权质押数据不可用，无法判断质押风险。")
    elif pledge.get("ratio") is None:
        checks.append(
            _flag(
                "pledge",
                "股权质押",
                "ok",
                0.0,
                "未出现在最新质押名单中（视为无质押）",
                f"交易日 {pledge['trade_date']}",
            )
        )
    else:
        pledge_ratio = pledge["ratio"]
        if pledge_ratio >= 50:
            level = "danger"
            message = f"股权质押比例 {pledge_ratio:.1f}%（危险阈值 50%）"
        elif pledge_ratio >= 30:
            level = "warn"
            message = f"股权质押比例 {pledge_ratio:.1f}%（关注阈值 30%）"
        else:
            level = "ok"
            message = f"股权质押比例 {pledge_ratio:.1f}%，未触发风险阈值"
        checks.append(
            _flag(
                "pledge",
                "股权质押",
                level,
                round(pledge_ratio, 2),
                message,
                f"交易日 {pledge['trade_date']}",
            )
        )

    # ------------------------------------------------------------------
    # c) 应收账款 / 营业收入
    # ------------------------------------------------------------------
    accounts_rece = balance.get("accounts_rece") if balance else None
    revenue = (
        _metric(abstract["metrics"], "营业总收入", "营业收入")
        if abstract
        else None
    )
    if accounts_rece is None or revenue is None or revenue <= 0:
        checks.append(
            _flag(
                "receivables",
                "应收账款",
                "ok",
                None,
                "数据不足，无法判断",
                "未取得应收账款或营业收入数据",
            )
        )
        warnings.append("应收账款或营业收入数据不可用，无法判断回款质量。")
    else:
        ratio = accounts_rece / revenue * 100
        if ratio >= 100:
            level = "danger"
            message = f"应收账款占营业总收入 {ratio:.1f}%（危险阈值 100%）"
        elif ratio >= 50:
            level = "warn"
            message = f"应收账款占营业总收入 {ratio:.1f}%（关注阈值 50%）"
        else:
            level = "ok"
            message = f"应收账款占营业总收入 {ratio:.1f}%，未触发风险阈值"

        ar_yoy = balance.get("accounts_rece_yoy") if balance else None
        revenue_yoy = (
            _metric(abstract["metrics"], "营业总收入增长率", "营业收入增长率")
            if abstract
            else None
        )
        trend_warn = (
            ar_yoy is not None
            and revenue_yoy is not None
            and ar_yoy > revenue_yoy + 30
        )
        if trend_warn:
            if level == "ok":
                level = "warn"
            message += (
                f"；应收账款同比 {ar_yoy:.1f}%，快于营收同比 "
                f"{revenue_yoy:.1f}% 超过 30 个百分点"
            )

        detail = (
            f"应收账款 {_format_yi(accounts_rece)} / 营业总收入 "
            f"{_format_yi(revenue)}"
        )
        if ar_yoy is not None and revenue_yoy is not None:
            detail += f"；应收账款同比 {ar_yoy:.1f}%，营收同比 {revenue_yoy:.1f}%"
        if source_note:
            detail += f"（{source_note}）"
        checks.append(
            _flag(
                "receivables",
                "应收账款",
                level,
                round(ratio, 2),
                message,
                detail,
            )
        )

    # ------------------------------------------------------------------
    # d) 经营现金流 / 净利润
    # ------------------------------------------------------------------
    ocf = (
        _metric(abstract["metrics"], "经营现金流量净额") if abstract else None
    )
    net_profit = (
        _metric(abstract["metrics"], "归母净利润", "净利润")
        if abstract
        else None
    )
    if ocf is None or net_profit is None:
        checks.append(
            _flag(
                "operating_cashflow",
                "经营现金流",
                "ok",
                None,
                "数据不足，无法判断",
                "未取得经营现金流或净利润数据",
            )
        )
        warnings.append("经营现金流或净利润数据不可用，无法判断利润含金量。")
    elif net_profit > 0 and ocf < 0:
        ratio = ocf / net_profit
        message = (
            f"经营现金流为负（{_format_yi(ocf)}），净利润为正"
            f"（{_format_yi(net_profit)}），利润未转化为现金"
        )
        checks.append(
            _flag(
                "operating_cashflow",
                "经营现金流",
                "danger",
                round(ratio, 2),
                message,
                f"（{source_note}）" if source_note else "",
            )
        )
    elif net_profit > 0:
        ratio = ocf / net_profit
        if ratio < 0.5:
            level = "warn"
            message = (
                f"经营现金流/净利润 {ratio:.2f}，低于 0.5，利润含金量偏低"
            )
        else:
            level = "ok"
            message = f"经营现金流/净利润 {ratio:.2f}，利润现金含量正常"
        checks.append(
            _flag(
                "operating_cashflow",
                "经营现金流",
                level,
                round(ratio, 2),
                message,
                f"经营现金流 {_format_yi(ocf)} / 净利润 "
                f"{_format_yi(net_profit)}"
                + (f"（{source_note}）" if source_note else ""),
            )
        )
    else:
        ratio = ocf / net_profit if net_profit != 0 else None
        message = (
            f"净利润为负（{_format_yi(net_profit)}），现金流/净利润比率不适用；"
            f"经营现金流 {_format_yi(ocf)}"
        )
        checks.append(
            _flag(
                "operating_cashflow",
                "经营现金流",
                "ok",
                round(ratio, 2) if ratio is not None else None,
                message,
                f"（{source_note}）" if source_note else "",
            )
        )

    score_map = {"ok": 0, "warn": 50, "danger": 100}
    risk_score = int(
        round(sum(score_map[check["level"]] for check in checks) / len(checks))
    )
    if risk_score >= 70:
        level = "高风险"
    elif risk_score >= 30:
        level = "关注"
    else:
        level = "低风险"

    triggered = [check for check in checks if check["level"] in ("warn", "danger")]
    missing = [check for check in checks if check["value"] is None]
    if triggered:
        body = "、".join(
            f"{check['name']}（{check['message']}，"
            f"{_LEVEL_LABEL[check['level']]}）"
            for check in triggered
        )
        summary = f"检测到 4 项中的 {len(triggered)} 项需要关注：{body}。"
    else:
        summary = "4 项财务排雷指标均未触发。"
    if missing:
        summary += f" 另有 {len(missing)} 项数据不足，未参与判断。"

    result = {
        "success": True,
        "ticker": code,
        "as_of": cutoff.isoformat(),
        "checks": checks,
        "risk_score": risk_score,
        "level": level,
        "summary": summary,
        "warnings": warnings,
    }
    _cache_set(_RESULT_CACHE, cache_key, result)
    return result


# ============================================================================
# 7. 事件日历
# ============================================================================

_EVENT_BRIEF = {
    "解禁": "限售解禁",
    "财报披露": "财报披露",
    "除权除息": "除权除息",
    "业绩预告": "业绩预告窗口",
}


def _forecast_deadline(period):
    """按报告期推算业绩预告的法定披露截止日（保守规则）。"""
    if period.month == 12:
        return date(period.year + 1, 1, 31)
    if period.month == 6:
        return date(period.year, 7, 15)
    if period.month == 9:
        return date(period.year, 10, 15)
    if period.month == 3:
        return date(period.year, 4, 15)
    return None


def _next_forecast_window(today):
    """返回下一个业绩预告披露窗口 (deadline, report_period)；没有则 None。"""
    candidates = []
    for year in (today.year, today.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            period = date(year, month, day)
            deadline = _forecast_deadline(period)
            if deadline and deadline >= today:
                candidates.append((deadline, period))
    return min(candidates) if candidates else None


def build_event_calendar(ticker, days=30, today=None) -> dict:
    """把解禁、财报披露、除权除息、业绩预告窗口合并为未来事件日历。

    WHY：单看某一类事件容易低估“事件叠加”的冲击。例如某股在 20 天内
    同时遇到解禁 + 三季报披露 + 除权除息，抛压与不确定性会共振。
    本函数复用上面的取数函数，按日期排序输出统一结构，供 Agent 直接
    注入提示词或 WebUI 渲染时间轴。

    业绩预告事件使用**法定披露窗口**（年报 1/31、中报 7/15、三季报
    10/15、一季报 4/15）估算：只有业绩触及预告条件时才强制披露，
    因此它是“窗口”而不是确定的公司公告日。

    参数：
      ticker: A 股代码或名称
      days: 向前看的天数，默认 30
      today: 基准日（date 或 YYYY-MM-DD），默认今天

    返回：
      dict，包含 success/ticker/as_of/horizon_days/events/counts/
      summary/warnings。event 含 date/type/title/detail/level。
    """
    raw = str(ticker or "").strip()
    code = _resolve_code(raw)
    if not is_a_share_code(code):
        return _invalid_ticker(raw)

    try:
        _require_akshare()
    except ImportError as exc:
        return _failure(code, str(exc))

    base_date = _to_date(today) if today else date.today()
    if base_date is None:
        return _failure(code, f"无法解析 today='{today}'，请使用 YYYY-MM-DD。")

    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 30

    months = max(1, (days + 29) // 30)
    warnings = []
    events = []
    counts = {"解禁": 0, "财报披露": 0, "除权除息": 0, "业绩预告": 0}

    # ---- 解禁 ----
    try:
        lockup = get_lockup_expiry(code, months=months, today=base_date)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        lockup = _failure(code, str(exc))
    warnings.extend(f"解禁：{text}" for text in lockup.get("warnings", []))
    for item in lockup.get("items", []):
        if not _in_window(item.get("release_date"), base_date, days):
            continue
        release = _to_date(item["release_date"])
        delta = (release - base_date).days
        title = "限售解禁"
        if item.get("shares") is not None:
            title += f" {item['shares'] / 1e4:.1f} 万股"
        detail_bits = []
        if item.get("ratio") is not None:
            label = (item.get("ratio_name") or "").removeprefix("占").removesuffix(
                "比例"
            )
            detail_bits.append(
                f"占{label or '总股本'} {item['ratio'] * 100:.2f}%"
            )
        if item.get("share_type"):
            detail_bits.append(item["share_type"])
        events.append(
            {
                "date": item["release_date"],
                "type": "解禁",
                "title": title,
                "detail": "，".join(detail_bits),
                "level": "warn" if delta <= 14 else "info",
            }
        )
        counts["解禁"] += 1

    # ---- 财报披露（当前期 + 下一期，找落在窗口内的预约日）----
    disclosure = None
    disclosure_ok = False
    for label in _disclosure_period_candidates(base_date):
        try:
            candidate = get_report_disclosure_calendar(code, period=label)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            warnings.append(f"财报披露：{label} 获取失败：{type(exc).__name__}")
            continue
        if candidate.get("success"):
            disclosure_ok = True
        warnings.extend(
            f"财报披露：{text}" for text in candidate.get("warnings", [])
        )
        if candidate.get("success") and any(
            _in_window(item.get("disclose_date"), base_date, days)
            for item in candidate.get("items", [])
        ):
            disclosure = candidate
            break
        if disclosure is None and candidate.get("success"):
            disclosure = candidate

    if disclosure:
        for item in disclosure.get("items", []):
            if not _in_window(item.get("disclose_date"), base_date, days):
                continue
            title = f"{disclosure['period']} 预约披露"
            detail_bits = []
            if item.get("actual_date"):
                detail_bits.append(f"实际披露 {item['actual_date']}")
            if item.get("change_reason"):
                detail_bits.append(item["change_reason"])
            events.append(
                {
                    "date": item["disclose_date"],
                    "type": "财报披露",
                    "title": title,
                    "detail": "，".join(detail_bits),
                    "level": "info",
                }
            )
            counts["财报披露"] += 1

    # ---- 除权除息 ----
    try:
        dividend = get_dividend_schedule(code, days=days, today=base_date)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        dividend = _failure(code, str(exc))
    warnings.extend(f"除权除息：{text}" for text in dividend.get("warnings", []))
    for item in dividend.get("items", []):
        if not _in_window(item.get("ex_date"), base_date, days):
            continue
        title = "除权除息"
        if item.get("cash_per_share") is not None:
            title += f"（每 10 股派 {item['cash_per_share']:.2f} 元）"
        detail_bits = []
        if item.get("record_date"):
            detail_bits.append(f"股权登记日 {item['record_date']}")
        if item.get("progress"):
            detail_bits.append(f"方案进度：{item['progress']}")
        events.append(
            {
                "date": item["ex_date"],
                "type": "除权除息",
                "title": title,
                "detail": "，".join(detail_bits),
                "level": "info",
            }
        )
        counts["除权除息"] += 1

    # ---- 业绩预告窗口 ----
    # 只有法定窗口落在当前视野内才查询个股预告，避免每次构建 30 天日历
    # 都触发 6 个报告期的全市场请求。
    window = _next_forecast_window(base_date)
    forecast = {"success": True, "latest": None, "warnings": []}
    if window and _in_window(window[0].isoformat(), base_date, days):
        try:
            forecast = get_earnings_forecast(
                code, curr_date=base_date.isoformat()
            )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            forecast = _failure(code, str(exc))
        warnings.extend(
            f"业绩预告：{text}" for text in forecast.get("warnings", [])
        )

        deadline, period = window
        title = (
            "业绩预告披露窗口"
            f"（{_report_period_label(period.isoformat())}）"
        )
        detail = (
            "A 股业绩预告为强制披露制度：业绩大幅变动需在此前公告，"
            "窗口前后常出现博弈与波动"
        )
        if forecast.get("latest"):
            detail += f"；最近已披露：{forecast['summary']}"
        events.append(
            {
                "date": deadline.isoformat(),
                "type": "业绩预告",
                "title": title,
                "detail": detail,
                "level": "info",
            }
        )
        counts["业绩预告"] += 1

    events.sort(key=lambda item: (item["date"], item["type"]))

    sub_success = [
        bool(lockup.get("success")),
        disclosure_ok,
        bool(dividend.get("success")),
        bool(forecast.get("success")),
    ]
    if not any(sub_success):
        result = _failure(
            code, "事件日历构建失败：解禁、披露、分红、业绩预告接口均不可用。", warnings
        )
        result.update(
            {
                "as_of": base_date.isoformat(),
                "horizon_days": days,
                "events": [],
                "counts": counts,
            }
        )
        return result

    if not events:
        summary = (
            f"未来 {days} 天暂无解禁、财报披露、除权除息或业绩预告窗口。"
        )
    else:
        briefs = []
        for event in events[:5]:
            event_date = _to_date(event["date"])
            delta = (event_date - base_date).days
            suffix = "（今天）" if delta == 0 else f"（{delta} 天后）"
            briefs.append(
                f"{event_date.strftime('%m-%d')} "
                f"{_EVENT_BRIEF.get(event['type'], event['type'])}{suffix}"
            )
        summary = f"未来 {days} 天有 {len(events)} 个事件：" + "、".join(briefs)
        if len(events) > 5:
            summary += f" 等共 {len(events)} 个"
        summary += "。"

    return {
        "success": True,
        "ticker": code,
        "as_of": base_date.isoformat(),
        "horizon_days": days,
        "events": events,
        "counts": counts,
        "summary": summary,
        "warnings": warnings,
    }

