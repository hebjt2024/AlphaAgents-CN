# ============================================================================
# LLM 客户端基类模块
# 定义了所有 LLM 客户端的抽象基类和通用的响应内容规范化函数
# ============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是抽象基类（Abstract Base Class, ABC）？】
#   抽象基类是一种"不能直接实例化，只能被继承"的类。
#   它定义了"接口规范"——规定子类必须实现哪些方法，但不实现具体逻辑。
#
#   【通俗比喻】想象一份"合同模板"：
#   - 合同模板规定了必须填写的条款（抽象方法）
#   - 但模板本身不是一份完整的合同（不能直接用）
#   - 你必须基于模板填写具体内容（子类实现），才能成为有效合同
#
#   在本模块中：
#   - BaseLLMClient 是抽象基类（合同模板）
#   - 它规定了所有 LLM 客户端必须有 get_llm() 和 validate_model() 方法
#   - 但 BaseLLMClient 自己不实现这两个方法（只写 pass）
#   - OpenAIClient、AnthropicClient 等子类必须实现这两个方法
#   - 如果子类没实现，Python 会在实例化时报错 TypeError
#
#   【为什么需要抽象基类？】
#   1. 统一接口：所有客户端都有相同的方法名，上层代码不需要区分用哪个
#   2. 强制规范：子类忘记实现方法时，Python 立即报错，不会等到运行时才发现
#   3. 多态：上层代码可以面向 BaseLLMClient 编程，不用关心具体子类
#
# 【什么是 @abstractmethod？】
#   @abstractmethod 是一个装饰器，标注在抽象基类的方法上。
#   它告诉 Python："这个方法必须在子类中实现，否则子类也不能被实例化。"
#   在本模块中，get_llm() 和 validate_model() 都标了 @abstractmethod。
#
# 【什么是模板方法模式（Template Method Pattern）？】
#   基类定义"做什么"（接口），子类实现"怎么做"（具体逻辑）。
#   这就是模板方法模式的核心思想。
#   BaseLLMClient 说"每个客户端都要能 get_llm"，但不说"怎么 get"——
#   OpenAIClient 用 ChatOpenAI 实现，AnthropicClient 用 ChatAnthropic 实现。
#
# 【什么是 isinstance()？】
#   isinstance(obj, 类型) 检查 obj 是否是某个类型的实例。
#   例如 isinstance("hello", str) 返回 True。
#   normalize_content 函数用 isinstance 检查 response.content 是否是 list。
#
# 【什么是列表推导式？】
#   [表达式 for 变量 in 可迭代对象] 是一种简洁创建列表的语法。
#   例如：[x*2 for x in [1,2,3]] → [2, 4, 6]
#   normalize_content 中用列表推导式提取文本块。
#
# 【什么是 .get() 方法？】
#   dict.get(key, default) 安全地从字典取值。
#   如果 key 不存在，返回 default 而不是报错。
#   例如 {"a": 1}.get("b", 0) → 0
#
# 【什么是 .join()？】
#   "分隔符".join(列表) 把列表中的字符串用分隔符连接成一个字符串。
#   例如 "\n".join(["a", "b", "c"]) → "a\nb\nc"
#
# 【什么是 super()？】
#   super() 用于调用父类的方法。
#   在子类的 __init__ 中，super().__init__(...) 调用父类的初始化方法。
#   这样公共的初始化逻辑写在父类里，子类不用重复写。
# ============================================================================
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：OpenAIClient 调用 LLM 后处理返回结果
#
#   第 1 步：调用 LLM
#     response = chat_openai.invoke("分析苹果股票")
#     → OpenAI 返回结构化内容：
#       content = [{"type": "reasoning", ...}, {"type": "text", "text": "苹果..."}]
#
#   第 2 步：规范化内容
#     normalize_content(response)
#     → 检测到 content 是 list
#     → 提取 type="text" 的块，得到 ["苹果..."]
#     → 用 "\n".join() 连接 → "苹果..."
#     → 写回 response.content = "苹果..."
#
#   第 3 步：返回规范化结果
#     → 下游智能体收到纯字符串，不需要处理结构化格式
# ============================================================================

