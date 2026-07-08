# =============================================================================
# TradingAgents/graph/signal_processing.py
# =============================================================================
# 模块：SignalProcessor - 交易信号处理与决策提取
# =============================================================================
#
# =============================================================================
# 【前置知识】（零基础读者必读）
# =============================================================================
#
# 本文件涉及的核心概念，如果你完全没有 Python 和 LangChain 经验，请先阅读以下说明。
#
# --- 概念 1：什么是"交易信号"（Trading Signal）？ ---
# 在交易系统中，"信号"是一个简洁的决策词，表示"应该做什么"。
# 常见的交易信号包括：
#   - BUY（买入）：强烈建议买入
#   - OVERWEIGHT（超配）：建议买入但比 BUY 稍弱，偏多
#   - HOLD（持有）：维持现状，不买也不卖
#   - UNDERWEIGHT（低配）：建议卖出但比 SELL 稍弱，偏空
#   - SELL（卖出）：强烈建议卖出
#
# --- 概念 2：为什么需要信号处理？ ---
# 投资组合经理（Portfolio Manager）的最终输出是一段详细的分析文本，
# 包含了推理过程、风险提示、建议理由等。例如：
#   "Based on comprehensive analysis of all risk perspectives,
#    market conditions, and fundamental factors, I recommend a BUY
#    rating for AAPL with a target price of $200. The company shows
#    strong revenue growth of 15% year-over-year, supported by..."
#
# 这段文本对人类来说很容易理解，但对于自动化交易系统来说太长了。
# 自动化系统只需要一个简洁的决策词（如 "BUY"）来执行交易操作。
#
# 信号处理器的作用就是：从长文本中提取出核心决策词。
# 输入：一段详细的分析文本（可能几百字）
# 输出：一个简洁的决策词（5 个词之一）
#
# --- 概念 3：如何提取信号？ ---
# 本系统使用 LLM 来提取信号，而不是用正则表达式或关键词匹配。
# 为什么用 LLM？
#   - 分析文本的表达方式千变万化，关键词匹配可能遗漏。
#   - 例如 "I would lean towards accumulating more shares" 意思是 BUY，
#     但不包含 "BUY" 这个词。
#   - LLM 能理解语义，即使表达方式不同也能准确提取决策。
#
# 提取流程：
#   1. 构建系统提示词，要求 LLM 只输出 5 个决策词之一
#   2. 构建人类消息，包含完整的分析文本
#   3. 调用 LLM，获取回复
#   4. 清理回复文本（去除空白字符），返回决策词
#
# --- 概念 4：LangChain 消息格式 ---
# LangChain 使用元组 ("角色", "内容") 表示消息：
#   - ("system", "你是一个金融分析师...")：系统消息，设定 LLM 的角色和任务
#   - ("human", "分析这份数据...")：人类消息，提供具体数据
#
# 调用 LLM 时传入消息列表：
#   llm.invoke([("system", "系统提示词"), ("human", "具体数据")])
#   → 返回 AIMessage(content="LLM 的回复内容")
#   → .content 提取回复的文本内容
#
# --- 概念 5：Python 特有语法说明 ---
#
# a. .strip()（去除空白字符）
#    "  BUY  \n".strip() → "BUY"
#    去除字符串开头和结尾的空白字符（空格、换行符、制表符等）。
#    LLM 的回复可能包含多余的空白字符，需要清理。
#
# b. .invoke(messages).content（方法链式调用）
#    先调用 .invoke(messages) 获取 LLM 回复对象，
#    再访问 .content 属性获取文本内容。
#    类似其他语言的链式调用：llm.invoke(messages).content
#
# c. f"..."（f-string 格式化字符串）
#    f"Extract the trading signal from the following analysis:\n{full_signal}"
#    {full_signal} 会被替换为变量 full_signal 的值。
#
# d. \n（换行符）
#    "\n" 表示换行，用于分隔提示词的不同部分。
#
# =============================================================================
#
# 【文件说明】
# 这个模块负责从投资组合经理的完整分析文本中提取简洁的交易信号。
# 交易信号是一个单词（如 "BUY"、"SELL" 等），用于自动化交易系统执行操作。
# =============================================================================

