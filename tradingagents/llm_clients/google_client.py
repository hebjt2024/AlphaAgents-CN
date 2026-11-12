# ============================================================================
# Google Gemini LLM 客户端
# 封装 LangChain 的 ChatGoogleGenerativeAI，用于访问 Google 的 Gemini 系列模型
# 支持 Gemini 2.5 和 Gemini 3 系列，自动处理不同模型的思考级别映射
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件封装了 Google Gemini 大语言模型的访问接口。以下是需要了解的核心概念：
#
# 1. Google Gemini API 特性：
#    - Google 的 Gemini（原名 Bard）是 Google 的大语言模型系列。
#    - Gemini 模型系列：
#      * Gemini Pro：专业版，推理能力强，适合复杂任务
#      * Gemini Flash：快速版，速度更快，适合高频调用
#      * Gemini Flash Lite：轻量版，成本最低
#    - API 端点：https://generativelanguage.googleapis.com/v1
#    - 需要 API 密钥（GOOGLE_API_KEY），通常存储在 .env 文件中
#    - 不同版本的 Gemini 对"思考模式"（Thinking Mode）的支持不同：
#      * Gemini 3 系列：使用 thinking_level 参数
#        可选值："minimal"、"low"、"medium"、"high"
#        但 Pro 版不支持 "minimal"，需要映射为 "low"
#      * Gemini 2.5 系列：使用 thinking_budget 参数
#        -1 = 动态思考（自动决定思考量）
#         0 = 禁用思考
#    - Gemini 3 模型返回的内容是结构化块列表格式，需要规范化
#
# 2. LangChain 的 ChatGoogleGenerativeAI：
#    - LangChain 对 Google Gemini API 的封装类
#    - 安装方式：pip install langchain-google-genai
#    - 基本用法：
#        llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", google_api_key="...")
#        response = llm.invoke("你好")  # 返回 AIMessage
#        print(response.content)
#
# 3. 参数映射（Parameter Mapping）：
#    - 不同版本的 Gemini 使用不同的"思考"参数名和值
#    - 本文件的核心逻辑就是将统一的 thinking_level 映射为各版本的具体参数：
#      用户选择 thinking_level = "high"
#      → 如果是 Gemini 3 Flash：thinking_level = "high"（直接使用）
#      → 如果是 Gemini 3 Pro 且 thinking_level = "minimal"：映射为 "low"
#      → 如果是 Gemini 2.5：thinking_budget = -1（high 映射为动态思考）
#    - 这种映射让用户不需要了解各版本的差异，统一使用一个参数
#
# 4. 继承与子类化：
#    - NormalizedChatGoogleGenerativeAI 继承 ChatGoogleGenerativeAI
#    - 重写 invoke() 方法添加内容规范化
#    - 与 Anthropic 客户端的 NormalizedChatAnthropic 类似
#
# 5. 本文件的运行时数据流：
#    CLI 用户选择 Google 作为提供商，选择 "gemini-3-flash-preview" 作为模型
#    → config["llm_provider"] = "google", config["google_thinking_level"] = "high"
#    → factory.py 调用 GoogleClient(model="gemini-3-flash-preview", thinking_level="high")
#    → get_llm() 被调用：
#      → model.lower() 包含 "gemini-3" → 使用 thinking_level 参数
#      → 不包含 "pro" → 不需要 minimal → low 映射
#      → llm_kwargs["thinking_level"] = "high"
#      → 创建 NormalizedChatGoogleGenerativeAI(model="gemini-3-flash-preview", thinking_level="high")
#    → 分析引擎使用返回的 LLM 实例：
#      → llm.invoke("分析 NVDA 股票")
#      → NormalizedChatGoogleGenerativeAI.invoke() 被调用
#      → super().invoke() 调用 ChatGoogleGenerativeAI 的原始方法
#      → Gemini API 返回 AIMessage（content 是结构化块列表）
#      → normalize_content() 将内容规范化为纯字符串
#      → 返回规范化后的 AIMessage
# ============================================================================

import os

# typing 模块：Python 标准库，提供类型注解
from typing import Any

# LangChain 的 Google Gemini 聊天模型封装
# ChatGoogleGenerativeAI 是 LangChain 对 Google Gemini API 的封装类
# 安装方式：pip install langchain-google-genai
# 基本用法：
#   llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", google_api_key="your-key")
#   response = llm.invoke("Hello")  # 返回 AIMessage 对象
from langchain_google_genai import ChatGoogleGenerativeAI