# ABC：Abstract Base Class（抽象基类），用于定义接口规范
# 继承 ABC 的类可以定义抽象方法，子类必须实现
# abstractmethod：抽象方法装饰器，子类必须实现被装饰的方法
# 【什么是 from...import...？】从 abc 模块导入 ABC 和 abstractmethod
# abc 是 Python 内置模块，提供抽象基类功能
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager

# Any：任意类型，表示"什么类型都行"
#   用于不确定具体类型时的类型注解
# Optional：可选类型（可以是某种类型或 None）
#   例如 Optional[str] 表示"字符串或 None"
from typing import Any

# =============================================================================
# 可观测性（token / 成本 / 延迟）公共辅助函数
# =============================================================================
#
# 这些函数是"与供应商无关"的，供各供应商客户端复用：
#   - estimate_tokens()   ：没有 usage 元数据时的兜底 token 估算
#   - extract_token_usage()：从各种响应形态中提取 token 数
#   - observe_llm_call()  ：上下文管理器，测量延迟并记录调用
#
# 记录入口是 tradingagents/services/observability.py 的"当前 run"注册表。
# 为了避免循环 import（observability -> services -> graph -> llm_clients），
# 这里采用**惰性导入**：只有真正发生 LLM 调用时才 import。

# CJK 字符范围：汉字、扩展 A、日文假名、韩文、全角标点与全角字符
_CJK_RE = re.compile(
    "["
    "\u3000-\u303f"  # CJK 标点
    "\u3040-\u30ff"  # 平假名 / 片假名
    "\u3400-\u4dbf"  # 扩展 A
    "\u4e00-\u9fff"  # 基本汉字
    "\uf900-\ufaff"  # 兼容汉字
    "\uff00-\uffef"  # 全角字符
    "]"
)

#: 拉丁字符的"字符数 → token 数"换算比例（经验值）
_LATIN_CHARS_PER_TOKEN = 4

#: 缓存已导入的 observability 模块，避免每次调用都走 importlib
_OBSERVABILITY_MODULE: Any = None


def estimate_tokens(text: str) -> int:
    """粗略估算一段文本的 token 数。

    规则（经验值，仅用于供应商没有返回 usage 时的兜底）：

    * 中日韩字符：约 1 个字符 ≈ 1 个 token
    * 拉丁字符：约 4 个字符 ≈ 1 个 token

    估算结果会被标记为 ``tokens_estimated=True``，下游必须知道它不是权威值。

    Args:
        text: 待估算的文本

    Returns:
        估算的 token 数（不小于 0）
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    latin = len(text) - cjk
    return int(cjk + math.ceil(latin / _LATIN_CHARS_PER_TOKEN))


def flatten_text(value: Any) -> str:
    """把各种形态的输入/输出摊平成纯文本，用于 token 兜底估算。

    支持：字符串、消息列表、LangChain 消息对象、``{"content": ...}`` 字典、
    OpenAI 风格的 ``{"type": "text", "text": ...}`` 内容块。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(flatten_text(item) for item in value)
    if isinstance(value, dict):
        if "content" in value:
            return flatten_text(value["content"])
        if "text" in value:
            return flatten_text(value["text"])
        return ""
    content = getattr(value, "content", None)
    if content is not None:
        return flatten_text(content)
    return ""


