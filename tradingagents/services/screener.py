"""全市场多因子选股 / 每日候选池。

设计原则
--------
1. **横截面**：不逐只拉历史，使用全市场快照 + 最新业绩报表 + 可选资金流排行；
2. **可解释**：输出每个因子的 0-100 分、覆盖度和触发理由，而不是一个黑箱分；
3. **可降级**：业绩/资金流接口失败时，用现有因子继续打分并显式标注覆盖不足；
4. **可复盘**：每次运行落库，第二天还能看到昨天选了什么、参数是什么。

因子与风格
----------
- value：PE/PB 越低越好（亏损 PE<=0 直接 0 分）
- quality：ROE、毛利率、每股经营现金流
- growth：营收同比、净利同比
- momentum：60 日涨跌幅、年初至今、当日涨跌
- liquidity：成交额、换手率、量比
- capital：主力净流入占比（可选）

风格只是因子权重不同，用户仍能看到每个子分。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pandas as pd

from tradingagents.dataflows.ashare_rules import (
    is_st,
    price_limit_pct,
)
from tradingagents.dataflows.screener_data import (
    get_financial_snapshot,
    get_fund_flow_snapshot,
    get_universe_snapshot,
)
from tradingagents.storage import save_screener_run

logger = logging.getLogger(__name__)

#: 选股风格与因子权重。
SCREENER_STYLES: dict[str, dict[str, Any]] = {
    "balanced": {
        "label": "均衡",
        "weights": {
            "value": 0.20,
            "quality": 0.20,
            "growth": 0.15,
            "momentum": 0.20,
            "liquidity": 0.15,
            "capital": 0.10,
        },
    },
    "value": {
        "label": "价值",
        "weights": {
            "value": 0.40,
            "quality": 0.20,
            "growth": 0.10,
            "momentum": 0.10,
            "liquidity": 0.10,
            "capital": 0.10,
        },
    },
    "growth": {
        "label": "成长",
        "weights": {
            "value": 0.10,
            "quality": 0.15,
            "growth": 0.40,
            "momentum": 0.15,
            "liquidity": 0.10,
            "capital": 0.10,
        },
    },
    "momentum": {
        "label": "动量",
        "weights": {
            "value": 0.05,
            "quality": 0.10,
            "growth": 0.10,
            "momentum": 0.45,
            "liquidity": 0.20,
            "capital": 0.10,
        },
    },
    "quality": {
        "label": "质量",
        "weights": {
            "value": 0.15,
            "quality": 0.40,
            "growth": 0.20,
            "momentum": 0.05,
            "liquidity": 0.10,
            "capital": 0.10,
        },
    },
}

#: 板块分类。
BOARDS = {
    "main": "主板",
    "gem": "创业板",
    "star": "科创板",
    "bse": "北交所",
}


def _board(code: str) -> str:
    if code.startswith("30"):
        return "gem"
    if code.startswith("68"):
        return "star"
    if code.startswith(("4", "8", "920")):
        return "bse"
    return "main"


def _rank_score(series: pd.Series, higher_better: bool = True) -> pd.Series:
    """把横截面数值转成 0-100 分（百分位）。

    缺失值保持 NaN，而不是给 50：否则"没有业绩数据"会被伪装成"质量中性"，
    综合分覆盖度也会永远显示 100%。
    """
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(float("nan"), index=series.index)
    ranked = numeric.rank(pct=True, method="average") * 100
    if not higher_better:
        ranked = 100 - ranked
    return ranked


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(float("nan"), index=frame.index)


def _weighted_mean(
    columns: list[tuple[pd.Series, float]], index: pd.Index
) -> pd.Series:
    """按可用权重求均值；全部缺失时返回 NaN。

    直接把加权后的值再做普通均值会把满分压到 1/3，动量因子会因此失真。
    """
    if not columns:
        return pd.Series(float("nan"), index=index)
    weighted = pd.Series(0.0, index=index)
    weights = pd.Series(0.0, index=index)
    for series, weight in columns:
        available = series.notna()
        weighted += series.fillna(0.0) * weight
        weights += available.astype(float) * weight
    return weighted / weights.replace(0, pd.NA)


def _mean_scores(columns: list[pd.Series], index: pd.Index) -> pd.Series:
    return _weighted_mean([(series, 1.0) for series in columns], index)


def _factor_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """计算 6 个因子分（0-100）。"""
    result = pd.DataFrame(index=frame.index)

    pe = pd.to_numeric(_col(frame, "pe_ttm"), errors="coerce")
    pb = pd.to_numeric(_col(frame, "pb"), errors="coerce")
    pe_score = _rank_score(pe.where(pe > 0), higher_better=False)
    pb_score = _rank_score(pb.where(pb > 0), higher_better=False)
    # 亏损/负净资产不能因为"缺失"拿到中性 50 分，直接 0 分更符合价值直觉。
    # 缺失值必须保持 NaN，否则覆盖度会把"没有 PE"误报成"价值 0 分且已覆盖"。
    pe_score = pe_score.mask(pe <= 0, 0.0)
    pb_score = pb_score.mask(pb <= 0, 0.0)
    value_score = (pe_score + pb_score) / 2
    # 亏损（PE<=0）不是"估值中性"，直接给 0 分，避免低 PB 把亏损股抬进价值榜。
    value_score = value_score.mask(pe.notna() & (pe <= 0), 0.0)
    result["value"] = value_score

    quality_columns = [
        _rank_score(_col(frame, "roe")),
        _rank_score(_col(frame, "gross_margin")),
        _rank_score(_col(frame, "ocf_ps")),
    ]
    result["quality"] = _mean_scores(quality_columns, frame.index)

    growth_columns = [
        _rank_score(_col(frame, "revenue_growth")),
        _rank_score(_col(frame, "profit_growth")),
    ]
    result["growth"] = _mean_scores(growth_columns, frame.index)

    momentum_columns = [
        (_rank_score(_col(frame, "change_60d")), 0.5),
        (_rank_score(_col(frame, "change_ytd")), 0.3),
        (_rank_score(_col(frame, "change_pct")), 0.2),
    ]
    result["momentum"] = _weighted_mean(momentum_columns, frame.index)

    liquidity_columns = [
        (_rank_score(_col(frame, "amount")), 0.5),
        (_rank_score(_col(frame, "turnover")), 0.3),
        (_rank_score(_col(frame, "volume_ratio")), 0.2),
    ]
    result["liquidity"] = _weighted_mean(liquidity_columns, frame.index)

    if "main_net_ratio" in frame.columns:
        result["capital"] = _rank_score(frame["main_net_ratio"])
    else:
        result["capital"] = float("nan")

    return result.round(2)


def _composite(
    factors: pd.DataFrame, style: str
) -> tuple[pd.Series, pd.Series]:
    weights = SCREENER_STYLES.get(style, SCREENER_STYLES["balanced"])["weights"]
    score = pd.Series(0.0, index=factors.index)
    weight_sum = pd.Series(0.0, index=factors.index)
    for factor, weight in weights.items():
        if factor not in factors.columns:
            continue
        values = pd.to_numeric(factors[factor], errors="coerce")
        available = values.notna()
        score += values.fillna(0.0) * weight
        weight_sum += available.astype(float) * weight
    composite = (score / weight_sum.replace(0, pd.NA)).fillna(0.0)
    coverage = (weight_sum / sum(weights.values())).fillna(0.0)
    return composite.round(2), coverage.round(2)


def _industry_neutralize(
    composite: pd.Series, frame: pd.DataFrame
) -> pd.Series:
    """把综合分按行业均值中心化，避免整条赛道一起高分。

    这不是严格的行业中性因子模型，但足以回答"同一行业里谁更强"：
    ``adjusted = score - industry_mean + 50``，再截断到 0-100。
    """
    if "industry" not in frame.columns:
        return composite
    groups = frame["industry"].fillna("未知").astype(str)
    means = composite.groupby(groups).transform("mean")
    return (composite - means + 50).clip(0, 100).round(2)


def _reason(row: pd.Series) -> list[str]:
    """挑出得分最高的 2-3 个因子作为入选理由，保证每只票都有解释。"""
    checks = [
        ("quality", "ROE", "roe", "%"),
        ("growth", "净利增速", "profit_growth", "%"),
        ("momentum", "60日涨幅", "change_60d", "%"),
        ("capital", "主力净流入占比", "main_net_ratio", "%"),
        ("value", "PE", "pe_ttm", ""),
        ("liquidity", "成交额", "amount", "亿"),
    ]
    candidates: list[tuple[float, str]] = []
    for factor, label, column, unit in checks:
        value = row.get(column)
        if value is None or pd.isna(value):
            continue
        score = float(row.get(f"score_{factor}") or 0)
        if column == "amount":
            value = float(value) / 1e8
        candidates.append((score, f"{label} {float(value):.2f}{unit}"))
    candidates.sort(key=lambda item: item[0], reverse=True)
    strong = [text for score, text in candidates if score >= 65]
    return (strong or [text for _, text in candidates])[:3]


def _flags(row: pd.Series, params: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    code = str(row.get("code") or "")
    name = str(row.get("name") or "")
    if is_st(name):
        flags.append("ST")
    pe = row.get("pe_ttm")
    if pe is not None and not pd.isna(pe) and float(pe) <= 0:
        flags.append("亏损")
    pb = row.get("pb")
    if pb is not None and not pd.isna(pb) and float(pb) > 10:
        flags.append("高PB")
    change = row.get("change_pct")
    if change is not None and not pd.isna(change):
        limit = price_limit_pct(code, name) * 100
        if float(change) >= limit - 0.3:
            flags.append("接近涨停")
        elif float(change) <= -limit + 0.3:
            flags.append("接近跌停")
    turnover = row.get("turnover")
    if turnover is not None and not pd.isna(turnover) and float(turnover) > 20:
        flags.append("高换手")
    change_60d = row.get("change_60d")
    if change_60d is not None and not pd.isna(change_60d) and float(change_60d) < -30:
        flags.append("深度回撤")
    if float(row.get("coverage") or 0) < 0.6:
        flags.append("因子覆盖不足")
    return flags


def _apply_filters(
    frame: pd.DataFrame, params: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, int]]:
    counts = {"start": len(frame)}
    if frame.empty:
        return frame, counts

    filtered = frame.copy()
    if params.get("exclude_st", True):
        filtered = filtered[~filtered["name"].map(is_st)]
    if params.get("exclude_loss", False):
        pe = pd.to_numeric(_col(filtered, "pe_ttm"), errors="coerce")
        filtered = filtered[pe > 0]
    if params.get("exclude_limit", True):
        def _near_limit(row: pd.Series) -> bool:
            change = row.get("change_pct")
            if change is None or pd.isna(change):
                return False
            limit = price_limit_pct(str(row.get("code") or ""), str(row.get("name") or "")) * 100
            return (
                float(change) >= limit - 0.3
                or float(change) <= -limit + 0.3
            )

        filtered = filtered[~filtered.apply(_near_limit, axis=1)]
    counts["after_st_limit"] = len(filtered)

    min_amount = float(params.get("min_amount_yi") or 0) * 1e8
    if min_amount > 0:
        amount = pd.to_numeric(_col(filtered, "amount"), errors="coerce").fillna(0)
        filtered = filtered[amount >= min_amount]
    counts["after_amount"] = len(filtered)

    min_cap = float(params.get("min_market_cap_yi") or 0) * 1e8
    if min_cap > 0:
        cap = pd.to_numeric(_col(filtered, "market_cap"), errors="coerce").fillna(0)
        filtered = filtered[cap >= min_cap]
    max_cap = params.get("max_market_cap_yi")
    if max_cap:
        cap = pd.to_numeric(_col(filtered, "market_cap"), errors="coerce").fillna(0)
        filtered = filtered[cap <= float(max_cap) * 1e8]
    counts["after_cap"] = len(filtered)

    boards = params.get("boards") or []
    if boards and "all" not in boards:
        allowed = {str(board) for board in boards}
        filtered = filtered[
            filtered["code"].map(lambda code: _board(code) in allowed)
        ]
    counts["after_board"] = len(filtered)
    return filtered, counts


def run_screener(
    *,
    top: int = 30,
    style: str = "balanced",
    min_amount_yi: float = 1.0,
    min_market_cap_yi: float = 30.0,
    max_market_cap_yi: float | None = None,
    exclude_st: bool = True,
    exclude_limit: bool = True,
    exclude_loss: bool = False,
    boards: list[str] | None = None,
    include_financials: bool = True,
    include_fund_flow: bool = False,
    industry_neutral: bool = False,
    save: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """运行一次全市场选股，返回 Top-N 与运行摘要。"""
    style = style if style in SCREENER_STYLES else "balanced"
    top = max(1, min(int(top), 200))
    snapshot = get_universe_snapshot()
    warnings: list[str] = list(snapshot.attrs.get("warnings") or [])
    if snapshot.empty:
        return {
            "success": False,
            "message": "全市场快照为空，无法选股",
            "items": [],
            "summary": {"warnings": warnings},
        }

    frame = snapshot.copy()
    if include_financials:
        financials = get_financial_snapshot(today)
        warnings.extend(financials.attrs.get("warnings") or [])
        if not financials.empty:
            frame = frame.merge(financials, on="code", how="left", suffixes=("", "_fin"))
    if include_fund_flow:
        fund_flow = get_fund_flow_snapshot()
        warnings.extend(fund_flow.attrs.get("warnings") or [])
        if not fund_flow.empty:
            frame = frame.merge(fund_flow, on="code", how="left")

    params = {
        "top": top,
        "style": style,
        "min_amount_yi": min_amount_yi,
        "min_market_cap_yi": min_market_cap_yi,
        "max_market_cap_yi": max_market_cap_yi,
        "exclude_st": exclude_st,
        "exclude_limit": exclude_limit,
        "exclude_loss": exclude_loss,
        "boards": boards or ["all"],
        "include_financials": include_financials,
        "include_fund_flow": include_fund_flow,
        "industry_neutral": industry_neutral,
    }

    filtered, counts = _apply_filters(frame, params)
    if filtered.empty:
        summary = {
            "universe": len(snapshot),
            "after_filters": 0,
            "counts": counts,
            "style": style,
            "style_label": SCREENER_STYLES[style]["label"],
            "warnings": warnings,
        }
        run_id = None
        if save:
            try:
                run_id = save_screener_run(
                    style, params, summary, [], (today or date.today()).isoformat()
                )
            except Exception as exc:
                warnings.append(f"选股结果归档失败：{exc}")
                summary["warnings"] = warnings
        return {
            "success": True,
            "run_id": run_id,
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "params": params,
            "items": [],
            "summary": summary,
        }

    # 过滤后索引会保留原始行号（例如 [0, 2, 3, 6]）。因子分与综合分必须
    # 在同一套索引上计算，否则赋值时按索引对齐会产生 NaN。
    filtered = filtered.reset_index(drop=True)
    factors = _factor_scores(filtered)
    composite, coverage = _composite(factors, style)
    if industry_neutral:
        composite = _industry_neutralize(composite, filtered)
    filtered["score"] = composite
    filtered["coverage"] = coverage
    for factor in factors.columns:
        filtered[f"score_{factor}"] = factors[factor]

    filtered["board"] = filtered["code"].map(_board)
    filtered["flags"] = filtered.apply(lambda row: _flags(row, params), axis=1)
    filtered["reasons"] = filtered.apply(_reason, axis=1)
    filtered_count = len(filtered)
    filtered = filtered.sort_values(
        ["score", "amount"], ascending=[False, False]
    ).head(top)

    items: list[dict[str, Any]] = []
    for rank, (_, row) in enumerate(filtered.iterrows(), start=1):
        items.append(
            {
                "rank": rank,
                "ticker": row.get("code"),
                "name": row.get("name") or row.get("name_fin") or "",
                "industry": row.get("industry") if not pd.isna(row.get("industry")) else "",
                "board": BOARDS.get(str(row.get("board")), str(row.get("board"))),
                "price": _round(row.get("price"), 2),
                "change_pct": _round(row.get("change_pct"), 2),
                "amount_yi": _round(float(row.get("amount") or 0) / 1e8, 2),
                "market_cap_yi": _round(float(row.get("market_cap") or 0) / 1e8, 2),
                "pe_ttm": _round(row.get("pe_ttm"), 2),
                "pb": _round(row.get("pb"), 2),
                "roe": _round(row.get("roe"), 2),
                "gross_margin": _round(row.get("gross_margin"), 2),
                "revenue_growth": _round(row.get("revenue_growth"), 2),
                "profit_growth": _round(row.get("profit_growth"), 2),
                "change_60d": _round(row.get("change_60d"), 2),
                "change_ytd": _round(row.get("change_ytd"), 2),
                "volume_ratio": _round(row.get("volume_ratio"), 2),
                "turnover": _round(row.get("turnover"), 2),
                "main_net_ratio": _round(row.get("main_net_ratio"), 2),
                "score": _round(row.get("score"), 2),
                "coverage": _round(row.get("coverage"), 2),
                "factor_scores": {
                    factor: _round(row.get(f"score_{factor}"), 2)
                    for factor in factors.columns
                },
                "reasons": list(row.get("reasons") or []),
                "flags": list(row.get("flags") or []),
            }
        )

    avg_coverage = float(filtered["coverage"].mean()) if len(filtered) else 0.0
    summary = {
        "universe": len(snapshot),
        "after_filters": filtered_count,
        "counts": counts,
        "style": style,
        "style_label": SCREENER_STYLES[style]["label"],
        "weights": SCREENER_STYLES[style]["weights"],
        "industry_neutral": bool(industry_neutral),
        "average_coverage": round(avg_coverage, 4),
        "factor_coverage": {
            factor: round(float(factors[factor].notna().mean()), 4)
            if factor in factors.columns and len(factors)
            else 0.0
            for factor in SCREENER_STYLES[style]["weights"]
        },
        "warnings": warnings,
    }
    run_date = (today or date.today()).isoformat()
    run_id = None
    if save:
        try:
            run_id = save_screener_run(style, params, summary, items, run_date)
        except Exception as exc:
            logger.warning("选股结果归档失败: %s", exc)
            warnings.append(f"选股结果归档失败：{exc}")
            summary["warnings"] = warnings
    return {
        "success": True,
        "run_id": run_id,
        "run_date": run_date,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "summary": summary,
        "items": items,
    }


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return round(number, digits)


__all__ = ["SCREENER_STYLES", "BOARDS", "run_screener"]
