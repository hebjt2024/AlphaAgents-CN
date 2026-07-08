# ============================================================================
# OpenAI LLM 客户端
# 封装 LangChain 的 ChatOpenAI，支持 OpenAI 官方 API 及兼容的第三方服务
# 兼容的服务：OpenAI、Ollama（本地）、OpenRouter（聚合）、xAI（Grok）
# ============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是适配器模式（Adapter Pattern）？】
#   适配器模式是一种设计模式：让"接口不兼容的类"能够一起工作。
#
#   【通俗比喻】想象你有一个国标插头（OpenAI API），但插座是美标的（上游代码）。
#   你需要一个"转换器"（适配器）让两者能连接。适配器不改变插头也不改变插座，
#   只是在中间做一个转换。
#
#   在本模块中：
#   - LangChain 提供了 ChatOpenAI 类（它知道怎么和 OpenAI API 通信）
#   - 但我们的系统使用 BaseLLMClient 接口（get_llm() / validate_model()）
#   - OpenAIClient 就是"适配器"，把 ChatOpenAI 适配成 BaseLLMClient 接口
#   - 上层代码调用 OpenAIClient.get_llm()，内部返回 ChatOpenAI 实例
#
#   适配器模式 vs 直接使用：
#   - 直接使用：上层代码直接创建 ChatOpenAI → 换提供商要改大量代码
#   - 适配器：上层代码通过 OpenAIClient 间接使用 ChatOpenAI
#             换提供商只需换适配器（如 AnthropicClient）
#
# 【什么是 ChatOpenAI？】
#   ChatOpenAI 是 LangChain 库提供的一个类，封装了与 OpenAI Chat API 的通信。
#   它负责：
#   - 发送 HTTP 请求到 OpenAI 服务器
#   - 处理认证（API Key）
#   - 处理响应解析
#   - 支持流式输出、函数调用等高级功能
#   你不需要自己写 HTTP 请求代码，ChatOpenAI 帮你做好了。
#
# 【什么是 Responses API？为什么用 use_responses_api=True？】
#   OpenAI 有两套 API：
#   - Chat Completions API（旧版）：传统的聊天接口
#   - Responses API（新版）：新一代接口，提供更一致的行为
#   本模块对原生 OpenAI 使用 Responses API（use_responses_api=True），
#   对第三方兼容服务（xAI、Ollama、OpenRouter）使用 Chat Completions API。
#
# 【什么是环境变量（Environment Variable）？】
#   环境变量是操作系统级别的配置参数。
#   API 密钥通常存在环境变量中，而不是写死在代码里（更安全）。
#   os.environ.get("OPENAI_API_KEY") 从系统环境变量中读取名为 OPENAI_API_KEY 的值。
#   【为什么用环境变量？】
#   - 安全：密钥不写在代码里，不会被提交到 Git 仓库泄露
#   - 灵活：不同环境（开发/测试/生产）用不同密钥，只改环境变量不改代码
#
# 【什么是 super()？】
#   super() 用于调用父类的方法。
#   在 OpenAIClient.__init__ 中，super().__init__(model, base_url, **kwargs)
#   调用 BaseLLMClient.__init__，把 model、base_url、kwargs 存到 self 属性中。
#   这样公共的初始化逻辑不用在子类里重复写。
#
# 【什么是方法重写（Override）？】
#   子类重新定义父类中的方法。例如 OpenAIClient 重写了 get_llm() 和 validate_model()
#   ——父类中这两个方法只有 pass（空实现），子类提供了具体实现。
#   调用时，Python 自动调用子类的版本（多态）。
#
# 【什么是 ** 拆包？】
#   **dict 在函数调用时把字典拆包成关键字参数。
#   例如：llm_kwargs = {"model": "gpt-5", "api_key": "xxx"}
#         NormalizedChatOpenAI(**llm_kwargs)
#         等价于 NormalizedChatOpenAI(model="gpt-5", api_key="xxx")
#
# 【什么是子类化和方法重写（NormalizedChatOpenAI）？】
#   class NormalizedChatOpenAI(ChatOpenAI) 创建了 ChatOpenAI 的子类。
#   子类继承了父类的所有功能，但可以重写（override）某些方法。
#   这里重写了 invoke() 方法，在调用后添加内容规范化逻辑。
#   这是一种"装饰器模式"的实现——在不修改原类的情况下增强功能。
# ============================================================================
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：系统使用 OpenAI 分析苹果股票
#
#   第 1 步：创建客户端（通过工厂函数）
#     client = create_llm_client(provider="openai", model="gpt-5-mini", api_key="sk-...")
#     → 工厂创建 OpenAIClient(model="gpt-5-mini", base_url=None, provider="openai")
#     → super().__init__ 存储属性：self.model, self.base_url, self.kwargs
#
#   第 2 步：获取 LLM 实例
#     llm = client.get_llm()
#     → 构建 llm_kwargs = {"model": "gpt-5-mini"}
#     → provider == "openai" 不在 _PROVIDER_CONFIG 中（原生 OpenAI）
#     → 设置 use_responses_api = True
#     → 转发 api_key 等参数
#     → 返回 NormalizedChatOpenAI(model="gpt-5-mini", use_responses_api=True, ...)
#
#   第 3 步：调用 LLM
#     response = llm.invoke("分析苹果股票的基本面")
#     → NormalizedChatOpenAI.invoke 被调用
#     → super().invoke(...) 调用 ChatOpenAI.invoke（发送 API 请求）
#     → OpenAI 返回：content = [{"type": "reasoning", ...}, {"type": "text", "text": "分析结果..."}]
#     → normalize_content(response) 提取文本
#     → response.content 变成 "分析结果..."（纯字符串）
#     → 返回规范化后的 response
#
#   第 4 步：上游使用结果
#     智能体拿到 response.content = "分析结果..."，直接使用，不需要处理结构化格式
# ============================================================================

