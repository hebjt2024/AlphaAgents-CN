# ============================================================================
# Anthropic Claude LLM 客户端
# 封装 LangChain 的 ChatAnthropic，用于访问 Anthropic 的 Claude 系列模型
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件封装了 Anthropic Claude 大语言模型的访问接口。以下是需要了解的核心概念：
#
# 1. Anthropic Claude API 特性：
#    - Anthropic 是一家 AI 公司，其旗舰产品是 Claude 系列大语言模型。
#    - Claude 模型系列：
#      * Opus：最强版本，顶级推理能力，适合复杂分析任务
#      * Sonnet：平衡版，性能与成本的折中，适合大多数场景
#      * Haiku：轻量版，速度最快，适合简单高频调用
#    - API 端点：https://api.anthropic.com/
#    - 需要 API 密钥（ANTHROPIC_API_KEY），通常存储在 .env 文件中
#    - Claude 4.5+ 支持"扩展思考"（Extended Thinking）：
#      模型会在回答前进行更深入的推理，类似于人类的"思考过程"
#      这会导致返回内容为结构化块列表格式，而非纯字符串
#
# 2. LangChain 的 ChatAnthropic：
#    - LangChain 是构建 LLM 应用的框架（pip install langchain langchain-anthropic）
#    - ChatAnthropic 是 LangChain 对 Anthropic API 的封装类
#    - 它将 API 调用抽象为统一的接口：invoke() 方法
#    - 示例：
#        llm = ChatAnthropic(model="claude-sonnet-4-6")
#        response = llm.invoke("你好")  # 返回 AIMessage 对象
#        print(response.content)  # 获取回复文本
#
# 3. 继承与子类化（Inheritance & Subclassing）：
#    - Python 面向对象编程的核心概念：子类继承父类的所有属性和方法
#    - 子类可以重写（Override）父类的方法来改变行为
#    - super().method() 调用父类的同名方法
#    - 本文件中 NormalizedChatAnthropic 继承 ChatAnthropic，
#      重写 invoke() 方法添加内容规范化逻辑
#
# 4. 内容规范化（Content Normalization）：
#    - Claude 在使用扩展思考或工具调用时，返回的 content 可能是：
#      * 纯字符串："分析结果..."（正常情况）
#      * 结构化块列表：[{"type": "text", "text": "分析结果..."}, ...]（扩展思考时）
#    - 下游代码期望收到纯字符串，因此需要"规范化"：
#      将结构化块列表转换为纯字符串，保持一致的接口
#
# 5. 模型验证白名单：
#    - validators.py 维护了每个提供商支持的模型列表
#    - validate_model("anthropic", "claude-sonnet-4-6") 检查模型是否有效
#    - 作用：防止用户输入无效的模型名称，提前发现配置错误
#
# 6. 本文件的运行时数据流：
#    CLI 用户选择 Anthropic 作为提供商，选择 "claude-sonnet-4-6" 作为模型
#    → cli/main.py 创建配置：config["llm_provider"] = "anthropic"
#    → factory.py 调用 AnthropicClient(model="claude-sonnet-4-6", **kwargs)
#    → AnthropicClient.__init__() 调用 super().__init__() 初始化基类
#    → get_llm() 被调用：
#      → 从 _PASSTHROUGH_KWARGS 中提取需要转发的参数
#      → 创建 NormalizedChatAnthropic(model="claude-sonnet-4-6", **kwargs)
#    → 分析引擎使用返回的 LLM 实例：
#      → llm.invoke("分析 NVDA 股票")
#      → NormalizedChatAnthropic.invoke() 被调用
#      → super().invoke() 调用 ChatAnthropic 的原始方法
#      → Claude API 返回 AIMessage（content 可能是列表或字符串）
#      → normalize_content() 将内容规范化为纯字符串
#      → 返回规范化后的 AIMessage 给分析引擎
# ============================================================================

# typing 模块：Python 标准库，提供类型注解
# Any 表示任意类型，Optional 表示值可以为 None
from typing import Any, Optional

# LangChain 的 Anthropic 聊天模型封装
# ChatAnthropic 是 LangChain 对 Anthropic Claude API 的封装类
# 安装方式：pip install langchain-anthropic
# 基本用法：
#   llm = ChatAnthropic(model="claude-sonnet-4-6", api_key="your-key")
#   response = llm.invoke("Hello")  # 返回 AIMessage 对象
#   print(response.content)  # 获取回复文本内容
from langchain_anthropic import ChatAnthropic

