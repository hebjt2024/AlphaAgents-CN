# =====================================================================
# 模块：cli/stats_handler.py
# 功能：LangChain 回调处理器 - 统计数据收集
# 说明：实现 LangChain 的 BaseCallbackHandler 接口，
#       在 LLM 调用和工具调用过程中自动收集统计数据。
#
# 核心概念 - LangChain 回调机制（Callback System）：
#   LangChain 提供了一套回调系统，允许开发者在 LLM 执行的关键节点
#   插入自定义逻辑。类似于"钩子函数"（Hook）模式。
#
#   回调生命周期：
#   1. on_llm_start / on_chat_model_start → LLM 开始调用时触发
#   2. on_llm_end → LLM 调用完成时触发（可获取 token 使用量）
#   3. on_tool_start → 工具开始执行时触发
#
#   回调处理器通过继承 BaseCallbackHandler 来实现，
#   然后传递给 LLM 或 Agent 的构造函数。
#
# 核心概念 - 线程安全（Thread Safety）：
#   LangChain 的回调可能在多个线程中同时触发，
#   因此需要使用 threading.Lock 来保护共享数据，
#   防止竞态条件（Race Condition）导致数据错乱。
# =====================================================================

# =====================================================================
# 【前置知识】
# =====================================================================
#
# 本文件实现了一个 LangChain 回调处理器，用于收集 LLM 调用统计信息。
# 以下是需要了解的核心概念：
#
# 1. LangChain 框架简介：
#    - LangChain 是构建 LLM（大语言模型）应用的最流行 Python 框架。
#    - 安装方式：pip install langchain langchain-core
#    - 核心概念：
#      * LLM：大语言模型（如 GPT、Claude、Gemini），接收文本输入，返回文本输出
#      * Agent（智能体）：能自主调用工具和 LLM 完成任务的 AI 程序
#      * Tool（工具）：Agent 可以调用的外部功能（如搜索、计算、查询数据库）
#      * Message（消息）：对话中的单元，分为 HumanMessage（用户）、AIMessage（AI）、
#        ToolMessage（工具返回的数据）
#    - 在 TradingAgents 中，LangChain 用于驱动各分析师、研究员、交易员智能体。
#
# 2. LangChain 回调处理器（Callback Handler）：
#    - 回调是一种"事件驱动"编程模式：当某个事件发生时，自动调用预先注册的函数。
#    - LangChain 的 BaseCallbackHandler 定义了一组回调方法（钩子函数）：
#      * on_llm_start()：LLM 开始生成回复时触发
#      * on_chat_model_start()：聊天模型开始生成时触发（与 on_llm_start 类似但针对聊天模型）
#      * on_llm_end()：LLM 完成回复时触发（可获取 token 使用量）
#      * on_tool_start()：工具开始执行时触发
#      * on_tool_end()：工具执行完成时触发
#    - 工作原理：
#      1. 创建回调处理器实例：handler = StatsCallbackHandler()
#      2. 传递给 LLM 或 Agent：llm = ChatOpenAI(callbacks=[handler])
#      3. LangChain 在适当时候自动调用处理器方法，无需手动调用
#    - 类比：回调就像快递通知——你不用一直盯着快递，下单后系统会自动通知你。
#
# 3. 线程安全与 Lock（互斥锁）：
#    - 线程（Thread）：程序中可以并行执行的独立执行流。
#    - 问题：当多个线程同时修改同一个变量时，可能产生"竞态条件"（Race Condition），
#      导致数据不一致。例如两个线程同时执行 self.count += 1，可能只加了 1 而不是 2。
#    - 解决方案：使用 Lock（互斥锁）保护共享数据。
#    - Lock 的工作原理：
#      * threading.Lock() 创建一个锁对象
#      * with self._lock: 获取锁（如果锁被其他线程持有，则等待）
#      * 在 with 块内，只有一个线程能执行，其他线程排队等待
#      * 离开 with 块后自动释放锁，下一个线程获取锁继续执行
#    - 类比：Lock 就像公共厕所的门锁——一次只能进一个人，出来后下一个人才能进。
#
# 4. Token（令牌）：
#    - LLM 不直接处理文字，而是将文字切分成"token"（令牌）。
#    - 一个 token 大约等于 0.75 个英文单词或 0.5 个中文字。
#    - LLM 按 token 计费：输入 token（tokens_in）和输出 token（tokens_out）。
#    - 统计 token 使用量可以帮助控制成本和了解系统资源消耗。
#
# 5. 本文件的运行时数据流：
#    程序启动
#    → cli/main.py 创建 StatsCallbackHandler 实例：stats_handler = StatsCallbackHandler()
#    → 传递给 TradingAgentsGraph：graph = TradingAgentsGraph(callbacks=[stats_handler])
#    → 分析引擎开始运行
#    → 每次调用 LLM 时：
#        LangChain 自动触发 on_chat_model_start() → llm_calls += 1
#        LangChain 自动触发 on_llm_end() → 从响应中提取 token 数，累加到 tokens_in/tokens_out
#    → 每次调用工具时：
#        LangChain 自动触发 on_tool_start() → tool_calls += 1
#    → 分析完成后：
#        cli/main.py 调用 stats_handler.get_stats() 获取统计快照
#        → 返回 {"llm_calls": 15, "tool_calls": 8, "tokens_in": 12000, "tokens_out": 3500}
#        → 在终端底部显示：LLM: 15 | Tools: 8 | Tokens: 12.0k↑ 3.5k↓
# =====================================================================

