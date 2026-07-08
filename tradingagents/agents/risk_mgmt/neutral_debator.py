# ============================================================================
# 模块：中性风险分析师 (Neutral Risk Debator)
# 角色：风险辩论中的"中立第三方"，提供平衡、数据驱动的风险评估，
#       调和激进和保守两方的极端观点
# 核心功能：
#   1. 评估交易员的交易方案
#   2. 提供平衡、数据驱动的风险评估
#   3. 调和激进和保守两方的观点
#   4. 寻找两方论点中的合理部分，提出折中方案
# 技术栈：LangChain 的 LLM 直接调用（invoke）、LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    中性风险分析师用 LLM 来"平衡"激进和保守两方的观点，提供客观评估。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。中性风险分析师不使用工具，
#    而是直接用 LLM 分析交易方案和辩论历史，生成平衡论点。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    中性风险分析师只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。中性风险分析师是图中的一个节点，
#    与激进和保守分析师交替发言，形成三方风险辩论。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    中性风险分析师从黑板上读取：
#      - risk_debate_state（风险辩论状态：完整历史、各方最新论点等）
#      - trader_plan（交易员的交易方案，需要评估的对象）
#    中性风险分析师写回到黑板上：
#      - risk_debate_state（更新辩论历史、中性方最新论点）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_risk_neutral(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层负责"配置"（传入 llm），内层负责"执行"（接收 state）。
#
# 7. 什么是风险辩论机制？
#    本系统使用三方风险辩论来评估交易方案：
#    - 激进风险分析师（Aggressive）：主张高风险高回报
#    - 保守风险分析师（Conservative）：主张谨慎和风险规避
#    - 中性风险分析师（Neutral）：提供平衡、数据驱动的评估 ← 本文件
#    三方交替发言，互相辩论。投资组合经理最后综合评估，做出最终决策。
#
#    为什么需要"中性方"？
#    激进和保守两方容易陷入极端。中性方作为"调解人"：
#    1. 找出双方论点中的合理部分
#    2. 用客观数据调和两方分歧
#    3. 提出平衡的折中方案
#    4. 防止辩论陷入"非此即彼"的僵局
#    这种"三方平衡"机制是多 Agent 系统提高决策质量的关键设计。
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
# 工厂函数：create_risk_neutral
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建中性风险分析师需要传入 LLM 实例。
# 工厂函数模式解决这个矛盾：
#   外层 create_risk_neutral(llm) 接收 llm，
#   内层 neutral_debator_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 变量。
# ============================================================================
def create_risk_neutral(llm):
    """
    创建中性风险分析师节点函数（工厂函数模式）。

    中性风险分析师扮演"中立第三方"角色，在风险辩论中：
    - 评估交易员的交易方案
    - 提供平衡、数据驱动的风险评估
    - 调和激进和保守两方的观点

    参数：
        llm: 大语言模型实例，驱动推理和辩论
    """

    # ========================================================================
    # 内层节点函数：neutral_debator_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def neutral_debator_node(state) -> dict:
        """
        中性风险分析师节点：LangGraph 工作流中的标准节点函数。

        每次调用时，中性风险分析师会：
        1. 查看风险辩论历史和各方最新论点
        2. 阅读交易员的交易方案
        3. 生成中性方论点（平衡评估、调和分歧）
        4. 更新风险辩论状态

        参数：
            state: 状态字典，包含：
                - risk_debate_state: 风险辩论状态
                - trader_plan: 交易员的交易方案

        【读取的状态字段】：
            - state["risk_debate_state"]：风险辩论状态
              - .history：完整辩论历史
              - .neutral_history：中性方自己的发言历史
              - .current_neutral_response：中性方最新论点
              - .current_aggressive_response：激进方最新论点（需回应）
              - .current_conservative_response：保守方最新论点（需回应）
              - .latest_speaker：最后发言者
              - .count：辩论轮数
            - state["trader_plan"]：交易员的交易方案

        【写入的状态字段】：
            - "risk_debate_state"：更新后的辩论状态
              - history：追加了中性方新论点
              - neutral_history：追加了中性方新论点
              - current_neutral_response：更新为中性方最新论点
              - latest_speaker：设为 "Neutral"
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
        neutral_history = risk_debate_state.get("neutral_history", "")
        latest_speaker = risk_debate_state.get("latest_speaker", "")
        current_response = risk_debate_state.get("current_response", "")
        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

        # 提取交易员的交易方案
        # 这是中性风险分析师需要评估的交易方案
        trader_plan = state["trader_plan"]

        # ====================================================================
        # 确定中性分析师需要回应的"对手论点"
        #
        # 如果最后发言者是激进分析师（Aggressive）：
        #   → 中性需要回应激进的论点
        # 如果最后发言者是保守分析师（Conservative）：
        #   → 中性需要回应保守的论点
        #
        # 这确保了辩论的"对话性"——总是直接回应上一个发言者的观点
        # ====================================================================
        if latest_speaker == "Aggressive":
            opponent_response = current_aggressive_response
        else:
            opponent_response = current_conservative_response

        # ====================================================================
        # 构建中性分析师的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。中性分析师用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："You are a Neutral Risk Analyst. Your role is to provide
        #          a balanced, data-driven assessment of the proposed trade."
        #   → 角色定义：中性风险分析师。
        #     核心任务：提供平衡、数据驱动的评估。
        #     关键词"balanced"和"data-driven"强调客观性。
        #
        # 第 2 段："Weigh both the aggressive and conservative arguments
        #          and find the middle ground."
        #   → 任务定义：权衡激进和保守两方的论点，寻找中间立场。
        #     关键词"middle ground"要求找到折中方案，不是简单倒向某一方。
        #
        # 第 3 段："Acknowledge valid points from both sides and propose a
        #          compromise that balances risk and reward."
        #   → 策略 1：承认双方的合理观点，提出平衡风险和回报的折中方案。
        #     关键词"Acknowledge"要求尊重对方的有效论点，
        #     而不是一味否定——这是中性分析师与激进的"dismantle"和
        #     保守的"challenge"的区别。
        #
        # 第 4 段："Engage with the latest argument and clarify where each
        #          side may be right or wrong."
        #   → 策略 2：回应最新论点，澄清各方哪里对、哪里错。
        #     中性分析师的职责是"裁判"——判断谁说得有理。
        #
        # 第 5 段："Be objective and concise, supporting your conclusions
        #          with relevant data."
        #   → 风格要求：客观、简洁，用相关数据支撑结论。
        #     关键词"objective"和"concise"强调中立和效率。
        #
        # 注入的变量：
        #   {trader_plan}：交易员的交易方案（评估对象）
        #   {history}：完整辩论历史（让 LLM 看到之前的辩论）
        #   {opponent_response}：对手最新论点（需要回应的对象）
        #
        # 与激进和保守分析师的对比：
        #   激进："dismantle their points"（拆解对方）
        #   保守："challenge their risk appetite"（质疑对方）
        #   中性："Acknowledge valid points from both sides"（承认双方合理点）← 更温和
        #
        #   激进："persuasive and confident"（有说服力、自信）
        #   保守："firm and analytical"（坚定、分析性）
        #   中性："objective and concise"（客观、简洁）← 更中立
        # ====================================================================
        prompt = f"""You are a Neutral Risk Analyst. Your role is to provide a balanced, data-driven assessment of the proposed trade. Weigh both the aggressive and conservative arguments and find the middle ground.

