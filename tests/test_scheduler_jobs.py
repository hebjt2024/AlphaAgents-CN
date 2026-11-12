"""定时分析任务执行测试。"""

import datetime
import os
import unittest
from unittest.mock import patch

from tradingagents import scheduler


class SchedulerJobTests(unittest.TestCase):
    def test_due_job_runs_and_marks_date(self):
        job = {
            "id": 1,
            "ticker": "600519",
            "schedule_time": "18:00",
        }
        with (
            patch.dict(os.environ, {"SCHEDULE_ENABLED": "1"}, clear=True),
            patch.object(scheduler, "is_cn_trading_day", return_value=True),
            patch.object(scheduler, "cn_today_str", return_value="2026-11-12"),
            patch.object(scheduler, "datetime") as dt,
            patch(
                "tradingagents.storage.due_scheduled_jobs",
                return_value=[job],
            ),
            patch(
                "tradingagents.storage.mark_scheduled_run",
            ) as marked,
            patch.object(
                scheduler,
                "run_analysis",
                return_value={"decision": "BUY", "report_id": 9},
            ) as run,
        ):
            dt.now.return_value = datetime.datetime(2026, 11, 12, 18, 30)
            scheduler._run_scheduled_jobs()

        run.assert_called_once_with(
            "600519",
            "2026-11-12",
            source="scheduled_job",
            notify=True,
        )
        marked.assert_called_once_with(1, "2026-11-12")


if __name__ == "__main__":
    unittest.main()
