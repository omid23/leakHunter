import pytest

from leakhunter.domain.errors import InvalidTransition, UnknownActionId
from leakhunter.domain.models import ActionType, RunStatus

IDLE = "i-0a1b2c3d4e5f67890"
VOL = "vol-0f1e2d3c4b5a69788"


async def test_spike_question_ends_awaiting_approval_with_grounded_proposals(service, services):
    run = await service.run_question("Why did our AWS bill spike?")
    assert run.status is RunStatus.awaiting_approval
    assert [(a.action, a.resource_id, a.estimated_monthly_saving) for a in run.proposed_actions] == [
        (ActionType.stop_instance, IDLE, 571.39),
        (ActionType.delete_volume, VOL, 160.0),
    ]
    assert run.grounding.ok and run.grounding.figures_checked > 0
    assert [c.name for c in run.tool_calls] == ["get_cost_data", "get_resource_details"]
    assert {c.resource_id for c in run.report.root_causes} == {IDLE, "asg-web-prod"}
    assert "asg-web-prod" not in {a.resource_id for a in run.proposed_actions}
    assert "$731.39" in run.report.summary
    assert run.executions == [] and services.executor.executed == []  # nothing runs before approval
    assert run.prompt_version and run.model
    names = [e.name for e in run.trace]
    assert names[0] == "safety_check" and "validation" in names
    assert {"tool:get_cost_data", "tool:get_resource_details", "llm_call"} <= set(names)
    assert len(run.messages) == 6  # user, (assistant, tool results) x2, final assistant


async def test_approve_executes_only_the_approved_action(service, services):
    run = await service.run_question("Why did our AWS bill spike?")
    run = await service.approve(run.id, ["act_1"], actor="operator")
    assert run.status is RunStatus.completed
    assert [(e.action_id, e.status) for e in run.executions] == [("act_1", "stopped (mock)")]
    assert [a.id for a in services.executor.executed] == ["act_1"]
    assert run.approval.decision == "approved" and run.approval.action_ids == ["act_1"]
    with pytest.raises(InvalidTransition):  # cannot approve twice
        await service.approve(run.id, ["act_2"], actor="operator")
    assert len(services.executor.executed) == 1


async def test_reject_executes_nothing(service, services):
    run = await service.run_question("Why did our AWS bill spike?")
    run = await service.reject(run.id, "not now", actor="operator")
    assert run.status is RunStatus.completed and run.executions == []
    assert run.approval.decision == "rejected" and run.approval.reason == "not now"
    assert services.executor.executed == []


async def test_unknown_action_id_is_rejected_without_side_effects(service, services):
    run = await service.run_question("Why did our AWS bill spike?")
    with pytest.raises(UnknownActionId):
        await service.approve(run.id, ["act_1", "act_99"], actor="operator")
    assert (await service.get(run.id)).status is RunStatus.awaiting_approval
    assert services.executor.executed == []


async def test_no_spike_month_completes_without_proposals(service):
    run = await service.run_question("Why did our bill spike in September?")
    assert run.status is RunStatus.completed
    assert run.proposed_actions == [] and run.grounding.ok
    assert "no significant spike" in run.report.summary and "3.2%" in run.report.summary


async def test_specific_resource_question(service):
    run = await service.run_question("Is i-0a1b2c3d4e5f67890 doing anything useful?")
    assert "2.1% average CPU" in run.report.summary and IDLE in run.report.summary
    assert IDLE in {a.resource_id for a in run.proposed_actions}


async def test_runs_are_listed_newest_first(service):
    first = await service.run_question("Why did our AWS bill spike?")
    second = await service.run_question("Why did our bill spike in September?")
    assert [r.id for r in await service.list()] == [second.id, first.id]