Acknowledge valid points from both sides and propose a compromise that balances risk and reward. Engage with the latest argument and clarify where each side may be right or wrong.

Be objective and concise, supporting your conclusions with relevant data.

Trader's proposed plan:
{trader_plan}

Conversation history of the debate:
{history}

Latest opponent argument:
{opponent_response}
"""

        # ====================================================================
        # LLM 直接调用，生成中性方论点
        #
        # 【什么是 llm.invoke()？】
        # 中性分析师不使用工具，直接进行推理和辩论。
        # llm.invoke(prompt) 将提示词发送给 LLM 并返回推理结果。
        # response.content 是 LLM 生成的文本回复（中性方论点）。
        # ====================================================================
        response = llm.invoke(prompt)

        # 为论点添加发言者标签，方便在辩论历史中区分不同角色
        # 辩论历史中的格式：
        #   "Aggressive Analyst: ..."（激进方发言）
        #   "Conservative Analyst: ..."（保守方发言）
        #   "Neutral Analyst: ..."（中性方发言）
        argument = f"Neutral Analyst: {response.content}"

        # ====================================================================
        # 更新风险辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 返回 {"risk_debate_state": new_risk_debate_state}
        # → LangGraph 会用 new_risk_debate_state 替换全局的 risk_debate_state
        #
        # 【状态更新逻辑】
        # 1. 将新论点追加到完整辩论历史（history）和中性方历史（neutral_history）
        # 2. 将新论点设为 current_neutral_response
        # 3. latest_speaker 设为 "Neutral"（当前发言者）
        # 4. count + 1（辩论轮数）
        # 5. 保留其他方的当前论点不变（不修改 aggressive 和 conservative 的论点）
        #
        # 【为什么要保留所有字段？】
        # 返回的字典会替换全局状态中的 risk_debate_state。
        # 如果不保留原有字段，辩论历史和其他方的论点会丢失。
        # 所以需要把所有字段都写进新字典。
        #
        # 与激进和保守分析师的对称性：
        # 激进更新 aggressive_history + current_aggressive_response
        # 保守更新 conservative_history + current_conservative_response
        # 中性更新 neutral_history + current_neutral_response ← 本文件
        # ====================================================================
        new_risk_debate_state = {
            "history": history + "\n" + argument,                                              # 追加到完整辩论历史
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),             # 保留激进方历史
            "conservative_history": risk_debate_state.get("conservative_history", ""),         # 保留保守方历史
            "neutral_history": neutral_history + "\n" + argument,                              # 追加到中性方历史
            "latest_speaker": "Neutral",                                                          # 当前发言者：中性方
            "current_aggressive_response": risk_debate_state.get("current_aggressive_response", ""),    # 保留激进方最新论点
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),  # 保留保守方最新论点
            "current_neutral_response": argument,                                                 # 更新中性方最新论点
            "count": risk_debate_state["count"] + 1,                                             # 辩论轮数 +1
        }

        # 返回状态更新，只更新 risk_debate_state
        return {"risk_debate_state": new_risk_debate_state}

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_risk_neutral 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("neutral_debator", create_risk_neutral(llm))
    # ========================================================================
    return neutral_debator_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设激进和保守分析师已各发言一轮
#
# 步骤 1：保守分析师发言后的状态
#   state = {
#       "trader_plan": "交易方案：在 $170 买入 100 股 AAPL...",
#       "risk_debate_state": {
#           "history": "Aggressive Analyst: 应该加倍仓位...\n
#                       Conservative Analyst: 加倍仓位是极其危险的！...",
#           "aggressive_history": "Aggressive Analyst: ...",
#           "conservative_history": "Conservative Analyst: ...",
#           "neutral_history": "",
#           "latest_speaker": "Conservative",
#           "current_aggressive_response": "Aggressive Analyst: ...",
#           "current_conservative_response": "Conservative Analyst: ...",
#           "current_neutral_response": "",
#           "count": 2,
#       }
#   }
#
# 步骤 2：LangGraph 调用 neutral_debator_node(state)
#   → 提取交易方案 trader_plan
#   → latest_speaker = "Conservative" → opponent_response = current_conservative_response
#   → 构建 prompt（注入交易方案、辩论历史、保守方论点）
#   → llm.invoke(prompt) → 生成中性方论点
#
# 步骤 3：LLM 返回中性方论点
#   response.content = "激进分析师指出 AAPL 基本面强劲是有道理的，
#                       但保守分析师对波动率的担忧也合理。
#                       建议折中：维持 100 股仓位，但收紧止损到 $163，
#                       并设置分批建仓策略以降低入场风险..."
#   argument = "Neutral Analyst: 激进分析师指出 AAPL 基本面强劲是有道理的..."
#
# 步骤 4：更新辩论状态并返回
#   new_risk_debate_state = {
#       "history": "Aggressive Analyst: ...\nConservative Analyst: ...\n
#                   Neutral Analyst: 激进分析师指出...",
#       "neutral_history": "Neutral Analyst: 激进分析师指出...",
#       "latest_speaker": "Neutral",
#       "count": 3,
#   }
#
# 步骤 5：LangGraph 检查辩论轮数
#   → 如果 count < max_risk_debate_rounds → 回到激进分析师（反驳中性方）
#   → 如果 count >= max_risk_debate_rounds → 路由到投资组合经理做最终决策
#
# 步骤 6：投资组合经理读取完整三方辩论历史，综合评估做出最终决策
#   → 最终决策可能是 "Buy" / "Overweight" / "Hold" / "Underweight" / "Sell"
#
# ============================================================================