# 从当前包（llm_clients）的 base_client 模块导入基类和工具函数
# BaseLLMClient：所有 LLM 客户端的抽象基类，提供通用接口
# normalize_content：内容规范化函数，将结构化块列表转为纯字符串
from .base_client import BaseLLMClient, normalize_content

# 从当前包的 validators 模块导入模型验证函数
# validate_model：检查模型名称对指定提供商是否有效
from .validators import validate_model


# 需要从用户配置转发给 ChatAnthropic 的参数名列表
# 这是一个元组（tuple），包含所有需要从 self.kwargs 传递给 ChatAnthropic 构造函数的参数名
# 元组与列表的区别：元组不可变，适合存储不需要修改的常量集合
_PASSTHROUGH_KWARGS = (
    "timeout",             # 请求超时时间（秒），超过后放弃请求
    "max_retries",         # 最大重试次数，网络不稳定时自动重试
    "api_key",             # Anthropic API 密钥（通常从环境变量 ANTHROPIC_API_KEY 读取）
    "max_tokens",          # 最大输出 token 数，限制 LLM 回复长度
    "callbacks",           # LangChain 回调处理器列表，用于跟踪 LLM 调用统计
    "http_client",         # 自定义 HTTP 客户端（高级用法，一般不需要）
    "http_async_client",   # 自定义异步 HTTP 客户端（高级用法，一般不需要）
    "effort",              # 思考努力程度（"high"/"medium"/"low"），控制 Claude 的思考深度
)


# ============================================================================
# 类：NormalizedChatAnthropic
# 功能：带内容规范化的 ChatAnthropic 子类
#
# 继承自 ChatAnthropic，重写 invoke() 方法，
# 在调用父类方法后自动将返回内容规范化为纯字符串。
#
# 为什么需要规范化：
#   Claude 在使用扩展思考（Extended Thinking）或工具调用时，
#   返回的 content 属性不是纯字符串，而是结构化块列表：
#     [{"type": "text", "text": "实际回复内容"}, ...]
#   下游代码（如分析师 Agent）期望收到纯字符串，
#   如果不规范化，会导致类型错误或数据解析失败。
#
# 运行时数据流示例：
#   1. 分析师 Agent 调用 llm.invoke("分析 NVDA 股票的基本面")
#   2. NormalizedChatAnthropic.invoke() 被调用
#   3. super().invoke() 调用 ChatAnthropic 的原始方法
#   4. Claude API 返回 AIMessage(content=[{"type": "text", "text": "NVDA 是..."}])
#   5. normalize_content() 将 content 转为纯字符串 "NVDA 是..."
#   6. 返回 AIMessage(content="NVDA 是...")（content 已是纯字符串）
# ============================================================================
class NormalizedChatAnthropic(ChatAnthropic):
    """带内容规范化的 ChatAnthropic 子类。

    Claude 模型在使用扩展思考（extended thinking）或工具调用时，
    返回的内容可能是结构化块列表格式。

    这个子类重写 invoke() 方法，自动将内容规范化为纯字符串，
    确保下游智能体获得一致的数据格式。
    """

    def invoke(self, input, config=None, **kwargs):
        """调用 LLM 并规范化返回内容。

        重写父类 ChatAnthropic 的 invoke() 方法。

        Args:
            input: 发送给 LLM 的消息
                可以是字符串、消息列表等格式
                例如 "分析 NVDA 股票" 或 [HumanMessage(content="...")]
            config: LangChain 运行配置（可选）
                包含回调、标签等运行时配置
            **kwargs: 额外参数
                传递给父类 invoke() 的额外关键字参数

        Returns:
            规范化后的 AIMessage 对象，content 为纯字符串

        数据流：
            input → super().invoke(input) → 原始 AIMessage
            → normalize_content(AIMessage) → 规范化后的 AIMessage
        """
        # super().invoke() 调用父类 ChatAnthropic 的 invoke() 方法
        # 这是 Python 继承中的标准做法：先调用父类方法获取结果，再进行后处理
        # 父类方法会：
        #   1. 将 input 格式化为 API 请求
        #   2. 向 Anthropic API 发送请求
        #   3. 等待 API 返回响应
        #   4. 将响应封装为 AIMessage 对象返回
        # normalize_content() 对返回的 AIMessage 进行内容规范化
        # 将结构化块列表 content 转换为纯字符串 content
        return normalize_content(super().invoke(input, config, **kwargs))


