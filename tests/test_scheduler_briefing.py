"""定时任务中的每日简报测试。"""

import os
import unittest
from unittest.mock import patch

from tradingagents import scheduler


class SchedulerBriefingTests(unittest.TestCase):
    def test_briefing_disabled_by_default(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.services.daily_briefing.send_daily_briefing"
            ) as mocked,
        ):
            scheduler._run_briefing_once()
        mocked.assert_not_called()

    def test_briefing_skip_non_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_BRIEFING": "1"}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=False),
            patch(
                "tradingagents.services.daily_briefing.send_daily_briefing"
            ) as mocked,
        ):
            scheduler._run_briefing_once()
        mocked.assert_not_called()

    def test_briefing_run_on_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_BRIEFING": "1"}, clear=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch(
                "tradingagents.services.daily_briefing.send_daily_briefing",
                return_value={
                    "as_of": "2026-11-11",
                    "notify": {"wecom": {"ok": True}},
                },
            ) as mocked,
        ):
            scheduler._run_briefing_once()
        mocked.assert_called_once_with()

    def test_briefing_takes_priority_over_separate_pushes(self):
        with (
            patch.dict(
                os.environ,
                {"SCHEDULE_BRIEFING": "1", "SCHEDULE_STOCKS": ""},
                clear=True,
            ),
            patch.object(scheduler, "_run_briefing_once") as briefing,
            patch.object(scheduler, "_run_alerts_once") as alerts,
            patch.object(scheduler, "_run_review_once") as review,
        ):
            scheduler._run_once()

        briefing.assert_called_once_with()
        alerts.assert_not_called()
        review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
