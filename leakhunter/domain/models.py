"""Domain models (Pydantic v2). They double as data-validation layer, LLM output schema and API output."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from leakhunter.domain.errors import InvalidTransition

# --------------------------------------------------------------------------- enums


class Signal(str, Enum):
    idle_instance = "idle_instance"
    unattached_volume = "unattached_volume"


class ActionType(str, Enum):
    """The action allow-list: nothing else can ever be proposed or executed."""

    stop_instance = "stop_instance"
    delete_volume = "delete_volume"


class ErrorKind(str, Enum):
    """One error vocabulary for tools and runs."""

    # tool errors
    stale_data = "stale_data"
    malformed_input = "malformed_input"
    not_found = "not_found"
    timeout = "timeout"
    invalid_arguments = "invalid_arguments"
    invalid_response = "invalid_response"
    internal = "internal"
    # run errors
    invalid_model_output = "invalid_model_output"
    llm_unavailable = "llm_unavailable"
    llm_request_rejected = "llm_request_rejected"
    model_refusal = "model_refusal"
    max_steps = "max_steps"


FATAL_TOOL_ERRORS = frozenset({ErrorKind.stale_data, ErrorKind.malformed_input})
RETRYABLE_TOOL_ERRORS = frozenset({ErrorKind.timeout, ErrorKind.internal})


class RunStatus(str, Enum):
    created = "created"
    refused = "refused"
    running = "running"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    completed = "completed"
    failed = "failed"


TERMINAL_STATUSES = frozenset({RunStatus.refused, RunStatus.completed, RunStatus.failed})

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.created: frozenset({RunStatus.refused, RunStatus.running}),
    RunStatus.running: frozenset({RunStatus.awaiting_approval, RunStatus.completed, RunStatus.failed}),
    RunStatus.awaiting_approval: frozenset({RunStatus.executing, RunStatus.completed}),
    RunStatus.executing: frozenset({RunStatus.completed, RunStatus.failed}),
    RunStatus.refused: frozenset(),
    RunStatus.completed: frozenset(),
    RunStatus.failed: frozenset(),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- billing data


class CostLineItem(BaseModel):
    usage_date: date
    service: str
    usage_type: str
    resource_id: str
    usage_amount: float
    unblended_cost: float
    env: str | None = None


class RowError(BaseModel):
    line_no: int
    reason: str
    raw: str


class BillingExport(BaseModel):
    period: str
    generated_at: datetime
    items: list[CostLineItem]
    row_errors: list[RowError] = Field(default_factory=list)
    source_file: str

    @property
    def total_rows(self) -> int:
        return len(self.items) + len(self.row_errors)


class Resource(BaseModel):
    resource_id: str
    service: str
    resource_type: str
    state: str
    region: str
    env: str
    name: str
    instance_type: str | None = None
    instance_count: int | None = None
    size_gib: int | None = None
    avg_cpu_7d_pct: float | None = None
    attached_to: str | None = None
    detached_at: datetime | None = None
    created_at: datetime


# --------------------------------------------------------------------------- analysis


class ResourceCost(BaseModel):
    resource_id: str
    service: str
    env: str | None
    cost: float


class ResourceDelta(BaseModel):
    resource_id: str
    service: str
    current: float
    previous: float | None
    delta_abs: float | None
    delta_pct: float | None


class CostSummary(BaseModel):
    period: str
    total: float
    by_service: dict[str, float]
    by_resource: list[ResourceCost]


class CostComparison(BaseModel):
    period: str
    previous_period: str | None
    current_total: float
    previous_total: float | None
    delta_abs: float | None
    delta_pct: float | None
    is_spike: bool
    spike_threshold_pct: float
    by_service_current: dict[str, float]
    by_service_previous: dict[str, float] | None
    top_movers: list[ResourceDelta]


class DataQuality(BaseModel):
    source_file: str
    generated_at: datetime
    age_hours: float
    is_stale: bool
    total_rows: int
    skipped_rows: int
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    resource_id: str
    signal: Signal
    evidence: dict[str, Any]
    estimated_monthly_saving: float
    below_threshold: bool
    recommended_action: ActionType | None


class ResourceDetail(BaseModel):
    resource: Resource
    period: str
    period_cost: float
    previous_period_cost: float | None
    findings: list[Finding]


# --------------------------------------------------------------------------- LLM output schema


class RootCause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str
    explanation: str


class ReportAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    resource_id: str
    estimated_monthly_saving: float
    rationale: str


class AnalysisReport(BaseModel):
    """The model's final answer. Kept constraint-free so its JSON schema is valid for structured output."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    root_causes: list[RootCause]
    proposed_actions: list[ReportAction]
    caveats: list[str]


# --------------------------------------------------------------------------- run state


class ProposedAction(BaseModel):
    id: str
    action: ActionType
    resource_id: str
    estimated_monthly_saving: float
    rationale: str
    signal: Signal


class ProposalCorrection(BaseModel):
    resource_id: str
    field: str
    claimed: Any
    corrected: Any


class GroundingReport(BaseModel):
    figures_checked: int
    unmatched: list[str]
    proposal_corrections: list[ProposalCorrection]
    ok: bool


class TraceEvent(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    step: int
    name: str
    status: str
    duration_ms: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    id: str
    step: int
    name: str
    arguments: dict[str, Any]
    ok: bool
    error_kind: ErrorKind | None = None
    error_message: str | None = None
    attempts: int
    duration_ms: float


class Approval(BaseModel):
    decision: Literal["approved", "rejected"]
    action_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    actor: str
    at: datetime = Field(default_factory=utcnow)


class ExecutionResult(BaseModel):
    action_id: str
    action: ActionType
    resource_id: str
    status: str
    at: datetime = Field(default_factory=utcnow)


class RunError(BaseModel):
    kind: ErrorKind
    message: str


class Refusal(BaseModel):
    rule: str
    reason: str


class RunState(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    question: str
    faults: list[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.created
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    evidence: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    report: AnalysisReport | None = None
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    grounding: GroundingReport | None = None
    refusal: Refusal | None = None
    approval: Approval | None = None
    executions: list[ExecutionResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: RunError | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    prompt_version: str | None = None
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def transition_to(self, target: RunStatus) -> None:
        """The one place run lifecycle rules are enforced."""
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(self.status.value, target.value)
        self.status = target
        self.updated_at = utcnow()

    def fail(self, kind: ErrorKind, message: str) -> None:
        self.error = RunError(kind=kind, message=message)
        self.transition_to(RunStatus.failed)

    def add_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + int(value or 0)
