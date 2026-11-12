# ============================================================================
# 模块：激进风险分析师 (Aggressive Risk Debator)
# 角色：风险辩论中的"激进方"，主张高风险高回报策略，质疑过度保守的风险规避
# 核心功能：
#   1. 评估交易员的交易方案
#   2. 主张更高风险敞口和更激进的仓位配置
#   3. 与保守和中性风险分析师辩论
#   4. 挑战保守观点，强调增长潜力和机会成本
# 技术栈：LangChain 的 LLM 直接调用（invoke）、LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    激进风险分析师用 LLM 来"评估"交易方案、"主张"高风险高回报策略。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。激进风险分析师不使用工具，
#    而是直接用 LLM 分析交易方案和辩论历史，生成激进论点。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    激进风险分析师只用到了 llm.invoke() ——最基础的 LLM 调用方法。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。激进风险分析师是图中的一个节点，
#    与保守和中性分析师交替发言，形成三方风险辩论。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    激进风险分析师从黑板上读取：
#      - risk_debate_state（风险辩论状态：完整历史、各方最新论点等）
#      - trader_plan（交易员的交易方案，需要评估的对象）
#    激进风险分析师写回到黑板上：
#      - risk_debate_state（更新辩论历史、激进方最新论点）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_risk_aggressive(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层负责"配置"（传入 llm），内层负责"执行"（接收 state）。
#
# 7. 什么是风险辩论机制？
#    本系统使用三方风险辩论来评估交易方案：
#    - 激进风险分析师（Aggressive）：主张高风险高回报 ← 本文件
#    - 保守风险分析师（Conservative）：主张谨慎和风险规避
#    - 中性风险分析师（Neutral）：提供平衡、数据驱动的评估
#    三方交替发言，互相辩论。投资组合经理最后综合评估，做出最终决策。
#
#    与牛熊研究员辩论的区别：
#    - 牛熊辩论：讨论"应该买还是卖"（投资方向）
#    - 风险辩论：讨论"这个交易方案的风险如何"（风险评估）
#    风险辩论发生在交易员制定方案之后，是对方案的"压力测试"。
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



