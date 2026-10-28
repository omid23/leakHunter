"""Pure cost analysis: summaries, period comparison, top movers and the spike flag."""

from __future__ import annotations

from collections import defaultdict

from leakhunter.domain.models import CostComparison, CostLineItem, CostSummary, ResourceCost, ResourceDelta
from leakhunter.domain.money import money, pct


def summarize(period: str, items: list[CostLineItem]) -> CostSummary:
    by_service: dict[str, float] = defaultdict(float)
    by_resource: dict[str, float] = defaultdict(float)
    meta: dict[str, tuple[str, str | None]] = {}
    for item in items:
        by_service[item.service] += item.unblended_cost
        by_resource[item.resource_id] += item.unblended_cost
        meta.setdefault(item.resource_id, (item.service, item.env))
    resources = [
        ResourceCost(resource_id=rid, service=meta[rid][0], env=meta[rid][1], cost=money(cost))
        for rid, cost in by_resource.items()
    ]
    resources.sort(key=lambda r: (-r.cost, r.resource_id))
    return CostSummary(
        period=period,
        total=money(sum(item.unblended_cost for item in items)),
        by_service={service: money(cost) for service, cost in sorted(by_service.items())},
        by_resource=resources,
    )


def resource_cost(summary: CostSummary | None, resource_id: str) -> float | None:
    if summary is None:
        return None
    for entry in summary.by_resource:
        if entry.resource_id == resource_id:
            return entry.cost
    return 0.0


def compare(
    current: CostSummary,
    previous: CostSummary | None,
    *,
    spike_threshold_pct: float,
    top_movers_limit: int,
) -> CostComparison:
    """Compare two periods. Without a previous period, movers are the most expensive resources."""
    if previous is None:
        movers = [
            ResourceDelta(
                resource_id=r.resource_id,
                service=r.service,
                current=r.cost,
                previous=None,
                delta_abs=None,
                delta_pct=None,
            )
            for r in current.by_resource[:top_movers_limit]
        ]
        return CostComparison(
            period=current.period,
            previous_period=None,
            current_total=current.total,
            previous_total=None,
            delta_abs=None,
            delta_pct=None,
            is_spike=False,
            spike_threshold_pct=spike_threshold_pct,
            by_service_current=current.by_service,
            by_service_previous=None,
            top_movers=movers,
        )

    previous_costs = {r.resource_id: r for r in previous.by_resource}
    current_costs = {r.resource_id: r for r in current.by_resource}
    deltas: list[ResourceDelta] = []
    for rid in sorted(set(previous_costs) | set(current_costs)):
        now = current_costs[rid].cost if rid in current_costs else 0.0
        before = previous_costs[rid].cost if rid in previous_costs else 0.0
        service = (current_costs.get(rid) or previous_costs[rid]).service
        delta = money(now - before)
        deltas.append(
            ResourceDelta(
                resource_id=rid,
                service=service,
                current=now,
                previous=before,
                delta_abs=delta,
                delta_pct=pct(delta, before),
            )
        )
    movers = sorted((d for d in deltas if (d.delta_abs or 0) > 0), key=lambda d: (-(d.delta_abs or 0), d.resource_id))
    delta_total = money(current.total - previous.total)
    delta_pct = pct(delta_total, previous.total)
    return CostComparison(
        period=current.period,
        previous_period=previous.period,
        current_total=current.total,
        previous_total=previous.total,
        delta_abs=delta_total,
        delta_pct=delta_pct,
        is_spike=delta_pct is not None and delta_pct >= spike_threshold_pct,
        spike_threshold_pct=spike_threshold_pct,
        by_service_current=current.by_service,
        by_service_previous=previous.by_service,
        top_movers=movers[:top_movers_limit],
    )


def previous_period(period: str) -> str:
    year, month = (int(part) for part in period.split("-"))
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"
