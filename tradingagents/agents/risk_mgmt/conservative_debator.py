# ============================================================================
# 模块：保守风险分析师 (Conservative Risk Debator)
# 角色：风险辩论中的"保守方"，主张谨慎和风险规避，质疑激进策略的下行风险
# 核心功能：
#   1. 评估交易员的交易方案
#   2. 强调下行风险、止损策略和仓位控制
#   3. 与激进和中性风险分析师辩论
#   4. 挑战激进观点，强调风险管理和资本保护
# 技术栈：LangChain 的 LLM 直接调用（invoke）、LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    保守风险分析师用 LLM 来"评估"交易方案、"强调"下行风险和风险规避。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。保守风险分析师不使用工具，
#    而是直接用 LLM 分析交易方案和辩论历史，生成保守论点。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    保守风险分析师只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。保守风险分析师是图中的一个节点，
#    与激进和中性分析师交替发言，形成三方风险辩论。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    保守风险分析师从黑板上读取：
#      - risk_debate_state（风险辩论状态：完整历史、各方最新论点等）
#      - trader_plan（交易员的交易方案，需要评估的对象）
#    保守风险分析师写回到黑板上：
#      - risk_debate_state（更新辩论历史、保守方最新论点）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_risk_conservative(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层负责"配置"（传入 llm），内层负责"执行"（接收 state）。
#
# 7. 什么是风险辩论机制？
#    本系统使用三方风险辩论来评估交易方案：
#    - 激进风险分析师（Aggressive）：主张高风险高回报
#    - 保守风险分析师（Conservative）：主张谨慎和风险规避 ← 本文件
#    - 中性风险分析师（Neutral）：提供平衡、数据驱动的评估
#    三方交替发言，互相辩论。投资组合经理最后综合评估，做出最终决策。
#
#    为什么需要"保守方"？
#    激进方可能过度乐观，忽视下行风险。保守方作为"刹车"，
#    确保系统不会因为过度自信而做出过于冒险的决策。
#    这种"平衡机制"是多 Agent 系统的核心价值之一。
#
# 8. 什么是 llm.invoke()？
#    invoke() 是 LLM 的最基础调用方法：传入文本提示词，返回文本回复。
#
# 9. 系统中的整体流程（本节点的位置）
#    用户输入 → [分析师] → [牛熊辩论] → [研究经理] → [交易员] → [风险辩论] ← 本文件
#                                                                ↓
#                                                        [组合经理最终决策]
#
# ============================================================================

import time
import json


