from datetime import date

from leakhunter.domain.analysis import compare, previous_period, resource_cost, summarize
from leakhunter.domain.models import CostLineItem


def item(rid: str, cost: float, service: str = "AmazonEC2", day: int = 1, period: str = "2026-10") -> CostLineItem:
    year, month = (int(p) for p in period.split("-"))
    return CostLineItem(
        usage_date=date(year, month, day),
        service=service,
        usage_type="x",
        resource_id=rid,
        usage_amount=1,
        unblended_cost=cost,
        env="dev",
    )


def test_summarize_totals_by_service_and_resource():
    summary = summarize("2026-10", [item("a", 1.25), item("a", 1.25, day=2), item("b", 3.0, service="AmazonS3")])
    assert summary.total == 5.5
    assert summary.by_service == {"AmazonEC2": 2.5, "AmazonS3": 3.0}
    assert [(r.resource_id, r.cost) for r in summary.by_resource] == [("b", 3.0), ("a", 2.5)]


def test_compare_detects_spike_and_ranks_positive_movers():
    previous = summarize("2026-09", [item("a", 10, period="2026-09"), item("b", 10, period="2026-09")])
    current = summarize("2026-10", [item("a", 30), item("b", 8), item("new", 5)])
    result = compare(current, previous, spike_threshold_pct=10, top_movers_limit=5)
    assert result.delta_abs == 23.0
    assert result.delta_pct == 115.0
    assert result.is_spike
    assert [m.resource_id for m in result.top_movers] == ["a", "new"]
    new = result.top_movers[1]
    assert new.previous == 0.0 and new.delta_pct is None  # zero previous: no percentage


def test_compare_below_threshold_is_not_a_spike():
    previous = summarize("2026-09", [item("a", 100, period="2026-09")])
    current = summarize("2026-10", [item("a", 105)])
    assert not compare(current, previous, spike_threshold_pct=10, top_movers_limit=5).is_spike


def test_compare_without_previous_period_uses_absolute_figures():
    current = summarize("2026-10", [item("a", 1), item("b", 9)])
    result = compare(current, None, spike_threshold_pct=10, top_movers_limit=1)
    assert result.previous_total is None and result.delta_abs is None and not result.is_spike
    assert [m.resource_id for m in result.top_movers] == ["b"]


def test_resource_cost_and_previous_period():
    summary = summarize("2026-10", [item("a", 2)])
    assert resource_cost(summary, "a") == 2.0
    assert resource_cost(summary, "missing") == 0.0
    assert resource_cost(None, "a") is None
    assert previous_period("2026-10") == "2026-09"
    assert previous_period("2026-01") == "2025-12"