import os  # 操作系统接口，用于读取环境变量（API 密钥等）
# 【什么是 os 模块？】Python 内置模块，提供与操作系统交互的功能。
# os.environ.get("KEY") 从环境变量中读取名为 KEY 的值
from typing import Any, Optional

# LangChain 的 OpenAI 聊天模型封装
# ChatOpenAI 是 LangChain 提供的类，封装了与 OpenAI API 的通信
# 【什么是 langchain_openai？】LangChain 的 OpenAI 集成包
# 需要安装：pip install langchain-openai
from langchain_openai import ChatOpenAI

# 从 base_client 模块导入基类和规范化函数
# BaseLLMClient: 抽象基类，OpenAIClient 继承自它（适配器模式）
# normalize_content: 将 LLM 响应内容规范化为纯字符串的函数
from .base_client import BaseLLMClient, normalize_content

# 从 validators 模块导入模型验证函数
# validate_model: 验证模型名称对特定提供商是否有效
from .validators import validate_model


class NormalizedChatOpenAI(ChatOpenAI):
    """带内容规范化的 ChatOpenAI 子类。

    OpenAI 的 Responses API 返回的内容是结构化块列表格式：
    [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]

    这个子类重写了 invoke() 方法，在返回结果前自动将内容规范化为纯字符串，
    确保下游智能体获得一致的数据格式。

    【为什么要创建子类？】
    我们想在 ChatOpenAI 的 invoke() 返回结果后自动做内容规范化。
    但 ChatOpenAI 是第三方库的类，不能直接修改它的源代码。
    创建子类并重写 invoke() 方法，是"增强"已有类的标准做法。
    这也是"装饰器模式"的一种实现——在不修改原类的情况下添加新功能。
    """

    def invoke(self, input, config=None, **kwargs):
        """调用 LLM 并规范化返回内容。

        执行流程：
        1. super().invoke() 调用父类（ChatOpenAI）的 invoke 方法，发送请求
        2. normalize_content() 将返回的结构化内容转为纯字符串
        3. 返回规范化后的结果

        【什么是 super().invoke()？】
        super() 获取父类（ChatOpenAI），super().invoke() 调用父类的 invoke 方法。
        这样新方法 = 父类原有逻辑 + 我们添加的规范化逻辑。
        如果不调用 super().invoke()，就不会发送 API 请求，功能就坏了。

        Args:
            input: 发送给 LLM 的消息（可以是字符串或消息列表）
            config: LangChain 运行配置（可选）
            **kwargs: 额外参数，如 stop、temperature 等

        Returns:
            规范化后的 AIMessage 对象，content 为纯字符串
        """
        # super().invoke(input, config, **kwargs) 调用父类 ChatOpenAI 的 invoke 方法
        # 这会发送 HTTP 请求到 OpenAI API，获取 LLM 的响应
        # normalize_content(...) 对返回的响应做内容规范化
        # 两步合在一起：先获取响应，再规范化，最后返回
        return normalize_content(super().invoke(input, config, **kwargs))


