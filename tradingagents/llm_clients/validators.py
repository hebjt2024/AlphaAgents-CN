# ============================================================================
# 模型名称验证器模块
# 为每个 LLM 提供商维护一个受支持的模型白名单
# 只验证模型名称是否有效，不强制限制参数值
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件维护了每个 LLM 提供商支持的模型白名单，用于验证模型名称是否有效。
# 以下是需要了解的核心概念：
#
# 1. 模型验证白名单的作用：
#    - 白名单（Whitelist）是一种安全机制：只允许列表中明确列出的值。
#    - 在 LLM 客户端中，白名单用于验证用户配置的模型名称是否正确。
#    - 作用：
#      * 提前发现配置错误（如拼写错误的模型名）
#      * 提供更好的错误提示（告诉用户哪个模型可用）
#      * 防止向 API 发送无效请求（节省时间和 API 调用费用）
#    - 示例：用户输入 "gpt5-mini"（少了一个连字符）
#      → validate_model("openai", "gpt5-mini") 返回 False
#      → 程序可以提示用户正确的名称是 "gpt-5-mini"
#
# 2. LLM 提供商（Provider）：
#    - LLM 提供商是提供大语言模型 API 服务的公司/平台。
#    - 主要提供商：
#      * OpenAI：GPT 系列模型（GPT-5、GPT-4.1 等）
#      * Anthropic：Claude 系列模型（Opus、Sonnet、Haiku）
#      * Google：Gemini 系列模型（Pro、Flash）
#      * xAI：Grok 系列模型
#      * OpenRouter：聚合平台，可以访问多个提供商的模型
#      * Ollama：本地部署的 LLM 服务，可以运行开源模型
#    - 不同提供商的 API 接口和模型名称不同，因此需要分别维护白名单
#
# 3. 特殊规则：
#    - ollama 和 openrouter 允许任意模型名称：
#      * ollama：用户可以在本地运行任意开源模型，模型名称由用户决定
#      * openrouter：聚合平台，模型列表不断变化，无法穷举
#    - 不在 VALID_MODELS 白名单中的提供商也允许任意模型名称：
#      这避免了新提供商被误拒（向前兼容设计）
#
# 4. Python 概念：
#    - 字典（dict）：键值对集合，用 {} 定义
#    - 列表（list）：有序可变序列，用 [] 定义
#    - in 运算符：检查元素是否在列表/字典中
#      例如 "gpt-5-mini" in ["gpt-5-mini", "gpt-5-nano"] 返回 True
#    - .lower()：将字符串转为小写
#
# 5. 本文件的运行时数据流：
#    用户在 CLI 中选择提供商和模型
#    → cli/main.py 创建配置：config["llm_provider"] = "openai", config["deep_think_llm"] = "gpt-5.2"
#    → factory.py 创建对应的客户端：OpenaiClient(model="gpt-5.2", ...)
#    → 客户端调用 validate_model("openai", "gpt-5.2")
#    → 检查 "gpt-5.2" 是否在 VALID_MODELS["openai"] 列表中
#    → 在列表中 → 返回 True → 验证通过
#    → 如果不在列表中 → 返回 False → 可能提示用户选择有效模型
# ============================================================================


# 各提供商支持的模型白名单
# 这是一个字典：{提供商名称: [支持的模型名称列表]}
# 每个列表包含该提供商所有已知可用的模型名称
# 注意：此列表可能随各供应商发布新模型而更新
# 添加新模型时只需在对应列表中添加名称即可
VALID_MODELS = {
    # ---- OpenAI 模型 ----
    # OpenAI 提供 GPT 系列大语言模型
    # 模型命名规则：gpt-版本号[-变体]
    "openai": [
        # GPT-5 系列（最新一代）
        "gpt-5.4-pro",     # 专业版：最强推理能力，适合复杂分析，价格最高
        "gpt-5.4",         # 标准版：最新前沿模型，支持 1M 上下文
        "gpt-5.2",         # 精简版：强推理能力，性价比高
        "gpt-5.1",         # 更早版本
        "gpt-5",           # 基础版
        "gpt-5-mini",      # 轻量版：速度快、成本低，适合高频调用
        "gpt-5-nano",      # 最小版：极致速度，适合简单任务
        # GPT-4.1 系列（上一代）
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ],

    # ---- Anthropic Claude 模型 ----
    # Anthropic 提供 Claude 系列大语言模型
    # 模型命名规则：claude-版本-代号
    # 代号含义：opus（最强）、sonnet（平衡）、haiku（快速）
    "anthropic": [
        # Claude 4.6 系列（最新）
        "claude-opus-4-6",   # Opus：最强版本，顶级推理能力
        "claude-sonnet-4-6", # Sonnet：平衡版，性能与成本的折中
        # Claude 4.5 系列
        "claude-opus-4-5",   # Opus 4.5
        "claude-sonnet-4-5", # Sonnet 4.5：适合 Agent 和编程任务
        "claude-haiku-4-5",  # Haiku：轻量版，速度最快，适合近即时响应
    ],

    # ---- Google Gemini 模型 ----
    # Google 提供 Gemini 系列大语言模型
    # 模型命名规则：gemini-版本-变体[-preview]
    # 变体含义：pro（专业）、flash（快速）、flash-lite（轻量）
    "google": [
        # Gemini 3.1 系列（预览版）
        "gemini-3.1-pro-preview",        # Pro 预览版：推理优先，适合复杂工作流
        "gemini-3.1-flash-lite-preview", # Flash Lite 预览版：最具性价比
        # Gemini 3 系列（预览版）
        "gemini-3-flash-preview",        # Flash 预览版：下一代快速模型
        # Gemini 2.5 系列（稳定版）
        "gemini-2.5-pro",                # Pro 稳定版
        "gemini-2.5-flash",              # Flash 稳定版：平衡速度和稳定性
        "gemini-2.5-flash-lite",         # Flash Lite：快速低成本
    ],

    # ---- xAI Grok 模型 ----
    # xAI（Elon Musk 的 AI 公司）提供 Grok 系列大语言模型
    # 模型命名规则：grok-版本[-变体]
    # 变体含义：fast（快速）、reasoning（推理）、non-reasoning（非推理）
    "xai": [
        # Grok 4.1 系列
        "grok-4-1-fast-reasoning",      # 快速推理版：高性能，支持 2M 上下文
        "grok-4-1-fast-non-reasoning",  # 快速非推理版：速度优化，支持 2M 上下文
        # Grok 4 系列
        "grok-4-0709",                  # 旗舰模型
        "grok-4-fast-reasoning",        # 快速推理版
        "grok-4-fast-non-reasoning",    # 快速非推理版
    ],
}


