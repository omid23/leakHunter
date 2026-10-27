"""The LLM port: provider-neutral message types and the ``LLMClient`` protocol every adapter honours."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

StopReason = Literal["tool_use", "end_turn", "max_tokens", "refusal"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultBlock(BaseModel):
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """One conversation turn. Assistant turns keep the provider's raw content for verbatim replay."""

    role: Literal["user", "assistant"]
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResultBlock] = Field(default_factory=list)
    provider_payload: list[dict[str, Any]] | None = None


class LLMRequest(BaseModel):
    system: str
    messages: list[Message]
    tools: list[dict[str, Any]]
    output_schema: dict[str, Any]


class LLMResponse(BaseModel):
    stop_reason: StopReason
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    model: str
    provider_payload: list[dict[str, Any]] | None = None


class LLMClient(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...


class LLMError(Exception):
    """Base class for adapter failures."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached (connection, rate limit, 5xx)."""


class LLMRequestError(LLMError):
    """The provider rejected the request (4xx); retrying will not help."""
