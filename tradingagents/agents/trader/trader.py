# ============================================================================
# 模块：交易员 (Trader)
# 角色：负责基于研究经理的投资计划，制定具体的交易执行方案
# 核心功能：
#   1. 接收研究经理的投资计划（买入/卖出/持有的建议）
#   2. 制定具体的交易执行方案（入场策略、仓位大小、止损止盈等）
#   3. 输出交易指令，供风险分析师评估
# 技术栈：LangChain 的 ChatPromptTemplate、MessagesPlaceholder、
#         bind_tools、| 管道操作符（LCEL）、LangGraph 节点函数模式
# ============================================================================
#
# ============================================================================
# 【前置知识】—— 面向零 Python / 零 LangChain 经验读者
# ============================================================================
#
# 1. 什么是 LLM（大语言模型）？
#    LLM 是经过海量文本训练的 AI 模型，能理解和生成自然语言。
#    交易员用 LLM 来理解投资计划，制定具体交易执行方案。
#
# 2. 什么是 Agent（智能体）？
#    Agent = LLM + 工具 + 提示词。
#    交易员 Agent 接收投资计划，生成可执行的交易方案。
#
# 3. 什么是 LangChain？
#    LangChain 是构建 LLM 应用的 Python 框架。
#    交易员用到了 ChatPromptTemplate（提示词模板）、bind_tools（工具绑定）、
#    | 管道操作符（LCEL 链式调用）、invoke（执行链）。
#
# 4. 什么是 LangGraph？
#    LangGraph 是工作流编排引擎。交易员是图中的一个节点，
#    在研究经理做出裁决后执行，为风险分析师提供辩论对象。
#
# 5. 什么是状态（state）？
#    状态是所有节点共享的 Python 字典（dict），像"公共黑板"。
#    交易员从黑板上读取：
#      - company_of_interest（公司名）
#      - investment_plan（投资计划，来自研究经理）
#      - messages（历史消息列表）
#    交易员写回到黑板上：
#      - messages（LLM 回复追加到消息历史）
#      - trader_plan（交易方案，供风险分析师评估）
#
# 6. 什么是工厂函数（Factory Function）？
#    create_trader(llm) 是工厂函数：传入 LLM，返回一个节点函数。
#    外层负责"配置"（传入 llm），内层负责"执行"（接收 state）。
#
# 7. 什么是 ChatPromptTemplate？
#    ChatPromptTemplate 是 LangChain 的"聊天提示词模板"，
#    把文本组织成有角色区分的消息序列（system/human/ai/tool）。
#
# 8. 什么是 MessagesPlaceholder？
#    MessagesPlaceholder 是一个"动态占位符"，在提示词模板中占一个位置，
#    运行时把实际的消息列表填进去，让 Agent 能看到完整的对话历史。
#
# 9. 什么是 bind_tools()？
#    bind_tools(tools) 将 Python 函数转换为 LLM 可理解的工具定义，
#    附加到每次 LLM 请求中。LLM 根据工具定义决定何时调用哪个工具。
#
# 10. 什么是 | 管道操作符（LCEL）？
#     LCEL = LangChain Expression Language。
#     用 | 把组件串联成"数据处理流水线"：prompt | llm.bind_tools(tools)
#     前一个组件的输出 = 后一个组件的输入。
#
# 11. 什么是 invoke()？
#     invoke() 是 LangChain 链的执行方法。传入输入数据，返回输出结果。
#
# 12. 什么是 result.tool_calls？
#     tool_calls 是 LLM 返回的工具调用请求列表。如果不为空，说明 LLM
#     想调用工具获取数据；如果为空，说明 LLM 已完成分析，生成了最终回复。
#
# 13. 交易员在系统中的角色和职责
#     交易员是连接"研究阶段"和"风险评估阶段"的桥梁：
#     - 它接收研究经理的投资计划（投资方向和大方向）
#     - 它制定具体的交易方案（入场价格、仓位大小、止损止盈等）
#     - 它的方案会被风险分析师（激进/保守/中性）评估和辩论
#
# 14. 系统中的整体流程（本节点的位置）
#     用户输入 → [分析师] → [牛熊辩论] → [研究经理裁决] → [交易员] ← 本文件
#                                                          ↓
#                                              [风险辩论] → [组合经理最终决策]
#
# ============================================================================

