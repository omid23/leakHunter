"""ReportValidator: parse the model's final answer, enforce the action allow-list and evidence, ground figures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from leakhunter.domain.grounding import check_grounding, collect_numbers
from leakhunter.domain.models import (
    ActionType,
    AnalysisReport,
    Finding,
    GroundingReport,
    ProposalCorrection,
    ProposedAction,
)
from leakhunter.domain.money import money
from leakhunter.domain.safety import ACTION_ALLOWLIST

log = logging.getLogger("leakhunter.validator")

SAVING_TOLERANCE = 0.005


class InvalidModelOutput(Exception):
    pass


@dataclass
class ValidationOutcome:
    report: AnalysisReport
    proposed_actions: list[ProposedAction]
    grounding: GroundingReport
    violations: list[str] = field(default_factory=list)


def extract_json(text: str) -> str:
    """Tolerate code fences or prose around the JSON object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise InvalidModelOutput("no JSON object in the final answer")
    return text[start : end + 1]


def evidence_findings(evidence: dict[str, list[dict[str, Any]]]) -> list[Finding]:
    return [
        Finding.model_validate(finding)
        for result in evidence.get("get_resource_details", [])
        for detail in result.get("details", [])
        for finding in detail.get("findings", [])
    ]


class ReportValidator:
    def validate(self, text: str | None, evidence: dict[str, list[dict[str, Any]]]) -> ValidationOutcome:
        if not text:
            raise InvalidModelOutput("empty final answer")
        try:
            report = AnalysisReport.model_validate_json(extract_json(text))
        except ValidationError as exc:
            raise InvalidModelOutput(f"schema validation failed: {exc.error_count()} error(s)") from exc

        actionable = {
            (f.resource_id, f.recommended_action): f
            for f in evidence_findings(evidence)
            if f.recommended_action is not None and not f.below_threshold
        }
        proposals: list[ProposedAction] = []
        corrections: list[ProposalCorrection] = []
        violations: list[str] = []
        for claimed in report.proposed_actions:
            try:
                action = ActionType(claimed.action)
            except ValueError:
                violations.append(f"action '{claimed.action}' on {claimed.resource_id} is not allow-listed")
                continue
            if action not in ACTION_ALLOWLIST:
                violations.append(f"action '{action.value}' is not allow-listed")
                continue
            finding = actionable.get((claimed.resource_id, action))
            if finding is None:
                violations.append(f"{action.value} on {claimed.resource_id} has no supporting finding in the evidence")
                continue
            if any(p.resource_id == finding.resource_id and p.action == action for p in proposals):
                violations.append(f"duplicate proposal {action.value} on {claimed.resource_id}")
                continue
            saving = claimed.estimated_monthly_saving
            if abs(saving - finding.estimated_monthly_saving) > SAVING_TOLERANCE:
                corrections.append(
                    ProposalCorrection(
                        resource_id=finding.resource_id,
                        field="estimated_monthly_saving",
                        claimed=saving,
                        corrected=finding.estimated_monthly_saving,
                    )
                )
                saving = finding.estimated_monthly_saving
            proposals.append(
                ProposedAction(
                    id=f"act_{len(proposals) + 1}",
                    action=action,
                    resource_id=finding.resource_id,
                    estimated_monthly_saving=saving,
                    rationale=claimed.rationale,
                    signal=finding.signal,
                )
            )

        for violation in violations:
            log.warning("proposal dropped", extra={"violation": violation})
        numbers = collect_numbers(evidence)
        numbers.add(money(sum(p.estimated_monthly_saving for p in proposals)))
        numbers.add(money(sum(f.estimated_monthly_saving for f in actionable.values())))
        grounding = check_grounding(report, numbers, corrections)
        return ValidationOutcome(report=report, proposed_actions=proposals, grounding=grounding, violations=violations)
