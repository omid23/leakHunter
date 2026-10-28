"""Anomaly rules (Strategy). Each rule inspects one resource and its period cost and may yield a Finding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leakhunter.domain.models import ActionType, Finding, Resource, Signal
from leakhunter.domain.money import money


@dataclass(frozen=True)
class RuleContext:
    idle_cpu_threshold_pct: float
    min_action_saving_usd: float
    period: str


class AnomalyRule(Protocol):
    signal: Signal
    action: ActionType

    def matches(self, resource: Resource, ctx: RuleContext) -> bool: ...

    def evidence(self, resource: Resource, ctx: RuleContext) -> dict[str, object]: ...


class IdleInstanceRule:
    signal = Signal.idle_instance
    action = ActionType.stop_instance

    def matches(self, resource: Resource, ctx: RuleContext) -> bool:
        return (
            resource.service == "ec2"
            and resource.resource_type == "instance"
            and resource.state == "running"
            and resource.avg_cpu_7d_pct is not None
            and resource.avg_cpu_7d_pct < ctx.idle_cpu_threshold_pct
        )

    def evidence(self, resource: Resource, ctx: RuleContext) -> dict[str, object]:
        return {
            "state": resource.state,
            "avg_cpu_7d_pct": resource.avg_cpu_7d_pct,
            "idle_cpu_threshold_pct": ctx.idle_cpu_threshold_pct,
            "instance_type": resource.instance_type,
            "running_since": resource.created_at.isoformat(),
        }


class UnattachedVolumeRule:
    signal = Signal.unattached_volume
    action = ActionType.delete_volume

    def matches(self, resource: Resource, ctx: RuleContext) -> bool:
        return (
            resource.service == "ec2"
            and resource.resource_type == "volume"
            and resource.state == "available"
            and resource.attached_to is None
        )

    def evidence(self, resource: Resource, ctx: RuleContext) -> dict[str, object]:
        return {
            "state": resource.state,
            "attached_to": None,
            "detached_at": resource.detached_at.isoformat() if resource.detached_at else None,
            "size_gib": resource.size_gib,
        }


DEFAULT_RULES: tuple[AnomalyRule, ...] = (IdleInstanceRule(), UnattachedVolumeRule())


def evaluate(
    resource: Resource,
    period_cost: float,
    ctx: RuleContext,
    rules: tuple[AnomalyRule, ...] = DEFAULT_RULES,
) -> list[Finding]:
    """Saving = the resource's cost in the analysed period (assumption A7), so it matches the data exactly."""
    findings = []
    saving = money(period_cost)
    below = saving < ctx.min_action_saving_usd
    for rule in rules:
        if not rule.matches(resource, ctx):
            continue
        evidence = rule.evidence(resource, ctx)
        evidence.update(
            {
                "period": ctx.period,
                "period_cost": saving,
                "min_action_saving_usd": ctx.min_action_saving_usd,
            }
        )
        findings.append(
            Finding(
                resource_id=resource.resource_id,
                signal=rule.signal,
                evidence=evidence,
                estimated_monthly_saving=saving,
                below_threshold=below,
                recommended_action=None if below else rule.action,
            )
        )
    return findings
