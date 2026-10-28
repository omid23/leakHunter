import asyncio

import pytest
from pydantic import BaseModel

from leakhunter.domain.models import ErrorKind
from leakhunter.tools.base import ToolError, ToolRegistry, ToolSpec, strict_schema
from leakhunter.tools.runtime import ToolRuntime


class Args(BaseModel):
    x: int
    flag: bool = True


class Out(BaseModel):
    y: int


class FakeTool:
    def __init__(self, behaviour):
        self.spec = ToolSpec("fake", "a fake tool", Args, Out)
        self.behaviour = behaviour
        self.calls = 0

    async def run(self, args):
        self.calls += 1
        return await self.behaviour(self.calls, args)


def runtime(tool, attempts=2):
    return ToolRuntime(ToolRegistry([tool]), timeout_s=0.05, max_attempts=attempts, backoff_s=0)


async def call(rt, args=None):
    events = []
    result = await rt.call("fake", args if args is not None else {"x": 1}, step=1, trace=events.append)
    return result, events


async def test_success_returns_validated_data_and_traces_attempt():
    async def ok(n, args):
        return Out(y=args.x + 1)

    result, events = await call(runtime(FakeTool(ok)))
    assert result.ok and result.data == {"y": 2} and result.attempts == 1
    assert [(e.name, e.status) for e in events] == [("tool:fake", "ok")]


async def test_timeout_is_retried_then_reported_with_attempts():
    async def slow(n, args):
        await asyncio.sleep(1)

    tool = FakeTool(slow)
    result, events = await call(runtime(tool))
    assert not result.ok
    assert result.error_kind is ErrorKind.timeout
    assert result.attempts == 2 and tool.calls == 2
    assert "timeout after 2 attempts" in result.error_message
    assert [e.detail["attempt"] for e in events] == [1, 2]


async def test_transient_failure_recovers_on_retry():
    async def flaky(n, args):
        if n == 1:
            raise RuntimeError("boom")
        return Out(y=0)

    result, _ = await call(runtime(FakeTool(flaky)))
    assert result.ok and result.attempts == 2


@pytest.mark.parametrize("kind", [ErrorKind.stale_data, ErrorKind.not_found, ErrorKind.malformed_input])
async def test_non_retryable_errors_are_not_retried(kind):
    async def fail(n, args):
        raise ToolError(kind, "nope")

    tool = FakeTool(fail)
    result, _ = await call(runtime(tool))
    assert result.error_kind is kind and tool.calls == 1 and result.error_message == "nope"
    assert result.fatal == (kind in {ErrorKind.stale_data, ErrorKind.malformed_input})


async def test_invalid_output_is_classified():
    class Wrong(BaseModel):
        z: str

    async def bad(n, args):
        return Wrong(z="?")

    result, _ = await call(runtime(FakeTool(bad), attempts=1))
    assert result.error_kind is ErrorKind.invalid_response


async def test_invalid_arguments_and_unknown_tool():
    async def ok(n, args):
        return Out(y=1)

    tool = FakeTool(ok)
    rt = runtime(tool)
    result, _ = await call(rt, {"x": "not-int", "extra": 1})
    assert result.error_kind is ErrorKind.invalid_arguments and tool.calls == 0
    events = []
    unknown = await rt.call("nope", {}, step=1, trace=events.append)
    assert unknown.error_kind is ErrorKind.invalid_arguments and events[0].status == "error"


def test_strict_schema_closes_objects_and_requires_all_fields():
    schema = strict_schema(Args)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["x", "flag"]
    assert "title" not in schema and "default" not in schema["properties"]["flag"]
    definition = ToolRegistry([FakeTool(None)]).definitions()[0]
    assert definition["strict"] is True and definition["name"] == "fake"