# 从当前包（llm_clients）的 base_client 模块导入基类和工具函数
# BaseLLMClient：所有 LLM 客户端的抽象基类，提供通用接口
# normalize_content：内容规范化函数，将结构化块列表转为纯字符串
from .base_client import BaseLLMClient, normalize_content, observe_llm_call

# 从当前包的 validators 模块导入模型验证函数
# validate_model：检查模型名称对指定提供商是否有效
from .validators import validate_model


# ============================================================================
# 类：NormalizedChatGoogleGenerativeAI
# 功能：带内容规范化的 ChatGoogleGenerativeAI 子类
#
# Gemini 3 模型返回的内容是结构化块列表格式（类似于 Claude 的扩展思考）。
# 这个子类重写 invoke() 方法，自动将内容规范化为纯字符串。
#
# 运行时数据流示例：
#   1. 分析师 Agent 调用 llm.invoke("分析 NVDA 股票")
#   2. NormalizedChatGoogleGenerativeAI.invoke() 被调用
#   3. super().invoke() 调用父类的原始方法
#   4. Gemini API 返回 AIMessage(content=[{"type": "text", "text": "NVDA 是..."}])
#   5. normalize_content() 将 content 转为纯字符串 "NVDA 是..."
#   6. 返回 AIMessage(content="NVDA 是...")
# ============================================================================
class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """带内容规范化的 ChatGoogleGenerativeAI 子类。

    Gemini 3 模型返回的内容是结构化块列表格式。
    这个子类重写 invoke() 方法，自动将内容规范化为纯字符串。

    【可观测性】
    重写的 invoke() 还会用 :func:`observe_llm_call` 包装真正的补全调用，
    记录模型、供应商、token 用量与耗时。
    """

    # 可观测性元信息：记录时写入 run 日志（provider + model）
    observability_provider: str = "google"
    observability_model: str = ""

    def invoke(self, input, config=None, **kwargs):
        """调用 LLM 并规范化返回内容。

        重写父类 ChatGoogleGenerativeAI 的 invoke() 方法。

        Args:
            input: 发送给 LLM 的消息（字符串或消息列表）
            config: LangChain 运行配置（可选）
            **kwargs: 额外参数

        Returns:
            规范化后的 AIMessage 对象，content 为纯字符串
        """
        # super().invoke() 调用父类的原始 invoke() 方法
        # observe_llm_call 在此处测量墙钟延迟并把 token 用量写入当前 run 的度量
        with observe_llm_call(
            provider=self.observability_provider,
            model=self.observability_model or self.model,
            prompt_input=input,
        ) as obs:
            response = super().invoke(input, config, **kwargs)
            obs["response"] = response
        # normalize_content() 将返回的 AIMessage 内容规范化为纯字符串
        return normalize_content(response)


