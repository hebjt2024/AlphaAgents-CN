"""分析图编排预设。

Preset 只描述“跑哪些阶段”，具体模型与数据源仍由 config 决定。
Portfolio Manager（最终裁决）始终保留，确保一定输出 final_trade_decision。
"""

from __future__ import annotations

ALL_ANALYSTS = ["market", "social", "news", "fundamentals"]

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