# threading 模块：Python 标准库，用于多线程编程
# Lock 是一种互斥锁（Mutex），用于保护共享资源
# 同时只有一个线程可以持有锁，其他线程必须等待
# 基本用法：
#   lock = threading.Lock()
#   with lock:          # 获取锁（如果被占用则等待）
#       shared_data += 1  # 安全地修改共享数据
#   # 离开 with 块后自动释放锁
import threading

# typing 模块：Python 标准库，提供类型注解
# Any 表示任意类型（不确定具体类型时使用）
# Dict 表示字典类型，如 Dict[str, Any] 表示键为字符串、值为任意类型的字典
# List 表示列表类型，如 List[str] 表示字符串列表
# Union 表示多个类型中的任意一个，如 Union[str, int] 表示字符串或整数
from typing import Any

# langchain_core.callbacks.BaseCallbackHandler：
#   LangChain 回调系统的基类，所有自定义回调处理器都需要继承它
#   它定义了回调方法的接口，子类只需重写需要的方法
#   基类中所有回调方法都是空的（pass），子类按需重写
#   安装方式：pip install langchain-core
from langchain_core.callbacks import BaseCallbackHandler

# langchain_core.messages.AIMessage：
#   AI 助手消息类，包含 LLM 的回复内容和元数据
#   其中 usage_metadata 属性包含了 token 使用统计
#   usage_metadata 是一个字典：{"input_tokens": 100, "output_tokens": 50}
#   LangChain 中有三种主要消息类型：
#     - HumanMessage：用户发送的消息
#     - AIMessage：AI 助手的回复
#     - ToolMessage：工具执行后返回的结果
from langchain_core.messages import AIMessage

# langchain_core.outputs.LLMResult：
#   LLM 调用结果的封装类，包含生成的内容和 token 使用信息
#   结构：LLMResult.generations[选择索引][生成索引].message
#   通常 generations[0][0] 就是第一个（也是唯一的）生成结果
from langchain_core.outputs import LLMResult


