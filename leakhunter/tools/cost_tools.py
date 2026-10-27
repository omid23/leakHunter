"""The two data tools the agent can call: ``get_cost_data`` and ``get_resource_details``."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from leakhunter.config import Settings
from leakhunter.data.billing_repository import BillingSource
from leakhunter.data.clock import Clock
from leakhunter.data.resource_repository import ResourceRepository
from leakhunter.domain import analysis, quality
from leakhunter.domain.errors import PeriodNotFound
from leakhunter.domain.models import CostComparison, CostSummary, DataQuality, ErrorKind, ResourceDetail
from leakhunter.domain.rules import RuleContext, evaluate
from leakhunter.tools.base import ToolError, ToolSpec

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_RESOURCE_IDS = 20


def resolve_period(period: str, current_period: str) -> str:
    if period == "current":
        return current_period
    if not _PERIOD.match(period):
        raise ToolError(ErrorKind.invalid_arguments, f"period must be 'YYYY-MM' or 'current', got '{period}'")
    return period


class GetCostDataArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str = Field("current", description="Billing month as 'YYYY-MM', or 'current'.")
    compare_with_previous: bool = Field(True, description="Also load the previous month and compute deltas.")


class CostDataResult(BaseModel):
    comparison: CostComparison
    data_quality: DataQuality


class GetResourceDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_ids: list[str] = Field(description="Resource ids to inspect (at most 20).")
    period: str = Field("current", description="Billing month as 'YYYY-MM', or 'current'.")


class ResourceDetailsResult(BaseModel):
    details: list[ResourceDetail]
    missing: list[str]


class GetCostDataTool:
    spec = ToolSpec(
        name="get_cost_data",
        description=(
            "Load the AWS billing export for a month and compare it with the previous month. Returns totals, "
            "per-service costs, the top cost movers (resources whose cost grew most) and data-quality facts "
            "(export age, skipped malformed rows). Call this first."
        ),
        args_model=GetCostDataArgs,
        result_model=CostDataResult,
    )

    def __init__(self, billing: BillingSource, clock: Clock, settings: Settings) -> None:
        self._billing = billing
        self._clock = clock
        self._settings = settings

    async def run(self, args: GetCostDataArgs) -> CostDataResult:
        s = self._settings
        period = resolve_period(args.period, s.current_period)
        try:
            export = self._billing.load(period)
        except PeriodNotFound as exc:
            raise ToolError(ErrorKind.not_found, str(exc)) from exc

        data_quality = quality.assess(export, self._clock.now(), s.stale_after_hours)
        if data_quality.is_stale:
            raise ToolError(
                ErrorKind.stale_data,
                f"Billing export {export.source_file} for {period} was generated at "
                f"{export.generated_at:%Y-%m-%d %H:%M} UTC, {data_quality.age_hours / 24:.1f} days ago "
                f"(limit {s.stale_after_hours:g} h). Refusing to analyse stale billing data.",
            )
        if quality.malformed_ratio_exceeded(data_quality.skipped_rows, data_quality.total_rows, s.malformed_fail_ratio):
            raise ToolError(
                ErrorKind.malformed_input,
                f"{data_quality.skipped_rows} of {data_quality.total_rows} rows in {export.source_file} are "
                f"malformed (limit {s.malformed_fail_ratio:.0%}). Refusing to analyse unreliable billing data.",
            )

        current = analysis.summarize(period, export.items)
        previous: CostSummary | None = None
        if args.compare_with_previous:
            previous_period = analysis.previous_period(period)
            try:
                previous_export = self._billing.load(previous_period)
            except PeriodNotFound:
                data_quality.warnings.append(f"no previous period ({previous_period}) to compare")
            else:
                previous = analysis.summarize(previous_period, previous_export.items)
                if previous_export.row_errors:
                    data_quality.warnings.append(
                        f"{len(previous_export.row_errors)} malformed rows skipped in {previous_export.source_file}"
                    )

        comparison = analysis.compare(
            current,
            previous,
            spike_threshold_pct=s.spike_threshold_pct,
            top_movers_limit=s.top_movers_limit,
        )
        return CostDataResult(comparison=comparison, data_quality=data_quality)


class GetResourceDetailsTool:
    spec = ToolSpec(
        name="get_resource_details",
        description=(
            "Inspect specific resources: inventory facts (state, utilization, attachment), their cost in the "
            "period and the previous one, and anomaly findings (idle instance, unattached volume) with the "
            "estimated monthly saving and the recommended action."
        ),
        args_model=GetResourceDetailsArgs,
        result_model=ResourceDetailsResult,
    )

    def __init__(self, billing: BillingSource, resources: ResourceRepository, settings: Settings) -> None:
        self._billing = billing
        self._resources = resources
        self._settings = settings

    async def run(self, args: GetResourceDetailsArgs) -> ResourceDetailsResult:
        s = self._settings
        if len(args.resource_ids) > MAX_RESOURCE_IDS:
            raise ToolError(ErrorKind.invalid_arguments, f"at most {MAX_RESOURCE_IDS} resource ids per call")
        period = resolve_period(args.period, s.current_period)
        current = self._summary(period)
        if current is None:
            raise ToolError(ErrorKind.not_found, f"no billing export for period '{period}'")
        previous = self._summary(analysis.previous_period(period))

        found, missing = self._resources.get_many(list(dict.fromkeys(args.resource_ids)))
        ctx = RuleContext(
            idle_cpu_threshold_pct=s.idle_cpu_threshold_pct,
            min_action_saving_usd=s.min_action_saving_usd,
            period=period,
        )
        details = []
        for resource in found:
            period_cost = analysis.resource_cost(current, resource.resource_id) or 0.0
            details.append(
                ResourceDetail(
                    resource=resource,
                    period=period,
                    period_cost=period_cost,
                    previous_period_cost=analysis.resource_cost(previous, resource.resource_id),
                    findings=evaluate(resource, period_cost, ctx),
                )
            )
        return ResourceDetailsResult(details=details, missing=missing)

    def _summary(self, period: str) -> CostSummary | None:
        try:
            return analysis.summarize(period, self._billing.load(period).items)
        except PeriodNotFound:
            return None