# ============================================================================
# 工厂函数：create_risk_conservative
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建保守风险分析师需要传入 LLM 实例。
# 工厂函数模式解决这个矛盾：
#   外层 create_risk_conservative(llm) 接收 llm，
#   内层 conservative_debator_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 变量。
# ============================================================================
def create_risk_conservative(llm):
    """
    创建保守风险分析师节点函数（工厂函数模式）。

    保守风险分析师扮演"保守方"角色，在风险辩论中：
    - 评估交易员的交易方案
    - 强调下行风险、止损策略和仓位控制
    - 挑战激进和中性分析师的乐观观点

    参数：
        llm: 大语言模型实例，驱动推理和辩论
    """

    # ========================================================================
    # 内层节点函数：conservative_debator_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def conservative_debator_node(state) -> dict:
        """
        保守风险分析师节点：LangGraph 工作流中的标准节点函数。

        每次调用时，保守风险分析师会：
        1. 查看风险辩论历史和各方最新论点
        2. 阅读交易员的交易方案
        3. 生成保守方论点（强调下行风险、止损、仓位控制）
        4. 更新风险辩论状态

        参数：
            state: 状态字典，包含：
                - risk_debate_state: 风险辩论状态
                - trader_plan: 交易员的交易方案

        【读取的状态字段】：
            - state["risk_debate_state"]：风险辩论状态
              - .history：完整辩论历史
              - .conservative_history：保守方自己的发言历史
              - .current_conservative_response：保守方最新论点
              - .current_aggressive_response：激进方最新论点（需反驳）
              - .current_neutral_response：中性方最新论点（需回应）
              - .latest_speaker：最后发言者
              - .count：辩论轮数
            - state["trader_plan"]：交易员的交易方案

        【写入的状态字段】：
            - "risk_debate_state"：更新后的辩论状态
              - history：追加了保守方新论点
              - conservative_history：追加了保守方新论点
              - current_conservative_response：更新为保守方最新论点
              - latest_speaker：设为 "Conservative"
              - count：辩论轮数 +1
        """
        # 获取风险辩论的当前状态
        # risk_debate_state 包含：
        #   - history: 完整的辩论历史
        #   - aggressive_history: 激进分析师的发言历史
        #   - conservative_history: 保守分析师的发言历史
        #   - neutral_history: 中性分析师的发言历史
        #   - current_aggressive_response: 激进分析师的最新论点
        #   - current_conservative_response: 保守分析师的最新论点
        #   - current_neutral_response: 中性分析师的最新论点
        #   - latest_speaker: 最后发言者
        #   - count: 辩论轮数
        risk_debate_state = state["risk_debate_state"]

        # 提取辩论历史和各方最新论点
        # 使用 .get("key", "") 提供默认值，防止状态为空时报错
        history = risk_debate_state.get("history", "")
        conservative_history = risk_debate_state.get("conservative_history", "")
        latest_speaker = risk_debate_state.get("latest_speaker", "")
        current_response = risk_debate_state.get("current_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")
        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")

        # 提取交易员的交易方案
        # 这是保守风险分析师需要评估的交易方案
        trader_plan = state["trader_investment_plan"]

        # ====================================================================
        # 确定保守分析师需要回应的"对手论点"
        #
        # 如果最后发言者是激进分析师（Aggressive）：
        #   → 保守需要回应激进的论点
        # 如果最后发言者是中性分析师（Neutral）：
        #   → 保守需要回应中性的论点
        #
        # 这确保了辩论的"对话性"——总是直接回应上一个发言者的观点
        # ====================================================================
        if latest_speaker == "Aggressive":
            opponent_response = current_aggressive_response
        else:
            opponent_response = current_neutral_response

        # ====================================================================
        # 构建保守分析师的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。保守分析师用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："You are a Conservative Risk Analyst. Your role is to
        #          identify downside risks and advocate for caution."
        #   → 角色定义：保守风险分析师。
        #     核心任务：识别下行风险，主张谨慎。
        #
        # 第 2 段："Evaluate the proposed trade and argue why the trader
        #          should reduce risk exposure or implement stricter risk
        #          controls."
        #   → 任务定义：评估交易方案，论证为什么应该减少风险敞口
        #     或实施更严格的风险控制。
        #
        # 第 3 段："Question overly optimistic assumptions and emphasize
        #          potential losses, volatility, and worst-case scenarios."
        #   → 策略 1：质疑过度乐观的假设，强调潜在损失、波动性和最坏情况。
        #     最坏情况分析是保守分析师的核心工作——"如果出了问题怎么办？"
        #
        # 第 4 段："Engage directly with the latest argument from the
        #          aggressive or neutral analyst and challenge their
        #          risk appetite."
        #   → 策略 2：直接回应激进或中性的最新论点，挑战他们的风险偏好。
        #     关键词"challenge"要求有力地质疑对方的乐观假设。
        #
        # 第 5 段："Be firm and analytical, supporting your position with
        #          concrete risk metrics and historical precedents."
        #   → 风格要求：坚定且分析性强，用具体的风险指标和历史先例支撑立场。
        #     保守论点需要有依据，不能只是"我觉得太冒险了"。
        #
        # 注入的变量：
        #   {trader_plan}：交易员的交易方案（评估对象）
        #   {history}：完整辩论历史（让 LLM 看到之前的辩论）
        #   {opponent_response}：对手最新论点（需要反驳的对象）
        #
        # 与激进分析师的对称性：
        #   激进关注"增长潜力、机会成本" ↔ 保守关注"下行风险、潜在损失"
        #   激进"质疑保守" ↔ 保守"质疑激进"
        #   激进"dismantle" ↔ 保守"challenge"
        # ====================================================================
        prompt = f"""你是一名保守风险分析师。你的职责是识别下行风险并主张谨慎行事。请评估提议的交易，论证为何交易员应当降低风险敞口或实施更严格的风险控制。

质疑过度乐观的假设，并强调潜在损失、波动性和最坏情形。直接针对激进方或中性分析师的最新论点，挑战他们的风险偏好。

要坚定且注重分析，用具体的风险指标和历史先例支撑你的立场。

交易员提议的计划：
{trader_plan}

辩论对话历史：
{history}

对手最新论点：
{opponent_response}
"""

        # [英文备份] prompt (en):
        # prompt = f"""You are a Conservative Risk Analyst. Your role is to identify downside risks and advocate for caution. Evaluate the proposed trade and argue why the trader should reduce risk exposure or implement stricter risk controls.
        #
        # Question overly optimistic assumptions and emphasize potential losses, volatility, and worst-case scenarios. Engage directly with the latest argument from the aggressive or neutral analyst and challenge their risk appetite.
        #
        # Be firm and analytical, supporting your position with concrete risk metrics and historical precedents.
        #
        # Trader's proposed plan:
        # {trader_plan}
        #
        # Conversation history of the debate:
        # {history}
        #
        # Latest opponent argument:
        # {opponent_response}
        # """

        # ====================================================================
        # LLM 直接调用，生成保守方论点
        #
        # 【什么是 llm.invoke()？】
        # 保守分析师不使用工具，直接进行推理和辩论。
        # llm.invoke(prompt) 将提示词发送给 LLM 并返回推理结果。
        # response.content 是 LLM 生成的文本回复（保守方论点）。
        # ====================================================================
        response = llm.invoke(prompt)

        # 为论点添加发言者标签，方便在辩论历史中区分不同角色
        # 辩论历史中的格式：
        #   "Aggressive Analyst: ..."（激进方发言）
        #   "Conservative Analyst: ..."（保守方发言）
        #   "Neutral Analyst: ..."（中性方发言）
        argument = f"Conservative Analyst: {response.content}"

        # ====================================================================
        # 更新风险辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 返回 {"risk_debate_state": new_risk_debate_state}
        # → LangGraph 会用 new_risk_debate_state 替换全局的 risk_debate_state
        #
        # 【状态更新逻辑】
        # 1. 将新论点追加到完整辩论历史（history）和保守方历史（conservative_history）
        # 2. 将新论点设为 current_conservative_response
        # 3. latest_speaker 设为 "Conservative"（当前发言者）
        # 4. count + 1（辩论轮数）
        # 5. 保留其他方的当前论点不变（不修改 aggressive 和 neutral 的论点）
        #
        # 【为什么要保留所有字段？】
        # 返回的字典会替换全局状态中的 risk_debate_state。
        # 如果不保留原有字段，辩论历史和其他方的论点会丢失。
        # 所以需要把所有字段都写进新字典。
        #
        # 与激进分析师的对称性：
        # 激进更新 aggressive_history + current_aggressive_response
        # 保守更新 conservative_history + current_conservative_response
        # ====================================================================
        new_risk_debate_state = {
            "history": history + "\n" + argument,                                              # 追加到完整辩论历史
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),             # 保留激进方历史
            "conservative_history": conservative_history + "\n" + argument,                    # 追加到保守方历史
            "neutral_history": risk_debate_state.get("neutral_history", ""),                     # 保留中性方历史
            "latest_speaker": "Conservative",                                                    # 当前发言者：保守方
            "current_aggressive_response": risk_debate_state.get("current_aggressive_response", ""),    # 保留激进方最新论点
            "current_conservative_response": argument,                                            # 更新保守方最新论点
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),            # 保留中性方最新论点
            "count": risk_debate_state["count"] + 1,                                             # 辩论轮数 +1
        }

        # 返回状态更新，只更新 risk_debate_state
        return {"risk_debate_state": new_risk_debate_state}

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_risk_conservative 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("conservative_debator", create_risk_conservative(llm))
    # ========================================================================
    return conservative_debator_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设激进分析师已发言，主张加大仓位
