"""定时任务中的市场复盘推送测试。"""

import os
import unittest
from unittest.mock import patch

from tradingagents import scheduler


class SchedulerReviewTests(unittest.TestCase):
    def test_review_disabled_by_default(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.services.market_review.send_market_review"
            ) as mocked,
        ):
            scheduler._run_review_once()
        mocked.assert_not_called()

    def test_review_skip_non_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_REVIEW": "1"}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=False),
            patch(
                "tradingagents.services.market_review.send_market_review"
            ) as mocked,
        ):
            scheduler._run_review_once()
        mocked.assert_not_called()

    def test_review_run_on_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_REVIEW": "1"}, clear=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch(
                "tradingagents.services.market_review.send_market_review",
                return_value={
                    "as_of": "2026-11-11",
                    "notify": {"wecom": {"ok": True}},
                },
            ) as mocked,
        ):
            scheduler._run_review_once()
        mocked.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
