import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "webui" / "frontend"
INDEX = FRONTEND / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    match = re.search(r"\n(?:async\s+)?function\s+\w+\(", source[start + 1:])
    if not match:
        return source[start:]
    return source[start:start + 1 + match.start()]


def test_trust_summary_is_used_by_workbench_and_report_center():
    html = _read(INDEX)
    trust = _function_block(html, "trustSummaryHtml")
    assert "function trustSummaryHtml(" in html
    assert "trustSummaryHtml(" in _function_block(html, "renderResult")
    assert "trustSummaryHtml(" in _function_block(html, "openReport")
    assert "escapeHtml(" in trust
    for field in (
        "evidence_assessment",
        "decision_validation",
        "decision_diff",
        "model_decision",
        "run_metrics",
        "citations",
    ):
        assert field in trust


def test_report_sections_use_native_details_and_consistent_order():
    html = _read(INDEX)
    result = _function_block(html, "renderResult")
    report = _function_block(html, "openReport")
    assert "report-head" not in html
    assert "onclick=\"this.parentElement.classList.toggle" not in html
    details = _function_block(html, "reportDetailsHtml")
    assert "<details" in details and "<summary>" in details
    assert "reportDetailsHtml(" in _function_block(html, "finalDecisionSectionHtml")
    assert result.index("plainSummarySectionHtml(view)") < result.index("trustSummaryHtml(data)")
    assert result.index("trustSummaryHtml(data)") < result.index("decisionSectionsHtml(view)")
    assert result.index("decisionSectionsHtml(view)") < result.index("finalDecisionSectionHtml(view")
    assert result.index("finalDecisionSectionHtml(view") < result.index("analystSectionsHtml(view)")
    assert result.index("analystSectionsHtml(view)") < result.index("structuredDecisionSectionHtml(view)")
    assert report.index("plain_summary") < report.index("trustSummaryHtml(r)")
    assert report.index("trustSummaryHtml(r)") < report.index("final_trade_decision")


def test_active_job_persistence_and_result_recovery_exist():
    html = _read(INDEX)
    start = _function_block(html, "startAnalysis")
    polling = _function_block(html, "pollAnalysisJob")
    assert "alphaagents.activeJob" in html
    assert "localStorage.setItem('alphaagents.activeJob'" in html
    assert "localStorage.removeItem('alphaagents.activeJob'" in html
    assert "setActiveJob(result.job_id)" in start
    assert "clearActiveJob()" in start
    assert "'/api/jobs/'" in polling
    assert "'/result'" in polling
    for status in ("queued", "running", "completed", "failed", "cancelled"):
        assert status in polling
    assert "setInterval(poll, 2000)" in html
    assert "if (state.jobPollTimer) return" in html
    assert "state.jobPollInFlight" in html
    assert "'/cancel'" in _function_block(html, "stopAnalysis")
    assert "'/api/stop'" in _function_block(html, "stopAnalysis")
    assert "resumeActiveJob();" in html


def test_workbench_tabs_have_aria_and_keyboard_navigation():
    html = _read(INDEX)
    assert 'class="tabs" role="tablist"' in html
    assert html.count('role="tab"') == 3
    assert html.count('role="tabpanel"') == 3
    assert html.count('aria-selected="') == 3
    assert html.count('aria-controls="tab-') == 3
    switch = _function_block(html, "switchTab")
    assert "setAttribute('aria-selected'" in switch
    assert "tabIndex" in switch
    keyboard = _function_block(html, "handleTabKeydown")
    assert "ArrowLeft" in keyboard and "ArrowRight" in keyboard


def test_responsive_motion_and_export_coverage():
    html = _read(INDEX)
    table_rule = re.search(r"\.table-wrap\s*\{([^}]*)\}", html)
    assert table_rule and "overflow-x: auto" in table_rule.group(1)
    assert "@media (prefers-reduced-motion: reduce)" in html
    export = _function_block(html, "exportResultMarkdown")
    assert "trustSummaryMarkdown(d)" in export
    assert "人话结论" in export
    assert "决策卡" in export
    assert "持仓建议" in export
    assert "最终决策" in export
    assert "原始结构化块" in export


