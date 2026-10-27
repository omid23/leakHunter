"""The grader must not be lenient: the oracle passes everything, broken clients fail the right criteria."""

import json

import pytest

from evals.runner import load_cases, run_evals, wilson_interval
from evals.scorers import SCORERS
from leakhunter.agent.llm import LLMRequest, LLMResponse
from leakhunter.agent.scripted_client import ScriptedLLMClient
from tests.conftest import make_settings

pytestmark = pytest.mark.evals


class NullClient:
    """Answers immediately with an empty report: never uses tools, never proposes anything."""

    name = "null"
    model = "null"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        empty = {"summary": "", "root_causes": [], "proposed_actions": [], "caveats": []}
        return LLMResponse(stop_reason="end_turn", text=json.dumps(empty), model=self.model)


class InventingClient(ScriptedLLMClient):
    """Follows the workflow but inflates the claimed saving and invents a figure."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await super().complete(request)
        if response.stop_reason == "end_turn" and response.text:
            report = json.loads(response.text)
            report["summary"] += " Switching regions would also save $4,321.00."
            for action in report["proposed_actions"]:
                action["estimated_monthly_saving"] *= 2
            response.text = json.dumps(report)
        return response


async def evaluate(factory, case_ids=None):
    return await run_evals(client="test", client_factory=factory, settings=make_settings(), case_ids=case_ids)


async def test_oracle_scores_100_percent():
    report = await evaluate(ScriptedLLMClient)
    assert report["harness_errors"] == 0
    assert all(c["pass_rate"] == 1.0 for c in report["criteria"].values()), report["cases"]


async def test_null_client_fails_task_completion_and_groundedness():
    report = await evaluate(NullClient)
    assert report["criteria"]["task_completion"]["pass_rate"] < 0.5
    # an empty answer is vacuously grounded, but it misses every expected saving
    assert report["criteria"]["groundedness"]["pass_rate"] < 0.5
    assert any("total saving" in f for f in report["cases"]["happy_path_spike"]["failures"])
    assert report["criteria"]["tool_accuracy"]["pass_rate"] == 0.0
    assert report["criteria"]["safety"]["pass_rate"] < 1.0  # it never proposes, so "not over-refusing" breaks


async def test_inventing_client_fails_groundedness_only_where_it_lies():
    report = await evaluate(InventingClient, ["happy_path_spike", "unsafe_delete_all_prod"])
    failures = report["cases"]["happy_path_spike"]["failures"]
    assert any("ungrounded figures" in f for f in failures)
    assert any("proposal savings corrected" in f for f in failures)
    assert report["cases"]["unsafe_delete_all_prod"]["failures"] == []


async def test_harness_errors_are_not_scored():
    class Exploding(ScriptedLLMClient):
        async def complete(self, request):
            raise RuntimeError("harness bug")

    report = await evaluate(Exploding, ["happy_path_spike"])
    assert report["harness_errors"] == 1  # an unexpected crash is a harness error...
    assert report["criteria"] == {}  # ...and is never scored as a model failure


def test_wilson_interval_bounds():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(10, 10)
    assert 0.6 < low < 1.0 and high == 1.0


def test_every_case_criterion_has_a_scorer():
    assert {c for case in load_cases() for c in case["criteria"]} <= set(SCORERS)
