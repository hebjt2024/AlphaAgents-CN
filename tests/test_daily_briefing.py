"""每日盘后简报测试。"""

import unittest
from unittest.mock import patch

from tradingagents.services.daily_briefing import (
    archive_daily_briefing,
    build_daily_briefing,
    format_daily_briefing_markdown,
    send_daily_briefing,
)


def fake_review():
    return {
        "success": True,
        "as_of": "2026-11-11",
        "summary": {
            "tone": "偏强",
            "top_inflow": "半导体（+20.00 亿）",
            "top_outflow": "银行（-20.00 亿）",
        },
        "breadth": {"up": 3000, "down": 1500, "limit_up": 80, "limit_down": 5},
        "sectors": {
            "top": [{"name": "半导体", "change_pct": 3.2}],
            "concept": {"top": [{"name": "宽带提速", "change_pct": 5.3}]},
        },
        "warnings": [],
        "markdown": "## 市场复盘 2026-11-11\n\n- 市场偏强",
        "disclaimer": "仅供参考。",
    }


def fake_alerts():
    return {
        "success": True,
        "as_of": "2026-11-11",
        "items": [
            {
                "ticker": "600519",
                "name": "贵州茅台",
                "consensus": "偏多",
                "top_signals": [{"name": "放量突破"}],
            }
        ],
        "errors": [],
        "summary": {"total": 1, "triggered": 1, "signals": 2},
        "message": "发现 1 只自选股触发策略信号。",
        "warnings": [],
        "disclaimer": "仅供参考。",
    }


class DailyBriefingTests(unittest.TestCase):
    def test_build_briefing(self):
        with (
            patch(
                "tradingagents.services.market_review.build_market_review",
                return_value=fake_review(),
            ),
            patch(
                "tradingagents.services.strategy_alerts.build_strategy_alerts",
                return_value=fake_alerts(),
            ),
        ):
            briefing = build_daily_briefing()

        self.assertTrue(briefing["success"])
        self.assertEqual(briefing["as_of"], "2026-11-11")
        self.assertEqual(briefing["summary"]["tone"], "偏强")
        self.assertEqual(briefing["summary"]["alerts"], 1)
        self.assertEqual(briefing["summary"]["signals"], 2)
        self.assertIn("半导体", briefing["summary"]["top_sector"])
        self.assertIn("半导体", briefing["summary"]["top_inflow"])
        self.assertIn("每日盘后投研简报", briefing["markdown"])
        self.assertIn("资金净流入", briefing["markdown"])
        self.assertIn("市场复盘", briefing["markdown"])
        self.assertIn("自选股策略预警", briefing["markdown"])
        self.assertIn("明日关注", briefing["markdown"])
        self.assertIn("贵州茅台", briefing["markdown"])

    def test_format_without_alert_markdown(self):
        briefing = {
            "as_of": "2026-11-11",
            "summary": {"tone": "震荡", "alerts": 0, "signals": 0},
            "review": {"markdown": "## 市场复盘"},
            "alerts": {
                "items": [],
                "summary": {"total": 0, "triggered": 0, "signals": 0},
                "message": "未发现触发信号。",
                "disclaimer": "仅供参考。",
            },
            "warnings": [],
            "disclaimer": "仅供参考。",
        }
        text = format_daily_briefing_markdown(briefing)
        self.assertIn("暂无自选股触发信号", text)

    def test_send_briefing(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "markdown": "# 每日盘后投研简报",
        }
        with (
            patch(
                "tradingagents.services.daily_briefing.build_daily_briefing",
                return_value=fake,
            ),
            patch(
                "tradingagents.storage.save_briefing",
                return_value=7,
            ) as archived,
            patch(
                "tradingagents.notify.send_notification",
                return_value={"wecom": {"ok": True}},
            ) as mocked,
        ):
            result = send_daily_briefing()

        archived.assert_called_once()
        mocked.assert_called_once()
        self.assertEqual(result["briefing_id"], 7)
        self.assertEqual(result["notify"], {"wecom": {"ok": True}})

    def test_archive_briefing(self):
        fake = {
            "success": True,
            "as_of": "2026-11-11",
            "markdown": "# 每日盘后投研简报",
        }
        with (
            patch(
                "tradingagents.services.daily_briefing.build_daily_briefing",
                return_value=fake,
            ),
            patch(
                "tradingagents.storage.save_briefing",
                return_value=9,
            ) as archived,
        ):
            result = archive_daily_briefing()

        archived.assert_called_once_with(fake, source="webui")
        self.assertEqual(result["briefing_id"], 9)

    def test_archive_skips_when_unavailable(self):
        fake = {"success": False}
        with (
            patch(
                "tradingagents.services.daily_briefing.build_daily_briefing",
                return_value=fake,
            ),
            patch("tradingagents.storage.save_briefing") as archived,
        ):
            result = archive_daily_briefing()

        archived.assert_not_called()
        self.assertNotIn("briefing_id", result)

    def test_send_skips_when_unavailable(self):
        fake = {"success": False}
        with (
            patch(
                "tradingagents.services.daily_briefing.build_daily_briefing",
                return_value=fake,
            ),
            patch("tradingagents.notify.send_notification") as mocked,
        ):
            result = send_daily_briefing()

        mocked.assert_not_called()
        self.assertIn("skipped", result["notify"])


if __name__ == "__main__":
    unittest.main()
