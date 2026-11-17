"""市场复盘：指数、情绪、行业热度与涨停分布。

数据来自 AkShare 的 Sina / 乐咕 / 东财接口，均为实时快照；
接口不可用时降级返回已获取的部分，并在 warnings 中说明。
"""

from __future__ import annotations

import copy
import time
from datetime import datetime
from typing import Any

INDEX_CODES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
]

_CACHE_TTL = 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _ak():
    from tradingagents.dataflows import akshare_adapter

    if akshare_adapter.ak is None:
        raise ImportError("未安装 akshare，无法获取市场复盘数据。")
    return akshare_adapter.ak


def _call(func, *args, **kwargs):
    from tradingagents.dataflows.akshare_adapter import retry_with_backoff

    return retry_with_backoff(
        lambda: func(*args, **kwargs),
        max_retries=1,
        base_delay=1.0,
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _warning(prefix: str, exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return f"{prefix}：{type(exc).__name__}: {text[:120]}"


def _fetch_indices(ak) -> list[dict[str, Any]]:
    index_func = getattr(ak, "index_zh_spot_sina", None) or getattr(
        ak, "stock_zh_index_spot_sina", None
    )
    if index_func is None:
        return []
    frame = _call(index_func)
    if frame is None or frame.empty:
        return []
    result = []
    for code, fallback_name in INDEX_CODES:
        rows = frame[frame["代码"] == code]
        if rows.empty:
            continue
        row = rows.iloc[0]
        result.append(
            {
                "code": code,
                "name": str(row.get("名称") or fallback_name),
                "close": round(_num(row.get("最新价")), 2),
                "change_pct": round(_num(row.get("涨跌幅")), 2),
                "turnover_yi": round(_num(row.get("成交额")) / 1e8, 1),
            }
        )
    return result


def _fetch_breadth(ak) -> dict[str, Any]:
    frame = _call(ak.stock_market_activity_legu)
    if frame is None or frame.empty:
        return {}
    items = {
        str(row.get("item") or "").strip(): row.get("value")
        for _, row in frame.iterrows()
    }

    def number(key: str) -> int:
        return int(_num(items.get(key)))

    up = number("上涨")
    down = number("下跌")
    flat = number("平盘")
    total = up + down + flat
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "up_ratio": round(up / total, 4) if total else 0.0,
        "limit_up": number("涨停"),
        "real_limit_up": number("真实涨停"),
        "limit_down": number("跌停"),
        "real_limit_down": number("真实跌停"),
        "suspended": number("停牌"),
        "activity": str(items.get("活跃度") or ""),
        "stat_time": str(items.get("统计日期") or ""),
    }


def _fetch_sector_group(ak, indicator: str) -> dict[str, Any]:
    frame = _call(ak.stock_sector_spot, indicator=indicator)
    if frame is None or frame.empty:
        return {"total": 0, "top": [], "bottom": []}

    sectors = []
    for _, row in frame.iterrows():
        name = str(row.get("板块") or "").strip()
        if not name:
            continue
        sectors.append(
            {
                "name": name,
                "change_pct": round(_num(row.get("涨跌幅")), 2),
                "turnover_yi": round(_num(row.get("总成交额")) / 1e8, 1),
                "stock_count": int(_num(row.get("公司家数"))),
                "leader": str(row.get("股票名称") or "").strip(),
                "leader_change_pct": round(_num(row.get("个股-涨跌幅")), 2),
            }
        )

    sectors.sort(key=lambda item: item["change_pct"], reverse=True)
    return {
        "total": len(sectors),
        "top": sectors[:5],
        "bottom": list(reversed(sectors[-5:])),
    }


def _fetch_sectors(ak) -> dict[str, Any]:
    industry = _fetch_sector_group(ak, "行业")
    try:
        concept = _fetch_sector_group(ak, "概念")
    except Exception:
        concept = {"total": 0, "top": [], "bottom": []}
    return {
        "total": industry["total"],
        "top": industry["top"],
        "bottom": industry["bottom"],
        "concept": concept,
    }


def _fetch_fund_flow(ak) -> dict[str, Any]:
    flow_func = getattr(ak, "stock_fund_flow_industry_ths", None) or getattr(
        ak, "stock_fund_flow_industry", None
    )
    if flow_func is None:
        return {"top": [], "bottom": []}
    frame = _call(flow_func, symbol="即时")
    if frame is None or frame.empty:
        return {"top": [], "bottom": []}

    items = []
    for _, row in frame.iterrows():
        name = str(row.get("行业") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "change_pct": round(_num(row.get("行业-涨跌幅")), 2),
                "net_yi": round(_num(row.get("净额")), 2),
                "inflow_yi": round(_num(row.get("流入资金")), 2),
                "outflow_yi": round(_num(row.get("流出资金")), 2),
                "stock_count": int(_num(row.get("公司家数"))),
                "leader": str(row.get("领涨股") or "").strip(),
                "leader_change_pct": round(_num(row.get("领涨股-涨跌幅")), 2),
            }
        )

    items.sort(key=lambda item: item["net_yi"], reverse=True)
    return {
        "top": items[:5],
        "bottom": list(reversed(items[-5:])),
    }