def extract_token_usage(response: Any) -> tuple[int, int, bool]:
    """从 LLM 响应中提取 ``(prompt_tokens, completion_tokens, tokens_estimated)``。

    依次尝试以下来源（第一个能给出数字的即采用）：

    1. ``response.usage_metadata`` —— LangChain 标准字段
       （OpenAI / Anthropic / Google 都填这里）
    2. ``response.response_metadata["token_usage"]`` —— OpenAI 原始字段
    3. ``response.response_metadata["usage"]`` —— Anthropic 原始字段
    4. ``response.usage`` —— 供应商 SDK 的 Usage 对象

    全部取不到时返回 ``(0, 0, True)``，表示"需要走字符数兜底估算"。
    """
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        prompt = usage.get("input_tokens") or usage.get("prompt_tokens")
        completion = usage.get("output_tokens") or usage.get("completion_tokens")
        if prompt is not None or completion is not None:
            return int(prompt or 0), int(completion or 0), False

    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        raw = metadata.get("token_usage")
        if isinstance(raw, dict):
            prompt = raw.get("prompt_tokens") or raw.get("input_tokens")
            completion = raw.get("completion_tokens") or raw.get("output_tokens")
            if prompt is not None or completion is not None:
                return int(prompt or 0), int(completion or 0), False
        raw = metadata.get("usage")
        if isinstance(raw, dict):
            prompt = raw.get("prompt_tokens") or raw.get("input_tokens")
            completion = raw.get("completion_tokens") or raw.get("output_tokens")
            if prompt is not None or completion is not None:
                return int(prompt or 0), int(completion or 0), False

    raw = getattr(response, "usage", None)
    if raw is not None:
        prompt = getattr(raw, "prompt_tokens", None)
        if prompt is None:
            prompt = getattr(raw, "input_tokens", None)
        completion = getattr(raw, "completion_tokens", None)
        if completion is None:
            completion = getattr(raw, "output_tokens", None)
        if prompt is not None or completion is not None:
            return int(prompt or 0), int(completion or 0), False

    return 0, 0, True


def _observability_module() -> Any:
    """惰性获取 observability 模块（避免模块级循环 import）。

    正常路径是 ``importlib.import_module("tradingagents.services.observability")``。
    如果该包因可选依赖缺失而无法导入，则退化为"直接从文件加载"，
    保证可观测性在最小化环境里依然可用（模块名保持一致，状态唯一）。

    返回 ``None`` 表示该模块彻底不可用；此时所有记录都是 no-op。
    """
    global _OBSERVABILITY_MODULE
    if _OBSERVABILITY_MODULE is not None:
        return _OBSERVABILITY_MODULE
    module = None
    try:
        import importlib

        module = importlib.import_module("tradingagents.services.observability")
    except Exception:
        module = None
    if module is None:
        module = _load_observability_from_file()
    _OBSERVABILITY_MODULE = module
    return module


def _load_observability_from_file() -> Any:
    """兜底：绕过 ``tradingagents.services`` 包的 ``__init__`` 直接加载模块。

    仅在标准 import 失败时使用（例如 ``tradingagents.services`` 的其他子模块
    因为缺少可选依赖而无法导入）。这里必须保证：

    * 模块以规范名 ``tradingagents.services.observability`` 注册到
      ``sys.modules``，这样后续任何 ``from tradingagents.services.observability
      import X`` 拿到的都是**同一个模块实例**（ContextVar 注册表状态唯一）；
    * 父包 ``tradingagents.services`` 也被正确登记，属性链
      ``tradingagents.services.observability`` 可用。
    """
    try:
        import importlib.util
        import sys
        from pathlib import Path

        name = "tradingagents.services.observability"
        path = Path(__file__).resolve().parent.parent / "services" / "observability.py"
        if not path.exists():
            return None

        # 父包若已导入则复用，否则尽量导入
        parent = sys.modules.get("tradingagents.services")
        if parent is None:
            try:
                import importlib

                parent = importlib.import_module("tradingagents.services")
            except Exception:
                parent = None

        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # 先登记再执行，避免模块内部再次 import 自己时得到重复实例
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            return None
        if parent is not None:
            parent.observability = module
        return module
    except Exception:  # pragma: no cover - 兜底路径
        return None


def _record_call(
    *,
    provider: str,
    model: str,
    stage: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    ok: bool,
    error: str,
    tokens_estimated: bool,
) -> None:
    """把一次调用写入当前 run 的度量；任何异常都被吞掉（绝不影响业务）。"""
    module = _observability_module()
    if module is None:
        return
    try:
        module.record_llm_call(
            stage=stage,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            ok=ok,
            error=error,
            tokens_estimated=tokens_estimated,
        )
    except Exception:  # pragma: no cover - 度量失败绝不影响业务主流程
        pass