#
# 步骤 1：激进分析师发言后的状态
#   state = {
#       "trader_plan": "交易方案：在 $170 买入 100 股 AAPL...",
#       "risk_debate_state": {
#           "history": "Aggressive Analyst: 这个交易方案过于保守！应该加倍仓位...",
#           "aggressive_history": "Aggressive Analyst: ...",
#           "conservative_history": "",
#           "neutral_history": "",
#           "latest_speaker": "Aggressive",
#           "current_aggressive_response": "Aggressive Analyst: ...",
#           "current_conservative_response": "",
#           "current_neutral_response": "",
#           "count": 1,
#       }
#   }
#
# 步骤 2：LangGraph 调用 conservative_debator_node(state)
#   → 提取交易方案 trader_plan
#   → latest_speaker = "Aggressive" → opponent_response = current_aggressive_response
#   → 构建 prompt（注入交易方案、辩论历史、激进方论点）
#   → llm.invoke(prompt) → 生成保守方反驳论点
#
# 步骤 3：LLM 返回保守方论点
#   response.content = "加倍仓位是极其危险的！AAPL 的波动率在上升，
#                       如果回调到 $160，200 股的损失将是 $2000...
#                       历史上类似的激进策略曾多次导致大幅亏损..."
#   argument = "Conservative Analyst: 加倍仓位是极其危险的！..."
#
# 步骤 4：更新辩论状态并返回
#   new_risk_debate_state = {
#       "history": "Aggressive Analyst: ...\nConservative Analyst: 加倍仓位是极其危险的！...",
#       "conservative_history": "Conservative Analyst: 加倍仓位是极其危险的！...",
#       "latest_speaker": "Conservative",
#       "count": 2,
#   }
#
# 步骤 5：LangGraph 路由到中性分析师或激进分析师
#   → 三方交替辩论，直到达到最大辩论轮数
#   → 最后路由到投资组合经理做最终决策
#
# ============================================================================