# ---------------------------------------------------------------------------
# 导入依赖
# ---------------------------------------------------------------------------
from langchain_openai import ChatOpenAI     # OpenAI 兼容的聊天模型接口
                        # ChatOpenAI 是 LangChain 提供的 LLM 封装类
                        # 可以调用 LLM 的 API 来生成文本回复
                        # .invoke(messages) 发送消息列表并获取 LLM 回复
                        # .content 属性获取回复的文本内容


# =============================================================================
# 类：SignalProcessor
# =============================================================================
class SignalProcessor:
    """
    从交易决策文本中提取核心交易信号。

    【职责】
    将投资组合经理的详细分析文本（可能几百字）转化为简洁的决策词（如 "BUY"）。
    使用 LLM 来理解分析文本的语义，准确提取交易决策。

    【为什么需要信号处理？】
    投资组合经理输出的分析文本包含大量推理过程和建议理由，
    但自动化交易系统只需要一个简洁的决策词来执行操作。
    信号处理器就像一个"翻译官"——把长篇大论翻译成一个词。

    【5 种交易信号】
    - BUY（买入）：强烈建议买入
    - OVERWEIGHT（超配）：建议买入，偏多
    - HOLD（持有）：维持现状
    - UNDERWEIGHT（低配）：建议卖出，偏空
    - SELL（卖出）：强烈建议卖出

    【运行时数据流示例（信号处理的完整过程）】

    输入（投资组合经理的完整分析文本）：
        full_signal = (
            "Based on comprehensive analysis of all risk perspectives, "
            "market conditions, and fundamental factors, I recommend a BUY "
            "rating for AAPL with a target price of $200. The company shows "
            "strong revenue growth of 15% year-over-year, supported by record "
            "quarterly earnings. While RSI indicates overbought conditions at "
            "72.3, the fundamental strength justifies the valuation. Risk "
            "analysis suggests a 60% position size with stop loss at $170..."
        )
        # ↑ 这是一段很长的分析文本

    处理过程：
        # 1. 构建消息列表
        messages = [
            ("system",
             "You are a financial signal extractor. "
             "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, "
             "UNDERWEIGHT, SELL. "
             "Output only the single rating word."),
            ("human",
             "Extract the trading signal from the following analysis:\n"
             "Based on comprehensive analysis of all risk perspectives, "
             "market conditions... I recommend a BUY rating for AAPL..."),
        ]

        # 2. 调用 LLM
        result = llm.invoke(messages).content
        # LLM 理解了分析文本的语义，返回：
        # result = "BUY"
        # 或者可能带有空白字符：result = "  BUY  \n"

        # 3. 清理并返回
        signal = result.strip()
        # signal = "BUY"

    输出：
        "BUY"  # 简洁的决策词
    """

    # -------------------------------------------------------------------------
    # 构造函数
    # -------------------------------------------------------------------------
    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """
        初始化信号处理器。

        参数:
            quick_thinking_llm (ChatOpenAI): 快速思考 LLM 实例。
                信号提取使用快速思考 LLM，因为这是一个简单的分类任务——
                只需要从文本中提取一个词，不需要深度推理。
                快速 LLM 能更快完成且成本更低。

        运行时数据流示例：
            signal_processor = SignalProcessor(quick_thinking_llm)
            # self.llm = ChatOpenAI(model="gpt-4o-mini")
            # 后续调用：
            #   signal = signal_processor.process_signal("Based on...BUY rating...")
            #   signal = "BUY"
        """
        # 保存 LLM 实例
        # 后续在 process_signal 方法中使用
        self.llm = quick_thinking_llm

    # -------------------------------------------------------------------------
    # 核心方法：处理交易信号
    # -------------------------------------------------------------------------
    def process_signal(self, full_signal: str) -> str:
        """
        从完整的交易决策文本中提取核心交易信号。

        【处理流程】
        1. 构建系统提示词，要求 LLM 只输出 5 个决策词之一
        2. 构建人类消息，包含完整的分析文本
        3. 调用 LLM，获取回复
        4. 清理回复文本（去除空白字符），返回决策词

        【为什么用 LLM 而不是关键词匹配？】
        投资组合经理的表达方式千变万化：
        - "I recommend a BUY rating" → BUY（直接包含关键词）
        - "I would lean towards accumulating more shares" → BUY（隐含意思）
        - "The risk-reward profile suggests reducing exposure" → SELL（隐含意思）
        - "Maintaining current position is prudent" → HOLD（隐含意思）

        LLM 能理解语义，即使表达方式不同也能准确提取决策。
        而关键词匹配可能遗漏不包含标准决策词的表达。

        参数:
            full_signal (str): 完整的交易决策文本（来自投资组合经理）

        返回:
            str: 核心交易信号（BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL 之一）

        运行时数据流示例：
            # 输入（投资组合经理的完整分析）：
            full_signal = (
                "Based on comprehensive analysis of all risk perspectives, "
                "market conditions, and fundamental factors, I recommend a BUY "
                "rating for AAPL with a target price of $200. The company shows "
                "strong revenue growth of 15% year-over-year..."
            )

            # 调用方法：
            signal = self.process_signal(full_signal)

            # 内部执行：

            # 1. 构建消息列表
            messages = [
                ("system",
                 "You are a financial signal extractor. "
                 "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, "
                 "UNDERWEIGHT, SELL. "
                 "Output only the single rating word."),
                ("human",
                 f"Extract the trading signal from the following analysis:\\n{full_signal}"),
            ]

            # 2. 调用 LLM
            # Python 语法说明：.invoke(messages) 发送消息给 LLM
            #   .content 访问回复的文本内容
            # 这是方法链式调用
            result = self.llm.invoke(messages).content
            # LLM 返回：
            # result = "BUY"  # 或者可能 "  BUY  \\n"（带空白字符）

            # 3. 清理并返回
            # Python 语法说明：.strip() 去除开头和结尾的空白字符
            #   "  BUY  \\n".strip() → "BUY"
            signal = result.strip()

            # 返回：
            # signal = "BUY"

            # ----

            # 另一个示例（看空时）：
            full_signal = (
                "After careful consideration of all risk factors and market "
                "conditions, I believe the current valuation is unsustainable. "
                "The P/E ratio of 28.5 is significantly above the industry "
                "average, and technical indicators show bearish divergence..."
            )
            signal = self.process_signal(full_signal)
            # signal = "SELL"

            # ----

            # 又一个示例（中性时）：
            full_signal = (
                "The mixed signals from technical and fundamental analysis "
                "suggest maintaining current position. While fundamentals are "
                "strong, technical indicators show overbought conditions..."
            )
            signal = self.process_signal(full_signal)
            # signal = "HOLD"
        """
        # 构建消息列表
        # Python 语法说明：[...] 是列表，每个元素是一个元组 ("角色", "内容")
        messages = [
            # 系统消息：设定 LLM 的角色和任务
            # 要求 LLM 只输出 5 个决策词之一，不输出任何其他内容
            # 这确保输出格式一致，便于自动化处理
            (
                "system",
                "You are a financial signal extractor. "
                "Extract the rating as exactly one of: BUY, OVERWEIGHT, HOLD, "
                "UNDERWEIGHT, SELL. "
                "Output only the single rating word.",
            ),
            # 人类消息：提供完整的分析文本供 LLM 提取信号
            # Python 语法说明：f"..." 是 f-string 格式化字符串
            #   {full_signal} 会被替换为参数 full_signal 的值
            #   \n 是换行符，分隔提示词和分析文本
            (
                "human",
                f"Extract the trading signal from the following analysis:\n{full_signal}",
            ),
        ]

        # 调用 LLM 提取信号
        # Python 语法说明：.invoke(messages).content
        #   .invoke(messages) 发送消息列表给 LLM，返回一个 AIMessage 对象
        #   .content 访问 AIMessage 的 content 属性，获取回复的文本内容
        # 这是方法链式调用：先调用 invoke，再访问返回值的 content 属性
        result = self.llm.invoke(messages).content

        # 清理结果并返回
        # Python 语法说明：.strip()
        #   去除字符串开头和结尾的空白字符（空格、换行符、制表符等）
        #   LLM 的回复可能包含多余的空白字符，需要清理
        #   例如："  BUY  \n".strip() → "BUY"
        return result.strip()