# ============================================================================
# 类：GoogleClient
# 功能：Google Gemini 模型的 LLM 客户端
#
# 继承自 BaseLLMClient，实现 get_llm() 和 validate_model() 方法。
# 核心功能是处理不同 Gemini 版本之间的思考级别参数映射。
#
# 运行时数据流示例（Gemini 3 Flash，thinking_level="high"）：
#   client = GoogleClient(model="gemini-3-flash-preview", thinking_level="high")
#   → get_llm() 被调用
#   → model_lower = "gemini-3-flash-preview"
#   → "gemini-3" in model_lower → True → 使用 thinking_level 参数
#   → "pro" not in model_lower → 不需要 minimal → low 映射
#   → llm_kwargs["thinking_level"] = "high"
#   → 创建 NormalizedChatGoogleGenerativeAI(model="gemini-3-flash-preview", thinking_level="high")
#
# 运行时数据流示例（Gemini 2.5 Pro，thinking_level="high"）：
#   client = GoogleClient(model="gemini-2.5-pro", thinking_level="high")
#   → get_llm() 被调用
#   → model_lower = "gemini-2.5-pro"
#   → "gemini-3" not in model_lower → False → 使用 thinking_budget 参数
#   → thinking_level == "high" → thinking_budget = -1（动态思考）
#   → 创建 NormalizedChatGoogleGenerativeAI(model="gemini-2.5-pro", thinking_budget=-1)
# ============================================================================
class GoogleClient(BaseLLMClient):
    """Google Gemini 模型的 LLM 客户端。

    支持 Gemini 2.5 和 Gemini 3 系列，自动处理不同模型之间的
    思考级别（thinking level）参数映射。
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        """初始化 Google 客户端。

        Args:
            model: Gemini 模型名称，如 "gemini-2.5-pro"
                完整模型列表见 validators.py 中的 VALID_MODELS["google"]
            base_url: 自定义 API 地址（可选）
            **kwargs: 额外参数
                重要参数：thinking_level（"high" 或 "minimal"）
        """
        # 调用父类 BaseLLMClient 的构造函数
        # 存储模型名称、API 地址和额外参数
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """返回配置好的 ChatGoogleGenerativeAI 实例。

        Gemini 不同系列的模型对"思考级别"的参数定义不同：
        - Gemini 3 系列：使用 thinking_level 参数，值为 "low"/"high" 等
        - Gemini 2.5 系列：使用 thinking_budget 参数，-1 表示动态，0 表示禁用

        这个方法会自动根据模型系列做正确的参数映射。

        Returns:
            配置好的 NormalizedChatGoogleGenerativeAI 实例

        参数映射逻辑：
            thinking_level = "high"
            → Gemini 3 Flash: thinking_level = "high"（直接使用）
            → Gemini 3 Pro + "minimal": thinking_level = "low"（Pro 不支持 minimal）
            → Gemini 2.5: thinking_budget = -1（high → 动态思考）
            → Gemini 2.5 + "minimal": thinking_budget = 0（禁用思考）
        """
        # 初始化参数字典，始终包含 model 参数
        llm_kwargs = {"model": self.model}

        # 转发通用参数
        # 这些参数对所有 Gemini 版本都通用
        # 遍历参数名元组，从 self.kwargs 中提取存在的参数
        for key in ("timeout", "max_retries", "google_api_key", "callbacks",
                     "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # 兼容 GOOGLE_API_KEY 与 GOOGLE_GENAI_API_KEY 两种环境变量写法
        if "google_api_key" not in llm_kwargs:
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv(
                "GOOGLE_GENAI_API_KEY"
            )
            if api_key:
                llm_kwargs["google_api_key"] = api_key

        # ---- 处理思考级别（thinking level）参数 ----
        # 不同 Gemini 版本对思考级别的支持不同：
        #   Gemini 3 Pro：支持 "low"、"high"
        #   Gemini 3 Flash：支持 "minimal"、"low"、"medium"、"high"
        #   Gemini 2.5：使用 thinking_budget（0=禁用，-1=动态）
        # 从 kwargs 中获取 thinking_level 参数（可能为 None）
        thinking_level = self.kwargs.get("thinking_level")

        if thinking_level:
            # 转为小写，方便比较
            # 例如 "Gemini-3-Flash-Preview" → "gemini-3-flash-preview"
            model_lower = self.model.lower()

            if "gemini-3" in model_lower:
                # ---- Gemini 3 系列 ----
                # 使用 thinking_level 参数（原生支持）
                # Gemini 3 Pro 不支持 "minimal" 级别，需要映射为 "low"
                if "pro" in model_lower and thinking_level == "minimal":
                    thinking_level = "low"  # Pro 版不支持 minimal，降级为 low
                llm_kwargs["thinking_level"] = thinking_level

            else:
                # ---- Gemini 2.5 系列 ----
                # 将 thinking_level 映射为 thinking_budget
                # thinking_budget 是数值参数：
                #   -1 = 动态思考（模型自动决定思考量）
                #    0 = 禁用思考（不进行额外推理）
                # high → -1（动态思考），其他 → 0（禁用思考）
                llm_kwargs["thinking_budget"] = -1 if thinking_level == "high" else 0

        # 使用收集的参数创建 NormalizedChatGoogleGenerativeAI 实例
        # **llm_kwargs 解包字典为关键字参数
        #
        # 【可观测性】
        # observability_provider / observability_model 用于把每次调用的
        # 供应商与模型名记录进 run 日志（成本估算按模型名查价格表）。
        llm_kwargs["observability_provider"] = "google"
        llm_kwargs["observability_model"] = self.model
        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证模型名称对 Google 是否有效。

        委托给 validators.py 的 validate_model() 函数检查。

        Returns:
            True 表示模型有效（在白名单中），False 表示无效

        数据流：
            self.model = "gemini-2.5-pro"
            → validate_model("google", "gemini-2.5-pro")
            → 检查 "gemini-2.5-pro" 是否在 VALID_MODELS["google"] 列表中
            → 在列表中 → 返回 True
        """
        return validate_model("google", self.model)
