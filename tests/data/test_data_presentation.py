"""Source data is transformed and presented correctly: independent csv-module figures == tool == API == narrative."""

import csv
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

import pytest

from leakhunter.config import FIXTURES_DIR
from tests.conftest import VIEWER

IDLE = "i-0a1b2c3d4e5f67890"
VOL = "vol-0f1e2d3c4b5a69788"


def independent_totals(period: str) -> tuple[Decimal, dict[str, Decimal]]:
    """Exact Decimal sums straight from the CSV, sharing no code with the application."""
    total = Decimal(0)
    by_resource: dict[str, Decimal] = defaultdict(Decimal)
    with (FIXTURES_DIR / f"billing_{period}.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            cost = Decimal(row["unblended_cost"])
            total += cost
            by_resource[row["resource_id"]] += cost
    return total, by_resource


def cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@pytest.mark.parametrize(("period", "expected"), [("2026-08", 845.40), ("2026-09", 818.13), ("2026-10", 1543.27)])
def test_independent_totals_match_the_plan(period, expected):
    assert cents(independent_totals(period)[0]) == expected


def test_api_figures_equal_independent_figures(client):
    oct_total, oct_res = independent_totals("2026-10")
    sep_total, sep_res = independent_totals("2026-09")
    run = client.post("/api/runs", json={"question": "Why did our AWS bill spike?"}, headers=VIEWER).json()
    run = client.get(f"/api/runs/{run['id']}", headers=VIEWER).json()
    comparison = run["evidence"]["get_cost_data"][0]["comparison"]

    assert comparison["current_total"] == cents(oct_total)
    assert comparison["previous_total"] == cents(sep_total)
    assert comparison["delta_abs"] == cents(oct_total - sep_total) == 725.14
    movers = {m["resource_id"]: m["delta_abs"] for m in comparison["top_movers"]}
    assert movers[IDLE] == cents(oct_res[IDLE] - sep_res[IDLE]) == 571.39
    assert movers["asg-web-prod"] == cents(oct_res["asg-web-prod"] - sep_res["asg-web-prod"]) == 134.64

    savings = {a["resource_id"]: a["estimated_monthly_saving"] for a in run["proposed_actions"]}
    assert savings == {IDLE: cents(oct_res[IDLE]), VOL: cents(oct_res[VOL])}  # saving = period cost (A7)
    assert round(sum(savings.values()), 2) == 731.39

    summary = run["report"]["summary"]
    for figure in ("$818.13", "$1,543.27", "+$725.14", "+88.6%", "$731.39"):
        assert figure in summary


def test_all_presented_money_has_two_decimals(client):
    run = client.post("/api/runs", json={"question": "Why did our AWS bill spike?"}, headers=VIEWER).json()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, float) and (
                    "cost" in key or "total" in key or "saving" in key or key == "delta_abs"
                ):
                    assert round(value, 2) == value, (key, value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(run["evidence"])
    walk(run["proposed_actions"])
