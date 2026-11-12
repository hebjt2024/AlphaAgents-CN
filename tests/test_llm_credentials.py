"""LLM API Key 预检测试（不访问网络）。"""

import os
import unittest
from unittest.mock import patch

from tradingagents.default_config import _env_int, _env_str
from tradingagents.llm_clients.auth import (
    ensure_provider_credentials,
    resolve_api_key,
)
from tradingagents.llm_clients.factory import create_llm_client


class ApiKeyResolutionTests(unittest.TestCase):
    def test_missing_anthropic_key_raises_clear_error(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolve_api_key("anthropic"))
            with self.assertRaises(RuntimeError) as ctx:
                ensure_provider_credentials("anthropic")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertIn(".env", str(ctx.exception))

    def test_anthropic_key_from_env(self):
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True
        ):
            self.assertEqual(resolve_api_key("anthropic"), "test-key")

    def test_minimax_alias(self):
        with patch.dict(
            os.environ, {"MINIMAX_API_KEY": "mini-key"}, clear=True
        ):
            key = resolve_api_key(
                "anthropic", "https://api.minimax.cn/anthropic"
            )
        self.assertEqual(key, "mini-key")

    def test_google_alias(self):
        with patch.dict(
            os.environ, {"GOOGLE_GENAI_API_KEY": "g-key"}, clear=True
        ):
            self.assertEqual(resolve_api_key("google"), "g-key")

    def test_ollama_needs_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ensure_provider_credentials("ollama"))

    def test_localhost_needs_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(
                ensure_provider_credentials(
                    "openai", "http://localhost:11434/v1"
                )
            )

    def test_unknown_provider_is_not_prechecked(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ensure_provider_credentials("unknown"))


class DefaultConfigEnvTests(unittest.TestCase):
    def test_env_str(self):
        with patch.dict(os.environ, {"TEST_CFG_STR": "  value  "}, clear=False):
            self.assertEqual(_env_str("TEST_CFG_STR", "fallback"), "value")
        with patch.dict(os.environ, {"TEST_CFG_STR": "   "}, clear=False):
            self.assertEqual(_env_str("TEST_CFG_STR", "fallback"), "fallback")

    def test_env_int(self):
        with patch.dict(os.environ, {"TEST_CFG_INT": "3"}, clear=False):
            self.assertEqual(_env_int("TEST_CFG_INT", 1), 3)
        with patch.dict(os.environ, {"TEST_CFG_INT": "abc"}, clear=False):
            self.assertEqual(_env_int("TEST_CFG_INT", 1), 1)


class FactoryPrecheckTests(unittest.TestCase):
    def test_factory_fails_before_creating_client(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                create_llm_client("anthropic", "claude-sonnet-4-6")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