# 需要从用户配置转发给 ChatOpenAI 的参数名列表
# 这些参数会从 self.kwargs 中提取并传递给 ChatOpenAI 构造函数
# 【什么是元组（tuple）？】用圆括号定义的不可变序列，如 ("a", "b", "c")
# 这里用元组而不是列表，因为这些参数名是固定的，不需要修改
_PASSTHROUGH_KWARGS = (
    "timeout",             # 请求超时时间（秒）—— 超过这个时间还没响应就报错
    "max_retries",         # 最大重试次数 —— 请求失败后自动重试的次数
    "reasoning_effort",    # OpenAI 推理努力程度（low/medium/high）—— 控制思考深度
    "api_key",             # API 密钥 —— 认证用的密钥
    "callbacks",           # LangChain 回调（用于日志、监控等）—— 事件钩子
    "http_client",         # 自定义 HTTP 客户端（用于 SSL 配置）—— 代理/证书
    "http_async_client",   # 自定义异步 HTTP 客户端 —— 异步请求用
)

# 各提供商的 API 地址和 API 密钥环境变量名
# 每个元组包含：(API 基础地址, 环境变量名)
# 【什么是字典？】{键: 值} 的集合
# 这里键是提供商名称，值是元组 (base_url, env_var_name)
# 如果 env_var_name 为 None，表示该提供商不需要 API 密钥（如本地 Ollama）
_PROVIDER_CONFIG = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),           # xAI Grok
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),  # OpenRouter
    "ollama": ("http://localhost:11434/v1", None),            # 本地 Ollama（无需密钥）
}


