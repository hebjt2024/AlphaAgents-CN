# ============================================================================
# LLM 客户端工厂函数
# 根据提供商名称创建对应的 LLM 客户端实例
# ============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是 LLM？】
#   LLM（Large Language Model，大语言模型）是像 GPT、Claude、Gemini 这样的 AI 模型。
#   它们能理解自然语言并生成回答。TradingAgents 系统使用 LLM 来"思考"和"决策"——
#   分析师智能体读数据、写报告、做判断，都依赖 LLM。
#
# 【什么是 LLM 客户端？为什么需要它？】
#   要使用 LLM，需要通过网络 API 向 LLM 提供商（如 OpenAI）发送请求。
#   "客户端"就是负责与 API 通信的代码封装。
#
#   不同的 LLM 提供商有不同的 API 格式：
#   - OpenAI（GPT 系列）：使用 OpenAI 的 API 格式
#   - Anthropic（Claude）：使用 Anthropic 的 API 格式
#   - Google（Gemini）：使用 Google 的 API 格式
#
#   如果上层代码直接调用某个提供商的 API，换提供商就得改大量代码。
#   LLM 客户端的作用就是"封装差异"——上层代码用统一的接口，
#   客户端内部处理不同提供商的 API 差异。
#
# 【什么是工厂模式（Factory Pattern）？】
#   工厂模式是一种设计模式：用一个"工厂函数"来创建对象，而不是直接 new/实例化。
#
#   【通俗比喻】想象你去买电脑：
#   - 没有工厂模式：你需要自己去找 CPU 厂家、内存厂家、硬盘厂家，自己组装
#   - 有工厂模式：你只要告诉电脑工厂"我要一台 OpenAI 型号的电脑"，
#     工厂内部知道怎么组装，给你一台成品
#
#   在本文件中：
#   - create_llm_client 就是"工厂函数"
#   - 你告诉它 provider="openai" 和 model="gpt-5-mini"
#   - 它内部决定创建哪种客户端对象（OpenAIClient、AnthropicClient 等）
#   - 返回给你一个配置好的客户端实例
#
#   工厂模式的好处：
#   1. 调用方不需要知道具体有哪些客户端类、怎么实例化
#   2. 集中管理创建逻辑，添加新提供商时只改工厂函数
#   3. 返回统一类型（BaseLLMClient），上层代码可以用统一方式操作
#
# 【什么是 **kwargs？】
#   **kwargs 是 Python 的可变关键字参数语法。
#   在函数定义中，**kwargs 收集所有"参数名=值"形式的参数，打包成字典。
#   在函数调用中，**dict 把字典拆包成关键字参数。
#   这里用 **kwargs 是为了灵活地传递各种额外参数（如 API Key、超时时间等），
#   不需要把每个参数都显式声明。
#
# 【什么是 Optional？】
#   Optional[str] 表示这个参数可以是 str 类型或 None。
#   base_url: Optional[str] = None 表示 base_url 可以不传（默认 None）。
#
# 【什么是继承和多态？】
#   OpenAIClient、AnthropicClient、GoogleClient 都继承自 BaseLLMClient。
#   这意味着它们都有 get_llm() 和 validate_model() 方法（多态）。
#   工厂函数返回 BaseLLMClient 类型，但实际对象是子类实例。
#   上层代码不需要知道具体是哪个子类，直接调用 get_llm() 就行。
# ============================================================================
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：用户想用 OpenAI 的 GPT-5-mini 模型分析股票
#
#   第 1 步：调用工厂函数
#     client = create_llm_client(provider="openai", model="gpt-5-mini")
#
#   第 2 步：工厂内部决策
#     provider.lower() → "openai"
#     匹配 if provider_lower in ("openai", "ollama", "openrouter"):
#     → 创建 OpenAIClient(model="gpt-5-mini", base_url=None, provider="openai")
#
#   第 3 步：返回客户端实例
#     client 是一个 OpenAIClient 对象，但它"看起来像" BaseLLMClient
#     （因为继承了基类）
#
#   第 4 步：上层使用客户端
#     llm = client.get_llm()  # 返回 ChatOpenAI 实例
#     result = llm.invoke("分析苹果股票")  # 调用 LLM
# ============================================================================

# Optional: 类型注解，表示参数可以是某种类型或 None
# 例如 Optional[str] 表示"字符串或 None"

# AnthropicClient: Anthropic（Claude）的客户端
from .anthropic_client import AnthropicClient
from .auth import ensure_provider_credentials

# 从 base_client 模块导入基类 BaseLLMClient
# 所有客户端都继承自这个基类，工厂返回的类型也是 BaseLLMClient
# 【什么是相对导入？】from .xxx 中的点表示"当前包目录下"
from .base_client import BaseLLMClient

