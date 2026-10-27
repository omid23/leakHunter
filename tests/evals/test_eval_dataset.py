import json
from pathlib import Path

import pytest

from leakhunter.tools.faults import FaultKind

DATASET = json.loads((Path(__file__).resolve().parents[2] / "evals" / "dataset.json").read_text())
CASES = DATASET["cases"]
CRITERIA = set(DATASET["criteria"])
KNOWN_EXPECT_KEYS = {
    "status", "final_status", "error_kind", "error_message_contains", "actions", "must_propose", "total_saving",
    "must_mention", "must_not_propose", "required_tools", "forbidden_tools", "max_tool_calls", "tool_args",
    "refused", "warnings_contain", "no_proposals", "min_attempts", "executed",
}  # fmt: skip


def test_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) == 11


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_schema(case):
    assert case["question"].strip()
    assert case["criteria"] and set(case["criteria"]) <= CRITERIA
    for fault in case["faults"]:
        FaultKind(fault)  # raises on an unknown fault
    assert case["expect"]["status"]
    assert set(case["expect"]) <= KNOWN_EXPECT_KEYS, set(case["expect"]) - KNOWN_EXPECT_KEYS
    if "decision" in case:
        assert case["decision"]["type"] in {"approve", "reject"}
        assert "final_status" in case["expect"]


def test_every_criterion_is_covered():
    covered = {criterion for case in CASES for criterion in case["criteria"]}
    assert covered == CRITERIA


def test_both_directions_are_present():
    safety = [c["expect"].get("refused") for c in CASES if "safety" in c["criteria"]]
    assert True in safety and False in safety  # refuse AND do not over-refuse
    completion_actions = [c["expect"].get("actions") for c in CASES if "task_completion" in c["criteria"]]
    assert [] in completion_actions and any(completion_actions)  # propose AND propose nothing
