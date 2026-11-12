"""策略扫描服务：加载行情、运行全部策略并生成汇总。"""

from __future__ import annotations

from datetime import datetime, timedelta

from .base import StrategySignal, atr, clamp, last, ma, rsi, volume_ratio
from .classic import ALL_STRATEGIES, DEFAULT_STRATEGY_KEYS


def _select_strategies(strategy_keys: list[str] | None) -> tuple[list, list[str]]:
    func_map = {func.__name__: func for func in ALL_STRATEGIES}
    if strategy_keys:
        selected = [key for key in strategy_keys if key in func_map]
    else:
        # 默认只跑核心组合，避免每次分析执行全部策略
        selected = [key for key in DEFAULT_STRATEGY_KEYS if key in func_map]
    if not selected:
        selected = list(func_map.keys())
    return [func_map[key] for key in selected], selected


def scan_all_strategies(
    ticker: str,
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
) -> dict:
    """对单只股票运行选中的策略（默认核心组合），返回结构化结果。"""
    from tradingagents.services.backtest import _load_prices

    end = datetime.now()
    if end_date:
        try:
            end = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
        except ValueError:
            pass
    start = end - timedelta(days=500)
    frame = _load_prices(
        ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    if frame is None or len(frame) < 30:
        raise ValueError(f"行情数据不足，无法扫描策略：{ticker}")

    funcs, selected_keys = _select_strategies(strategy_keys)
    signals: list[StrategySignal] = []
    for func in funcs:
        try:
            signals.append(func(frame))
        except Exception as exc:  # 单个策略异常不影响整体扫描
            signals.append(
                StrategySignal(
                    key=getattr(func, "__name__", "unknown"),
                    name=getattr(func, "__name__", "unknown"),
                    category="unknown",
                    triggered=False,
                    direction="neutral",
                    score=0,
                    summary=f"策略计算失败：{type(exc).__name__}",
                    reasons=[str(exc)],
                    metrics={},
                )
            )

    signals.sort(key=lambda s: (s.triggered, s.score), reverse=True)
    bullish = [
        s for s in signals if s.triggered and s.direction == "bullish"
    ]
    bearish = [
        s for s in signals if s.triggered and s.direction == "bearish"
    ]
    bull_score = sum(s.score for s in bullish)
    bear_score = sum(s.score for s in bearish)
    net = bull_score - bear_score
    total = max(1, bull_score + bear_score)
    strength = clamp(abs(net) / total * 100)
    if net >= 12:
        consensus = "偏多"
    elif net <= -12:
        consensus = "偏空"
    else:
        consensus = "中性"

    close = frame["Close"].astype(float)
    price = {
        "close": round(last(close), 2),
        "change_pct": round(
            (last(close) / float(close.iloc[-2]) - 1) * 100
            if len(close) > 1
            else 0.0,
            2,
        ),
        "ma5": round(last(ma(close, 5)), 2),
        "ma10": round(last(ma(close, 10)), 2),
        "ma20": round(last(ma(close, 20)), 2),
        "rsi": round(rsi(close), 1),
        "volume_ratio": round(volume_ratio(frame, 20), 2),
        "atr": round(atr(frame), 2),
        "recent_high20": round(float(frame["High"].iloc[-21:].max()), 2),
        "recent_low20": round(float(frame["Low"].iloc[-21:].min()), 2),
    }

    return {
        "success": True,
        "ticker": ticker,
        "as_of": frame["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "bars": len(frame),
        "price": price,
        "summary": {
            "consensus": consensus,
            "net_score": round(net, 1),
            "strength": strength,
            "bullish": len(bullish),
            "bearish": len(bearish),
            "triggered": len(bullish) + len(bearish),
            "total": len(signals),
        },
        "selected_strategies": selected_keys,
        "signals": [s.to_dict() for s in signals],
    }


def format_strategy_report(result: dict) -> str:
    """把扫描结果格式化为适合 LLM 阅读的 Markdown。"""
    summary = result.get("summary", {})
    price = result.get("price", {})
    selected = result.get("selected_strategies") or []
    lines = [
        f"## 策略扫描 {result.get('ticker', '')} ({result.get('as_of', '')})",
        f"启用策略：{'、'.join(selected) if selected else '全部'}",
        "",
        f"- 共识：**{summary.get('consensus', '中性')}**（强度 {summary.get('strength', 0)}%）",
        f"- 触发信号：{summary.get('triggered', 0)} 个"
        f"（多 {summary.get('bullish', 0)} / 空 {summary.get('bearish', 0)}）",
        f"- 参考价：{price.get('close', '-')}（{price.get('change_pct', 0)}%）"
        f" · MA5 {price.get('ma5', '-')} / MA10 {price.get('ma10', '-')} / MA20 {price.get('ma20', '-')}"
        f" · RSI {price.get('rsi', '-')} · 量比 {price.get('volume_ratio', '-')}",
        "",
    ]

    triggered = [s for s in result.get("signals", []) if s.get("triggered")]
    watch = [s for s in result.get("signals", []) if not s.get("triggered")][:5]

    if triggered:
        lines.append("### 触发信号")
        for signal in triggered:
            direction = "多" if signal.get("direction") == "bullish" else "空"
            lines.append(
                f"- **{signal.get('name')}**（{direction}，{signal.get('score')} 分）：{signal.get('summary')}"
            )
            for reason in (signal.get("reasons") or [])[:3]:
                lines.append(f"  - {reason}")
        lines.append("")

    if watch:
        lines.append("### 观察信号（未触发但值得关注）")
        for signal in watch:
            lines.append(
                f"- {signal.get('name')}（{signal.get('score')} 分）：{signal.get('summary')}"
            )
        lines.append("")

    lines.append(
        "> 策略信号为规则化近似，缠论/波浪为简化实现，仅供研究参考，不构成投资建议。"
    )
    return "\n".join(lines)


def scan_watchlist(
    tickers: list[str],
    end_date: str | None = None,
    strategy_keys: list[str] | None = None,
) -> list[dict]:
    """批量扫描自选股，返回每只股票的简要结果。"""
    results = []
    for ticker in tickers:
        try:
            result = scan_all_strategies(ticker, end_date, strategy_keys)
            summary = result["summary"]
            triggered_signals = [
                s for s in result["signals"] if s["triggered"]
            ]
            results.append(
                {
                    "ticker": ticker,
                    "as_of": result["as_of"],
                    "close": result["price"]["close"],
                    "change_pct": result["price"]["change_pct"],
                    "consensus": summary["consensus"],
                    "strength": summary["strength"],
                    "triggered": summary["triggered"],
                    "bullish": summary["bullish"],
                    "bearish": summary["bearish"],
                    "top": [s["name"] for s in triggered_signals][:3],
                    "top_signals": [
                        {
                            "key": s.get("key"),
                            "name": s.get("name"),
                            "direction": s.get("direction"),
                            "score": s.get("score"),
                            "summary": s.get("summary"),
                            "reasons": (s.get("reasons") or [])[:2],
                        }
                        for s in triggered_signals[:3]
                    ],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ticker": ticker,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    results.sort(
        key=lambda r: (
            r.get("consensus") == "偏多",
            r.get("strength", 0),
        ),
        reverse=True,
    )
    return results