def _fetch_limit_up_industries(ak, as_of: str) -> list[dict[str, Any]]:
    date = (as_of or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
    frame = _call(ak.stock_zt_pool_em, date=date)
    if frame is None or frame.empty or "所属行业" not in frame.columns:
        return []
    counts = frame["所属行业"].fillna("其他").astype(str).value_counts()
    return [
        {"name": str(name), "count": int(count)}
        for name, count in counts.head(5).items()
    ]


def _flow_label(item: dict[str, Any] | None) -> str:
    if not item or not item.get("name"):
        return ""
    return f"{item['name']}（{float(item.get('net_yi') or 0):+.2f} 亿）"


def _build_summary(breadth: dict[str, Any], sectors: dict[str, Any]) -> dict[str, Any]:
    if not breadth:
        return {"tone": "未知", "text": "市场情绪数据暂不可用。", "up_ratio": 0.0}

    up_ratio = float(breadth.get("up_ratio") or 0)
    limit_up = int(breadth.get("limit_up") or 0)
    limit_down = int(breadth.get("limit_down") or 0)
    if up_ratio >= 0.6 and limit_up >= max(1, limit_down * 2):
        tone = "偏强"
    elif up_ratio <= 0.4:
        tone = "偏弱"
    else:
        tone = "震荡"

    top = (sectors.get("top") or [{}])[0]
    bottom = (sectors.get("bottom") or [{}])[0]
    text = (
        f"市场{tone}：上涨 {breadth.get('up', 0)} 家 / 下跌 {breadth.get('down', 0)} 家，"
        f"涨停 {limit_up} 家、跌停 {limit_down} 家。"
    )
    if top.get("name"):
        text += (
            f"领涨行业为 {top['name']}（{top.get('change_pct', 0):+.2f}%），"
        )
    if bottom.get("name"):
        text += f"领跌行业为 {bottom['name']}（{bottom.get('change_pct', 0):+.2f}%）。"
    return {"tone": tone, "text": text, "up_ratio": up_ratio}


def build_market_review(date: str | None = None) -> dict[str, Any]:
    """生成市场复盘快照。``date`` 仅用于提示，数据始终为实时快照。"""
    key = date or "latest"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return copy.deepcopy(cached[1])

    ak = _ak()
    warnings: list[str] = []
    indices: list[dict[str, Any]] = []
    breadth: dict[str, Any] = {}
    sectors: dict[str, Any] = {
        "total": 0,
        "top": [],
        "bottom": [],
        "concept": {"total": 0, "top": [], "bottom": []},
    }
    fund_flow: dict[str, Any] = {"top": [], "bottom": []}
    limit_up_industries: list[dict[str, Any]] = []

    try:
        indices = _fetch_indices(ak)
    except Exception as exc:
        warnings.append(_warning("指数数据获取失败", exc))

    try:
        breadth = _fetch_breadth(ak)
    except Exception as exc:
        warnings.append(_warning("市场情绪获取失败", exc))

    try:
        sectors = _fetch_sectors(ak)
    except Exception as exc:
        warnings.append(_warning("行业数据获取失败", exc))

    try:
        fund_flow = _fetch_fund_flow(ak)
    except Exception as exc:
        warnings.append(_warning("行业资金流获取失败", exc))

    as_of = str(breadth.get("stat_time") or "")[:10]
    if not as_of:
        as_of = datetime.now().strftime("%Y-%m-%d")

    try:
        limit_up_industries = _fetch_limit_up_industries(ak, as_of)
    except Exception as exc:
        warnings.append(_warning("涨停行业获取失败", exc))

    if date and date[:10] != as_of:
        warnings.append(
            f"复盘数据为实时快照，无法回溯 {date[:10]}；当前数据日期为 {as_of}。"
        )

    summary = _build_summary(breadth, sectors)
    summary["top_inflow"] = _flow_label((fund_flow.get("top") or [None])[0])
    summary["top_outflow"] = _flow_label((fund_flow.get("bottom") or [None])[0])
    result = {
        "success": bool(indices or breadth or sectors.get("total")),
        "as_of": as_of,
        "requested_date": date[:10] if date else "",
        "indices": indices,
        "breadth": breadth,
        "sectors": sectors,
        "fund_flow": fund_flow,
        "limit_up_industries": limit_up_industries,
        "summary": summary,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": "市场复盘为实时数据快照，仅供研究参考，不构成投资建议。",
    }
    result["markdown"] = format_market_review_markdown(result)
    _cache[key] = (now, result)
    return copy.deepcopy(result)


def send_market_review(date: str | None = None) -> dict[str, Any]:
    """生成并推送市场复盘到已配置的 Webhook 渠道。"""
    review = build_market_review(date)
    if not review.get("success"):
        review["notify"] = {
            "skipped": review.get("message") or "市场复盘数据不可用"
        }
        return review

    from tradingagents.notify import send_notification

    title = f"市场复盘 {review.get('as_of', '')}".strip()
    text = review.get("markdown") or format_market_review_markdown(review)
    review["notify"] = send_notification(title, text)
    return review


def format_market_review_markdown(review: dict[str, Any]) -> str:
    """把复盘结果格式化为 Markdown。"""
    as_of = review.get("as_of") or datetime.now().strftime("%Y-%m-%d")
    lines = [f"## 市场复盘 {as_of}", ""]

    indices = review.get("indices") or []
    if indices:
        lines.extend(
            [
                "### 指数表现",
                "| 指数 | 收盘 | 涨跌幅 | 成交额 |",
                "|---|---:|---:|---:|",
            ]
        )
        for item in indices:
            lines.append(
                f"| {item.get('name', '')} | {item.get('close', 0):.2f} | "
                f"{item.get('change_pct', 0):+.2f}% | "
                f"{item.get('turnover_yi', 0):.1f} 亿 |"
            )
        lines.append("")

    breadth = review.get("breadth") or {}
    if breadth:
        lines.extend(
            [
                "### 市场情绪",
                f"- 上涨 {breadth.get('up', 0)} 家 / 下跌 {breadth.get('down', 0)} 家"
                f" / 平盘 {breadth.get('flat', 0)} 家",
                f"- 涨停 {breadth.get('limit_up', 0)} 家"
                f"（真实 {breadth.get('real_limit_up', 0)} 家）"
                f" / 跌停 {breadth.get('limit_down', 0)} 家"
                f"（真实 {breadth.get('real_limit_down', 0)} 家）",
                f"- 活跃度：{breadth.get('activity', '-')}",
                "",
            ]
        )

    sectors = review.get("sectors") or {}
    concept = sectors.get("concept") or {}
    for title, items in (
        ("行业领涨", sectors.get("top") or []),
        ("行业领跌", sectors.get("bottom") or []),
        ("概念领涨", concept.get("top") or []),
        ("概念领跌", concept.get("bottom") or []),
    ):
        if not items:
            continue
        lines.extend(
            [
                f"### {title}",
                "| 行业 | 涨跌幅 | 成交额 | 领涨股 |",
                "|---|---:|---:|---|",
            ]
        )
        for item in items:
            lines.append(
                f"| {item.get('name', '')} | {item.get('change_pct', 0):+.2f}% | "
                f"{item.get('turnover_yi', 0):.1f} 亿 | "
                f"{item.get('leader', '')} "
                f"({item.get('leader_change_pct', 0):+.2f}%) |"
            )
        lines.append("")

    fund_flow = review.get("fund_flow") or {}
    for title, items in (
        ("资金净流入居前", fund_flow.get("top") or []),
        ("资金净流出居前", fund_flow.get("bottom") or []),
    ):
        if not items:
            continue
        lines.extend(
            [
                f"### {title}",
                "| 行业 | 净额 | 流入 | 流出 | 涨跌幅 | 领涨股 |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for item in items:
            lines.append(
                f"| {item.get('name', '')} | {item.get('net_yi', 0):+.2f} 亿 | "
                f"{item.get('inflow_yi', 0):.1f} 亿 | "
                f"{item.get('outflow_yi', 0):.1f} 亿 | "
                f"{item.get('change_pct', 0):+.2f}% | "
                f"{item.get('leader', '')} "
                f"({item.get('leader_change_pct', 0):+.2f}%) |"
            )
        lines.append("")

    industries = review.get("limit_up_industries") or []
    if industries:
        lines.append("### 涨停行业分布")
        lines.append(
            "、".join(
                f"{item.get('name', '')}（{item.get('count', 0)}）"
                for item in industries
            )
        )
        lines.append("")

    summary = review.get("summary") or {}
    if summary.get("text"):
        lines.extend(["### 结论", summary["text"], ""])

    warnings = review.get("warnings") or []
    if warnings:
        lines.append("### 数据提示")
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")

    lines.append(f"> {review.get('disclaimer', '仅供研究参考。')}")
    return "\n".join(lines)
