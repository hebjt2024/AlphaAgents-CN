"""东方财富 / 腾讯的直连备用源（不经过 AkShare）。

WHY 需要一条"直连"通道
----------------------
AkShare 把东方财富的多个**不同域名**封装成了同一个 `akshare` 数据源：

- ``push2.eastmoney.com``：实时快照、资金流排行
- ``push2his.eastmoney.com``：日线 / 分钟线 / 个股资金流（daykline）
- ``datacenter-web.eastmoney.com``：龙虎榜、北向、财务数据

这几个域名**互相独立**（限流通常按域名/接口分桶）。实测中经常出现
``push2`` 被限流、而 ``push2his`` / ``datacenter-web`` 仍然可用的情况。
路由层只把 ``akshare`` 当成一个数据源，于是"一个域名挂了"就直接判定
"资金流没有可用数据源"。

本模块提供**绕过 AkShare 的直连实现**，把它们作为独立的备用数据源接入
降级链，从而获得真正的多源冗余：

- :func:`get_em_capital_flow`  → push2his（个股资金流日线）
- :func:`get_em_dragon_tiger`  → datacenter-web（龙虎榜）
- :func:`get_tencent_quotes`   → qt.gtimg.cn（实时行情快照）

设计约束
--------
1. **只做一次尝试**：不发散重试，重试策略交给路由层的熔断器，
   避免限流时被放大成十几次请求。
2. **短超时**：默认 8 秒，可用 ``DIRECT_SOURCE_TIMEOUT`` 覆盖。
3. **绝不伪造**：取不到就抛 :class:`DirectSourceError`，让路由层如实降级。
4. **口径一致**：输出格式与 AkShare 版本保持一致，避免 LLM 看到两套字段。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: 单次请求超时（秒）。
TIMEOUT = float(os.getenv("DIRECT_SOURCE_TIMEOUT", "8") or 8)

_EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_FFLOW = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_TENCENT_QUOTE = "https://qt.gtimg.cn/q="

#: 东方财富公开 token（网页端固定值，用于访问行情接口）。
_EM_UT = "b2884a393a59ad64002292a3e90d46a5"


class DirectSourceError(RuntimeError):
    """直连备用源取数失败。"""


class DirectSourceRateLimited(DirectSourceError):
    """直连备用源被限流（或限流期间被直接断开连接）。"""


#: 限流特征：东方财富限流时会直接断开连接（RemoteDisconnected），
#: 不返回 429，因此必须按特征字符串识别，否则会被误判成普通网络故障，
#: 用更短的冷却时间反复重试，反而延长封禁。
_RATE_LIMIT_HINTS = (
    "429",
    "too many requests",
    "rate limit",
    "remotedisconnected",
    "connection aborted",
    "connection reset",
    "限流",
    "频繁",
)


def _is_rate_limit_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(hint in lowered for hint in _RATE_LIMIT_HINTS)


def _get(url: str, params: dict[str, Any] | None = None) -> requests.Response:
    """发一次 GET（短超时 + 浏览器 UA），失败抛 DirectSourceError。"""
    try:
        response = requests.get(
            url,
            params=params,
            timeout=TIMEOUT,
            headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"},
        )
        response.raise_for_status()
        return response
    except Exception as exc:  # noqa: BLE001 - 统一转成直连失败，交给路由层降级
        detail = f"{type(exc).__name__}: {exc}"
        if _is_rate_limit_error(detail):
            raise DirectSourceRateLimited(detail) from exc
        raise DirectSourceError(detail) from exc


def _exchange(code: str) -> str:
    from .ashare_rules import exchange_of

    return exchange_of(code)


def _secid(code: str) -> str:
    """六位代码 → 东方财富 secid（沪市 1.xxx，深市/北交所 0.xxx）。"""
    return f"{1 if _exchange(code) == 'sh' else 0}.{code}"


def _format_amount(value: Any) -> str:
    """把金额格式化为亿元/万元（与 AkShare 适配层口径一致）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 1e8:
        return f"{number / 1e8:.2f} 亿元"
    if abs(number) >= 1e4:
        return f"{number / 1e4:.2f} 万元"
    return f"{number:.2f}"


# ---------------------------------------------------------------------------
# 个股资金流（push2his daykline）
# ---------------------------------------------------------------------------
#: daykline 每行字段顺序（与 AkShare stock_individual_fund_flow 一致）。
_FFLOW_FIELDS = (
    "日期",
    "主力净流入-净额",
    "小单净流入-净额",
    "中单净流入-净额",
    "大单净流入-净额",
    "超大单净流入-净额",
    "主力净流入-净占比",
    "小单净流入-净占比",
    "中单净流入-净占比",
    "大单净流入-净占比",
    "超大单净流入-净占比",
    "收盘价",
    "涨跌幅",
)