class OpenAIClient(BaseLLMClient):
    """OpenAI 及兼容服务的 LLM 客户端。

    支持多种提供商：
    - OpenAI 原生：使用 Responses API（/v1/responses），
      支持所有模型系列（GPT-4.1、GPT-5）的 reasoning_effort 和函数工具
    - 第三方兼容：xAI、OpenRouter、Ollama 使用标准的 Chat Completions API

    【适配器模式说明】
    本类是适配器：把 LangChain 的 ChatOpenAI 适配成 BaseLLMClient 接口。
    上层代码通过 get_llm() 获取 LLM 实例，不需要知道底层用的是 ChatOpenAI。
    """

    def __init__(
        self,
        # model: 模型名称，如 "gpt-5-mini"、"gpt-4.1"
        model: str,
        # base_url: 自定义 API 地址（可选）
        # 如果为 None，根据 provider 自动选择默认地址
        base_url: Optional[str] = None,
        # provider: 提供商标识，决定使用哪个 API 地址和认证方式
        # 取值："openai"（默认）、"ollama"、"openrouter"、"xai"
        provider: str = "openai",
        # **kwargs: 额外参数，会存到 self.kwargs 中
        # 包括 timeout、api_key、callbacks 等（见 _PASSTHROUGH_KWARGS）
        **kwargs,
    ):
        """初始化 OpenAI 客户端。

        Args:
            model: 模型名称
            base_url: 自定义 API 地址（可选）
            provider: 提供商标识（"openai"、"ollama"、"openrouter"、"xai"）
            **kwargs: 额外参数
        """
        # 调用父类（BaseLLMClient）的 __init__ 方法
        # super() 获取父类，super().__init__(...) 调用父类的初始化
        # 这会把 model、base_url、kwargs 存到 self.model、self.base_url、self.kwargs
        # 【为什么调用 super().__init__？】公共初始化逻辑在父类中，
        # 子类不用重复写，只需要调用一下就行
        super().__init__(model, base_url, **kwargs)

        # 转为小写，确保不区分大小写
        # .lower() 把字符串转成全小写，如 "OpenAI" → "openai"
        # 存为实例属性，后续 get_llm() 中根据 self.provider 做不同处理
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """返回配置好的 ChatOpenAI 实例。

        这个方法会根据提供商类型自动配置：
        - API 地址（base_url）
        - 认证方式（API 密钥）
        - 是否使用 Responses API

        【这是对抽象方法 get_llm() 的具体实现】
        父类 BaseLLMClient 中 get_llm() 只有 pass（空实现），
        这里提供了真正的逻辑。

        Returns:
            配置好的 NormalizedChatOpenAI 实例
        """
        # 初始化参数字典，至少包含模型名称
        # llm_kwargs 是要传给 NormalizedChatOpenAI 构造函数的参数
        # {"model": "gpt-5-mini"} 是最基础的参数
        llm_kwargs = {"model": self.model}

        # ---- 处理不同提供商的 API 地址和认证 ----
        # 检查 self.provider 是否在 _PROVIDER_CONFIG 字典中
        # _PROVIDER_CONFIG 包含 xai、openrouter、ollama 的配置
        # 如果是这三个之一，说明是第三方兼容服务
        if self.provider in _PROVIDER_CONFIG:
            # 从 _PROVIDER_CONFIG 获取该提供商的 API 地址和密钥环境变量名
            # 【什么是元组拆包？】base_url, api_key_env = ("url", "ENV_VAR")
            # 把元组的两个元素分别赋值给两个变量
            base_url, api_key_env = _PROVIDER_CONFIG[self.provider]

            # 设置 API 基础地址
            # 这会告诉 ChatOpenAI 把请求发到哪个服务器
            llm_kwargs["base_url"] = base_url

            # 检查是否需要 API 密钥
            if api_key_env:
                # 从环境变量中读取 API 密钥
                # os.environ.get(api_key_env) 从系统环境变量中获取值
                # 例如 api_key_env="XAI_API_KEY"，则读取环境变量 XAI_API_KEY 的值
                api_key = os.environ.get(api_key_env)
                if api_key:
                    # 如果读到了密钥，设置到参数中
                    llm_kwargs["api_key"] = api_key
            else:
                # Ollama 不需要真实密钥，传一个占位字符串即可
                # 本地服务不需要认证，但 ChatOpenAI 要求必须有 api_key 参数
                # 所以传一个任意字符串 "ollama" 作为占位
                llm_kwargs["api_key"] = "ollama"

        # 如果不是第三方提供商，检查是否有用户自定义的 base_url
        # 这用于用户自己部署的 OpenAI 兼容服务
        elif self.base_url:
            # 如果用户提供了自定义 base_url，直接使用
            llm_kwargs["base_url"] = self.base_url

        # ---- 转发用户提供的额外参数 ----
        # 遍历 _PASSTHROUGH_KWARGS 列表，检查 self.kwargs 中是否有对应参数
        # 【什么是 for...in...？】遍历序列中的每个元素
        # self.kwargs 是父类 __init__ 中存储的额外参数字典
        for key in _PASSTHROUGH_KWARGS:
            # 检查 self.kwargs 字典中是否有这个参数
            # `in` 运算符检查键是否在字典中
            if key in self.kwargs:
                # 如果有，转发到 llm_kwargs 中
                # 例如 self.kwargs = {"timeout": 30, "api_key": "sk-xxx"}
                # → llm_kwargs["timeout"] = 30, llm_kwargs["api_key"] = "sk-xxx"
                llm_kwargs[key] = self.kwargs[key]

        # ---- 原生 OpenAI：使用 Responses API ----
        # Responses API 是 OpenAI 的新一代 API，提供了更一致的行为
        # 第三方提供商（xAI、Ollama、OpenRouter）使用 Chat Completions API
        # 只有 provider == "openai" 时才启用 Responses API
        if self.provider == "openai":
            # 设置使用 Responses API
            # 这会让 ChatOpenAI 调用 OpenAI 的新版 API（/v1/responses），
            # 而不是旧版 Chat Completions API（/v1/chat/completions）
            llm_kwargs["use_responses_api"] = True

        # 使用 ** 解包字典，将键值对作为关键字参数传入
        # 【什么是 ** 拆包？】**dict 把字典拆成关键字参数
        # 例如 llm_kwargs = {"model": "gpt-5", "api_key": "sk-xxx"}
        #   NormalizedChatOpenAI(**llm_kwargs)
        #   等价于 NormalizedChatOpenAI(model="gpt-5", api_key="sk-xxx")
        # 返回创建好的 NormalizedChatOpenAI 实例（带内容规范化的 ChatOpenAI）
        return NormalizedChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """验证模型名称对该提供商是否有效。

        【这是对抽象方法 validate_model() 的具体实现】

        Returns:
            True 表示模型有效，False 表示无效
        """
        # 调用 validators 模块的 validate_model 函数
        # 传入提供商名称和模型名称，返回 True/False
        # 例如 validate_model("openai", "gpt-5-mini") → True
        #         validate_model("openai", "nonexistent-model") → False
        return validate_model(self.provider, self.model)
