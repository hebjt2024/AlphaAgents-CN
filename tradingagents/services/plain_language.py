"""人话层：把行话和数字翻译成普通人能读懂的结论。

WHY 需要这一层
--------------
分析报告是写给"看得懂技术分析"的人看的：T+1、EMA、安全边际、赔率、敞口、
回撤、风险预算……这些词对专业读者是常识，对普通用户等于"每个字都认识、
连起来不知道在说什么"。

此前系统给出的最终结论是"持有（Hold）"，但用户真正想知道的是三件事：

1. **结论是什么** —— 买还是不买，一句话说完；
2. **我该做什么** —— 具体到"什么时候买、买多少、跌到哪认亏"；
3. **凭什么这么说** —— 三条人话理由 + 需要当心的地方。

本模块提供两条互补的路径：

- **规则层（零 LLM 成本、确定性、可测试）**
  :func:`build_plain_summary` 把决策卡 + 结构化决策翻译成"人话结论卡"；
  :func:`explain_terms` 给正文里的行话就地加括号解释；
  :func:`format_plain_summary` 渲染成 Markdown，供报告 / 导出 / 通知复用。
- **LLM 层（可选，按开关开启）**
  :func:`rewrite_in_plain_language` 把整篇报告改写成白话版。

设计约束
--------
1. **不新增事实**：白话层只解释已有结论和数字，绝不自己下判断。
2. **不破坏机器可读内容**：JSON 代码块、表格结构一律原样保留
   （:func:`explain_terms` 会跳过围栏代码块）。
3. **零 LLM 依赖**：规则层不调用任何模型，缺数据时降级为"暂无"而不是报错。
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 术语对照表：行话 → 人话
# ---------------------------------------------------------------------------
#: 行话 → 白话解释。
#:
#: 只收录**普通投资者真的会看不懂**、且解释一次就能明白的词。
#: 每个解释控制在 20 字以内，插入正文后不打断阅读节奏。
GLOSSARY: dict[str, str] = {
    # 交易制度
    "T+1": "当天买入、当天不能卖",
    "涨跌停": "主板一天最多涨 10%、跌 10%",
    "停牌": "暂时不能交易",
    "流动性": "能不能顺畅地买到卖出",
    "融资": "借钱买股票",
    "整数倍": "100 股的倍数",
    # 技术指标
    "EMA": "短期均线",
    "SMA": "平均线",
    "MACD": "趋势强弱指标",
    "RSI": "超买超卖指标",
    "ATR": "股价日常波动幅度",
    "均线": "一段时间平均成本线",
    "金叉": "短期均线上穿长期均线",
    "死叉": "短期均线下穿长期均线",
    "支撑位": "跌到这附近常有人接盘",
    "阻力位": "涨到这附近常有人卖",
    # 估值与基本面
    "估值分位": "现在的价格在历史上算贵还是便宜",
    "安全边际": "现在的价格比内在价值便宜多少",
    "盈利预期": "市场预计公司接下来能赚多少钱",
    "现金流": "公司实际收到和付出的钱",
    "营收": "公司卖东西收到的钱",
    "净利润": "公司赚到的钱（扣除所有成本后）",
    "市盈率": "按当前盈利水平，多少年能赚回股价",
    "市净率": "股价相当于公司账面净资产的多少倍",
    "股息": "公司分红给股东的钱",
    "ROE": "公司用股东的钱赚钱的效率",
    # 组合与风险
    "敞口": "持仓规模",
    "回撤": "从最高点跌下来的幅度",
    "赔率": "赚的空间和亏的空间的比例",
    "风险预算": "最多能亏多少钱",
    "最大回撤": "账户历史上最多亏过多少",
    "共振": "几个信号同时变好",
    "流动性风险": "想卖时卖不掉的风险",
    "波动率": "股价上下摆动的幅度",
    "分批": "分成几次操作",
    "试仓": "先买一点试试",
    "建仓": "买入",
    "加仓": "再买一些",
    "减仓": "卖出一部分",
    "清仓": "全部卖掉",
    "超配": "比平时买得多一点",
    "低配": "比平时买得少一点",
}

#: 决策 → 普通人看得懂的标签。
DECISION_PLAIN: dict[str, dict[str, str]] = {
    "BUY": {
        "label": "买入",
        "short": "可以买",
        "action": "可以考虑买入",
        "tone": "看多",
    },
    "OVERWEIGHT": {
        "label": "比平时多买一点",
        "short": "可以小幅加仓",
        "action": "可以小幅度加仓",
        "tone": "偏多",
    },
    "HOLD": {
        "label": "暂时不动",
        "short": "先别动",
        "action": "暂时不需要操作",
        "tone": "中性",
    },
    "UNDERWEIGHT": {
        "label": "比平时少拿一点",
        "short": "可以减仓",
        "action": "可以适当减仓",
        "tone": "偏空",
    },
    "SELL": {
        "label": "卖掉",
        "short": "卖掉",
        "action": "考虑卖出或清仓",
        "tone": "看空",
    },
}

#: 兜底决策（无法识别时）。
_DEFAULT_DECISION = "HOLD"


def _decision(decision: str | None) -> str:
    """把任意写法的决策词归一到 BUY/HOLD/SELL 五档。"""
    text = str(decision or "").strip().upper()
    if text in DECISION_PLAIN:
        return text
    aliases = {
        "买入": "BUY",
        "增持": "OVERWEIGHT",
        "超配": "OVERWEIGHT",
        "持有": "HOLD",
        "观望": "HOLD",
        "中性": "HOLD",
        "低配": "UNDERWEIGHT",
        "减持": "UNDERWEIGHT",
        "卖出": "SELL",
        "清仓": "SELL",
    }
    for key, value in aliases.items():
        if key in text:
            return value
    return _DEFAULT_DECISION


def _pct(value: Any, digits: int = 1) -> float:
    """把 0~1 的比例转成百分数（非法输入返回 0）。"""
    try:
        return round(float(value or 0) * 100, digits)
    except (TypeError, ValueError):
        return 0.0


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _shorten(text: str, limit: int) -> str:
    """把过长的一句话截断到 limit 字，保留完整语义边界。

    结论卡空间有限，长段落会挤掉后面的内容；完整原文在正文里仍然可见。
    """
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    # 优先在句号/分号处断开，读起来不突兀
    for mark in ("。", "；", "，", ";", ",", " "):
        index = text.rfind(mark, 0, limit)
        if index >= limit // 2:
            return text[: index + 1].rstrip() + "…"
    return text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 正文行话就地解释
# ---------------------------------------------------------------------------
def _split_code_blocks(text: str) -> list[tuple[str, bool]]:
    """把文本切成 (片段, 是否代码块) 的序列。

    WHY：报告末尾有一段 ```json 结构化决策块，往里插括号解释会把 JSON
    变成非法格式，下游 :func:`parse_structured_decision` 就再也解析不出来。
    """
    parts: list[tuple[str, bool]] = []
    for index, chunk in enumerate(re.split(r"(```.*?```)", text, flags=re.DOTALL)):
        if not chunk:
            continue
        parts.append((chunk, index % 2 == 1))
    return parts


def explain_terms(
    text: str,
    terms: list[str] | None = None,
    used: set[str] | None = None,
) -> str:
    """给正文里的行话**首次出现处**加上括号解释。

    参数
    ----
    text:
        原始文本（通常是组合经理的最终决策报告）。
    terms:
        需要解释的术语列表；``None`` 表示使用 :data:`GLOSSARY` 全部词条。
    used:
        已经解释过的术语集合；传入后这些词不再重复解释
        （用于把一份长报告拆成多段分别处理的情况）。

    返回
    ----
    str
        加了括号解释的文本。代码块（```…```）原样保留。

    示例
    ----
        >>> explain_terms("A 股实行 T+1，当日买入不得当日卖出。")
        'A 股实行 T+1（当天买入、当天不能卖），当日买入不得当日卖出。'
    """
    if not text:
        return ""
    glossary = GLOSSARY if terms is None else {k: GLOSSARY[k] for k in terms if k in GLOSSARY}
    if not glossary:
        return text
    explained = used if used is not None else set()

    # 长词优先：避免"均线"先于"EMA"被替换掉一部分。
    ordered = sorted(glossary.items(), key=lambda item: len(item[0]), reverse=True)

    output: list[str] = []
    for chunk, is_code in _split_code_blocks(text):
        if is_code:
            output.append(chunk)
            continue
        for term, meaning in ordered:
            if term in explained or term not in chunk:
                continue
            # 已经自带解释（"T+1（当天买入…）"）就不再重复加括号
            if f"{term}（" in chunk:
                explained.add(term)
                continue
            chunk = chunk.replace(term, f"{term}（{meaning}）", 1)
            explained.add(term)
        output.append(chunk)
    return "".join(output)


def strip_structured_block(text: str) -> str:
    """去掉报告末尾的 ```json 结构化决策块。

    WHY：那个 JSON 块是给程序读的，对普通读者只是噪音 —— 而且同一份信息
    已经由"结构化决策卡"用人话展示过了。展示层去掉它，导出/解析仍保留原文。
    """
    if not text:
        return ""
    # 只删带 decision 字段的 JSON 块，避免误删其它代码块
    patterns = [
        r"```json\s*\{[^`]*?\"decision\"[^`]*?\}\s*```",
        r"```\s*\{[^`]*?\"decision\"[^`]*?\}\s*```",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.rstrip() + ("\n" if cleaned != text and cleaned.strip() else "")


def find_terms(text: str) -> list[dict[str, str]]:
    """找出文本中出现的术语，用于前端渲染“术语对照表”。

    返回 ``[{"term": "T+1", "meaning": "当天买入、当天不能卖"}, …]``。
    """
    if not text:
        return []
    found: list[dict[str, str]] = []
    for term, meaning in GLOSSARY.items():
        if term in text:
            found.append({"term": term, "meaning": meaning})
    found.sort(key=lambda item: len(item["term"]), reverse=True)
    return found


# ---------------------------------------------------------------------------
# 数字 → 人话
# ---------------------------------------------------------------------------
def _explain_score(score: float) -> str:
    """把 0~100 的综合参考分翻译成一句人话。"""
    if score >= 75:
        return "信号很强，多方证据一致"
    if score >= 60:
        return "偏多，看涨的理由更充分"
    if score >= 45:
        return "不温不火，多空两边差不多，暂时看不出明确方向"
    if score >= 30:
        return "偏弱，看跌的理由更充分"
    return "信号很弱，不适合进场"


def _explain_confidence(confidence: float | None, structured: bool) -> str:
    """把置信度翻译成一句人话。"""
    if not structured or confidence is None:
        return "系统没能给出把握评估（结论里缺少结构化的置信度）"
    if confidence >= 75:
        return "系统把握比较大"
    if confidence >= 55:
        return "系统有一定把握"
    if confidence >= 35:
        return "系统把握不大，建议小仓位"
    if confidence > 0:
        return "系统基本没把握"
    return "系统明确表示没有把握"


def _explain_risk_reward(risk_reward: float) -> str:
    """把风险收益比翻译成人话。"""
    if risk_reward <= 0:
        return "算不出（缺少价格或止损位）"
    if risk_reward >= 3:
        return f"赚 {risk_reward:.1f} 元对应亏 1 元，比较划算"
    if risk_reward >= 2:
        return f"赚 {risk_reward:.1f} 元对应亏 1 元，还算可以"
    if risk_reward >= 1:
        return f"赚 {risk_reward:.1f} 元对应亏 1 元，优势不大"
    return f"赚 {risk_reward:.1f} 元对应亏 1 元，赚钱空间小于亏钱空间"


def _explain_prices(plan: dict, decision: str = "") -> list[dict[str, str]]:
    """把决策卡里的价位翻译成人话。

    看空/减仓时止损位在现价**上方**、目标位在**下方**，措辞必须反过来，
    否则会出现"止损位比现价低 -8%"这种自相矛盾的说法。A 股不能做空，
    这里索性不给方向性解读，只提示"减仓参考价"。
    """
    entry = _num(plan.get("entry"))
    stop = _num(plan.get("stop_loss"))
    target = _num(plan.get("take_profit"))
    bearish = _decision(decision) in ("UNDERWEIGHT", "SELL")
    items: list[dict[str, str]] = []

    if entry > 0:
        items.append(
            {
                "label": "现在的价格",
                "value": f"{entry:.2f} 元",
                "plain": "这是本次分析对应的参考价",
            }
        )
    if bearish:
        return items

    if entry > 0 and stop > 0:
        drop = (stop - entry) / entry * 100
        items.append(
            {
                "label": "止损位（认亏离场价）",
                "value": f"{stop:.2f} 元",
                "plain": f"跌到这里就该认赔卖掉，大约比现价低 {abs(drop):.1f}%",
            }
        )
    if entry > 0 and target > 0:
        rise = (target - entry) / entry * 100
        items.append(
            {
                "label": "目标位（可以了结的价）",
                "value": f"{target:.2f} 元",
                "plain": f"涨到这里可以考虑卖出，大约比现价高 {abs(rise):.1f}%",
            }
        )
    return items


# ---------------------------------------------------------------------------
# 人话结论卡
# ---------------------------------------------------------------------------
def extract_summary_bullets(decision_text: str, limit: int = 6) -> list[str]:
    """从报告的"执行摘要"小节里取要点条目。

    WHY 需要这个：组合经理的报告里，"执行摘要"就是作者写给人看的那一段
    （方向判断 / 入场策略 / 仓位规模 / 关键风险位 / 时间维度），
    比结构化 JSON 更完整，比正文论证更短。结构化 JSON 缺失时（回复被截断），
    这里是"为什么"最好的来源。
    """
    if not decision_text:
        return []
    lines = decision_text.splitlines()
    start = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and (
            "执行摘要" in stripped or "Executive Summary" in stripped
        ):
            start = index + 1
            break
    if start < 0:
        return []

    bullets: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")):
            # 只去掉项目符号本身（"- " / "* "），不能连着去掉加粗标记的 "*"
            text = re.sub(r"^[-*•]\s*", "", stripped)
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()
            if text:
                bullets.append(text)
    return bullets[:limit]


def build_plain_summary(
    ticker: str = "",
    decision: str = "HOLD",
    plan: dict | None = None,
    structured: dict | None = None,
    decision_text: str = "",
    position_advice: dict | None = None,
) -> dict[str, Any]:
    """生成"人话结论卡"。

    参数
    ----
    ticker:
        股票代码，用于句子里指名道姓。
    decision:
        最终决策（BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL）。
    plan:
        结构化决策卡（:func:`build_decision_plan` 的输出）。
    structured:
        结构化决策（:func:`parse_structured_decision` 的输出）。
    decision_text:
        组合经理的最终报告原文，用于提取理由与术语。
    position_advice:
        持仓建议（有持仓时才有），用于回答"我手里这只怎么办"。

    返回
    ----
    dict
        结构化的人话结论，前端与 CLI 直接消费：

        ``headline``
            一句话结论。
        ``decision_label`` / ``tone``
            决策的人话标签与倾向。
        ``action``
            该做什么（给新用户）。
        ``action_holding``
            已持仓时该做什么（有持仓建议时才有）。
        ``do_list``
            具体动作清单。
        ``why``
            三到五条人话理由。
        ``risks``
            人话风险提示。
        ``numbers``
            关键数字 + 白话解释。
        ``terms``
            术语对照表。
        ``confidence_note``
            置信度的人话解释。
        ``reliability``
            结论可信度提示（结构化 JSON 是否拿到）。
    """
    # 防御性归一：调用方可能把 reports 里的 Markdown 字符串（同名字段）
    # 误传进来。白话层绝不能因此崩溃 —— 显示层出错会直接毁掉整份结果。
    plan = plan if isinstance(plan, dict) else {}
    structured = structured if isinstance(structured, dict) else {}
    decision_text = decision_text if isinstance(decision_text, str) else ""
    decision_key = _decision(decision)
    meta = DECISION_PLAIN[decision_key]
    entry = _num(plan.get("entry"))
    position_pct = _pct(plan.get("position_pct"))
    score = _num(plan.get("score"))
    confidence = structured.get("confidence")

    # ---- 一句话结论 --------------------------------------------------
    # 结论先行：普通用户第一眼要看到"买 / 卖 / 不动"，而不是分析过程。
    headline_verb = {
        "BUY": "可以买",
        "OVERWEIGHT": "可以小幅加仓，别一次买满",
        "HOLD": "现在先不动",
        "UNDERWEIGHT": "可以适当减仓",
        "SELL": "建议卖出",
    }[decision_key]
    ticker_name = str(ticker or "").strip()
    if ticker_name:
        headline = f"结论：{ticker_name} {headline_verb}。"
    else:
        headline = f"结论：{headline_verb}。"

    # ---- 该做什么 ----------------------------------------------------
    action = meta["action"]
    if decision_key == "HOLD":
        action = "先不买、也不卖，等信号更明确再动手"
    if decision_key == "BUY" and position_pct > 0:
        action = f"可以买入，建议先买总资金的 {position_pct:.0f}% 左右"
    elif decision_key == "OVERWEIGHT" and position_pct > 0:
        action = f"可以小幅加仓，建议控制在总资金的 {position_pct:.0f}% 左右"
    elif decision_key in ("UNDERWEIGHT", "SELL"):
        action = "A 股不能做空，这里的「卖出」意思是减仓或清仓"

    # ---- 具体动作清单 ------------------------------------------------
    do_list: list[str] = []
    if decision_key in ("BUY", "OVERWEIGHT"):
        if entry > 0:
            do_list.append(f"在 {entry:.2f} 元附近买入，别追高")
        if position_pct > 0:
            do_list.append(f"第一次只买总资金的 {position_pct:.0f}%，留出加仓余地")
        else:
            do_list.append("目前证据不足以给出仓位，先观察几天")
    elif decision_key == "HOLD":
        do_list.append("不买、不卖，保持现状")
        do_list.append("等业绩或趋势出现明确变化再重新分析")
    else:
        do_list.append("A 股不能做空，把「卖出」理解成减仓或清仓")
        if entry > 0:
            do_list.append(f"以 {entry:.2f} 元附近的价格分批卖出，不要一次性砸出去")

    stop = _num(plan.get("stop_loss"))
    target = _num(plan.get("take_profit"))
    if decision_key in ("BUY", "OVERWEIGHT") and stop > 0 and entry > 0:
        do_list.append(
            f"跌到 {stop:.2f} 元（约 -{abs((stop - entry) / entry * 100):.1f}%）就认亏离场"
        )
    if decision_key in ("BUY", "OVERWEIGHT") and target > 0 and entry > 0:
        do_list.append(
            f"涨到 {target:.2f} 元（约 +{abs((target - entry) / entry * 100):.1f}%）可以了结"
        )

    # ---- 为什么（理由）----------------------------------------------
    # explained 记录已经解释过的词，避免同一个词在不同条目里重复加括号。
    explained: set[str] = set()
    why: list[str] = []
    for point in structured.get("key_points") or []:
        why.append(str(point))
    if not why:
        # 没有结构化理由时，优先用报告里的"执行摘要"要点 —— 那是作者
        # 自己写给人看的结论，比从决策卡里翻数字更像"理由"。
        why = extract_summary_bullets(decision_text)
    if not why:
        # 再退一步：从决策卡的"理由"里挑人能看懂的部分
        for reason in plan.get("reasons") or []:
            text = str(reason)
            if "触发策略" in text or "策略共识" in text:
                continue
            why.append(text)
    if not why:
        why = ["本次分析没有给出可复述的理由，建议展开下面的完整报告细看"]
    # 行话就地解释：让理由本身也能被普通人读懂。
    why = [explain_terms(item, used=explained) for item in why]
    # 结论卡只放"一眼能读完"的长度；完整段落仍在下面的正文里
    why = [_shorten(item, 140) for item in why]

    # ---- 风险 --------------------------------------------------------
    risks: list[str] = []
    for flag in structured.get("risk_flags") or []:
        risks.append(str(flag))
    if not risks:
        if plan.get("conflict"):
            risks.append("技术面规则和最终结论方向相反，两边都没把握")
        if not structured.get("structured"):
            risks.append("系统没有拿到完整的结构化结论（可能是回复被截断），可信度打折")
        if _num(plan.get("triggered")) <= 0:
            risks.append("这次没有触发任何买卖信号，说明时机还没到")
    if not risks:
        risks = ["主要风险是判断出错，请结合自己的资金和风险承受能力决定"]
    risks = [explain_terms(item, used=explained) for item in risks]

    # ---- 关键数字 ----------------------------------------------------
    numbers = _explain_prices(plan, decision_key)
    if _num(plan.get("risk_reward")) > 0:
        numbers.append(
            {
                "label": "风险收益比",
                "value": f"{_num(plan.get('risk_reward')):.2f}",
                "plain": _explain_risk_reward(_num(plan.get("risk_reward"))),
            }
        )
    if plan.get("score") is not None:
        numbers.append(
            {
                "label": "综合参考分",
                "value": f"{score:.0f} / 100",
                "plain": _explain_score(score),
            }
        )
    if plan.get("position_pct") is not None:
        position_text = (
            "这是建议买进的钱占全部资金的比例"
            if position_pct > 0
            else "本次不用动钱"
        )
        numbers.append(
            {
                "label": "建议仓位",
                "value": f"{position_pct:.0f}%",
                "plain": position_text,
            }
        )

    # ---- 术语对照 ----------------------------------------------------
    source_text = (decision_text or "") + "\n" + str(structured.get("summary") or "")
    terms = find_terms(source_text)

    # ---- 持仓建议 ----------------------------------------------------
    action_holding = ""
    if position_advice:
        advice = position_advice.get("advice") or {}
        action = advice.get("action") or ""
        delta = _num(advice.get("delta_qty"))
        if delta > 0:
            action_holding = f"{action}（买入 {delta:.0f} 股）"
        elif delta < 0:
            action_holding = f"{action}（卖出 {abs(delta):.0f} 股）"
        else:
            action_holding = f"{action}（维持 { _num(advice.get('current_qty')):.0f} 股不变）"

    return {
        "ticker": ticker,
        "decision": decision_key,
        "decision_label": meta["label"],
        "tone": meta["tone"],
        "headline": headline,
        "action": action,
        "action_holding": action_holding,
        "do_list": do_list,
        "why": why[:5],
        "risks": risks[:5],
        "numbers": numbers,
        "score_note": _explain_score(score) if plan.get("score") is not None else "",
        "confidence_note": _explain_confidence(confidence, bool(structured.get("structured"))),
        "terms": terms,
        "reliability": (
            "结论包含完整的结构化数据（评级/置信度/理由/引用）"
            if structured.get("structured")
            else "结论未包含结构化数据（可能是模型回复被截断），请以正文评级为准"
        ),
        "disclaimer": "以上为规则化整理的通俗解读，不构成投资建议。",
    }


def format_plain_summary(summary: dict | None) -> str:
    """把人话结论卡渲染成 Markdown（报告 / 导出 / 通知共用）。"""
    if not summary:
        return ""
    lines: list[str] = ["### 人话结论", ""]
    if summary.get("headline"):
        lines.append(f"**{summary['headline']}**")
        lines.append("")
    if summary.get("action"):
        lines.append(f"- **该怎么做**：{summary['action']}")
    if summary.get("action_holding"):
        lines.append(f"- **你手里有这只**：{summary['action_holding']}")

    do_list = summary.get("do_list") or []
    if do_list:
        lines.append("- **具体怎么做**：")
        lines.extend(f"    - {item}" for item in do_list)

    why = summary.get("why") or []
    if why:
        lines.append("- **为什么**：")
        lines.extend(f"    - {item}" for item in why)

    risks = summary.get("risks") or []
    if risks:
        lines.append("- **要当心**：")
        lines.extend(f"    - {item}" for item in risks)

    numbers = summary.get("numbers") or []
    if numbers:
        lines.append("- **关键数字**：")
        for item in numbers:
            lines.append(
                f"    - {item.get('label', '')} {item.get('value', '')}：{item.get('plain', '')}"
            )

    if summary.get("confidence_note"):
        lines.append(f"- **把握程度**：{summary['confidence_note']}")

    terms = summary.get("terms") or []
    if terms:
        # 用普通列表而不是 <details>：这段 Markdown 还要走导出/通知/报告中心，
        # 那些地方会过滤 HTML 标签（DOMPurify 白名单里没有 details）。
        lines.append("")
        lines.append("**术语对照**（看不懂的词都在这里）：")
        lines.extend(f"- **{item['term']}**：{item['meaning']}" for item in terms)

    lines.append("")
    lines.append(f"> {summary.get('disclaimer', '仅供参考。')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 可选：LLM 白话改写
# ---------------------------------------------------------------------------
_REWRITE_PROMPT = """你是一名面向普通散户的写作助手。请把下面这份股票分析报告改写成普通人能读懂的白话版。