# ============================================================================
# 函数：validate_model(provider, model)
# 功能：检查模型名称对指定提供商是否有效
#
# Args:
#   provider: LLM 提供商名称（不区分大小写）
#     例如 "openai"、"anthropic"、"google"、"xai"、"ollama"、"openrouter"
#   model: 要验证的模型名称
#     例如 "gpt-5-mini"、"claude-sonnet-4-6"、"gemini-2.5-pro"
#
# Returns:
#   bool: True 表示模型有效，False 表示无效
#
# 特殊规则：
#   - ollama 和 openrouter 允许任意模型名称（因为它们可以代理任意模型）
#   - 不在 VALID_MODELS 白名单中的提供商也允许任意模型名称（向前兼容）
#
# 运行时数据流示例：
#   场景1（OpenAI 有效模型）：
#     validate_model("openai", "gpt-5-mini")
#     → provider_lower = "openai"
#     → 不在 ("ollama", "openrouter") 中
#     → 在 VALID_MODELS 中
#     → "gpt-5-mini" in VALID_MODELS["openai"] → True
#     → 返回 True
#
#   场景2（OpenAI 无效模型）：
#     validate_model("openai", "gpt5-mini")  # 少了连字符
#     → provider_lower = "openai"
#     → 不在 ("ollama", "openrouter") 中
#     → 在 VALID_MODELS 中
#     → "gpt5-mini" in VALID_MODELS["openai"] → False（拼写错误）
#     → 返回 False
#
#   场景3（Ollama 任意模型）：
#     validate_model("ollama", "qwen3:latest")
#     → provider_lower = "ollama"
#     → 在 ("ollama", "openrouter") 中
#     → 直接返回 True（允许任意模型名称）
# =====================================================================
def validate_model(provider: str, model: str) -> bool:
    """检查模型名称对指定提供商是否有效。

    Args:
        provider: LLM 提供商名称（不区分大小写）
        model: 要验证的模型名称

    Returns:
        bool: True 表示模型有效，False 表示无效

    特殊规则：
    - ollama 和 openrouter 允许任意模型名称（因为它们可以代理任意模型）
    - 不在 VALID_MODELS 白名单中的提供商也允许任意模型名称
    """
    # 将提供商名称转为小写，确保查找不区分大小写
    # 例如 "OpenAI" → "openai"，"Google" → "google"
    provider_lower = provider.lower()

    # ollama 和 openrouter 是代理/聚合服务，可以运行任意模型
    # ollama：本地 LLM 运行环境，用户可以下载和运行任意开源模型
    # openrouter：聚合平台，模型列表不断变化，无法穷举
    # 因此对这两个提供商，直接返回 True（允许任意模型名称）
    if provider_lower in ("ollama", "openrouter"):
        return True

    # 如果提供商不在 VALID_MODELS 字典中，默认允许（避免误拒新提供商）
    # 这是一种"向前兼容"设计：当新增提供商但尚未更新白名单时，
    # 不会因为白名单缺失而阻止用户使用
    if provider_lower not in VALID_MODELS:
        return True

    # 使用 in 运算符检查模型名称是否在白名单列表中
    # VALID_MODELS[provider_lower] 是一个列表，如 ["gpt-5-mini", "gpt-5-nano", ...]
    # model in [...] 检查 model 是否是列表中的元素之一
    # 返回 True（模型有效）或 False（模型无效）
    return model in VALID_MODELS[provider_lower]
