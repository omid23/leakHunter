import json

import pytest

from leakhunter.agent.validator import InvalidModelOutput, ReportValidator, extract_json

FINDING = {
    "resource_id": "i-1",
    "signal": "idle_instance",
    "evidence": {"avg_cpu_7d_pct": 2.1, "period_cost": 571.39},
    "estimated_monthly_saving": 571.39,
    "below_threshold": False,
    "recommended_action": "stop_instance",
}
SMALL = {
    **FINDING,
    "resource_id": "i-2",
    "estimated_monthly_saving": 7.74,
    "below_threshold": True,
    "recommended_action": None,
}
EVIDENCE = {
    "get_cost_data": [{"comparison": {"current_total": 1543.27}}],
    "get_resource_details": [{"details": [{"findings": [FINDING, SMALL]}], "missing": []}],
}


def answer(actions, summary="Bill is $1,543.27; idle at 2.1%.") -> str:
    return json.dumps({"summary": summary, "root_causes": [], "proposed_actions": actions, "caveats": []})


def action(**overrides):
    base = {"action": "stop_instance", "resource_id": "i-1", "estimated_monthly_saving": 571.39, "rationale": "idle"}
    base.update(overrides)
    return base


validator = ReportValidator()


def test_valid_proposal_becomes_proposed_action_and_is_grounded():
    outcome = validator.validate(answer([action()]), EVIDENCE)
    [proposal] = outcome.proposed_actions
    assert (proposal.id, proposal.action.value, proposal.signal.value) == ("act_1", "stop_instance", "idle_instance")
    assert outcome.grounding.ok and outcome.violations == []


def test_wrong_saving_is_corrected_and_recorded():
    outcome = validator.validate(answer([action(estimated_monthly_saving=999.0)]), EVIDENCE)
    assert outcome.proposed_actions[0].estimated_monthly_saving == 571.39
    [correction] = outcome.grounding.proposal_corrections
    assert (correction.claimed, correction.corrected) == (999.0, 571.39)
    assert not outcome.grounding.ok


@pytest.mark.parametrize(
    "bad",
    [
        action(action="terminate_everything"),
        action(resource_id="i-unknown"),
        action(action="delete_volume"),  # no unattached-volume finding for i-1
        action(resource_id="i-2", estimated_monthly_saving=7.74),  # below threshold
    ],
)
def test_unsupported_proposals_are_dropped_as_violations(bad):
    outcome = validator.validate(answer([bad]), EVIDENCE)
    assert outcome.proposed_actions == [] and len(outcome.violations) == 1


def test_duplicates_are_dropped():
    outcome = validator.validate(answer([action(), action()]), EVIDENCE)
    assert len(outcome.proposed_actions) == 1 and "duplicate" in outcome.violations[0]


def test_ungrounded_narrative_is_flagged():
    outcome = validator.validate(answer([], summary="You will save $12,345.00"), EVIDENCE)
    assert not outcome.grounding.ok and outcome.grounding.unmatched


@pytest.mark.parametrize("text", [None, "", "no json here", '{"summary": "x"}', '{"summary": 1, "extra": true}'])
def test_unparseable_output_raises(text):
    with pytest.raises(InvalidModelOutput):
        validator.validate(text, EVIDENCE)


def test_extract_json_strips_fences():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
