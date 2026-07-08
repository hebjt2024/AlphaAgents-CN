# ============================================================================
# LLM 客户端层 - 包初始化
# 对外暴露基类 BaseLLMClient 和工厂函数 create_llm_client
# ============================================================================

# ============================================================================
# 【前置知识】
# ============================================================================
#
# 本文件是 LLM 客户端层的包初始化文件（__init__.py）。
# 以下是需要了解的核心概念：
#
# 1. Python 包（Package）与 __init__.py：
#    - 在 Python 中，包含 __init__.py 文件的目录被称为"包"（Package）。
#    - __init__.py 在包被导入时自动执行，用于初始化包。
#    - 它控制包的"公开接口"：哪些名称可以通过 from 包名 import * 导入。
#    - 类比：__init__.py 就像商店的"橱窗"，只展示想对外展示的商品。
#
# 2. 相对导入（Relative Import）：
#    - 使用 . 前缀表示从当前包导入
#    - .base_client 表示当前包（llm_clients）下的 base_client 模块
#    - .factory 表示当前包下的 factory 模块
#    - 相对导入使代码更具可移植性，不依赖包的绝对路径
#
# 3. 工厂模式（Factory Pattern）：
#    - create_llm_client 是一个工厂函数：
#      根据提供商名称创建不同的 LLM 客户端实例
#    - 用户不需要知道具体有哪些客户端类，只需调用工厂函数
#    - 示例：
#        client = create_llm_client("anthropic", model="claude-sonnet-4-6")
#        # 内部自动创建 AnthropicClient 实例
#        llm = client.get_llm()  # 获取配置好的 LLM 实例
#
# 4. __all__ 列表（公开 API 控制）：
#    - 定义了 from tradingagents.llm_clients import * 时导出的名称
#    - 这是一种"白名单"机制，控制包的公开 API
#    - 只有在 __all__ 中的名称才会被 import * 导入
#    - 不在 __all__ 中的内容仍可通过完整路径导入（如 from ...anthropic_client import AnthropicClient）
#
# 5. 本文件的运行时数据流：
#    分析引擎需要创建 LLM 实例
#    → from tradingagents.llm_clients import create_llm_client
#    → __init__.py 执行：
#      → 导入 BaseLLMClient（基类，定义通用接口）
#      → 导入 create_llm_client（工厂函数，创建具体客户端）
#    → 调用 create_llm_client("openai", model="gpt-5-mini", ...)
#    → factory.py 内部根据 "openai" 创建 OpenaiClient 实例
#    → client.get_llm() 返回配置好的 LLM 实例
#    → 分析引擎使用 LLM 实例进行推理
# ============================================================================

# 从当前包导入基类：所有 LLM 客户端的抽象父类
# BaseLLMClient 定义了所有客户端的通用接口：
#   - __init__(model, base_url, **kwargs)：初始化客户端
#   - get_llm() → Any：返回配置好的 LLM 实例
#   - validate_model() → bool：验证模型名称是否有效
# .base_client 表示从当前包（llm_clients 目录）导入 base_client.py 模块
from .base_client import BaseLLMClient

# 从当前包导入工厂函数：根据提供商名称创建对应的 LLM 客户端
# create_llm_client(provider, model, base_url, **kwargs) → BaseLLMClient
# .factory 表示从当前包导入 factory.py 模块
from .factory import create_llm_client

# __all__ 定义了 "from tradingagents.llm_clients import *" 时导出的名称
# 这是一种"白名单"机制，控制包的公开 API
# 只有 BaseLLMClient 和 create_llm_client 会被 import * 导入
# 其他模块（如 anthropic_client、google_client）需要显式导入
# 这样设计的好处：
#   1. 简化用户导入：只需 from tradingagents.llm_clients import create_llm_client
#   2. 隐藏实现细节：用户不需要知道内部有哪些客户端类
#   3. 降低耦合：内部实现变化不影响使用 __all__ 导出的接口
__all__ = ["BaseLLMClient", "create_llm_client"]
