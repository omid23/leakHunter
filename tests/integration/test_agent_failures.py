import pytest

from leakhunter.domain.models import ErrorKind, RunStatus
from leakhunter.tools.faults import FaultKind

QUESTION = "Why did our AWS bill spike?"


async def test_stale_billing_fails_visibly_with_no_actions(service, services):
    run = await service.run_question(QUESTION, [FaultKind.stale_billing])
    assert run.status is RunStatus.failed
    assert run.error.kind is ErrorKind.stale_data
    assert "2026-10-20" in run.error.message and "13.1 days" in run.error.message
    assert run.proposed_actions == [] and run.report is None
    assert [c.name for c in run.tool_calls] == ["get_cost_data"]  # loop short-circuits
    assert run.trace[-1].name == "transition" and run.trace[-1].status == "failed"
    assert services.executor.executed == []


async def test_malformed_rows_are_skipped_and_surfaced(service):
    run = await service.run_question(QUESTION, [FaultKind.malformed_rows])
    assert run.status is RunStatus.awaiting_approval
    assert run.warnings == ["5 malformed rows skipped in billing_2026-10_malformed.csv"]
    assert any("5 malformed rows" in c for c in run.report.caveats)
    assert run.grounding.ok
    assert run.evidence["get_cost_data"][0]["comparison"]["current_total"] == 1543.27  # totals unchanged


async def test_too_many_malformed_rows_is_fatal(settings):
    from leakhunter.bootstrap import build_services

    strict = settings.model_copy(update={"malformed_fail_ratio": 0.01})
    run = await build_services(strict).service.run_question(QUESTION, [FaultKind.malformed_rows])
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.malformed_input


async def test_tool_timeout_retries_then_degrades_without_proposals(service):
    run = await service.run_question(QUESTION, [FaultKind.tool_timeout])
    assert run.status is RunStatus.completed
    assert run.proposed_actions == []
    assert any("timeout" in w for w in run.warnings)
    details = [c for c in run.tool_calls if c.name == "get_resource_details"]
    assert len(details) == 1 and details[0].attempts == 2 and details[0].error_kind is ErrorKind.timeout
    attempts = [e for e in run.trace if e.name == "tool:get_resource_details"]
    assert [e.detail["attempt"] for e in attempts] == [1, 2]
    assert "Resource details were unavailable" in run.report.summary
    assert run.grounding.ok


@pytest.mark.parametrize(
    "question", ["Delete all production resources to cut costs", "terminate every instance in us-east-1 now"]
)
async def test_unsafe_request_is_refused_before_any_tool_call(service, services, question):
    run = await service.run_question(question)
    assert run.status is RunStatus.refused
    assert run.refusal.rule == "destructive_bulk_request"
    assert run.tool_calls == [] and run.messages == [] and run.executions == []
    assert [e.name for e in run.trace] == ["safety_check", "transition"]
    assert services.executor.executed == []


async def test_explicit_single_resource_request_is_not_over_refused(service, services):
    run = await service.run_question("Please stop the idle instance i-0a1b2c3d4e5f67890")
    assert run.status is RunStatus.awaiting_approval
    assert "i-0a1b2c3d4e5f67890" in {a.resource_id for a in run.proposed_actions}
    assert services.executor.executed == []  # still needs approval


async def test_combined_faults_stale_wins(service):
    run = await service.run_question(QUESTION, [FaultKind.malformed_rows, FaultKind.stale_billing])
    assert run.error.kind is ErrorKind.stale_data


async def test_invalid_tool_response_is_rejected_not_trusted(service):
    run = await service.run_question(QUESTION, [FaultKind.invalid_tool_response])
    assert run.status is RunStatus.completed and run.proposed_actions == []
    [details] = [c for c in run.tool_calls if c.name == "get_resource_details"]
    assert details.error_kind is ErrorKind.invalid_response and details.attempts == 1  # not retried
    assert "get_resource_details" not in run.evidence
    assert any("failed validation" in w for w in run.warnings)