# GoogleClient: Google（Gemini）的客户端
from .google_client import GoogleClient

# 从各客户端模块导入具体实现
# OpenAIClient: OpenAI 及兼容服务（Ollama、OpenRouter、xAI）的客户端
from .openai_client import OpenAIClient


def create_llm_client(
    # provider: LLM 提供商名称，如 "openai"、"anthropic"、"google"
    provider: str,

    # model: 模型名称/标识符，如 "gpt-5-mini"、"claude-opus-4-5"
    model: str,

    # base_url: 可选的自定义 API 端点地址
    # 如果为 None，使用提供商的默认地址
    # 可以用它指定代理服务器或本地部署的模型服务
    base_url: str | None = None,

    # **kwargs: 额外的提供商特定参数
    # 这些参数会原封不动地传给具体的客户端类
    # 【为什么用 **kwargs？】因为不同提供商支持不同参数，
    # 用 **kwargs 可以灵活传递，不用在工厂函数里声明所有参数
    **kwargs,
) -> BaseLLMClient:
    """为指定的 LLM 提供商创建客户端实例（工厂函数）。

    这个函数是创建 LLM 客户端的统一入口。无论用户想用 OpenAI、Anthropic
    还是 Google，都通过这个函数来创建，不需要关心底层实现细节。

    支持的主要提供商：
    - openai：OpenAI 官方 API（GPT 系列）
    - ollama：本地运行的 Ollama 服务（免费、离线）
    - openrouter：OpenRouter 聚合 API（统一访问多个模型）
    - xai：xAI 的 Grok 模型
    - anthropic：Anthropic 的 Claude 模型
    - google：Google 的 Gemini 模型

    Args:
        provider: LLM 提供商名称（不区分大小写）
        model: 模型名称/标识符，如 "gpt-5-mini"
        base_url: 可选的自定义 API 端点地址
        **kwargs: 额外的提供商特定参数，例如：
            - http_client: 自定义 httpx.Client（用于 SSL 代理或证书配置）
            - http_async_client: 自定义 httpx.AsyncClient（用于异步操作）
            - timeout: 请求超时时间（秒）
            - max_retries: 最大重试次数
            - api_key: API 密钥
            - callbacks: LangChain 回调函数

    Returns:
        配置好的 BaseLLMClient 子类实例

    Raises:
        ValueError: 当提供商名称不被支持时抛出异常
    """
    # 将提供商名称转为小写，确保不区分大小写的匹配
    # 【什么是 .lower()？】字符串方法，把所有大写字母转成小写
    # 例如：用户输入 "OpenAI" 或 "openai" 都会被转成 "openai"
    # 这样后续比较时不用考虑大小写问题
    provider_lower = provider.lower()

    # 提前校验 API Key，缺失时给出明确的中文配置提示，
    # 而不是等到 SDK 调用时抛出 "Could not resolve authentication method"。
    ensure_provider_credentials(
        provider_lower, base_url, kwargs.get("api_key")
    )

    # ---- OpenAI 兼容的提供商 ----
    # OpenAI、Ollama 和 OpenRouter 都使用兼容 OpenAI 的 API 格式
    # 所以它们共用同一个 OpenAIClient 类
    # 【什么是 in 运算符？】检查元素是否在元组中
    # 例如 "openai" in ("openai", "ollama", "openrouter") 返回 True
    if provider_lower in ("openai", "ollama", "openrouter"):
        # 创建 OpenAIClient 实例
        # 传入 model、base_url，以及 provider 标识（让 OpenAIClient 知道是哪个提供商）
        # **kwargs 把额外的参数（如 api_key、timeout）透传给 OpenAIClient
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)

    # xAI 的 Grok 也使用 OpenAI 兼容的 API 格式
    # 单独判断是因为 xAI 需要特殊处理 API 地址
    if provider_lower == "xai":
        return OpenAIClient(model, base_url, provider="xai", **kwargs)

    # ---- Anthropic 客户端 ----
    # Anthropic 的 Claude 使用独立的 API 格式，需要专门的客户端
    if provider_lower == "anthropic":
        return AnthropicClient(model, base_url, **kwargs)

    # ---- Google 客户端 ----
    # Google 的 Gemini 使用独立的 API 格式，需要专门的客户端
    if provider_lower == "google":
        return GoogleClient(model, base_url, **kwargs)

    # 如果提供商名称不在以上任何列表中，抛出异常
    # 【什么是 raise？】主动抛出异常，通知调用者出了问题
    # ValueError 是 Python 内置异常，表示"参数值不合法"
    # f"..." 是 f-string，{provider} 会被替换为实际传入的值
    raise ValueError(f"Unsupported LLM provider: {provider}")
