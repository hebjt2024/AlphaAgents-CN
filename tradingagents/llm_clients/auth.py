"""LLM 提供商 API Key 解析与预检。

不同提供商使用不同的环境变量名；提前检查可以避免把 SDK 底层的
"Could not resolve authentication method" 这类晦涩错误直接抛给用户。
"""

from __future__ import annotations

import os

# 每个提供商可接受的环境变量（按优先级）
_PROVIDER_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEYS",
    ),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_KEYS"),
    "google": ("GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY", "GOOGLE_API_KEYS"),
    "xai": ("XAI_API_KEY", "XAI_API_KEYS"),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"),
}

# 本地服务不需要真实密钥
_NO_KEY_PROVIDERS = {"ollama"}


def _env_aliases(provider: str, base_url: str | None = None) -> list[str]:
    provider = (provider or "").lower()
    aliases = list(_PROVIDER_ENV_ALIASES.get(provider, ()))
    # MiniMax 的 Anthropic 兼容端点通常使用 MINIMAX_API_KEY
    if provider == "anthropic" and base_url and "minimax" in base_url.lower():
        aliases.extend(("MINIMAX_API_KEY", "MINIMAX_API_KEYS"))
    return aliases


def _first_key(value: str | None) -> str | None:
    if not value:
        return None
    for chunk in value.replace(";", ",").replace("\n", ",").split(","):
        key = chunk.strip()
        if key:
            return key
    return None


def _is_local_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    from urllib.parse import urlparse

    host = (urlparse(str(base_url)).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def resolve_api_key(
    provider: str,
    base_url: str | None = None,
    explicit: str | None = None,
) -> str | None:
    """解析提供商的 API Key；找不到时返回 None。

    如果配置了 ``*_API_KEYS`` 多 Key，返回第一个用于凭证校验；
    真正的轮询由 ``key_pool.resolve_api_keys`` 负责。
    """
    if explicit:
        return _first_key(explicit)
    for name in _env_aliases(provider, base_url):
        value = _first_key(os.getenv(name))
        if value:
            return value
    return None


def ensure_provider_credentials(
    provider: str,
    base_url: str | None = None,
    explicit: str | None = None,
) -> str | None:
    """校验提供商密钥，缺失时抛出带操作指引的 RuntimeError。

    返回解析到的密钥（本地/无需密钥的提供商返回 None）。
    """
    provider = (provider or "").lower()
    if provider in _NO_KEY_PROVIDERS or _is_local_url(base_url):
        return None
    if provider not in _PROVIDER_ENV_ALIASES:
        # 未知 provider 交由 factory 抛 "Unsupported LLM provider"
        return None

    key = resolve_api_key(provider, base_url, explicit)
    if key:
        return key

    aliases = _env_aliases(provider, base_url)
    hint = " 或 ".join(aliases)
    raise RuntimeError(
        f"未配置 {provider} 的 API Key。请在项目根目录的 .env 中设置 {hint}。"
        "（若使用 MiniMax 的 Anthropic 兼容端点，可设置 ANTHROPIC_API_KEY "
        "或 MINIMAX_API_KEY。）"
    )
