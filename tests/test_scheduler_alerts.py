"""定时任务中的自选股策略预警测试。"""

import os
import unittest
from unittest.mock import patch

from tradingagents import scheduler


class SchedulerAlertTests(unittest.TestCase):
    def test_alerts_disabled_by_default(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "tradingagents.services.strategy_alerts.send_strategy_alerts"
            ) as mocked,
        ):
            scheduler._run_alerts_once()
        mocked.assert_not_called()

    def test_alerts_skip_non_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_ALERTS": "1"}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=False),
            patch(
                "tradingagents.services.strategy_alerts.send_strategy_alerts"
            ) as mocked,
        ):
            scheduler._run_alerts_once()
        mocked.assert_not_called()

    def test_alerts_run_on_trading_day(self):
        with (
            patch.dict(os.environ, {"SCHEDULE_ALERTS": "1"}, clear=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch(
                "tradingagents.services.strategy_alerts.send_strategy_alerts",
                return_value={
                    "message": "发现 1 只自选股触发策略信号。",
                    "notify": {"wecom": {"ok": True}},
                },
            ) as mocked,
        ):
            scheduler._run_alerts_once()
        mocked.assert_called_once_with(only_triggered=True)


if __name__ == "__main__":
    unittest.main()
