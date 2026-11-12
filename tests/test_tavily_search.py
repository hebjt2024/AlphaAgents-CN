"""Tavily 联网搜索适配器测试（不访问网络）。"""

import os
import unittest
from unittest.mock import Mock, patch

from tradingagents.dataflows import tavily_search as module


class TavilySearchTests(unittest.TestCase):
    def test_missing_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                module.tavily_search("贵州茅台 新闻")
        self.assertIn("TAVILY_API_KEY", str(ctx.exception))

    def test_search_formats_results(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "answer": "测试摘要",
            "results": [
                {
                    "title": "测试标题",
                    "url": "https://example.com/news",
                    "content": "测试正文",
                    "published_date": "2026-01-02",
                }
            ],
        }
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}, clear=True),
            patch.object(module.requests, "post", return_value=response) as post,
        ):
            output = module.tavily_search("测试查询", max_results=3)

        self.assertIn("测试摘要", output)
        self.assertIn("测试标题", output)
        self.assertIn("https://example.com/news", output)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["query"], "测试查询")
        self.assertEqual(payload["api_key"], "test-key")

    def test_stock_news_query(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": []}
        with (
            patch.dict(
                os.environ, {"TAVILY_API_KEYS": "test-key"}, clear=True
            ),
            patch.object(module.requests, "post", return_value=response),
        ):
            output = module.search_stock_news("600519", curr_date="2026-01-02")
        self.assertIn("未检索到相关结果", output)


if __name__ == "__main__":
    unittest.main()
