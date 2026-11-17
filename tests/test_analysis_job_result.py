"""分析任务处理器必须把完整分析结果交回队列。

历史行为：处理器固定返回 ``{"success": true, "job_id": ...}``，
队列随后覆盖 ``jobs.result_json``，导致页面刷新后 ``/api/jobs/{id}/result``
取不到任何报告。
"""

from webui.backend import server


def test_analysis_job_handler_returns_complete_result(monkeypatch):
    complete = {
        "decision": "HOLD",
        "company": "600519",
        "date": "2026-11-17",
        "reports": {"market_report": "ok"},
    }
    monkeypatch.setattr(
        server, "run_analysis_sync", lambda *args, **kwargs: complete
    )
    result = server._analysis_job_handler(
        "job-1",
        {
            "request": {"company": "600519", "date": "2026-11-17"},
            "owner": "",
        },
        None,
    )
    assert result == complete
