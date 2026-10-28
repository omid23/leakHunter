"""Anthropic Messages API adapter behind the ``LLMClient`` port (manual tool loop, strict tools, JSON output)."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from leakhunter.agent.llm import (
    LLMRequest,
    LLMRequestError,
    LLMResponse,
    LLMUnavailableError,
    Message,
    StopReason,
    ToolCall,
)
from leakhunter.config import Settings

log = logging.getLogger("leakhunter.llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_STOP_REASONS: dict[str, StopReason] = {
    "tool_use": "tool_use",
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop presentation-only keys (``title``) that add nothing to the structured-output contract."""
    if isinstance(schema, dict):
        return {k: output_schema(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [output_schema(v) for v in schema]
    return schema


def to_api_message(message: Message) -> dict[str, Any]:
    if message.role == "assistant":
        if message.provider_payload is not None:
            return {"role": "assistant", "content": message.provider_payload}  # verbatim replay (thinking blocks)
        content: list[dict[str, Any]] = []
        if message.text:
            content.append({"type": "text", "text": message.text})
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in message.tool_calls]
        return {"role": "assistant", "content": content}
    if message.tool_results:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
                for r in message.tool_results
            ],
        }
    return {"role": "user", "content": message.text or ""}


class AnthropicLLMClient:
    name = "anthropic"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.model = settings.model
        self._settings = settings
        self._fallbacks = settings.llm_fallbacks
        self._client = client or anthropic.AsyncAnthropic(timeout=settings.llm_timeout_s, max_retries=2)

    def build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Stable prefix first (tools, cached system); volatile context lives in the user turn."""
        return {
            "model": self.model,
            "max_tokens": self._settings.llm_max_tokens,
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "tools": request.tools,
            "messages": [to_api_message(m) for m in request.messages],
            # thinking is always on for this model family: no `thinking` param, depth is set by effort
            "output_config": {
                "effort": self._settings.effort,
                "format": {"type": "json_schema", "schema": output_schema(request.output_schema)},
            },
        }

    async def complete(self, request: LLMRequest) -> LLMResponse:
        params = self.build_params(request)
        try:
            if self._fallbacks:
                try:
                    message = await self._client.beta.messages.create(
                        **params, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                except anthropic.BadRequestError as exc:
                    # Keep structured output and drop fallbacks if the combination is rejected (logged once).
                    log.warning("server-side fallbacks rejected; continuing without them", extra={"error": str(exc)})
                    self._fallbacks = False
                    message = await self._client.messages.create(**params)
            else:
                message = await self._client.messages.create(**params)
        except anthropic.BadRequestError as exc:
            raise LLMRequestError(f"request rejected ({getattr(exc, 'request_id', None)}): {exc}") from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMUnavailableError(f"provider error {exc.status_code}") from exc
            raise LLMRequestError(f"provider rejected the request ({exc.status_code}): {exc}") from exc
        return self.parse(message)

    def parse(self, message: Any) -> LLMResponse:
        blocks = list(message.content or [])
        text = "".join(b.text for b in blocks if b.type == "text") or None
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input)) for b in blocks if b.type == "tool_use"]
        stop = _STOP_REASONS.get(message.stop_reason or "", "end_turn")
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            log.warning("model refusal", extra={"category": getattr(details, "category", None)})
        usage = message.usage
        return LLMResponse(
            stop_reason=stop,
            text=text,
            tool_calls=calls,
            usage={
                "input_tokens": usage.input_tokens or 0,
                "output_tokens": usage.output_tokens or 0,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
            },
            model=message.model,
            provider_payload=[b.to_dict() for b in blocks],
        )
