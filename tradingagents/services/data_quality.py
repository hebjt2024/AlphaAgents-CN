"""本地行情数据质量检查。

本地缓存能解决"重复下载"和"离线不可用"，但也会把坏数据固化下来：

- OHLC 不自洽（最高价低于收盘价）会让 ATR / 止损算出荒谬结果；
- 同一天两份不同价格会让回测随机漂移；
- 长期缺口可能是停牌，也可能是数据源漏抓，必须区分后再补；
- 本地最后一天太旧，会让"最新分析"悄悄用过时行情。

本模块只读本地库并输出可解释的问题清单，不自动修改数据。补洞由
``services/market_data.py`` 负责，避免"检查即修复"掩盖问题。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from tradingagents.services.backtest_engine import detect_gaps
from tradingagents.services.market_data import coverage
from tradingagents.storage.market_store import load_daily_bars

#: 单日涨跌幅超过该阈值视为异常（北交所 30% 涨跌停 + 新股无涨跌幅）。
EXTREME_RETURN = 0.31


def _issue(
    level: str, code: str, message: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "level": level,
        "code": code,
        "message": message,
        "detail": detail or {},
    }


def check_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """检查一张标准化行情表的内部一致性。"""
    issues: list[dict[str, Any]] = []
    if frame is None or frame.empty:
        return {"rows": 0, "issues": issues, "healthy": False}

    data = frame.copy()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"]).sort_values("Date")

    if data["Date"].duplicated().any():
        duplicates = int(data["Date"].duplicated().sum())
        issues.append(
            _issue("error", "duplicate_dates", f"存在 {duplicates} 个重复交易日")
        )

    required = ["Open", "High", "Low", "Close"]
    missing_columns = [column for column in required if column not in data.columns]
    if missing_columns:
        issues.append(
            _issue(
                "error",
                "missing_columns",
                "缺少必要列：" + ", ".join(missing_columns),
            )
        )
    else:
        invalid = (
            (data["High"] < data[["Open", "Close"]].max(axis=1) - 1e-6)
            | (data["Low"] > data[["Open", "Close"]].min(axis=1) + 1e-6)
            | (data["High"] < data["Low"] - 1e-6)
        )
        invalid_count = int(invalid.sum())
        if invalid_count:
            sample = [
                str(value)[:10] for value in data.loc[invalid, "Date"].head(5)
            ]
            issues.append(
                _issue(
                    "error",
                    "ohlc_inconsistent",
                    f"{invalid_count} 根 K 线的开高低收不自洽",
                    {"dates": sample},
                )
            )

        non_positive = (
            (data[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        )
        if non_positive.any():
            issues.append(
                _issue(
                    "error",
                    "non_positive_price",
                    f"{int(non_positive.sum())} 根 K 线存在非正价格",
                )
            )

        returns = data["Close"].pct_change()
        extreme = returns.abs() > EXTREME_RETURN
        if extreme.any():
            sample = [
                f"{str(data.loc[index, 'Date'])[:10]} {value * 100:+.2f}%"
                for index, value in returns[extreme].head(5).items()
            ]
            issues.append(
                _issue(
                    "warn",
                    "extreme_return",
                    f"{int(extreme.sum())} 个交易日涨跌幅超过 "
                    f"{EXTREME_RETURN * 100:.0f}%，需确认是否复权/新股/异常数据",
                    {"samples": sample},
                )
            )

    if "Volume" in data.columns:
        zero_volume = int((data["Volume"].fillna(0) <= 0).sum())
        if zero_volume:
            issues.append(
                _issue(
                    "info",
                    "zero_volume",
                    f"{zero_volume} 个交易日成交量为 0（可能停牌）",
                )
            )

    gaps = detect_gaps(data)
    long_gaps = [item for item in gaps if item["missing_sessions"] >= 10]
    if long_gaps:
        sample = [
            f"{item['prev_date'].date()} → {item['date'].date()} "
            f"缺 {item['missing_sessions']} 个工作日"
            for item in long_gaps[:5]
        ]
        issues.append(
            _issue(
                "warn",
                "long_gap",
                f"{len(long_gaps)} 段超过 10 个工作日的缺口，可能是长期停牌或数据缺失",
                {"samples": sample},
            )
        )
    elif gaps:
        issues.append(
            _issue(
                "info",
                "short_gap",
                f"{len(gaps)} 段短缺口（周末/节假日/短停牌），通常无需处理",
            )
        )

    return {
        "rows": int(len(data)),
        "first_date": str(data["Date"].iloc[0])[:10],
        "last_date": str(data["Date"].iloc[-1])[:10],
        "issues": issues,
        "healthy": not any(item["level"] == "error" for item in issues),
    }


def _freshness(last_date: str) -> list[dict[str, Any]]:
    """本地最后一天与应有交易日比较。"""
    issues: list[dict[str, Any]] = []
    if not last_date:
        return issues
    try:
        last = datetime.strptime(str(last_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return issues
    today = date.today()
    # 周末回退到周五；法定节假日不做精确判断，避免质量检查依赖交易日历网络。
    expected = today
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    lag = (expected - last).days
    if lag > 5:
        issues.append(
            _issue(
                "warn",
                "stale_data",
                f"本地行情最后一天为 {last_date}，距最近工作日 {lag} 天，可能未同步",
            )
        )
    elif lag > 1:
        issues.append(
            _issue(
                "info",
                "data_delay",
                f"本地行情最后一天为 {last_date}，落后最近工作日 {lag} 天（可能是假期/停牌）",
            )
        )
    return issues


def quality_report(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    adjust: str = "qfq",
) -> dict[str, Any]:
    """检查某标的本地行情的覆盖、新鲜度与内部一致性。"""
    code = str(ticker or "").strip()
    if start_date is None:
        start_date = (date.today() - timedelta(days=365 * 3)).isoformat()
    if end_date is None:
        end_date = date.today().isoformat()
    frame = load_daily_bars(code, start_date, end_date, adjust=adjust)
    report = check_frame(frame)
    cov = coverage(code, adjust=adjust)
    issues = list(report.get("issues") or [])
    if not len(frame):
        issues.append(
            _issue(
                "error",
                "no_local_data",
                "本地没有该标的行情，请先同步或运行一次回测/分析触发缓存",
            )
        )
    else:
        issues.extend(_freshness(report.get("last_date") or ""))
    return {
        "ticker": code,
        "adjust": adjust,
        "requested_start": str(start_date)[:10],
        "requested_end": str(end_date)[:10],
        "coverage": cov,
        "rows": report.get("rows", 0),
        "first_date": report.get("first_date", ""),
        "last_date": report.get("last_date", ""),
        "healthy": bool(report.get("healthy")) and bool(len(frame)),
        "issues": issues,
        "summary": (
            "本地行情健康"
            if report.get("healthy") and len(frame)
            else "存在需要关注的问题"
        ),
    }


__all__ = ["check_frame", "quality_report"]
