"""分析图编排预设。

Preset 只描述“跑哪些阶段”，具体模型与数据源仍由 config 决定。
Portfolio Manager（最终裁决）始终保留，确保一定输出 final_trade_decision。
"""

from __future__ import annotations

from tradingagents.strategies.classic import DEFAULT_STRATEGY_KEYS

ALL_ANALYSTS = ["market", "social", "news", "fundamentals"]

# 标准/精简只保留“互不重复”的核心信号族：
#   趋势（bull_trend / ma_golden_cross）、突破（volume_breakout）、
#   回踩（shrink_pullback）、反转（bottom_volume）、箱体（box_oscillation）。
# 同一 family 内再叠加 MACD 金叉、平台突破、RSI 反转等，聚合时只取该族最高分，
# 边际信息有限，因此放到“深度”里作为补充视角。
CORE_STRATEGY_KEYS = list(DEFAULT_STRATEGY_KEYS)

DEEP_EXTRA_STRATEGY_KEYS = [
    "macd_cross",
    "platform_breakout",
    "rsi_reversal",
    "emotion_cycle",
]

DEEP_STRATEGY_KEYS = CORE_STRATEGY_KEYS + DEEP_EXTRA_STRATEGY_KEYS

ANALYST_META = [
    {"key": "market", "label": "市场分析师", "description": "行情、技术指标、资金面"},
    {"key": "social", "label": "舆情分析师", "description": "社交媒体情绪与讨论热度"},
    {"key": "news", "label": "新闻分析师", "description": "个股新闻与宏观事件"},
    {"key": "fundamentals", "label": "基本面分析师", "description": "财报、估值与财务质量"},
]

PIPELINE_PRESETS: dict[str, dict] = {
    "lite": {
        "label": "精简",
        "description": "核心分析师 + 最终裁决，速度最快、成本最低",
        "analysts": ["market", "fundamentals"],
        "enable_debate": False,
        "enable_trader": False,
        "enable_risk": False,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "strategy_keys": list(CORE_STRATEGY_KEYS),
        "research_think": "deep",
        "parallel_risk": False,
    },
    "standard": {
        "label": "标准",
        "description": "完整分析师 → 多空辩论 → 交易员 → 风险辩论 → 裁决",
        "analysts": list(ALL_ANALYSTS),
        "enable_debate": True,
        "enable_trader": True,
        "enable_risk": True,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "strategy_keys": list(CORE_STRATEGY_KEYS),
        "research_think": "deep",
        "parallel_risk": False,
    },
    "standard-fast": {
        "label": "标准-快速",
        "description": "完整分析师、辩论与风控；研究经理用快速模型，风险三方并行，最终裁决仍用深模型",
        "analysts": list(ALL_ANALYSTS),
        "enable_debate": True,
        "enable_trader": True,
        "enable_risk": True,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "strategy_keys": list(CORE_STRATEGY_KEYS),
        "research_think": "quick",
        "parallel_risk": True,
    },
    "deep": {
        "label": "深度",
        "description": "完整流程 + 更多辩论轮次，适合重点标的",
        "analysts": list(ALL_ANALYSTS),
        "enable_debate": True,
        "enable_trader": True,
        "enable_risk": True,
        "max_debate_rounds": 2,
        "max_risk_discuss_rounds": 2,
        "strategy_keys": list(DEEP_STRATEGY_KEYS),
        "research_think": "deep",
        "parallel_risk": False,
    },
}


def get_preset(name: str) -> dict:
    """返回预设副本；未知预设回退到 standard。"""
    preset = PIPELINE_PRESETS.get((name or "").lower(), PIPELINE_PRESETS["standard"])
    return {
        "analysts": list(preset["analysts"]),
        "enable_debate": preset["enable_debate"],
        "enable_trader": preset["enable_trader"],
        "enable_risk": preset["enable_risk"],
        "max_debate_rounds": preset["max_debate_rounds"],
        "max_risk_discuss_rounds": preset["max_risk_discuss_rounds"],
        "strategy_keys": list(preset["strategy_keys"]),
        "research_think": preset.get("research_think", "deep"),
        "parallel_risk": bool(preset.get("parallel_risk", False)),
    }


def build_pipeline_config(
    analysts: list[str] | None = None,
    enable_debate: bool | None = None,
    enable_trader: bool | None = None,
    enable_risk: bool | None = None,
    preset: str = "standard",
) -> dict:
    """合并预设与用户自定义选项，做合法性约束。"""
    base = get_preset(preset)

    if analysts is not None:
        cleaned = [a for a in analysts if a in ALL_ANALYSTS]
        if cleaned:
            base["analysts"] = cleaned

    if enable_debate is not None:
        base["enable_debate"] = bool(enable_debate)
    if enable_trader is not None:
        base["enable_trader"] = bool(enable_trader)
    if enable_risk is not None:
        base["enable_risk"] = bool(enable_risk)

    # 风险辩论必须有交易员方案作为评估对象
    if not base["enable_trader"]:
        base["enable_risk"] = False

    return base
