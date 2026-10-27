"""Adapter tests with fake SDK objects: request shape, response mapping, error mapping. No network."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from leakhunter.agent.anthropic_client import FALLBACK_BETA, AnthropicLLMClient
from leakhunter.agent.llm import (
    LLMRequest,
    LLMRequestError,
    LLMUnavailableError,
    Message,
    ToolCall,
    ToolResultBlock,
)
from leakhunter.domain.models import AnalysisReport
from tests.conftest import make_settings

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class Block(SimpleNamespace):
    def to_dict(self):
        return dict(vars(self))


def sdk_message(blocks, stop_reason="end_turn"):
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=None, cache_creation_input_tokens=3
    )
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, usage=usage, model="claude-opus-5-5", stop_details=None
    )


class FakeEndpoint:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, plain=(), beta=()):
        self.messages = FakeEndpoint(plain)
        self.beta = SimpleNamespace(messages=FakeEndpoint(beta))


def request():
    tools = [{"name": "get_cost_data", "description": "d", "input_schema": {"type": "object"}, "strict": True}]
    return LLMRequest(
        system="SYSTEM",
        tools=tools,
        output_schema=AnalysisReport.model_json_schema(),
        messages=[
            Message(role="user", text="Question: why?"),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="toolu_1", name="get_cost_data", arguments={})],
                provider_payload=[
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "tool_use", "id": "toolu_1"},
                ],
            ),
            Message(
                role="user",
                tool_results=[
                    ToolResultBlock(tool_call_id="toolu_1", name="get_cost_data", content="{}", is_error=True)
                ],
            ),
        ],
    )


def adapter(client, **overrides):
    return AnthropicLLMClient(make_settings(llm_provider="anthropic", **overrides), client=client)


async def test_request_shape_uses_fallbacks_strict_tools_and_structured_output():
    client = FakeClient(beta=[sdk_message([Block(type="text", text='{"summary": "x"}')])])
    await adapter(client).complete(request())
    [params] = client.beta.messages.calls
    assert params["model"] == "claude-opus-5-5"
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    assert "thinking" not in params and "tool_choice" not in params
    assert params["output_config"]["effort"] == "medium"
    schema = params["output_config"]["format"]["schema"]
    assert params["output_config"]["format"]["type"] == "json_schema" and "title" not in schema
    assert schema["additionalProperties"] is False
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["tools"][0]["strict"] is True
    user, assistant, results = params["messages"]
    assert user == {"role": "user", "content": "Question: why?"}
    assert assistant["content"][0]["type"] == "thinking"  # replayed verbatim
    assert results["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}", "is_error": True}]


async def test_fallbacks_can_be_switched_off():
    client = FakeClient(plain=[sdk_message([])])
    await adapter(client, llm_fallbacks=False).complete(request())
    assert client.beta.messages.calls == [] and "fallbacks" not in client.messages.calls[0]


async def test_rejected_fallback_combination_degrades_to_plain_request_once():
    bad = anthropic.BadRequestError("unsupported", response=httpx2.Response(400, request=REQ), body=None)
    client = FakeClient(plain=[sdk_message([]), sdk_message([])], beta=[bad])
    llm = adapter(client)
    await llm.complete(request())
    await llm.complete(request())
    assert len(client.beta.messages.calls) == 1 and len(client.messages.calls) == 2


async def test_tool_use_response_mapping():
    blocks = [
        Block(type="thinking", thinking="", signature="s"),
        Block(
            type="tool_use",
            id="toolu_9",
            name="get_resource_details",
            input={"resource_ids": ["i-1"], "period": "current"},
        ),
    ]
    response = await adapter(FakeClient(beta=[sdk_message(blocks, "tool_use")])).complete(request())
    assert response.stop_reason == "tool_use"
    assert response.tool_calls == [
        ToolCall(id="toolu_9", name="get_resource_details", arguments={"resource_ids": ["i-1"], "period": "current"})
    ]
    assert response.provider_payload[0]["type"] == "thinking"
    assert response.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 3,
    }


@pytest.mark.parametrize(
    ("api", "ours"), [("refusal", "refusal"), ("max_tokens", "max_tokens"), ("stop_sequence", "end_turn")]
)
async def test_stop_reason_mapping(api, ours):
    response = await adapter(FakeClient(beta=[sdk_message([], api)])).complete(request())
    assert response.stop_reason == ours and response.text is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=REQ), body=None),
            LLMUnavailableError,
        ),
        (
            anthropic.InternalServerError("boom", response=httpx2.Response(503, request=REQ), body=None),
            LLMUnavailableError,
        ),
        (anthropic.APIConnectionError(request=REQ), LLMUnavailableError),
        (
            anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=REQ), body=None),
            LLMRequestError,
        ),
    ],
)
async def test_error_mapping(error, expected):
    with pytest.raises(expected):
        await adapter(FakeClient(beta=[error])).complete(request())


async def test_bad_request_without_fallbacks_is_a_request_error():
    bad = anthropic.BadRequestError("nope", response=httpx2.Response(400, request=REQ), body=None)
    with pytest.raises(LLMRequestError):
        await adapter(FakeClient(plain=[bad]), llm_fallbacks=False).complete(request())
