"""CostAgent: the agent loop (tool use across steps), failure handling and the human approval gate."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from leakhunter.agent.actions import ActionExecutor
from leakhunter.agent.llm import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    Message,
    ToolCall,
    ToolResultBlock,
)
from leakhunter.agent.prompts import NUDGE_MESSAGE, PROMPT_VERSION, REPAIR_MESSAGE, SYSTEM_PROMPT, build_user_turn
from leakhunter.agent.validator import InvalidModelOutput, ReportValidator, ValidationOutcome
from leakhunter.config import Settings
from leakhunter.data.clock import Clock
from leakhunter.domain.errors import InvalidTransition, UnknownActionId
from leakhunter.domain.models import (
    FATAL_TOOL_ERRORS,
    AnalysisReport,
    Approval,
    ErrorKind,
    Refusal,
    RunState,
    RunStatus,
    ToolCallRecord,
    TraceEvent,
)
from leakhunter.domain.safety import SafetyPolicy
from leakhunter.logging_setup import current_run_id
from leakhunter.tools.base import ToolResult
from leakhunter.tools.runtime import ToolRuntime

log = logging.getLogger("leakhunter.agent")


class CostAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        settings: Settings,
        clock: Clock,
        safety: SafetyPolicy | None = None,
        validator: ReportValidator | None = None,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.clock = clock
        self.safety = safety or SafetyPolicy()
        self.validator = validator or ReportValidator()

    # ------------------------------------------------------------------ analysis loop

    async def run(self, run: RunState, tools: ToolRuntime) -> RunState:
        token = current_run_id.set(run.id)
        try:
            await self._run(run, tools)
        finally:
            current_run_id.reset(token)
        log.info("run finished", extra={"status": run.status.value, "steps": len(run.tool_calls)})
        return run

    async def _run(self, run: RunState, tools: ToolRuntime) -> None:
        run.prompt_version = PROMPT_VERSION
        run.model = self.llm.model
        decision = self.safety.evaluate(run.question)
        self._trace(run, 0, "safety_check", "allowed" if decision.allowed else "refused", rule=decision.rule)
        if not decision.allowed:
            run.refusal = Refusal(rule=decision.rule or "unsafe", reason=decision.reason or "unsafe request")
            self._transition(run, RunStatus.refused)
            return

        self._transition(run, RunStatus.running)
        messages = [
            Message(role="user", text=build_user_turn(run.question, self.clock.now(), self.settings.current_period))
        ]
        repairs = 0
        nudged = False
        try:
            for step in range(1, self.settings.max_steps + 1):
                response = await self._complete(run, step, messages, tools)
                messages.append(
                    Message(
                        role="assistant",
                        text=response.text,
                        tool_calls=response.tool_calls,
                        provider_payload=response.provider_payload,
                    )
                )
                if response.stop_reason == "refusal":
                    self._fail(run, ErrorKind.model_refusal, "the model declined to answer")
                    return
                if response.stop_reason == "max_tokens":
                    self._fail(run, ErrorKind.invalid_model_output, "the model's answer was truncated (max_tokens)")
                    return
                if response.stop_reason == "tool_use" and response.tool_calls:
                    results = await self._execute_tools(run, step, response.tool_calls, tools)
                    messages.append(Message(role="user", tool_results=results))
                    fatal = next((r for r in run.tool_calls[-len(results) :] if r.error_kind and _is_fatal(r)), None)
                    if fatal is not None:
                        self._fail(
                            run, fatal.error_kind or ErrorKind.internal, fatal.error_message or "fatal tool error"
                        )
                        return
                    continue
                if not run.tool_calls and not nudged:
                    nudged = True
                    messages.append(Message(role="user", text=NUDGE_MESSAGE))
                    self._trace(run, step, "nudge", "sent")
                    continue
                try:
                    outcome = self.validator.validate(response.text, run.evidence)
                except InvalidModelOutput as exc:
                    self._trace(run, step, "validation", "invalid", error=str(exc))
                    if repairs >= self.settings.max_repair_turns:
                        self._fail(
                            run,
                            ErrorKind.invalid_model_output,
                            f"final answer invalid after {repairs} repair turn(s): {exc}",
                        )
                        return
                    repairs += 1
                    messages.append(Message(role="user", text=REPAIR_MESSAGE.format(error=exc)))
                    continue
                self._accept(run, step, outcome)
                return
            self._fail(run, ErrorKind.max_steps, f"no final answer within {self.settings.max_steps} steps")
        except LLMUnavailableError as exc:
            self._fail(run, ErrorKind.llm_unavailable, f"LLM unavailable: {exc}")
        except LLMError as exc:
            self._fail(run, ErrorKind.llm_unavailable, f"LLM request failed: {exc}")
        finally:
            run.messages = [m.model_dump(mode="json") for m in messages]

    async def _complete(self, run: RunState, step: int, messages: list[Message], tools: ToolRuntime) -> LLMResponse:
        request = LLMRequest(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools.definitions(),
            output_schema=AnalysisReport.model_json_schema(),
        )
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(self.llm.complete(request), timeout=self.settings.llm_timeout_s)
        except asyncio.TimeoutError as exc:
            raise LLMUnavailableError(f"no response within {self.settings.llm_timeout_s:g}s") from exc
        run.add_usage(response.usage)
        self._trace(
            run,
            step,
            "llm_call",
            response.stop_reason,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            model=response.model,
            usage=response.usage,
            tool_calls=[c.name for c in response.tool_calls],
        )
        return response

    async def _execute_tools(
        self, run: RunState, step: int, calls: list[ToolCall], tools: ToolRuntime
    ) -> list[ToolResultBlock]:
        """Calls from one assistant turn run concurrently; all results go back in one user message."""
        results: list[ToolResult] = await asyncio.gather(
            *(tools.call(call.name, call.arguments, step=step, trace=run.trace.append) for call in calls)
        )
        blocks = []
        for call, result in zip(calls, results, strict=True):
            run.tool_calls.append(
                ToolCallRecord(
                    id=call.id,
                    step=step,
                    name=call.name,
                    arguments=call.arguments,
                    ok=result.ok,
                    error_kind=result.error_kind,
                    error_message=result.error_message,
                    attempts=result.attempts,
                    duration_ms=result.duration_ms,
                )
            )
            if result.ok and result.data is not None:
                run.evidence.setdefault(call.name, []).append(result.data)
                for warning in result.data.get("data_quality", {}).get("warnings", []):
                    _add_warning(run, warning)
            elif not result.fatal:
                _add_warning(run, f"{call.name} unavailable: {result.error_message}")
            blocks.append(
                ToolResultBlock(
                    tool_call_id=call.id,
                    name=call.name,
                    content=json.dumps(result.for_model(), default=str),
                    is_error=not result.ok,
                )
            )
        return blocks

    def _accept(self, run: RunState, step: int, outcome: ValidationOutcome) -> None:
        run.report = outcome.report
        run.proposed_actions = outcome.proposed_actions
        run.grounding = outcome.grounding
        for violation in outcome.violations:
            _add_warning(run, f"proposal dropped: {violation}")
        self._trace(
            run,
            step,
            "validation",
            "ok",
            proposals=len(outcome.proposed_actions),
            violations=outcome.violations,
            grounded=outcome.grounding.ok,
            figures_checked=outcome.grounding.figures_checked,
            unmatched=outcome.grounding.unmatched,
        )
        self._transition(run, RunStatus.awaiting_approval if run.proposed_actions else RunStatus.completed)

    # ------------------------------------------------------------------ approval gate

    async def approve(self, run: RunState, action_ids: list[str], actor: str, executor: ActionExecutor) -> RunState:
        if run.status is not RunStatus.awaiting_approval:
            raise InvalidTransition(run.status.value, RunStatus.executing.value)
        known = {a.id: a for a in run.proposed_actions}
        unknown = [i for i in action_ids if i not in known]
        if unknown:
            raise UnknownActionId(unknown)
        self._transition(run, RunStatus.executing)
        selected = list(dict.fromkeys(action_ids))
        run.approval = Approval(decision="approved", action_ids=selected, actor=actor)
        self._trace(run, _last_step(run), "approval", "approved", action_ids=selected, actor=actor)
        try:
            for action_id in selected:
                result = await executor.execute(known[action_id])
                run.executions.append(result)
                self._trace(run, _last_step(run), "execution", result.status, action_id=action_id)
        except Exception as exc:  # the executor is an external dependency: fail visibly, keep what ran
            log.exception("execution failed")
            self._fail(run, ErrorKind.internal, f"execution failed: {exc}")
            return run
        self._transition(run, RunStatus.completed)
        return run

    def reject(self, run: RunState, reason: str | None, actor: str) -> RunState:
        if run.status is not RunStatus.awaiting_approval:
            raise InvalidTransition(run.status.value, RunStatus.completed.value)
        run.approval = Approval(decision="rejected", reason=reason, actor=actor)
        self._trace(run, _last_step(run), "approval", "rejected", reason=reason, actor=actor)
        self._transition(run, RunStatus.completed)
        return run

    # ------------------------------------------------------------------ helpers

    def _fail(self, run: RunState, kind: ErrorKind, message: str) -> None:
        previous = run.status
        run.fail(kind, message)
        self._trace(run, _last_step(run), "transition", "failed", previous=previous.value, error_kind=kind.value)
        log.warning("run failed", extra={"error_kind": kind.value, "error": message})

    def _transition(self, run: RunState, target: RunStatus) -> None:
        previous = run.status
        run.transition_to(target)
        self._trace(run, _last_step(run), "transition", target.value, previous=previous.value)

    @staticmethod
    def _trace(
        run: RunState, step: int, name: str, status: str, duration_ms: float | None = None, **detail: Any
    ) -> None:
        run.trace.append(TraceEvent(step=step, name=name, status=status, duration_ms=duration_ms, detail=detail))


def _is_fatal(record: ToolCallRecord) -> bool:
    return record.error_kind in FATAL_TOOL_ERRORS


def _add_warning(run: RunState, warning: str) -> None:
    if warning not in run.warnings:
        run.warnings.append(warning)


def _last_step(run: RunState) -> int:
    return run.trace[-1].step if run.trace else 0
