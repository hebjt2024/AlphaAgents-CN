"""A 股策略信号库。

提供可计算的经典策略信号（多头趋势、均线金叉、放量突破、缩量回踩、
底部放量、龙头、一阳夹三阴、箱体、缠论/波浪简化版、情绪周期等）。
"""

from .base import StrategySignal
from .classic import DEFAULT_STRATEGY_KEYS, STRATEGY_META
from .families import (
    FAMILY_LABELS,
    STRATEGY_FAMILIES,
    aggregate_by_family,
    family_label,
    family_of,
)
from .scanner import format_strategy_report, scan_all_strategies, scan_watchlist

__all__ = [
    "StrategySignal",
    "scan_all_strategies",
    "scan_watchlist",
    "format_strategy_report",
    "STRATEGY_META",
    "DEFAULT_STRATEGY_KEYS",
    "STRATEGY_FAMILIES",
    "FAMILY_LABELS",
    "aggregate_by_family",
    "family_of",
    "family_label",
]
