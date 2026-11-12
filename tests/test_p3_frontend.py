"""P3 前端治理回归测试。

背景：前端此前是 3450 行的单文件（内联 2350 行 JS、约 50 个全局函数），
且存在若干明确缺陷：

- 所有 fetch 都不检查 ``resp.ok``，422 会渲染成空白提示；
- DOMPurify 允许 ``img`` + ``src``，报告内容里的外链图片会变成追踪信标；
- DOMPurify 停留在 3.0.11（存在已公开的 mXSS 公告）；
- 流式日志区没有 ``aria-live``，屏幕阅读器完全无法感知分析进展；
- ``--text-3`` 次要文字对比度约 4.0:1，低于 WCAG AA。

本文件把这些约束固化为可自动校验的断言。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "webui" / "frontend"
INDEX = FRONTEND / "index.html"
CORE = FRONTEND / "js" / "core.js"
VENDOR = FRONTEND / "vendor"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FrontendStructureTests(unittest.TestCase):
    def test_core_module_exists_and_is_loaded(self):
        self.assertTrue(CORE.exists(), "核心工具模块 js/core.js 缺失")
        html = _read(INDEX)
        self.assertIn('/static/js/core.js', html)

    def test_utility_functions_are_not_duplicated(self):
        """核心工具只能有一份实现，避免两套逻辑漂移。"""
        html = _read(INDEX)
        for name in ("function escapeHtml", "function renderMarkdown", "function showToast"):
            self.assertNotIn(
                name, html, f"{name} 仍内联在 index.html 中，与 core.js 重复"
            )
        core = _read(CORE)
        for name in ("function escapeHtml", "function renderMarkdown", "function showToast"):
            self.assertIn(name, core)

    def test_all_fetch_calls_use_api_fetch(self):
        html = _read(INDEX)
        self.assertNotRegex(html, r"\bawait fetch\(")
        self.assertNotIn("resp.json()", html)
        # 所有 API 请求都应经过统一客户端
        self.assertGreater(html.count("apiFetch("), 20)


class XssHardeningTests(unittest.TestCase):
    def test_img_and_src_are_not_allowed(self):
        core = _read(CORE)
        self.assertNotIn("'img'", core)
        self.assertNotIn('"img"', core)
        self.assertIn("FORBID_ATTR", core)
        self.assertIn("'src'", core)

    def test_links_get_noopener(self):
        core = _read(CORE)
        self.assertIn("noopener", core)
        self.assertIn("noreferrer", core)

    def test_dompurify_is_not_outdated(self):
        """3.0.x 存在已公开的 mXSS 公告，必须升级。"""
        purify = _read(VENDOR / "purify.min.js")
        match = re.search(r"DOMPurify (\d+\.\d+\.\d+)", purify)
        self.assertIsNotNone(match, "无法识别 DOMPurify 版本")
        version = tuple(int(x) for x in match.group(1).split("."))
        self.assertGreaterEqual(
            version, (3, 2, 4), f"DOMPurify {match.group(1)} 存在已知 mXSS 公告"
        )

    def test_marked_is_recent(self):
        marked = _read(VENDOR / "marked.min.js")
        match = re.search(r"marked v(\d+\.\d+\.\d+)", marked)
        self.assertIsNotNone(match, "无法识别 marked 版本")
        version = tuple(int(x) for x in match.group(1).split("."))
        self.assertGreaterEqual(version, (12, 0, 0))

    def test_vendor_readme_records_versions(self):
        readme = _read(VENDOR / "README.md")
        self.assertIn("purify.min.js", readme)
        self.assertIn("3.4", readme, "vendor 版本表未更新")


class AccessibilityTests(unittest.TestCase):
    def test_streaming_log_has_aria_live(self):
        html = _read(INDEX)
        self.assertRegex(html, r'id="logBody"[^>]*aria-live="polite"')

    def test_toasts_container_is_a_status_region(self):
        html = _read(INDEX)
        self.assertRegex(html, r'id="toasts"[^>]*role="status"')

    def test_modal_is_a_dialog(self):
        html = _read(INDEX)
        self.assertRegex(html, r'class="modal-box"[^>]*role="dialog"')
        self.assertIn('aria-modal="true"', html)

    def test_connection_state_is_not_conveyed_by_colour_alone(self):
        html = _read(INDEX)
        self.assertRegex(html, r'id="connDot"[^>]*aria-hidden="true"')
        self.assertRegex(html, r'id="connText"[^>]*aria-live="polite"')

    def test_muted_text_meets_wcag_aa(self):
        """次要文字相对**同主题**面板背景的对比度需达到 4.5:1。

        注意必须按主题配对：深色的 ``--text-3`` 拿去和浅色的 ``--panel``
        比是没有意义的（会得到一个虚假的低对比度结论）。
        """
        html = _read(INDEX)
        themes = re.findall(r"(:root|body\.light)\s*\{([^}]*)\}", html)
        self.assertTrue(themes, "未找到主题样式块")

        def luminance(hex_color: str) -> float:
            r, g, b = (
                int(hex_color[1:3], 16),
                int(hex_color[3:5], 16),
                int(hex_color[5:7], 16),
            )

            def channel(value: float) -> float:
                value /= 255
                return (
                    value / 12.92
                    if value <= 0.04045
                    else ((value + 0.055) / 1.055) ** 2.4
                )

            return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

        checked = 0
        for selector, block in themes:
            text_match = re.search(r"--text-3:\s*(#[0-9a-fA-F]{6})", block)
            panel_match = re.search(r"--panel:\s*(#[0-9a-fA-F]{6})", block)
            if not (text_match and panel_match):
                continue
            text_color = text_match.group(1)
            panel = panel_match.group(1)
            l1, l2 = luminance(text_color), luminance(panel)
            ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
            checked += 1
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"{selector} 的 {text_color} 相对 {panel} 对比度仅 {ratio:.2f}，"
                "低于 WCAG AA 要求的 4.5:1",
            )
        self.assertGreaterEqual(checked, 2, "未能解析出两套主题的配色")


class ApiKeySupportTests(unittest.TestCase):
    def test_frontend_can_send_api_key(self):
        core = _read(CORE)
        self.assertIn("X-API-Key", core)
        self.assertIn("localStorage", core)

    def test_websocket_carries_api_key_as_query(self):
        html = _read(INDEX)
        self.assertIn("api_key=", html)

    def test_settings_has_api_key_input(self):
        html = _read(INDEX)
        self.assertIn('id="apiKeyInput"', html)


class JavaScriptSyntaxTests(unittest.TestCase):
    """有 node 时做真实语法校验，没有则跳过。"""

    def test_inline_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("环境中没有 node，跳过 JS 语法校验")
        html = _read(INDEX)
        blocks = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html
        )
        self.assertTrue(blocks)
        code = "\n".join(blocks)
        result = subprocess.run(
            [node, "--check", "-"],
            input=code,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            result.returncode, 0, f"内联脚本语法错误：\n{result.stderr}"
        )

    def test_core_module_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("环境中没有 node，跳过 JS 语法校验")
        result = subprocess.run(
            [node, "--check", str(CORE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            result.returncode, 0, f"core.js 语法错误：\n{result.stderr}"
        )


if __name__ == "__main__":
    unittest.main()
