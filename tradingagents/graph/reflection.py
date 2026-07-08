# =============================================================================
# TradingAgents/graph/reflection.py
# =============================================================================
# 模块：Reflector - 交易决策反思与记忆更新
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件涉及的核心概念，如果你完全没有 Python 和 LangChain 经验，请先阅读以下说明。
#
# --- 概念 1：什么是"反思"（Reflection）？ ---
# 在交易代理系统中，反思指的是：
# 交易完成后，根据实际收益/损失来回顾之前的决策，分析：
#   - 哪些决策是正确的？为什么？
#   - 哪些决策是错误的？原因是什么？
#   - 如何改进未来的决策？
#
# 通俗比喻：就像一个人类交易员在每笔交易后做"复盘"——
#   把当时的市场情况、自己做的决策、最终赚了还是亏了，都记在笔记本上，
#   以后遇到类似的市场情况就翻看笔记，避免重复犯错。
#
# --- 概念 2：反思机制的"为什么" ---
# 为什么需要反思？
#   AI 代理和人类一样，需要从经验中学习才能变强。
#   如果每次交易都"从零开始"，不参考过去的经验，那永远不会有进步。
#   反思机制让 AI 代理能够"积累经验"——把每次交易的教训存起来，
#   下次遇到类似情况时参考，从而做出更好的决策。
#
# 这就是"从经验中学习"的核心思想：
#   1. 做决策（propagate）→ 2. 观察结果（收益/损失）→ 3. 反思对错 → 4. 存入记忆
#   → 下次做决策时先检索记忆 → 做出更好的决策 → 循环
#
# --- 概念 3：BM25 检索的通俗概念 ---
# 反思结果存入"情景记忆"（FinancialSituationMemory）。
# 当下次分析类似的市场状况时，代理可以检索相关的历史反思。
#
# 检索使用 BM25 算法——一种"按关键词匹配度排序文档"的信息检索算法。
# 通俗解释：
#   - 记忆中存储了很多条（市场状况, 反思报告）对。
#   - 当遇到新的市场状况时，需要找到与之最相似的历史经验。
#   - BM25 先统计每个关键词在各历史状况中出现的频率。
#   - 出现频率越高，相关度越高。
#   - 但如果一个词在所有状况中都出现（如"股票"），它的权重会降低（没有区分度）。
#   - 最终给每条历史经验打一个相关度分数，按分数排序，取最相关的几条。
#
# 类比：就像搜索引擎搜索"苹果 股价 上涨"，
# 会优先返回同时包含这三个词的网页，而不是只包含其中一个词的网页。
#
# --- 概念 4：反思的流程 ---
# 1. 提取当前市场状况（从各分析报告中汇总成一段文本）
# 2. 提取该角色的分析/决策内容（如牛市研究员的辩论历史）
# 3. 将市场状况、决策内容、实际收益一起发给 LLM
# 4. LLM 分析决策的优劣，生成反思报告
# 5. 将（市场状况, 反思报告）存入该角色的记忆
#
# --- 概念 5：反思的 5 个角色 ---
# 系统对 5 个角色分别进行反思：
# 1. 牛市研究员（Bull Researcher）：看多分析的准确性
#    → 反思：看多论据是否成立？哪些因素被忽视？
# 2. 熊市研究员（Bear Researcher）：看空分析的准确性
#    → 反思：看空论据是否成立？哪些风险被高估/低估？
# 3. 交易员（Trader）：交易决策的质量
#    → 反思：交易计划是否合理？执行时机是否恰当？
# 4. 投资裁判（Invest Judge）：投资辩论裁判的评判
#    → 反思：裁判是否正确权衡了牛/熊方论据？判断是否偏颇？
# 5. 投资组合经理（Portfolio Manager）：风险管理的决策
#    → 反思：风险管理是否恰当？最终决策是否合理？
#
# --- 概念 6：LangChain 消息格式 ---
# LangChain 使用元组 ("角色", "内容") 表示消息：
#   - ("system", "你是一个金融分析师...")：系统消息，设定 LLM 的角色和任务
#   - ("human", "分析这份数据...")：人类消息，提供具体数据
#   - ("ai", "根据分析...")：AI 消息，LLM 的回复
#
# 调用 LLM 时传入消息列表：
#   llm.invoke([("system", "系统提示词"), ("human", "具体数据")])
#   → 返回 AIMessage(content="LLM 的回复内容")
#   → .content 提取回复的文本内容
#
# --- 概念 7：Python 特有语法说明 ---
#
# a. f"..."（f-string 格式化字符串）
#    f"Returns: {returns_losses}\n\nAnalysis: {report}"
#    把变量值嵌入字符串中，{变量名} 会被替换为变量的值。
#
# b. \n（换行符）
#    "\n\n" 表示两个换行（空一行），用于分隔不同部分的内容。
#
# c. .invoke(messages).content（方法链式调用）
#    先调用 .invoke(messages) 获取 LLM 回复对象，
#    再访问 .content 属性获取文本内容。
#    类似其他语言的链式调用：llm.invoke(messages).content
#
# d. [(a, b)]（元组列表）
#    [("市场状况", "反思报告")] 是一个包含一个元组的列表。
#    add_situations 接收这种格式，因为一次可以添加多条经验。
#
# =============================================================================
#
# 【文件说明】
# 这个模块实现了"反思"（Reflection）机制——这是 AI 代理系统中
# 实现"从经验中学习"的关键组件。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
from typing import Dict, Any                # 类型提示
                        # Dict[str, Any] 表示"键为字符串、值为任意类型的字典"
                        # 类型提示不影响运行，只是给人和 IDE 看的"说明书"
