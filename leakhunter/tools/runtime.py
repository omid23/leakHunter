"""ToolRuntime: one place for timeout, retry, argument/output validation and tracing of every tool call."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from leakhunter.domain.models import RETRYABLE_TOOL_ERRORS, ErrorKind, TraceEvent
from leakhunter.tools.base import ToolError, ToolRegistry, ToolResult

log = logging.getLogger("leakhunter.tools")

TraceSink = Callable[[TraceEvent], None]


class ToolRuntime:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_s: float,
        max_attempts: int,
        backoff_s: float,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.registry = registry
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s

    def definitions(self) -> list[dict[str, Any]]:
        return self.registry.definitions()

    async def call(self, name: str, raw_args: dict[str, Any], *, step: int, trace: TraceSink) -> ToolResult:
        started = time.perf_counter()
        tool = self.registry.get(name)
        if tool is None:
            return self._finish(name, step, trace, started, 0, ErrorKind.invalid_arguments, f"unknown tool '{name}'")
        try:
            args = tool.spec.args_model.model_validate(raw_args)
        except ValidationError as exc:
            message = f"invalid arguments: {exc.errors(include_url=False)}"
            return self._finish(name, step, trace, started, 0, ErrorKind.invalid_arguments, message)

        kind: ErrorKind = ErrorKind.internal
        message = ""
        attempt = 0
        for attempt in range(1, self.max_attempts + 1):
            attempt_started = time.perf_counter()
            try:
                output = await asyncio.wait_for(tool.run(args), timeout=self.timeout_s)
                data = tool.spec.result_model.model_validate(output.model_dump()).model_dump(mode="json")
            except asyncio.TimeoutError:
                kind, message = ErrorKind.timeout, f"timeout after {self.timeout_s:g}s"
            except ToolError as exc:
                kind, message = exc.kind, exc.message
            except ValidationError as exc:
                kind, message = (
                    ErrorKind.invalid_response,
                    f"tool output failed validation: {exc.error_count()} errors",
                )
            except Exception as exc:  # every unexpected failure becomes a classified result
                log.exception("tool %s crashed", name)
                kind, message = ErrorKind.internal, f"{type(exc).__name__}: {exc}"
            else:
                self._attempt_event(trace, step, name, attempt, "ok", attempt_started)
                return ToolResult(tool=name, ok=True, data=data, attempts=attempt, duration_ms=_ms(started))
            self._attempt_event(trace, step, name, attempt, "error", attempt_started, kind, message)
            if kind not in RETRYABLE_TOOL_ERRORS or attempt == self.max_attempts:
                break
            if self.backoff_s:
                await asyncio.sleep(self.backoff_s * attempt)

        if kind in RETRYABLE_TOOL_ERRORS and self.max_attempts > 1:
            message = f"{message} ({kind.value} after {attempt} attempts)"
        return self._finish(name, step, trace, started, attempt, kind, message)

    def _finish(
        self,
        name: str,
        step: int,
        trace: TraceSink,
        started: float,
        attempts: int,
        kind: ErrorKind,
        message: str,
    ) -> ToolResult:
        if attempts == 0:
            trace(TraceEvent(step=step, name=f"tool:{name}", status="error", detail={"error_kind": kind.value}))
        log.warning("tool failed", extra={"tool": name, "error_kind": kind.value, "attempts": attempts})
        return ToolResult(
            tool=name,
            ok=False,
            error_kind=kind,
            error_message=message,
            attempts=attempts,
            duration_ms=_ms(started),
        )

    @staticmethod
    def _attempt_event(
        trace: TraceSink,
        step: int,
        name: str,
        attempt: int,
        status: str,
        started: float,
        kind: ErrorKind | None = None,
        message: str | None = None,
    ) -> None:
        detail: dict[str, Any] = {"attempt": attempt}
        if kind is not None:
            detail.update(error_kind=kind.value, error=message)
        trace(TraceEvent(step=step, name=f"tool:{name}", status=status, duration_ms=_ms(started), detail=detail))
        log.info("tool attempt", extra={"tool": name, "attempt": attempt, "status": status})


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