@contextmanager
def observe_llm_call(
    *,
    provider: str,
    model: str,
    prompt_input: Any = None,
    stage: str | None = None,
) -> Iterator[dict[str, Any]]:
    """包装一次 LLM 调用：测量延迟、提取 token、写入当前 run 的度量。

    用法（以 ``NormalizedChatOpenAI.invoke`` 为例）::

        with observe_llm_call(
            provider=self.observability_provider,
            model=self.observability_model,
            prompt_input=input,
        ) as obs:
            response = super().invoke(input, config, **kwargs)
            obs["response"] = response
        return normalize_content(response)

    行为约定：

    * 调用成功：记录模型、供应商、token 数、耗时、估算成本。
    * 调用失败：记录 ``ok=False`` 与错误信息，然后**原样抛出**原异常
      （异常语义保持不变）；记录动作本身绝不会抛出新异常。
    * 没有活动 run（未调用 ``set_current_metrics``）：完全 no-op。
    * 供应商没有返回 usage 时：用字符数兜底估算，并标记
      ``tokens_estimated=True``。
    """
    started = time.perf_counter()
    box: dict[str, Any] = {}
    try:
        yield box
    except Exception as exc:
        _record_call(
            provider=provider,
            model=model,
            stage=stage,
            prompt_tokens=estimate_tokens(flatten_text(prompt_input)),
            completion_tokens=0,
            latency_ms=(time.perf_counter() - started) * 1000,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            tokens_estimated=True,
        )
        raise
    else:
        response = box.get("response")
        prompt_tokens, completion_tokens, estimated = extract_token_usage(response)
        if estimated:
            prompt_tokens = estimate_tokens(flatten_text(prompt_input))
            completion_tokens = estimate_tokens(flatten_text(response))
        _record_call(
            provider=provider,
            model=model,
            stage=stage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
            ok=True,
            error="",
            tokens_estimated=estimated,
        )


def normalize_content(response):
    """将 LLM 响应内容规范化为纯字符串格式。

    不同 LLM 供应商返回的内容格式各不相同：
    - OpenAI 的 Responses API 返回格式：[{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]
    - Google Gemini 3 返回格式：同样是结构化块列表
    - 下游的智能体（Agent）期望 response.content 是纯字符串

    这个函数的作用是：
    1. 检查 response.content 是否是列表格式
    2. 提取所有 type='text' 的文本块
    3. 用换行符连接所有文本，丢弃推理/元数据块
    4. 将规范化后的字符串写回 response.content

    【为什么需要规范化？】
    不同 LLM 返回格式不同，但下游代码（如智能体）期望统一的格式。
    这个函数就像"翻译官"，把各种格式都翻译成统一的纯字符串。

    Args:
        response: LLM 的原始响应对象，包含 content 属性

    Returns:
        规范化后的响应对象，content 属性已被转换为纯字符串
    """
    # 获取响应的内容
    # response.content 可能是：
    #   - 纯字符串（某些简单情况下）→ 不需要处理
    #   - 结构化块列表（OpenAI Responses API 格式）→ 需要提取文本
    content = response.content

    # isinstance() 检查 content 是否是 list 类型
    # 如果是列表，说明是结构化块格式，需要提取文本
    # 例如 content = [{"type": "text", "text": "分析结果..."}, {"type": "reasoning", ...}]
    if isinstance(content, list):
        # 列表推导式：遍历每个元素，提取文本内容
        # 这是一种条件表达式（三元运算符）的列表推导式
        # 逐元素处理逻辑：
        #   - 如果是字典且 type 为 'text'，取出 'text' 字段的值
        #   - 如果已经是字符串，直接使用
        #   - 其他情况（如推理块），返回空字符串
        texts = [
            # 第一条件：如果是字典类型且 type 为 'text'
            # isinstance(item, dict) 检查 item 是否是字典
            # item.get("type") == "text" 检查 type 字段是否为 "text"
            # 满足则取出 item.get("text", "") 的值（即文本内容）
            item.get("text", "") if isinstance(item, dict) and item.get("type") == "text"
            # 第二条件：如果已经是字符串，直接使用
            # 例如 content = ["纯文本字符串", {"type": "text", "text": "..."}]
            # 中的 "纯文本字符串" 部分
            else item if isinstance(item, str)
            # 第三条件：其他情况（如推理块 type="reasoning"），返回空字符串
            # 这些块的内容不需要，用空字符串占位
            else ""
            # 遍历 content 列表中的每个元素
            for item in content
        ]
        # join() 用换行符连接所有非空文本，t 是每个文本片段
        # "\n".join(["a", "", "b"]) → "a\nb"（空字符串会被跳过吗？
        # 实际上 join 不会跳过空字符串，但 t for t in texts if t 会过滤空字符串）
        # 这里用了生成器表达式 (t for t in texts if t)：
        #   只保留非空字符串（if t 在空字符串时为 False）
        response.content = "\n".join(t for t in texts if t)

    # 返回处理后的 response 对象
    # 如果 content 原来就是字符串（不是 list），直接原样返回
    return response