# =====================================================================
# 类：StatsCallbackHandler
# 功能：跟踪 LLM 调用次数、工具调用次数和 token 使用量
#
# 继承自 BaseCallbackHandler，重写其回调方法。
# 使用线程锁（Lock）保护所有共享数据，确保多线程环境下的数据一致性。
#
# 类比：StatsCallbackHandler 就像一个"监工"——
#   它站在旁边默默观察，每次 LLM 干活就记一笔，每次工具被使用也记一笔，
#   最后汇总成一份统计报告。
#
# 运行时数据流示例：
#   1. 创建实例：
#      handler = StatsCallbackHandler()
#      # 初始状态：llm_calls=0, tool_calls=0, tokens_in=0, tokens_out=0
#
#   2. LLM 第一次调用：
#      → on_chat_model_start() 触发 → llm_calls=1
#      → on_llm_end() 触发 → 从响应提取 input_tokens=500, output_tokens=120
#      → tokens_in=500, tokens_out=120
#
#   3. 工具第一次调用：
#      → on_tool_start() 触发 → tool_calls=1
#
#   4. LLM 第二次调用：
#      → on_chat_model_start() 触发 → llm_calls=2
#      → on_llm_end() 触发 → input_tokens=800, output_tokens=200
#      → tokens_in=1300, tokens_out=320
#
#   5. 获取统计：
#      handler.get_stats()
#      → 返回 {"llm_calls": 2, "tool_calls": 1, "tokens_in": 1300, "tokens_out": 320}
# =====================================================================
class StatsCallbackHandler(BaseCallbackHandler):
    """
    LangChain 回调处理器，用于跟踪统计信息

    跟踪的统计信息：
    - llm_calls：LLM 调用次数（每次 LLM 请求计数 +1）
    - tool_calls：工具调用次数（每次工具执行计数 +1）
    - tokens_in：输入的 token 总数
    - tokens_out：输出的 token 总数

    使用方式：
        handler = StatsCallbackHandler()
        graph = TradingAgentsGraph(callbacks=[handler])
        # ... 分析运行中，handler 自动收集统计数据 ...
        stats = handler.get_stats()
        print(stats)  # {"llm_calls": 10, "tool_calls": 5, ...}
    """

    def __init__(self) -> None:
        """
        初始化回调处理器

        步骤：
        1. super().__init__() 调用父类（BaseCallbackHandler）的构造函数
           这是 Python 继承的惯例，确保父类正确初始化
        2. 创建 threading.Lock() 实例用于线程同步
        3. 初始化所有统计计数器为 0
        """
        # super().__init__() 调用父类 BaseCallbackHandler 的构造函数
        # super() 返回父类的代理对象，通过它可以调用父类的方法
        # 这是 Python 面向对象编程的标准做法，确保父类被正确初始化
        super().__init__()
        # 创建线程锁，保护下面的共享数据
        # Lock 是一种互斥锁：同一时间只有一个线程可以获取锁
        # 在多线程环境下，如果没有锁，两个线程同时执行 self.llm_calls += 1
        # 可能导致只加了 1 而不是 2（竞态条件）
        # 使用 with self._lock: 确保每次只有一个线程修改计数器
        self._lock = threading.Lock()
        # LLM 调用次数计数器
        # 每次 LLM 被调用（on_llm_start 或 on_chat_model_start）时 +1
        self.llm_calls = 0
        # 工具调用次数计数器
        # 每次工具被调用（on_tool_start）时 +1
        self.tool_calls = 0
        # 输入 token 计数器（用户发送给 LLM 的 token 数）
        # 每次 LLM 调用完成后（on_llm_end），从 usage_metadata 中累加 input_tokens
        self.tokens_in = 0
        # 输出 token 计数器（LLM 返回的 token 数）
        # 每次 LLM 调用完成后（on_llm_end），从 usage_metadata 中累加 output_tokens
        self.tokens_out = 0

    # ==================================================================
    # 回调方法：on_llm_start()
    # 触发时机：当 LLM（非聊天模型）开始生成时
    # 参数：
    #   serialized：序列化的 LLM 配置信息（字典）
    #     包含 LLM 的名称、参数等元数据，可用于调试和日志
    #   prompts：发送给 LLM 的提示词列表
    #     是字符串列表，每个元素是一个完整的提示词
    #   **kwargs：额外的关键字参数（LangChain 可能传递其他信息）
    #     **kwargs 是 Python 语法，表示接收任意数量的关键字参数，打包为字典
    #
    # 运行时数据流示例：
    #   分析引擎调用 llm.invoke("分析 NVDA 股票")
    #   → LangChain 自动调用 handler.on_llm_start(serialized=..., prompts=["分析 NVDA 股票"])
    #   → with self._lock: self.llm_calls += 1
    #   → llm_calls 从 0 变为 1
    # ==================================================================
    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """当 LLM 开始时，增加 LLM 调用计数器"""
        # with self._lock 获取锁，确保原子操作
        # "原子操作"（Atomic Operation）是指不可被打断的操作
        # self.llm_calls += 1 实际上是两步：读取值 → 加 1 → 写回
        # 如果没有锁，两个线程可能同时读到相同的值，导致最终只加了 1
        # with self._lock 确保这三步作为一个整体执行，中间不会被其他线程打断
        # 在 with 块内对共享数据的修改是线程安全的
        with self._lock:
            self.llm_calls += 1

    # ==================================================================
    # 回调方法：on_chat_model_start()
    # 触发时机：当聊天模型（Chat Model）开始生成时
    #
    # 与 on_llm_start 的区别：
    #   - on_llm_start：通用 LLM（接受字符串提示词，如 text-davinci-003）
    #   - on_chat_model_start：聊天模型（接受消息列表，如 GPT-4、Claude）
    # 两者都计入 LLM 调用次数
    #
    # 参数：
    #   serialized：序列化的 LLM 配置信息（字典）
    #   messages：消息列表的列表
    #     外层列表对应多个选择（choices），内层列表是对话消息序列
    #     通常 messages[0] 是第一条对话的消息列表
    #   **kwargs：额外的关键字参数
    #
    # 运行时数据流示例：
    #   分析引擎调用 chat_model.invoke([HumanMessage(content="分析 NVDA 股票")])
    #   → LangChain 自动调用 handler.on_chat_model_start(
    #       serialized={"model_name": "gpt-5-mini", ...},
    #       messages=[[HumanMessage(content="分析 NVDA 股票")]]
    #     )
    #   → with self._lock: self.llm_calls += 1
    # ==================================================================
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """当聊天模型开始时，增加 LLM 调用计数器"""
        # 与 on_llm_start 相同的线程安全计数逻辑
        with self._lock:
            self.llm_calls += 1

    # ==================================================================
    # 回调方法：on_llm_end()
    # 触发时机：当 LLM 调用完成时
    #
    # 功能：从 LLM 响应中提取 token 使用量
    # token 使用量信息存储在 AIMessage 的 usage_metadata 属性中
    #
    # 数据结构说明：
    #   response（LLMResult 对象）的结构：
    #   response.generations = [
    #     [  # 第一个选择（通常只有一个选择）
    #       ChatGeneration(
    #         message=AIMessage(
    #           content="AI 回复的文本内容",
    #           usage_metadata={"input_tokens": 500, "output_tokens": 120}
    #         )
    #       )
    #     ]
    #   ]
    #   response.generations[0][0]：
    #     generations 是一个嵌套列表，外层是多个选择，内层是多个生成结果
    #     [0][0] 获取第一个选择的第一个生成结果
    #   generation.message：
    #     生成结果中的消息对象，通常是 AIMessage 实例
    #   message.usage_metadata：
    #     字典，包含 "input_tokens" 和 "output_tokens" 等键
    #
    # 运行时数据流示例：
    #   LLM 调用完成
    #   → LangChain 自动调用 handler.on_llm_end(response=LLMResult(...))
    #   → response.generations[0][0].message 是 AIMessage 对象
    #   → message.usage_metadata = {"input_tokens": 500, "output_tokens": 120}
    #   → with self._lock:
    #       self.tokens_in += 500   # 累加输入 token
    #       self.tokens_out += 120  # 累加输出 token
    # ==================================================================
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """从 LLM 响应中提取 token 使用量"""
        try:
            # 获取第一个选择的第一个生成结果
            # response.generations 是嵌套列表：[[generation1, generation2], [generation3]]
            # [0][0] 取第一个选择（外层）的第一个生成结果（内层）
            # 如果 generations 为空列表 []，[0] 会抛出 IndexError
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            # IndexError：generations 为空列表，无法索引 [0]
            # TypeError：generations 为 None，无法索引
            # 如果 generations 为空或结构不对，安全退出（不更新 token 统计）
            return

        # 初始化 usage_metadata 为 None
        usage_metadata = None
        # hasattr(obj, "attr") 检查对象是否有某个属性
        # 返回 True 或 False，不会抛出异常
        # 这里检查 generation 是否有 message 属性
        if hasattr(generation, "message"):
            message = generation.message
            # isinstance(obj, Class) 检查对象是否是某个类的实例
            # 这里确认 message 是 AIMessage 类型（而非其他消息类型）
            # 同时检查是否有 usage_metadata 属性
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        # 如果成功获取到 usage_metadata（不为 None）
        if usage_metadata:
            with self._lock:
                # dict.get(key, default) 安全获取字典值
                # 如果 key 不存在，返回默认值 0
                # usage_metadata 示例：{"input_tokens": 500, "output_tokens": 120}
                # 如果某个 key 不存在，get() 返回 0，不会报错
                self.tokens_in += usage_metadata.get("input_tokens", 0)
                self.tokens_out += usage_metadata.get("output_tokens", 0)

    # ==================================================================
    # 回调方法：on_tool_start()
    # 触发时机：当工具（Tool）开始执行时
    #
    # 参数：
    #   serialized：序列化的工具配置信息
    #     包含工具名称、描述、参数定义等
    #   input_str：传递给工具的输入字符串
    #     是工具收到的输入参数（如股票代码、日期等）
    #   **kwargs：额外的关键字参数
    #
    # 运行时数据流示例：
    #   分析师 Agent 决定调用 "get_stock_data" 工具
    #   → LangChain 自动调用 handler.on_tool_start(
    #       serialized={"name": "get_stock_data", ...},
    #       input_str='{"ticker": "NVDA", "date": "2024-05-10"}'
    #     )
    #   → with self._lock: self.tool_calls += 1
    #   → tool_calls 从 0 变为 1
    # ==================================================================
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """当工具开始时，增加工具调用计数器"""
        with self._lock:
            self.tool_calls += 1

    # ==================================================================
    # 方法：get_stats()
    # 功能：返回当前统计数据的快照
    # 返回值：字典，包含 llm_calls、tool_calls、tokens_in、tokens_out
    #
    # 使用线程锁确保读取时数据一致性
    # 如果不使用锁，可能在读取时另一个线程正在修改数据，导致读到不一致的值
    #
    # 运行时数据流示例：
    #   分析进行中，handler.get_stats() 被调用
    #   → with self._lock: 获取锁
    #   → 返回 {
    #       "llm_calls": 15,     # 已调用 LLM 15 次
    #       "tool_calls": 8,     # 已调用工具 8 次
    #       "tokens_in": 12000,  # 已使用 12000 输入 token
    #       "tokens_out": 3500   # 已使用 3500 输出 token
    #     }
    #   → cli/main.py 的 update_display() 函数将统计信息显示在终端底部
    #   → 显示效果：Agents: 3/10 | LLM: 15 | Tools: 8 | Tokens: 12.0k↑ 3.5k↓ | ⏱ 02:35
    # ==================================================================
    def get_stats(self) -> dict[str, Any]:
        """返回当前统计数据"""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