# ============================================================================
# 工厂函数：create_risk_aggressive
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建激进风险分析师需要传入 LLM 实例。
# 工厂函数模式解决这个矛盾：
#   外层 create_risk_aggressive(llm) 接收 llm，
#   内层 aggressive_debator_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 变量。
# ============================================================================
def create_risk_aggressive(llm):
    """
    创建激进风险分析师节点函数（工厂函数模式）。

    激进风险分析师扮演"激进方"角色，在风险辩论中：
    - 评估交易员的交易方案
    - 主张更高风险敞口和更激进的仓位
    - 挑战保守和中性分析师的谨慎观点

    参数：
        llm: 大语言模型实例，驱动推理和辩论
    """

    # ========================================================================
    # 内层节点函数：aggressive_debator_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def aggressive_debator_node(state) -> dict:
        """
        激进风险分析师节点：LangGraph 工作流中的标准节点函数。

        每次调用时，激进风险分析师会：
        1. 查看风险辩论历史和各方最新论点
        2. 阅读交易员的交易方案
        3. 生成激进方论点（主张更高风险、更大仓位）
        4. 更新风险辩论状态

        参数：
            state: 状态字典，包含：
                - risk_debate_state: 风险辩论状态
                - trader_plan: 交易员的交易方案

        【读取的状态字段】：
            - state["risk_debate_state"]：风险辩论状态
              - .history：完整辩论历史
              - .aggressive_history：激进方自己的发言历史
              - .current_aggressive_response：激进方最新论点
              - .current_conservative_response：保守方最新论点（需反驳）
              - .current_neutral_response：中性方最新论点（需回应）
              - .latest_speaker：最后发言者
              - .count：辩论轮数
            - state["trader_plan"]：交易员的交易方案

        【写入的状态字段】：
            - "risk_debate_state"：更新后的辩论状态
              - history：追加了激进方新论点
              - aggressive_history：追加了激进方新论点
              - current_aggressive_response：更新为激进方最新论点
              - latest_speaker：设为 "Aggressive"
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
        aggressive_history = risk_debate_state.get("aggressive_history", "")
        latest_speaker = risk_debate_state.get("latest_speaker", "")
        risk_debate_state.get("current_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

        # 提取交易员的交易方案
        # 这是激进风险分析师需要评估的交易方案
        trader_plan = state["trader_investment_plan"]

        # ====================================================================
        # 确定激进分析师需要回应的"对手论点"
        #
        # 如果最后发言者是保守分析师（Conservative）：
        #   → 激进需要回应保守的论点
        # 如果最后发言者是中性分析师（Neutral）：
        #   → 激进需要回应中性的论点
        #
        # 这确保了辩论的"对话性"——总是直接回应上一个发言者的观点
        # ====================================================================
        if latest_speaker == "Conservative":
            opponent_response = current_conservative_response
        else:
            opponent_response = current_neutral_response

        # ====================================================================
        # 构建激进分析师的提示词（Prompt）
        #
        # 【什么是提示词？】
        # 提示词是发送给 LLM 的指令文本。激进分析师用 f-string 直接构建。
        #
        # 【逐段解析提示词为什么这样设计】
        #
        # 第 1 段："You are an Aggressive Risk Analyst. Your role is to
        #          challenge conservative risk assessments and advocate for
        #          higher risk positions."
        #   → 角色定义：激进风险分析师。
        #     核心任务：挑战保守评估，主张更高风险仓位。
        #
        # 第 2 段："Evaluate the proposed trade and argue why the trader
        #          should take on MORE risk to maximize returns."
        #   → 任务定义：评估交易方案，论证为什么应该承担更多风险来最大化回报。
        #
        # 第 3 段："Question overly conservative risk management practices
        #          and emphasize the opportunity cost of being too cautious."
        #   → 策略 1：质疑过度保守的做法，强调过度谨慎的机会成本。
        #     机会成本 = 因为太保守而错过的收益。
        #
        # 第 4 段："Engage directly with the latest argument from the
        #          conservative or neutral analyst and dismantle their
        #          points with evidence and confidence."
        #   → 策略 2：直接回应保守或中性的最新论点，用证据和自信驳斥。
        #     关键词"dismantle"要求有力地拆解对方论点。
        #
        # 第 5 段："Be persuasive and confident, but always back your claims
        #          with data and reasoning."
        #   → 风格要求：有说服力、自信，但必须用数据和推理支撑。
        #
        # 注入的变量：
        #   {trader_plan}：交易员的交易方案（评估对象）
        #   {history}：完整辩论历史（让 LLM 看到之前的辩论）
        #   {opponent_response}：对手最新论点（需要反驳的对象）
        # ====================================================================
        prompt = f"""你是一名激进风险分析师。你的职责是质疑保守的风险评估，并主张更高的风险敞口。请评估提议的交易，论证为何交易员应当承担更多风险以最大化收益。

质疑过度保守的风险管理做法，并强调过于谨慎所带来的机会成本。直接针对保守方或中性分析师的最新论点，用证据和自信将其观点逐一拆解。

要有说服力、有信心，但始终用数据和推理支撑你的论断。

交易员提议的计划：
{trader_plan}

辩论对话历史：
{history}