class BaseLLMClient(ABC):
    """LLM 客户端的抽象基类。

    所有具体的 LLM 客户端（OpenAI、Anthropic、Google 等）都必须继承此类，
    并实现 get_llm() 和 validate_model() 两个抽象方法。

    这种设计模式称为"模板方法模式"：基类定义接口，子类实现具体逻辑。

    【什么是继承？】
    class OpenAIClient(BaseLLMClient) 表示 OpenAIClient 继承自 BaseLLMClient。
    OpenAIClient 自动拥有 BaseLLMClient 的所有属性和方法（如 __init__），
    同时必须实现 BaseLLMClient 中标记为 @abstractmethod 的方法。

    【为什么继承 ABC？】
    继承 ABC 后，如果子类没有实现所有 @abstractmethod 方法，
    Python 会在实例化时报 TypeError，而不是等到运行时才出问题。
    这是一种"早期错误发现"的机制。
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        """初始化 LLM 客户端。

        Args:
            model: 模型名称，如 "gpt-5-mini"、"claude-opus-4-5"
            base_url: API 基础 URL，如果为 None 则使用默认地址
            **kwargs: 额外的关键字参数（如 timeout、api_key 等），
                      会被存储在 self.kwargs 中供子类使用

        【什么是 **kwargs？】
        **kwargs 收集所有"参数名=值"形式的参数，打包成字典。
        例如调用 BaseLLMClient("gpt-5", timeout=30, api_key="xxx") 时，
        kwargs = {"timeout": 30, "api_key": "xxx"}
        """
        # 模型名称，存储为实例属性
        # self.model 可以在子类方法中通过 self.model 访问
        self.model = model          # 模型名称
        # API 地址，存储为实例属性
        # 如果为 None，子类会使用提供商的默认地址
        self.base_url = base_url    # API 地址
        # 额外参数字典，存储为实例属性
        # 子类可以从 self.kwargs 中取出需要的参数
        # 例如 OpenAIClient 会从中提取 timeout、api_key 等
        self.kwargs = kwargs        # 额外参数字典

    @abstractmethod
    def get_llm(self) -> Any:
        """返回配置好的 LLM 实例。

        这是一个抽象方法，子类必须实现。
        在 OpenAI 客户端中，返回 ChatOpenAI 实例；
        在 Anthropic 客户端中，返回 ChatAnthropic 实例。

        @abstractmethod 装饰器确保子类必须实现此方法，
        否则在实例化时会报错 TypeError。

        【什么是 @abstractmethod？】
        被这个装饰器标注的方法，子类必须实现（覆写）。
        如果子类没有实现，尝试创建子类实例时 Python 会报错：
          TypeError: Can't instantiate abstract class ... without
          an implementation for abstract method 'get_llm'
        这确保了接口的强制性。

        Returns:
            配置好的 LLM 实例（类型取决于具体的供应商）
        """
        # pass 是占位符，表示"什么都不做"
        # 抽象方法中 pass 表示"具体逻辑由子类实现"
        pass  # pass 是占位符，表示"什么都不做"

    @abstractmethod
    def validate_model(self) -> bool:
        """验证模型名称是否被当前客户端支持。

        Returns:
            True 表示模型有效，False 表示模型不被支持

        【为什么需要验证模型？】
        不同提供商支持不同的模型名称。例如 OpenAI 支持 "gpt-5-mini"，
        但传给 Anthropic 就无效。validate_model 在使用前检查模型名是否合法。
        """
        # pass 是占位符，具体验证逻辑由子类实现
        pass