from langchain_openai import ChatOpenAI     # OpenAI 兼容的聊天模型接口
                        # ChatOpenAI 是 LangChain 提供的 LLM 封装类
                        # 可以调用 LLM 的 API 来生成文本回复
                        # .invoke(messages) 发送消息列表并获取 LLM 回复
                        # .content 属性获取回复的文本内容


# =============================================================================
# 类：Reflector
# =============================================================================
class Reflector:
    """
    处理决策反思和记忆更新。

    【核心方法】
    - reflect_bull_researcher()：反思牛市研究员的分析
    - reflect_bear_researcher()：反思熊市研究员的分析
    - reflect_trader()：反思交易员的决策
    - reflect_invest_judge()：反思投资裁判的评判
    - reflect_portfolio_manager()：反思投资组合经理的决策

    【每个反思方法的通用流程】
    1. 提取当前市场状况（_extract_current_situation）
    2. 提取该角色的分析/决策内容
    3. 调用 LLM 进行反思（_reflect_on_component）
    4. 将反思结果存入该角色的记忆

    【运行时数据流总览（反思的完整过程）】

    假设交易完成后的状态和收益：
        current_state = {
            "market_report": "RSI: 72.3, MACD: bullish crossover...",
            "sentiment_report": "Social sentiment: 78% positive...",
            "news_report": "Apple announced record earnings...",
            "fundamentals_report": "P/E: 28.5, Revenue growth: 15%...",
            "investment_debate_state": {
                "bull_history": "Bull: AAPL shows strong revenue growth...",
                "bear_history": "Bear: P/E ratio too high...",
                "judge_decision": "Bull argument is stronger...",
                "count": 2,
            },
            "trader_investment_plan": "Buy 100 shares at $185...",
            "risk_debate_state": {
                "judge_decision": "Buy with 60% position...",
                "count": 3,
            },
        }
        returns_losses = 0.05  # 赚了 5%

    反思流程（以牛市研究员为例）：
        1. 提取市场状况：
           situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\nApple announced...\n\nP/E: 28.5..."

        2. 提取牛方辩论历史：
           bull_debate_history = "Bull: AAPL shows strong revenue growth..."

        3. 调用 LLM 反思：
           messages = [
               ("system", "You are an expert financial analyst tasked with reviewing..."),
               ("human", "Returns: 0.05\n\nAnalysis: Bull: AAPL shows...\n\nMarket Reports: RSI: 72.3..."),
           ]
           result = llm.invoke(messages).content
           # result = "The bull analysis was correct. Revenue growth was the key driver.
           #           However, RSI indicated overbought conditions which were not adequately
           #           addressed. Future recommendation: consider technical indicators more..."

        4. 存入记忆：
           bull_memory.add_situations([("RSI: 72.3...\n\n...", "反思报告...")])
           → 下次分析时，BM25 会根据当前市场状况检索这条反思
    """

    # -------------------------------------------------------------------------
    # 构造函数
    # -------------------------------------------------------------------------
    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """
        初始化反思器。

        参数:
            quick_thinking_llm (ChatOpenAI): 快速思考 LLM 实例。
                反思任务使用快速思考 LLM，因为反思是总结性的任务，
                不需要深度推理，快速 LLM 能更快完成且成本更低。

        运行时数据流示例：
            reflector = Reflector(quick_thinking_llm)
            # self.quick_thinking_llm = ChatOpenAI(model="gpt-4o-mini")
            # self.reflection_system_prompt = _get_reflection_prompt() 的返回值
            #   → 一段精心设计的系统提示词文本
        """
        # 保存 LLM 实例，后续在 _reflect_on_component 中使用
        self.quick_thinking_llm = quick_thinking_llm
        # 加载反思系统提示词
        # 系统提示词定义了 LLM 在反思时的角色、分析框架和输出要求
        self.reflection_system_prompt = self._get_reflection_prompt()

    # -------------------------------------------------------------------------
    # 私有方法：获取反思系统提示词
    # -------------------------------------------------------------------------
    # Python 语法说明：方法名前加 _ 表示"私有方法"（约定俗成）
    def _get_reflection_prompt(self) -> str:
        """
        获取反思的系统提示词。

        【提示词设计】
        这是一个精心设计的提示词，指导 LLM 如何进行反思分析。
        提示词分为 4 个部分：

        1. Reasoning（推理）：分析每个决策的正确/错误，找出影响因素
           - 判断决策是否正确（正确 = 收益增加，错误 = 收益减少）
           - 分析影响因素：市场情报、技术指标、价格走势、新闻分析、情绪分析、基本面等
           - 评估各因素在决策中的重要性

        2. Improvement（改进）：对错误决策提出修正建议
           - 提出详细的修正建议（如"在某日期将 HOLD 改为 BUY"）

        3. Summary（总结）：提炼经验教训，供未来参考
           - 总结学到的经验
           - 说明如何将经验应用于未来的交易场景
           - 建立类似情况之间的联系

        4. Query（查询）：将关键洞察压缩为简洁的句子
           - 不超过 1000 tokens
           - 便于后续检索和参考

        返回:
            str: 反思系统提示词文本

        运行时数据流示例：
            prompt = self._get_reflection_prompt()
            # prompt = "You are an expert financial analyst tasked with reviewing..."
            # 后续在 _reflect_on_component 中作为系统消息使用：
            #   messages = [("system", prompt), ("human", "具体数据...")]
        """
        return """
You are an expert financial analyst tasked with reviewing trading decisions/analysis and providing a comprehensive, step-by-step analysis. 
Your goal is to deliver detailed insights into investment decisions and highlight opportunities for improvement, adhering strictly to the following guidelines:

1. Reasoning:
   - For each trading decision, determine whether it was correct or incorrect. A correct decision results in an increase in returns, while an incorrect decision does the opposite.
   - Analyze the contributing factors to each success or mistake. Consider:
     - Market intelligence.
     - Technical indicators.
     - Technical signals.
     - Price movement analysis.
     - Overall market data analysis 
     - News analysis.
     - Social media and sentiment analysis.
     - Fundamental data analysis.
     - Weight the importance of each factor in the decision-making process.

2. Improvement:
   - For any incorrect decisions, propose revisions to maximize returns.
   - Provide a detailed list of corrective actions or improvements, including specific recommendations (e.g., changing a decision from HOLD to BUY on a particular date).

3. Summary:
   - Summarize the lessons learned from the successes and mistakes.
   - Highlight how these lessons can be adapted for future trading scenarios and draw connections between similar situations to apply the knowledge gained.

4. Query:
   - Extract key insights from the summary into a concise sentence of no more than 1000 tokens.
   - Ensure the condensed sentence captures the essence of the lessons and reasoning for easy reference.

Adhere strictly to these instructions, and ensure your output is detailed, accurate, and actionable. You will also be given objective descriptions of the market from a price movements, technical indicator, news, and sentiment perspective to provide more context for your analysis.
"""

    # -------------------------------------------------------------------------
    # 私有方法：提取当前市场状况
    # -------------------------------------------------------------------------
    def _extract_current_situation(self, current_state: Dict[str, Any]) -> str:
        """
        从状态中提取当前市场状况的文本描述。

        【为什么需要提取市场状况？】
        反思需要上下文——LLM 需要知道"当时的市场是什么样的"，
        才能判断决策是否合理。这个方法将四份分析师报告合并成一个
        市场状况描述文本。

        四份报告：
        1. market_report：市场分析报告（技术面：价格走势、指标等）
        2. sentiment_report：情绪分析报告（社交媒体情绪）
        3. news_report：新闻分析报告（相关事件）
        4. fundamentals_report：基本面分析报告（财务报表）

        参数:
            current_state (dict): 图的当前状态字典

        返回:
            str: 合并后的市场状况描述文本

        运行时数据流示例：
            current_state = {
                "market_report": "RSI: 72.3, MACD: bullish crossover, Bollinger: upper band...",
                "sentiment_report": "Social sentiment: 78% positive, Twitter mentions increased 45%...",
                "news_report": "Apple announced record quarterly earnings, beating estimates by 8%...",
                "fundamentals_report": "P/E ratio: 28.5, Revenue: $394B, Net income: $97B, EPS: $6.05...",
            }

            situation = self._extract_current_situation(current_state)
            # situation = "RSI: 72.3, MACD: bullish crossover, Bollinger: upper band...\n\n
            #              Social sentiment: 78% positive, Twitter mentions increased 45%...\n\n
            #              Apple announced record quarterly earnings, beating estimates by 8%...\n\n
            #              P/E ratio: 28.5, Revenue: $394B, Net income: $97B, EPS: $6.05..."

            # 这个文本后续会：
            # 1. 传给 LLM 作为反思的参考上下文
            # 2. 作为"市场状况"存入记忆，用于 BM25 检索
        """
        # 从状态中提取四份报告
        # Python 语法说明：current_state["key"] 从字典中取值
        curr_market_report = current_state["market_report"]
        curr_sentiment_report = current_state["sentiment_report"]
        curr_news_report = current_state["news_report"]
        curr_fundamentals_report = current_state["fundamentals_report"]

        # 用两个换行符分隔各报告，形成清晰的结构
        # Python 语法说明：f"..." 是 f-string 格式化字符串
        #   {curr_market_report} 会被替换为变量 curr_market_report 的值
        #   \n\n 表示两个换行符（空一行），用于分隔不同报告
        return f"{curr_market_report}\n\n{curr_sentiment_report}\n\n{curr_news_report}\n\n{curr_fundamentals_report}"

    # -------------------------------------------------------------------------
    # 私有方法：对单个组件进行反思
    # -------------------------------------------------------------------------
    def _reflect_on_component(
        self, component_type: str, report: str, situation: str, returns_losses
    ) -> str:
        """
        对单个组件（角色）进行反思。

        【LLM 调用方式】
        使用 LangChain 的标准消息格式调用 LLM：
        - system 消息：设定 LLM 的角色和任务（反思系统提示词）
        - human 消息：提供具体的反思数据（收益、决策、市场状况）

        【消息格式】
        LangChain 使用元组 ("role", "content") 表示消息：
        - ("system", ...)：系统提示词，定义 LLM 的行为
        - ("human", ...)：用户消息，包含具体数据

        参数:
            component_type (str): 组件类型标识（如 "BULL", "BEAR"）
            report (str): 该角色的分析/决策内容
            situation (str): 市场状况描述
            returns_losses: 收益/损失数据

        返回:
            str: LLM 生成的反思报告文本

        运行时数据流示例：
            # 调用 _reflect_on_component：
            result = self._reflect_on_component(
                component_type="BULL",
                report="Bull: AAPL shows strong revenue growth of 15% YoY...",
                situation="RSI: 72.3...\n\nSocial sentiment: 78% positive...\n\n...",
                returns_losses=0.05  # 赚了 5%
            )

            # 内部执行：
            # 构建消息列表：
            messages = [
                ("system", "You are an expert financial analyst tasked with reviewing..."),
                ("human", "Returns: 0.05\n\nAnalysis: Bull: AAPL shows strong revenue growth...\n\nObjective Market Reports for Reference: RSI: 72.3..."),
            ]

            # 调用 LLM：
            result = self.quick_thinking_llm.invoke(messages).content
            # LLM 收到：
            #   - 系统提示词：指导它如何进行反思分析
            #   - 人类消息：收益 +5%，分析内容是看多 AAPL，市场状况是 RSI 超买但营收增长强劲
            # LLM 生成反思报告：
            # result = "Reasoning: The bull analysis was correct as the stock gained 5%.
            #           Revenue growth was the key driver. However, RSI at 72.3 indicated
            #           overbought conditions which were not adequately addressed.
            #           Improvement: Future analysis should weight technical indicators more heavily.
            #           Summary: Strong fundamentals can overcome overbought technical signals
            #           in the short term, but risk management should account for potential pullbacks.
            #           Query: When fundamentals are strong but RSI is overbought, consider
            #           smaller position sizes to manage pullback risk."
        """
        # 构建消息列表
        # Python 语法说明：[...] 是列表，里面的每个元素是一个元组 ("角色", "内容")
        messages = [
            # 系统消息：设定 LLM 为专业金融分析师，遵循反思指南
            # self.reflection_system_prompt 是 _get_reflection_prompt() 返回的提示词
            ("system", self.reflection_system_prompt),
            # 人类消息：提供具体数据供 LLM 分析
            # Python 语法说明：f"..." 是 f-string 格式化字符串
            #   {returns_losses} 会被替换为收益值（如 0.05）
            #   {report} 会被替换为角色的分析/决策内容
            #   {situation} 会被替换为市场状况描述
            #   \n\n 表示空一行，用于分隔不同部分
            (
                "human",
                f"Returns: {returns_losses}\n\nAnalysis/Decision: {report}\n\nObjective Market Reports for Reference: {situation}",
            ),
        ]

        # 调用 LLM 进行反思
        # Python 语法说明：.invoke(messages).content
        #   .invoke(messages) 发送消息列表给 LLM，返回一个 AIMessage 对象
        #   .content 访问 AIMessage 的 content 属性，获取回复的文本内容
        # 这是方法链式调用：先调用 invoke，再访问返回值的 content 属性
        result = self.quick_thinking_llm.invoke(messages).content
        return result

    # -------------------------------------------------------------------------
    # 公开方法：反思牛市研究员
    # -------------------------------------------------------------------------
    def reflect_bull_researcher(self, current_state, returns_losses, bull_memory):
        """
        反思牛市研究员的分析，并更新其记忆。

        【牛市研究员职责】
        牛市研究员从看多角度分析股票，寻找支持买入的理由。
        反思时关注：看多论据是否成立？哪些因素被忽视？

        参数:
            current_state: 图的当前状态
            returns_losses: 收益/损失数据
            bull_memory: 牛市研究员的情景记忆实例

        运行时数据流示例：
            # 交易完成后调用：
            self.reflect_bull_researcher(current_state, 0.05, bull_memory)

            # current_state = {
            #     "market_report": "RSI: 72.3...",
            #     "investment_debate_state": {
            #         "bull_history": "Bull: AAPL shows strong revenue growth of 15% YoY...",
            #         ...
            #     },
            #     ...
            # }

            # 步骤 1：提取市场状况
            situation = self._extract_current_situation(current_state)
            # situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\n..."

            # 步骤 2：提取牛市研究员的辩论历史
            bull_debate_history = current_state["investment_debate_state"]["bull_history"]
            # bull_debate_history = "Bull: AAPL shows strong revenue growth of 15% YoY..."

            # 步骤 3：调用 LLM 进行反思
            result = self._reflect_on_component("BULL", bull_debate_history, situation, 0.05)
            # LLM 收到：收益 +5%，决策是看多 AAPL，市场状况是 RSI 超买但营收增长
            # result = "Reasoning: The bull analysis was correct. Revenue growth was the key driver..."

            # 步骤 4：将反思结果存入记忆
            # add_situations 接收 [(市场状况, 反思结果)] 列表
            bull_memory.add_situations([(situation, result)])
            # → 记忆中新增一条：(市场状况, 反思报告)
            # → 下次牛市研究员分析时，BM25 会检索与当前市场状况最相似的历史反思
        """
        # 步骤 1：提取市场状况
        situation = self._extract_current_situation(current_state)

        # 步骤 2：提取牛市研究员的辩论历史
        # 这是牛市研究员在投资辩论中的所有发言
        # Python 语法说明：current_state["investment_debate_state"]["bull_history"]
        #   嵌套字典取值：先取 investment_debate_state，再取 bull_history
        bull_debate_history = current_state["investment_debate_state"]["bull_history"]

        # 步骤 3：调用 LLM 进行反思
        # _reflect_on_component 内部会构建消息列表并调用 LLM
        result = self._reflect_on_component(
            "BULL", bull_debate_history, situation, returns_losses
        )

        # 步骤 4：将反思结果存入记忆
        # add_situations() 接收 (市场状况, 反思结果) 元组列表
        # Python 语法说明：[(a, b)] 是一个包含一个元组的列表
        #   add_situations 接收列表是因为一次可以添加多条经验
        # 记忆系统会存储这些配对，供未来检索
        bull_memory.add_situations([(situation, result)])

    # -------------------------------------------------------------------------
    # 公开方法：反思熊市研究员
    # -------------------------------------------------------------------------
    def reflect_bear_researcher(self, current_state, returns_losses, bear_memory):
        """
        反思熊市研究员的分析，并更新其记忆。

        【熊市研究员职责】
        熊市研究员从看空角度分析股票，寻找支持卖出的理由。
        反思时关注：看空论据是否成立？哪些风险被高估/低估？

        参数:
            current_state: 图的当前状态
            returns_losses: 收益/损失数据
            bear_memory: 熊市研究员的情景记忆实例

        运行时数据流示例：
            self.reflect_bear_researcher(current_state, 0.05, bear_memory)

            # 步骤 1：提取市场状况
            situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\n..."

            # 步骤 2：提取熊市研究员的辩论历史
            bear_debate_history = current_state["investment_debate_state"]["bear_history"]
            # bear_debate_history = "Bear: P/E ratio of 28.5 is above industry average..."

            # 步骤 3：调用 LLM 反思
            # LLM 分析：收益 +5%，但熊方看空——说明熊方分析可能有误
            # result = "Reasoning: The bear analysis was incorrect. The stock gained 5%
            #           despite the high P/E ratio. Revenue growth justified the valuation..."

            # 步骤 4：存入记忆
            bear_memory.add_situations([(situation, result)])
            # → 下次熊市研究员分析时，BM25 会检索相似的历史反思
            # → 避免在高增长股票上过度强调估值风险
        """
        # 步骤 1：提取市场状况
        situation = self._extract_current_situation(current_state)

        # 步骤 2：提取熊市研究员的辩论历史
        bear_debate_history = current_state["investment_debate_state"]["bear_history"]

        # 步骤 3：调用 LLM 进行反思
        result = self._reflect_on_component(
            "BEAR", bear_debate_history, situation, returns_losses
        )

        # 步骤 4：将反思结果存入记忆
        bear_memory.add_situations([(situation, result)])

    # -------------------------------------------------------------------------
    # 公开方法：反思交易员
    # -------------------------------------------------------------------------
    def reflect_trader(self, current_state, returns_losses, trader_memory):
        """
        反思交易员的决策，并更新其记忆。

        【交易员职责】
        交易员根据研究经理的投资判断，制定具体的交易计划。
        如：买入多少股、什么价格、止损位等。
        反思时关注：交易计划是否合理？执行时机是否恰当？

        参数:
            current_state: 图的当前状态
            returns_losses: 收益/损失数据
            trader_memory: 交易员的情景记忆实例

        运行时数据流示例：
            self.reflect_trader(current_state, 0.05, trader_memory)

            # 步骤 1：提取市场状况
            situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\n..."

            # 步骤 2：提取交易员的投资计划
            trader_decision = current_state["trader_investment_plan"]
            # trader_decision = "Buy 100 shares of AAPL at $185 with stop loss at $175..."

            # 步骤 3：调用 LLM 反思
            # LLM 分析：收益 +5%，交易计划是买 100 股
            # result = "Reasoning: The trading plan was profitable. Buying at $185 was
            #           well-timed. However, stop loss at $175 might be too tight given
            #           the volatility. Improvement: Consider wider stop loss at $170..."

            # 步骤 4：存入记忆
            trader_memory.add_situations([(situation, result)])
            # → 下次交易员制定计划时，BM25 会检索相似的市场状况和反思
            # → 参考历史经验调整止损位和仓位大小
        """
        # 步骤 1：提取市场状况
        situation = self._extract_current_situation(current_state)

        # 步骤 2：提取交易员的投资计划
        trader_decision = current_state["trader_investment_plan"]

        # 步骤 3：调用 LLM 进行反思
        result = self._reflect_on_component(
            "TRADER", trader_decision, situation, returns_losses
        )

        # 步骤 4：将反思结果存入记忆
        trader_memory.add_situations([(situation, result)])

    # -------------------------------------------------------------------------
    # 公开方法：反思投资裁判
    # -------------------------------------------------------------------------
    def reflect_invest_judge(self, current_state, returns_losses, invest_judge_memory):
        """
        反思投资裁判的评判，并更新其记忆。

        【投资裁判职责】
        投资裁判（即研究经理）综合牛/熊方辩论结果，做出投资判断。
        反思时关注：裁判是否正确权衡了牛/熊方论据？判断是否偏颇？

        参数:
            current_state: 图的当前状态
            returns_losses: 收益/损失数据
            invest_judge_memory: 投资裁判的情景记忆实例

        运行时数据流示例：
            self.reflect_invest_judge(current_state, 0.05, invest_judge_memory)

            # 步骤 1：提取市场状况
            situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\n..."

            # 步骤 2：提取投资裁判的决策
            judge_decision = current_state["investment_debate_state"]["judge_decision"]
            # judge_decision = "After analyzing both sides, the bull argument is more compelling
            #                    due to strong revenue growth. Recommendation: BUY"

            # 步骤 3：调用 LLM 反思
            # LLM 分析：收益 +5%，裁判判断 BUY 是正确的
            # result = "Reasoning: The judge correctly sided with the bull argument.
            #           Revenue growth was indeed the decisive factor. However, the judge
            #           should have given more weight to the bear's RSI concern.
            #           Improvement: When RSI is overbought, consider a more cautious
            #           rating like OVERWEIGHT instead of BUY..."

            # 步骤 4：存入记忆
            invest_judge_memory.add_situations([(situation, result)])
            # → 下次投资裁判做判断时，BM25 会检索相似的历史反思
            # → 在类似市场状况下更准确地权衡牛熊论据
        """
        # 步骤 1：提取市场状况
        situation = self._extract_current_situation(current_state)

        # 步骤 2：提取投资裁判的决策
        judge_decision = current_state["investment_debate_state"]["judge_decision"]

        # 步骤 3：调用 LLM 进行反思
        result = self._reflect_on_component(
            "INVEST JUDGE", judge_decision, situation, returns_losses
        )

        # 步骤 4：将反思结果存入记忆
        invest_judge_memory.add_situations([(situation, result)])

    # -------------------------------------------------------------------------
    # 公开方法：反思投资组合经理
    # -------------------------------------------------------------------------
    def reflect_portfolio_manager(self, current_state, returns_losses, portfolio_manager_memory):
        """
        反思投资组合经理的决策，并更新其记忆。

        【投资组合经理职责】
        投资组合经理综合风险分析三方辩论，做出最终交易决策。
        反思时关注：风险管理是否恰当？最终决策是否合理？

        参数:
            current_state: 图的当前状态
            returns_losses: 收益/损失数据
            portfolio_manager_memory: 投资组合经理的情景记忆实例

        运行时数据流示例：
            self.reflect_portfolio_manager(current_state, 0.05, portfolio_manager_memory)

            # 步骤 1：提取市场状况
            situation = "RSI: 72.3...\n\nSocial sentiment: 78%...\n\n..."

            # 步骤 2：提取投资组合经理的最终决策
            # 注意：这里的 judge_decision 来自 risk_debate_state（风险辩论）
            # 而不是 investment_debate_state（投资辩论）
            judge_decision = current_state["risk_debate_state"]["judge_decision"]
            # judge_decision = "Given balanced risk perspectives, BUY with 60% position size,
            #                    target $200, stop loss at $170"

            # 步骤 3：调用 LLM 反思
            # LLM 分析：收益 +5%，最终决策是 BUY 60% 仓位
            # result = "Reasoning: The portfolio manager's decision was profitable.
            #           60% position size was appropriate given the mixed risk signals.
            #           The stop loss at $170 provided adequate downside protection.
            #           Improvement: Could have increased position to 70% given the
            #           strong fundamentals. Summary: When fundamentals are strong and
            #           risk debate is balanced, 60% position with wide stop loss is effective.
            #           Query: Strong fundamentals + balanced risk debate → 60-70% position
            #           with stop loss 8-10% below entry."

            # 步骤 4：存入记忆
            portfolio_manager_memory.add_situations([(situation, result)])
            # → 下次投资组合经理做最终决策时，BM25 会检索相似的历史反思
            # → 参考历史经验调整仓位大小和风险管理策略
        """
        # 步骤 1：提取市场状况
        situation = self._extract_current_situation(current_state)

        # 步骤 2：提取投资组合经理的最终决策
        # 注意：这里的 judge_decision 来自 risk_debate_state（风险辩论）
        # 而不是 investment_debate_state（投资辩论）
        # 这是因为投资组合经理是风险辩论的裁判，所以他的决策存在风险辩论状态中
        judge_decision = current_state["risk_debate_state"]["judge_decision"]

        # 步骤 3：调用 LLM 进行反思
        result = self._reflect_on_component(
            "PORTFOLIO MANAGER", judge_decision, situation, returns_losses
        )

        # 步骤 4：将反思结果存入记忆
        portfolio_manager_memory.add_situations([(situation, result)])