# ============================================================================
# 类：AnthropicClient
# 功能：Anthropic Claude 模型的 LLM 客户端
#
# 继承自 BaseLLMClient，实现 get_llm() 和 validate_model() 方法。
# 作为 Anthropic 提供商的统一接口，被 factory.py 的 create_llm_client() 创建。
#
# 支持所有 Claude 系列模型：
#   - Opus（最强）：claude-opus-4-6, claude-opus-4-5
#   - Sonnet（平衡）：claude-sonnet-4-6, claude-sonnet-4-5
#   - Haiku（快速）：claude-haiku-4-5
#
# 运行时数据流示例：
#   factory.py: client = AnthropicClient(model="claude-sonnet-4-6", api_key="sk-...")
#   → __init__() 调用 super().__init__("claude-sonnet-4-6", None, api_key="sk-...")
#   → 基类 BaseLLMClient 存储 model="claude-sonnet-4-6" 和 kwargs={"api_key": "sk-..."}
#   → 分析引擎调用 client.get_llm()
#   → 从 kwargs 中提取 api_key、effort 等参数
#   → 创建 NormalizedChatAnthropic(model="claude-sonnet-4-6", api_key="sk-...", effort="high")
#   → 返回 LLM 实例
# ============================================================================
class AnthropicClient(BaseLLMClient):
    """Anthropic Claude 模型的 LLM 客户端。

    支持所有 Claude 系列模型：Opus（最强）、Sonnet（平衡）、Haiku（快速）。
    """

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        """初始化 Anthropic 客户端。

        Args:
            model: Claude 模型名称，如 "claude-opus-4-5"
                完整模型列表见 validators.py 中的 VALID_MODELS["anthropic"]
            base_url: 自定义 API 地址（可选）
                如果提供，会覆盖默认的 https://api.anthropic.com/
                用于使用代理或自部署的 Claude 服务
            **kwargs: 额外参数
                这些参数会被存储，在 get_llm() 时转发给 ChatAnthropic
                支持的参数见 _PASSTHROUGH_KWARGS 元组
        """
        # super().__init__() 调用父类 BaseLLMClient 的构造函数
        # 基类会存储 model、base_url 和 kwargs 供后续使用
        # self.model = model  → 存储模型名称
        # self.base_url = base_url  → 存储 API 地址
        # self.kwargs = kwargs  → 存储额外参数字典
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """返回配置好的 ChatAnthropic 实例。

        从存储的参数中提取需要转发的参数，创建 NormalizedChatAnthropic 实例。

        Returns:
            配置好的 NormalizedChatAnthropic 实例
            该实例继承自 ChatAnthropic，支持所有 Claude API 功能

        数据流：
            1. 创建 llm_kwargs 字典，包含 model 参数
            2. 如果配置了 base_url，传入 base_url 参数（用于代理/兼容API）
            3. 遍历 _PASSTHROUGH_KWARGS，从 self.kwargs 中提取存在的参数
            4. 使用提取的参数创建 NormalizedChatAnthropic 实例
            5. 返回实例给调用者（通常是 factory.py 或分析引擎）
        """
        # 初始化参数字典，始终包含 model 参数
        llm_kwargs = {"model": self.model}

        # 如果配置了 base_url（自定义 API 地址），传入 ChatAnthropic
        # 这是使用 MiniMax 等兼容协议代理时的关键配置
        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # 设置默认超时时间（5分钟），避免长报告生成时超时
        # 用户可以通过 kwargs 传入自定义 timeout 覆盖此默认值
        if "timeout" not in self.kwargs:
            llm_kwargs["timeout"] = 300.0  # 5分钟超时
        if "max_retries" not in self.kwargs:
            llm_kwargs["max_retries"] = 2  # 默认重试2次

        # 遍历需要转发的参数名列表
        for key in _PASSTHROUGH_KWARGS:
            # 检查 self.kwargs（用户提供的额外参数）中是否有该参数
            # self.kwargs 是在 __init__ 中通过 **kwargs 接收并存储的
            if key in self.kwargs:
                # 如果存在，添加到 llm_kwargs 字典中
                llm_kwargs[key] = self.kwargs[key]

        # 使用收集的参数创建 NormalizedChatAnthropic 实例
        # **llm_kwargs 解包字典为关键字参数
        # 等价于：NormalizedChatAnthropic(model="claude-sonnet-4-6", base_url="https://api.minimaxi.com/anthropic", api_key="...", ...)
        return NormalizedChatAnthropic(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证模型名称对 Anthropic 是否有效。

        委托给 validators.py 的 validate_model() 函数检查。

        Returns:
            True 表示模型有效（在白名单中），False 表示无效

        数据流：
            self.model = "claude-sonnet-4-6"
            → validate_model("anthropic", "claude-sonnet-4-6")
            → 检查 "claude-sonnet-4-6" 是否在 VALID_MODELS["anthropic"] 列表中
            → 在列表中 → 返回 True
        """
        return validate_model("anthropic", self.model)