def test_websocket_job_id_filter_prevents_cross_job_updates():
    html = _read(INDEX)
    handler = _function_block(html, "handleMessage")
    assert "const jobId = msg.job_id || (msg.data && msg.data.job_id)" in handler
    assert "if (state.activeJob && jobId !== state.activeJob) return;" in handler
    assert "!state.activeJob || !jobId || jobId !== state.activeJob" in handler
    assert "resumeActiveJob()" in handler
    assert "setActiveJob(" not in handler
    start_case = handler.split("case 'analysis_start':", 1)[1].split("case 'node_change':", 1)[0]
    assert "setActiveJob(" not in start_case
    assert "clearActiveJob(jobId)" in handler
    assert "failAnalysis(" in handler and "jobId);" in handler


def test_auth_changes_clear_the_persisted_active_job():
    html = _read(INDEX)
    login = _function_block(html, "loginUser")
    logout = _function_block(html, "logoutUser")
    save_key = _function_block(html, "saveApiKey")
    clear_key = _function_block(html, "clearApiKey")
    assert login.index("clearActiveJob()") < login.index("window.Core.setApiKey(data.token)")
    assert logout.index("'/api/auth/logout'") < logout.index("clearActiveJob()")
    assert logout.index("clearActiveJob()") < logout.index("window.Core.setApiKey('')")
    assert "clearActiveJob()" in save_key
    assert "clearActiveJob()" in clear_key


def test_blocked_reports_suppress_directional_outputs():
    html = _read(INDEX)
    guard = _function_block(html, "directionalDecisionBlocked")
    decisions = _function_block(html, "decisionSectionsHtml")
    final = _function_block(html, "finalDecisionSectionHtml")
    analysts = _function_block(html, "analystSectionsHtml")
    export = _function_block(html, "exportResultMarkdown")
    result = _function_block(html, "renderResult")
    assert "evidence.level === 'blocked'" in guard
    assert "evidence.allow_directional === false" in guard
    assert decisions.index("directionalDecisionBlocked(view)") < decisions.index("decisionPlanHtml(plan)")
    assert "决策卡与持仓建议未展示" in decisions
    assert final.index("directionalDecisionBlocked(view)") < final.index("const original")
    assert "最终决策：HOLD" in final
    assert "blockedDecisionText(view)" in final
    assert "原始分析，已被门禁阻断" in analysts
    assert "const blocked = directionalDecisionBlocked(view)" in export
    assert "blocked ? 'HOLD'" in export
    assert "blockedDecisionText(view)" in export
    assert "原始分析，已被门禁阻断" in export
    assert "const decision = blocked ? 'HOLD'" in result


def test_restore_running_job_rebuilds_pipeline_and_legacy_payload():
    html = _read(INDEX)
    restore = _function_block(html, "restoreRunningJob")
    pipeline = _function_block(html, "renderPipelineFromConfig")
    polling = _function_block(html, "pollAnalysisJob")
    assert "asRecord(meta.pipeline)" in restore
    assert "asRecord(meta.payload)" in restore
    assert "asRecord(payload.request)" in restore
    assert "meta.pipeline_preset || request.pipeline_preset" in restore
    assert "pipeline.strategy_keys, meta.strategies, request.strategies" in restore
    assert "renderPipelineFromConfig(cfg)" in restore
    assert "renderStrategyChecks(strategyKeys)" in restore
    assert "state.activeStrategies = strategyKeys.slice()" in restore
    assert "updatePresetButtons()" in restore
    assert "state.restoredJobId = job.job_id || state.activeJob" in restore
    assert "restoreRunningJob(job, state.restoredJobId !== jobId)" in polling
    assert "$('maxDebateRounds').value = cfg.max_debate_rounds" in pipeline
    assert "$('maxRiskRounds').value = cfg.max_risk_discuss_rounds" in pipeline


def test_inline_script_parses_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("环境中没有 node，跳过 JS 语法校验")
    html = _read(INDEX)
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html)
    assert blocks
    result = subprocess.run(
        [node, "--check", "-"],
        input="\n".join(blocks),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