# ChatPromptTemplate：构建结构化聊天提示词模板，支持多角色消息
# MessagesPlaceholder：在提示词中预留位置，运行时动态插入历史消息列表
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json

# --- 导入项目内部工具函数 ---
# build_instrument_context：构建目标公司的上下文信息字符串
from tradingagents.agents.utils.agent_utils import build_instrument_context
# get_config：获取全局配置
from tradingagents.dataflows.config import get_config


# ============================================================================
# 工厂函数：create_trader
# ============================================================================
# 【为什么使用工厂函数模式？】
#
# LangGraph 节点函数签名要求只接收 state 一个参数。
# 但创建交易员需要传入 LLM 实例。
# 工厂函数模式解决这个矛盾：
#   外层 create_trader(llm) 接收 llm，
#   内层 trader_node(state) 只接收 state，
#   内层通过"闭包"捕获外层的 llm 变量。
# ============================================================================
def create_trader(llm):
    """
    创建交易员节点函数（工厂函数模式）。

    交易员的角色和职责：
    - 接收研究经理的投资计划
    - 制定具体的交易执行方案
    - 输出交易指令，供风险分析师评估

    参数：
        llm: 大语言模型实例，驱动推理和决策
    """

    # ========================================================================
    # 内层节点函数：trader_node
    # ========================================================================
    # 这个函数注册到 LangGraph 图中，每次执行到这个节点时被调用。
    # ========================================================================
    def trader_node(state):
        """
        交易员节点：LangGraph 工作流中的标准节点函数。

        参数：
            state: 状态字典，包含：
                - company_of_interest: 目标公司名称
                - investment_plan: 研究经理的投资计划
                - messages: 历史消息列表

        【读取的状态字段】：
            - state["company_of_interest"]：目标公司名称
            - state["investment_plan"]：研究经理的投资计划
            - state["messages"]：历史消息列表

        【写入的状态字段】：
            - "messages"：将 LLM 回复追加到消息历史
            - "trader_plan"：交易执行方案，供风险分析师评估
        """
        # 构建目标公司的上下文信息
        # build_instrument_context() 返回类似：
        #   "The instrument to analyze is `AAPL`. Use this exact ticker ..."
        instrument_context = build_instrument_context(state["company_of_interest"])

        # 提取研究经理的投资计划
        # 这是牛熊研究员辩论后，研究经理做出的投资计划
        # 交易员需要根据这个计划制定具体的交易方案
        investment_plan = state["investment_plan"]

        # ====================================================================
        # 工具列表
        #
        # 交易员目前没有定义任何工具（空列表）。
        # 这意味着交易员只做"文本推理"——根据投资计划制定交易方案，
        # 不需要调用外部工具获取数据（数据已在投资计划中）。
        #
        # 为什么交易员没有工具？
        # 因为交易员的任务不是"获取数据"，而是"制定方案"。
        # 所有需要的信息（公司信息、投资计划）都已在提示词中。
        # ====================================================================
        tools = []

        # ====================================================================
        # 系统提示词（System Prompt）
        #
        # 逐段解析这段提示词为什么这样设计：
        #
        # 第 1 段："You are a trading assistant that makes specific trading
        #          decisions based on the investment plan provided by the
        #          research manager."
        #   → 角色定义：交易助手，根据投资计划制定具体交易决策。
        #
        # 第 2 段："Your task is to execute the plan by providing specific
        #          actionable trading instructions."
        #   → 任务定义：执行计划，提供"可操作的"交易指令。
        #     关键词"actionable"要求输出能直接执行的方案，不是泛泛的建议。
        #
        # 第 3 段："Use the investment plan to guide your decisions, ensuring
        #          that your trades align with the plan's strategic direction."
        #   → 约束条件：交易必须与投资计划的战略方向一致。
        #     不能偏离研究经理的决策（如计划是买入，不能变成卖出）。
        #
        # 第 4 段："Clearly communicate each trade, including rationale and
        #          any risks you identify."
        #   → 输出要求：清晰沟通每笔交易，包括理由和已识别的风险。
        #     让风险分析师能理解交易逻辑并评估风险。
        #
        # 第 5 段：最后的 + 字符串拼接了一段"协作提示词"，
        #          与其他分析师 Agent 的提示词结构一致。
        # ====================================================================
        system_message = (
            "You are a trading assistant that makes specific trading decisions based on the investment plan provided by the research manager. Your task is to execute the plan by providing specific actionable trading instructions. Use the investment plan to guide your decisions, ensuring that your trades align with the plan's strategic direction. Clearly communicate each trade, including rationale and any risks you identify."
            + " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable, prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
        )

        # ====================================================================
        # 构建 ChatPromptTemplate（聊天提示词模板）
        #
        # 【什么是 ChatPromptTemplate？】
        # LangChain 的"聊天提示词模板"，把文本组织成有角色区分的消息序列。
        # 消息角色：system（角色定义）、human（用户）、ai（AI回复）、tool（工具结果）。
        #
        # 【from_messages() 的结构】
        # 接收一个列表，每个元素是一条消息：
        #   - ("system", "内容")：系统消息，定义 AI 的角色和行为（最高优先级）
        #   - MessagesPlaceholder(...)：占位符，运行时插入历史消息列表
        #
        # 模板中的 {变量名} 是占位符，通过 .partial() 方法填充。
        #
        # 【协作提示词逐行解析】
        #
        # "You are a helpful AI assistant, collaborating with other assistants."
        #   → 多 Agent 协作基调：你是团队一员。
        #
        # "Use the provided tools to progress towards answering the question."
        #   → 鼓励用工具推进任务（虽然交易员没有工具，保持提示词一致性）。
        #
        # "If you are unable to fully answer, that's OK; another assistant
        #  with different tools will help where you left off."
        #   → 容错机制。
        #
        # "Execute what you can to make progress."
        #   → 行动导向。
        #
        # "If you or any other assistant has the FINAL TRANSACTION PROPOSAL:
        #  **BUY/HOLD/SELL** or deliverable, prefix your response with
        #  FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
        #   → 停止信号机制。
        #
        # "You have access to the following tools: {tool_names}."
        #   → {tool_names} 会被 partial() 填充（这里是空字符串，因为工具列表为空）。
        #
        # "{system_message}"
        #   → 插入上面的角色专属提示词。
        #
        # "For your reference, the current date is {current_date}. {instrument_context}"
        #   → 时间上下文和公司信息。
        #
        # "Investment plan from research manager: {investment_plan}"
        #   → 注入研究经理的投资计划，这是交易员工作的核心输入。
        # ====================================================================
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}"
                    "Investment plan from research manager: {investment_plan}",
                ),
                # ==================================================================
                # MessagesPlaceholder：运行时插入历史消息
                #
                # 【什么是 MessagesPlaceholder？】
                # 它是一个"动态占位符"，定义模板时先占位置，
                # 运行时（调用 invoke 时）把实际的消息列表填进去。
                #
                # 【为什么需要它？】
                # 交易员需要看到之前的消息历史（如分析师的报告、研究员的辩论等），
                # 以便在制定交易方案时参考完整的上下文。
                #
                # variable_name="messages"：
                #   告诉模板"运行时从输入中找名为 'messages' 的变量来填充"。
                #   后续 chain.invoke(state["messages"]) 时，state["messages"]
                #   是消息对象列表，会被自动展开填入此位置。
                # ==================================================================
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        # ====================================================================
        # .partial() 方法：预先填充部分变量
        #
        # 【什么是 partial()？】
        # partial() 可以"提前填好"模板中的 {变量} 占位符。
        # 每次调用返回一个新模板对象，已填的变量不再需要传入。
        #
        # 这里 5 次调用 partial()，分别填入 5 个变量：
        # ====================================================================
        # 填入角色专属提示词
        prompt = prompt.partial(system_message=system_message)
        # 将工具名称列表拼接成逗号分隔的字符串
        # 由于工具列表为空，这里会是空字符串 ""
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        # 填入当前交易日期
        prompt = prompt.partial(current_date=state["trade_date"])
        # 填入公司上下文信息
        prompt = prompt.partial(instrument_context=instrument_context)
        # 填入研究经理的投资计划——这是交易员工作的核心输入
        prompt = prompt.partial(investment_plan=investment_plan)

        # ====================================================================
        # 构建并执行 LangChain 链
        #
        # 【什么是 | 管道操作符（LCEL）？】
        # LCEL = LangChain Expression Language，LangChain 链式表达式语言。
        # 用 Python 的 | 操作符把组件串联成"数据处理流水线"：
        #   prompt | llm.bind_tools(tools)
        #   数据流向：输入 → prompt（格式化） → llm.bind_tools（LLM推理+工具） → 输出
        #
        # 类比 Unix 管道：cat file | grep "error" | sort
        #   前一个命令的输出 = 后一个命令的输入
        #
        # 【bind_tools() 的作用】
        # bind_tools(tools) 将工具信息附加到 LLM 请求中。
        # 由于交易员的工具列表为空，这里实际上没有绑定任何工具。
        # 但保持代码结构一致，便于未来扩展（如果交易员需要工具）。
        # ====================================================================
        chain = prompt | llm.bind_tools(tools)

        # ====================================================================
        # invoke()：执行链并获取结果
        #
        # 【什么是 invoke()？】
        # invoke() 是 LangChain 链的执行方法。
        # 传入 state["messages"]（历史消息列表），得到 LLM 的回复。
        #
        # 执行过程：
        #   1. state["messages"] → prompt 模板 → 填充 {messages} 占位符
        #   2. 完整消息序列 → LLM
        #   3. LLM 推理 → 返回 AIMessage 对象
        #
        # 【result 是什么？】
        # result 是 AIMessage 对象，包含：
        #   - result.content：LLM 的文本回复（交易方案）
        #   - result.tool_calls：LLM 请求调用的工具列表（这里应为空，因为没有工具）
        # ====================================================================
        result = chain.invoke(state["messages"])

        report = ""

        # 如果 LLM 没有请求工具调用，提取报告内容
        # 由于交易员没有工具，这里 tool_calls 应该总是为空
        # report 会包含 LLM 生成的交易方案
        if len(result.tool_calls) == 0:
            report = result.content

        # ====================================================================
        # 返回状态更新
        #
        # 【返回值的作用】
        # 在 LangGraph 中，返回的字典会被"合并"到全局状态中。
        #
        # 【返回的字段说明】
        # - "messages": [result]
        #   → 把 LLM 回复作为新消息追加到全局 messages 列表。
        #     包装成列表 [result] 是因为 LangGraph 的 messages reducer
        #     期望接收消息列表。
        #
        # - "trader_plan": report
        #   → 交易执行方案文本。包含具体的交易指令、理由和风险。
        #     供后续的风险分析师（激进/保守/中性）评估和辩论。
        # ====================================================================
        return {
            "messages": [result],
            "trader_plan": report,
        }

    # ========================================================================
    # 返回内部定义的节点函数（闭包）
    #
    # 工厂函数模式：create_trader 是工厂，返回配置好的节点函数。
    # 内层函数通过"闭包"捕获外层的 llm 变量。
    #
    # 在 LangGraph 中的注册方式：
    #   graph.add_node("trader", create_trader(llm))
    # ========================================================================
    return trader_node