要求：
1. 保留原文所有**结论、数字、价位、仓位比例**，一个都不能改；不要新增任何原文没有的事实或判断。
2. 结论先行：第一句就说清楚"买 / 卖 / 持有"，以及该做什么。
3. 把行话换成大白话；确实必须保留的术语（如 T+1）第一次出现时用括号解释一句。
4. 用短句和分条，不要大段连续文字；全文不超过 800 字。
5. 保留原有的引用标记（如 [公告]、[行情]）。
6. 直接输出改写后的 Markdown 正文，不要写"以下是改写"之类的开场白。
7. 如果原文信息不足，就照实说"数据不足"，不要脑补。

--- 原始报告 ---
{report}
"""


def rewrite_in_plain_language(
    report: str,
    llm: Any = None,
    ticker: str = "",
) -> str:
    """用 LLM 把整篇报告改写成白话版（可选功能）。

    参数
    ----
    report:
        原始报告文本。
    llm:
        已构造好的 LangChain 兼容聊天模型；``None`` 时直接返回空字符串
        （调用方据此判断"没有白话版"）。
    ticker:
        股票代码（可选，仅用于提示语）。

    返回
    ----
    str
        白话版正文；失败或未启用时返回 ``""``。
    """
    if not report or llm is None:
        return ""
    subject = f"以下是 {ticker} 的分析报告。\n\n" if ticker else ""
    try:
        response = llm.invoke(_REWRITE_PROMPT.format(report=subject + report))
        content = getattr(response, "content", response)
        return str(content or "").strip()
    except Exception:  # noqa: BLE001 - 改写失败不应影响主流程
        return ""


__all__ = [
    "GLOSSARY",
    "DECISION_PLAIN",
    "build_plain_summary",
    "explain_terms",
    "find_terms",
    "format_plain_summary",
    "rewrite_in_plain_language",
    "strip_structured_block",
]