对手最新论点：
{opponent_response}
"""

        # [英文备份] prompt (en):
        # prompt = f"""You are an Aggressive Risk Analyst. Your role is to challenge conservative risk assessments and advocate for higher risk positions. Evaluate the proposed trade and argue why the trader should take on MORE risk to maximize returns.
        #
        # Question overly conservative risk management practices and emphasize the opportunity cost of being too cautious. Engage directly with the latest argument from the conservative or neutral analyst and dismantle their points with evidence and confidence.
        #
        # Be persuasive and confident, but always back your claims with data and reasoning.
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
        # LLM 直接调用，生成激进方论点
        #
        # 【什么是 llm.invoke()？】
        # 激进分析师不使用工具，直接进行推理和辩论。
        # llm.invoke(prompt) 将提示词发送给 LLM 并返回推理结果。
        # response.content 是 LLM 生成的文本回复（激进方论点）。
        # ====================================================================
        response = llm.invoke(prompt)

        # 为论点添加发言者标签，方便在辩论历史中区分不同角色
        # 辩论历史中的格式：
        #   "Aggressive Analyst: ..."（激进方发言）
        #   "Conservative Analyst: ..."（保守方发言）
        #   "Neutral Analyst: ..."（中性方发言）
        argument = f"Aggressive Analyst: {response.content}"

        # ====================================================================
        # 更新风险辩论状态
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        # 返回 {"risk_debate_state": new_risk_debate_state}
        # → LangGraph 会用 new_risk_debate_state 替换全局的 risk_debate_state
        #
        # 【状态更新逻辑】
        # 1. 将新论点追加到完整辩论历史（history）和激进方历史（aggressive_history）
        # 2. 将新论点设为 current_aggressive_response
        # 3. latest_speaker 设为 "Aggressive"（当前发言者）
        # 4. count + 1（辩论轮数）
        # 5. 保留其他方的当前论点不变（不修改 conservative 和 neutral 的论点）
        #
        # 【为什么要保留所有字段？】
        # 返回的字典会替换全局状态中的 risk_debate_state。
        # 如果不保留原有字段，辩论历史和其他方的论点会丢失。
        # 所以需要把所有字段都写进新字典。
        # ====================================================================
        new_risk_debate_state = {
            "history": history + "\n" + argument,                                              # 追加到完整辩论历史
            "aggressive_history": aggressive_history + "\n" + argument,                         # 追加到激进方历史
            "conservative_history": risk_debate_state.get("conservative_history", ""),         # 保留保守方历史
            "neutral_history": risk_debate_state.get("neutral_history", ""),                     # 保留中性方历史
            "latest_speaker": "Aggressive",                                                      # 当前发言者：激进方
            "current_aggressive_response": argument,                                             # 更新激进方最新论点
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),  # 保留保守方最新论点
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),            # 保留中性方最新论点
            "count": risk_debate_state["count"] + 1,                                             # 辩论轮数 +1
        }

        # 返回状态更新，只更新 risk_debate_state
        return {"risk_debate_state": new_risk_debate_state}

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_risk_aggressive 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("aggressive_debator", create_risk_aggressive(llm))
    # ========================================================================
    return aggressive_debator_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设交易员提出了方案：在 $170 买入 AAPL
#
# 步骤 1：交易员方案完成后的状态
#   state = {
#       "trader_plan": "交易方案：在 $170 买入 100 股 AAPL，止损 $165...",
#       "risk_debate_state": {
#           "history": "",
#           "aggressive_history": "",
#           "conservative_history": "",
#           "neutral_history": "",
#           "latest_speaker": "",
#           "current_aggressive_response": "",
#           "current_conservative_response": "",
#           "current_neutral_response": "",
#           "count": 0,
#       }
#   }
#
# 步骤 2：LangGraph 调用 aggressive_debator_node(state)（激进方先发言）
#   → 提取交易方案 trader_plan
#   → latest_speaker 为空 → opponent_response = current_neutral_response（也为空）
#   → 构建 prompt（注入交易方案、辩论历史、对手论点）
#   → llm.invoke(prompt) → 生成激进方论点
#
# 步骤 3：LLM 返回激进方论点
#   response.content = "这个交易方案过于保守！考虑到 AAPL 的强劲基本面，
#                       应该加倍仓位到 200 股，取消止损以避免被洗出..."
#   argument = "Aggressive Analyst: 这个交易方案过于保守！..."
#
# 步骤 4：更新辩论状态并返回
#   new_risk_debate_state = {
#       "history": "Aggressive Analyst: 这个交易方案过于保守！...",
#       "aggressive_history": "Aggressive Analyst: 这个交易方案过于保守！...",
#       ...其他字段保持不变或为空...
#       "latest_speaker": "Aggressive",
#       "count": 1,
#   }
#
# 步骤 5：LangGraph 路由到保守或中性分析师
#   → 保守分析师读取 current_aggressive_response 作为反驳对象
#   → 三方交替辩论，直到达到最大辩论轮数
#   → 最后路由到投资组合经理做最终决策
#
# ============================================================================