# ============================================================================
# 【运行时数据流示例】
# ============================================================================
#
# 假设分析 AAPL，研究经理已做出裁决：
#
# 步骤 1：研究经理裁决后的状态
#   state = {
#       "company_of_interest": "AAPL",
#       "trade_date": "2024-01-15",
#       "investment_plan": "推荐：Buy。理由：营收增长 15%... 战略行动：建议在支撑位买入...",
#       "messages": [HumanMessage("..."), AIMessage("..."), ...],
#       "trader_plan": "",  # 初始为空
#       ...其他字段...
#   }
#
# 步骤 2：LangGraph 调用 trader_node(state)
#   → 构建 instrument_context
#   → 提取 investment_plan
#   → 构建 prompt，填充所有变量（包括投资计划）
#   → chain.invoke(state["messages"])
#
# 步骤 3：LLM 生成交易方案
#   result.tool_calls = []  # 没有工具调用
#   result.content = "交易方案：\n1. 在 $170 附近买入 100 股 AAPL\n2. 止损：$165\n3. 止盈：$185\n
#                       理由：基于研究经理的买入建议... 风险：市场波动可能导致..."
#   report = result.content
#
# 步骤 4：返回状态更新
#   → 返回 {
#       "messages": [result],
#       "trader_plan": "交易方案：\n1. 在 $170 附近买入..."
#   }
#
# 步骤 5：LangGraph 将 trader_plan 合并到全局状态
#   state["trader_plan"] = "交易方案：\n1. 在 $170 附近买入..."
#   → 进入风险辩论阶段（激进/保守/中性分析师评估这个交易方案）
#
# ============================================================================
