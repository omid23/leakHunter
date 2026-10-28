"""LLM-side failures with stub clients (LSP check: any LLMClient must be survivable by the orchestrator)."""

import pytest

from leakhunter.agent.llm import LLMRequest, LLMRequestError, LLMResponse, LLMUnavailableError, ToolCall
from leakhunter.agent.scripted_client import ScriptedLLMClient
from leakhunter.bootstrap import build_services
from leakhunter.domain.models import ErrorKind, RunStatus

QUESTION = "Why did our AWS bill spike?"


class Stub:
    name = "stub"
    model = "stub"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request):
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def text(body, stop="end_turn"):
    return LLMResponse(stop_reason=stop, text=body, model="stub")


def tool_call(n=1):
    return LLMResponse(
        stop_reason="tool_use",
        tool_calls=[
            ToolCall(id=f"t{n}", name="get_cost_data", arguments={"period": "current", "compare_with_previous": True})
        ],
        model="stub",
    )


async def run_with(settings, llm):
    services = build_services(settings, llm=llm)
    return await services.service.run_question(QUESTION), services


async def test_llm_unavailable_fails_visibly_without_actions(settings):
    run, services = await run_with(settings, Stub([LLMUnavailableError("connection refused")]))
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.llm_unavailable
    assert run.proposed_actions == [] and services.executor.executed == []


async def test_rejected_request_is_reported_as_such(settings):
    run, services = await run_with(settings, Stub([LLMRequestError("401 invalid api key")]))
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.llm_request_rejected
    assert services.executor.executed == []


async def test_llm_timeout_is_unavailable(settings):
    import asyncio

    class Hanging(Stub):
        async def complete(self, request):
            await asyncio.sleep(1)

    run, _ = await run_with(settings.model_copy(update={"llm_timeout_s": 0.05}), Hanging([None]))
    assert run.error.kind is ErrorKind.llm_unavailable and "no response" in run.error.message


async def test_garbage_output_gets_one_repair_turn_then_fails(settings):
    stub = Stub([tool_call(), text("not json at all"), text('{"summary": 42}')])
    run, _ = await run_with(settings, stub)
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.invalid_model_output
    assert len(stub.requests) == 3  # tool turn, first answer, repaired answer
    assert "could not be parsed" in stub.requests[-1].messages[-1].text
    assert [e.status for e in run.trace if e.name == "validation"] == ["invalid", "invalid"]


async def test_repair_turn_can_recover(settings):
    good = '{"summary": "No figures.", "root_causes": [], "proposed_actions": [], "caveats": []}'
    run, _ = await run_with(settings, Stub([tool_call(), text("oops"), text(good)]))
    assert run.status is RunStatus.completed and run.report.summary == "No figures."


async def test_endless_tool_calls_hit_max_steps(settings):
    run, _ = await run_with(settings, Stub([tool_call()]))
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.max_steps
    assert len(run.tool_calls) == settings.max_steps


@pytest.mark.parametrize(
    ("stop", "kind"), [("refusal", ErrorKind.model_refusal), ("max_tokens", ErrorKind.invalid_model_output)]
)
async def test_refusal_and_truncation(settings, stop, kind):
    run, _ = await run_with(settings, Stub([text(None, stop)]))
    assert run.status is RunStatus.failed and run.error.kind is kind


async def test_text_only_first_turn_is_nudged_once(settings):
    scripted = ScriptedLLMClient()

    class Lazy(Stub):
        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return text('{"summary": "", "root_causes": [], "proposed_actions": [], "caveats": []}')
            return await scripted.complete(request)

    run, _ = await run_with(settings, Lazy([None]))
    assert ("nudge", "sent") in [(e.name, e.status) for e in run.trace]
    assert run.status is RunStatus.awaiting_approval


async def test_model_proposing_unsupported_action_is_dropped(settings):
    scripted = ScriptedLLMClient()

    class Overreaching(Stub):
        async def complete(self, request):
            response = await scripted.complete(request)
            if response.stop_reason == "end_turn":
                import json

                report = json.loads(response.text)
                report["proposed_actions"].append(
                    {
                        "action": "terminate_instance",
                        "resource_id": "db-prod-main",
                        "estimated_monthly_saving": 260.4,
                        "rationale": "x",
                    }
                )
                report["proposed_actions"].append(
                    {
                        "action": "stop_instance",
                        "resource_id": "asg-web-prod",
                        "estimated_monthly_saving": 379.44,
                        "rationale": "x",
                    }
                )
                response.text = json.dumps(report)
            return response

    run, _ = await run_with(settings, Overreaching([None]))
    assert {a.resource_id for a in run.proposed_actions} == {"i-0a1b2c3d4e5f67890", "vol-0f1e2d3c4b5a69788"}
    assert sum("proposal dropped" in w for w in run.warnings) == 2