def get_em_capital_flow(
    ticker: str,
    curr_date: str | None = None,
    look_back_days: int = 20,
) -> str:
    """直连获取个股资金流（绕过 AkShare）。

    与 AkShare 实现保持相同的输出格式，便于上层无感切换。
    严格只返回 ``curr_date`` 当日及之前的数据，避免未来函数。
    """
    from .ashare_rules import is_a_share_code, normalize_code

    code = normalize_code(str(ticker or ""))
    if not code or not is_a_share_code(code):
        return f"[不适用] '{ticker}' 不是 A 股标的，无资金流向数据"

    params = {
        "lmt": "0",
        "klt": "101",
        "secid": _secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": _EM_UT,
        "_": int(datetime.now().timestamp() * 1000),
    }
    response = _get(_EM_FFLOW, params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise DirectSourceError(f"资金流返回非 JSON：{exc}") from exc
    klines = ((payload or {}).get("data") or {}).get("klines") or []
    if not klines:
        return f"No capital flow data found for '{ticker}'"

    rows: list[dict[str, str]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < len(_FFLOW_FIELDS):
            continue
        rows.append(dict(zip(_FFLOW_FIELDS, parts[: len(_FFLOW_FIELDS)], strict=False)))
    if not rows:
        return f"No capital flow data found for '{ticker}'"

    # 日期过滤：只保留分析日及之前，绝不把未来数据交给 LLM
    if curr_date:
        try:
            cutoff = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            cutoff = None
        if cutoff is not None:
            filtered = []
            for row in rows:
                try:
                    row_date = datetime.strptime(row["日期"][:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if row_date <= cutoff:
                    filtered.append(row)
            rows = filtered
            if not rows:
                return (
                    f"No capital flow data found for '{ticker}' "
                    f"before {curr_date} (source only keeps recent data)"
                )

    rows.sort(key=lambda row: row["日期"], reverse=True)
    rows = rows[: max(1, int(look_back_days))]

    net_values: list[float] = []
    for row in rows:
        try:
            net_values.append(float(row["主力净流入-净额"]))
        except (TypeError, ValueError):
            continue

    header = f"# Capital Flow data for {code} (A-share main capital flow)\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# 数据源: 东方财富 push2his 直连（AkShare 之外的备用通道）\n"
    if net_values:
        header += f"# 最近{len(net_values)}个交易日主力净流入合计: {_format_amount(sum(net_values))}\n"
        header += f"# 最新交易日主力净流入: {_format_amount(net_values[0])}\n"
    header += "# Unit: CNY. 正值=净流入，负值=净流出。\n\n"

    columns = [
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
    body = [",".join(columns)]
    for row in rows:
        body.append(",".join(str(row.get(column, "")) for column in columns))
    return header + "\n".join(body) + "\n"


# ---------------------------------------------------------------------------
# 龙虎榜（datacenter-web）
# ---------------------------------------------------------------------------
#: datacenter 字段 → 中文列名（与 AkShare stock_lhb_detail_em 对齐）。
_LHB_COLUMNS = {
    "TRADE_DATE": "上榜日",
    "SECURITY_CODE": "代码",
    "SECURITY_NAME_ABBR": "名称",
    "CLOSE_PRICE": "收盘价",
    "CHANGE_RATE": "涨跌幅",
    "BILLBOARD_NET_AMT": "龙虎榜净买额",
    "BILLBOARD_BUY_AMT": "龙虎榜买入额",
    "BILLBOARD_SELL_AMT": "龙虎榜卖出额",
    "TURNOVERRATE": "换手率",
    "EXPLANATION": "上榜原因",
    "D1_CLOSE_ADJCHRATE": "上榜后1日",
    "D5_CLOSE_ADJCHRATE": "上榜后5日",
    "D10_CLOSE_ADJCHRATE": "上榜后10日",
}


def get_em_dragon_tiger(
    ticker: str,
    curr_date: str | None = None,
    look_back_days: int = 60,
) -> str:
    """直连获取个股龙虎榜（datacenter-web，与 push2 是不同域名）。"""
    from .ashare_rules import is_a_share_code, normalize_code

    code = normalize_code(str(ticker or ""))
    if not code or not is_a_share_code(code):
        return f"[不适用] '{ticker}' 不是 A 股标的，无龙虎榜数据"

    if curr_date:
        try:
            end_dt = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d")
        except ValueError:
            end_dt = datetime.now()
    else:
        end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=max(1, int(look_back_days)))
    filter_text = (
        f'(SECURITY_CODE="{code}")'
        f"(TRADE_DATE<='{end_dt.strftime('%Y-%m-%d')}')"
        f"(TRADE_DATE>='{start_dt.strftime('%Y-%m-%d')}')"
    )
    params = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": "ALL",
        "pageNumber": "1",
        "pageSize": "20",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "filter": filter_text,
        "source": "WEB",
        "client": "WEB",
    }
    response = _get(_EM_DATACENTER, params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise DirectSourceError(f"龙虎榜返回非 JSON：{exc}") from exc

    rows = ((payload or {}).get("result") or {}).get("data") or []
    if not rows:
        return (
            f"No dragon-tiger (LHB) data found for '{code}' in "
            f"{start_dt.strftime('%Y-%m-%d')}~{end_dt.strftime('%Y-%m-%d')}"
        )

    summary: list[str] = []
    net_total = 0.0
    has_net = False
    for row in rows:
        try:
            net_total += float(row.get("BILLBOARD_NET_AMT"))
            has_net = True
        except (TypeError, ValueError):
            continue
    if has_net:
        summary.append(f"最近上榜次数: {len(rows)} 次")
        summary.append(f"区间龙虎榜净买额合计: {_format_amount(net_total)}")

    header = f"# Dragon-Tiger (LHB) data for {code}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# 数据源: 东方财富 datacenter-web 直连（AkShare 之外的备用通道）\n"
    if summary:
        header += "# " + "\n# ".join(summary) + "\n"
    header += "# 金额单位: 元。正值=净买入，负值=净卖出。\n\n"

    columns = list(_LHB_COLUMNS.values())
    body = [",".join(columns)]
    for row in rows:
        body.append(
            ",".join(
                _lhb_cell(row.get(key), key) for key in _LHB_COLUMNS
            )
        )
    return header + "\n".join(body) + "\n"


def _lhb_cell(value: Any, key: str) -> str:
    """把 datacenter 的原始值格式化为 CSV 单元格。"""
    if value is None:
        return ""
    if key == "TRADE_DATE":
        return str(value)[:10]
    if key in ("SECURITY_CODE",):
        return str(value).zfill(6)
    text = str(value)
    if "," in text:
        text = text.replace(",", "，")
    return text


# ---------------------------------------------------------------------------
# 腾讯实时行情（qt.gtimg.cn）
# ---------------------------------------------------------------------------
#: 腾讯行情字段下标 → 输出字段。
#: 依据 ``v_sh601118="1~名称~代码~当前价~昨收~今开~成交量(手)~...~时间~涨跌~涨跌%~最高~最低~...``
#: 的 ``~`` 分隔格式（下标为实测校准值）。
_TENCENT_FIELDS = {
    1: "name",
    3: "price",
    4: "prev_close",
    5: "open",
    6: "volume",          # 成交量（手）
    31: "change",
    32: "change_pct",
    33: "high",
    34: "low",
    37: "amount",         # 成交额（万元）
    38: "turnover",       # 换手率 %
    39: "pe_ttm",
    43: "amplitude",      # 振幅 %
    44: "float_market_cap",  # 流通市值（亿元）
    45: "market_cap",     # 总市值（亿元）
    46: "pb",
}


def _tencent_symbol(code: str) -> str:
    exchange = _exchange(code)
    return f"{exchange}{code}" if exchange else code


def get_tencent_quotes(tickers: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """直连获取实时快照（腾讯行情，与东方财富完全独立）。

    返回 ``{六位代码: quote}``；取不到时返回空字典（调用方自行决定降级）。
    """
    from .ashare_rules import normalize_code

    codes: list[str] = []
    for item in tickers or []:
        code = normalize_code(str(item))
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return {}

    # 腾讯一次请求支持多只，分批避免 URL 过长
    quotes: dict[str, dict[str, Any]] = {}
    for start in range(0, len(codes), 50):
        batch = codes[start : start + 50]
        url = _TENCENT_QUOTE + ",".join(_tencent_symbol(code) for code in batch)
        try:
            response = requests.get(
                url, timeout=TIMEOUT, headers={"User-Agent": _UA}
            )
            response.encoding = "gbk"
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.debug("腾讯行情获取失败: %s", exc)
            continue
        for line in response.text.splitlines():
            parsed = _parse_tencent_line(line)
            if parsed:
                quotes[parsed["ticker"]] = parsed
    return quotes


def _parse_tencent_line(line: str) -> dict[str, Any] | None:
    """解析形如 ``v_sh601118="1~海南橡胶~601118~5.31~...";`` 的一行。"""
    line = line.strip()
    if not line or "=" not in line or '"' not in line:
        return None
    left, _, right = line.partition("=")
    variable = left.strip().removeprefix("v_")
    if len(variable) < 8:
        return None
    code = variable[-6:]
    payload = right.strip().strip(";").strip('"')
    fields = payload.split("~")
    if len(fields) < 40:
        return None

    def value(index: int) -> Any:
        if index >= len(fields):
            return None
        raw = fields[index]
        if raw in ("", "-"):
            return None
        try:
            return float(raw)
        except ValueError:
            return raw

    quote: dict[str, Any] = {
        "ticker": code,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source": "tencent",
    }
    for index, key in _TENCENT_FIELDS.items():
        quote[key] = value(index)
    # 腾讯的名称字段是中文名，其余为数值；缺失字段返回 None，由调用方处理。
    return quote


__all__ = [
    "DirectSourceError",
    "DirectSourceRateLimited",
    "get_em_capital_flow",
    "get_em_dragon_tiger",
    "get_tencent_quotes",
]
