from datetime import datetime, timezone

import pytest

from leakhunter.data.billing_repository import BillingRepository
from leakhunter.data.clock import FixedClock
from leakhunter.data.resource_repository import ResourceRepository
from leakhunter.domain.models import ActionType, ErrorKind, Signal
from leakhunter.tools.base import ToolError
from leakhunter.tools.cost_tools import (
    GetCostDataArgs,
    GetCostDataTool,
    GetResourceDetailsArgs,
    GetResourceDetailsTool,
)

IDLE = "i-0a1b2c3d4e5f67890"
VOL = "vol-0f1e2d3c4b5a69788"


@pytest.fixture
def cost_tool(settings, fixtures_dir):
    return GetCostDataTool(BillingRepository(fixtures_dir), FixedClock(settings.now), settings)


@pytest.fixture
def details_tool(settings, fixtures_dir):
    return GetResourceDetailsTool(BillingRepository(fixtures_dir), ResourceRepository(fixtures_dir), settings)


async def test_current_period_comparison_matches_fixture_story(cost_tool):
    result = await cost_tool.run(GetCostDataArgs())
    c = result.comparison
    assert (c.period, c.previous_period) == ("2026-10", "2026-09")
    assert (c.current_total, c.previous_total, c.delta_abs, c.delta_pct) == (1543.27, 818.13, 725.14, 88.63)
    assert c.is_spike
    assert [(m.resource_id, m.delta_abs) for m in c.top_movers[:2]] == [
        (IDLE, 571.39),
        ("asg-web-prod", 134.64),
    ]
    assert result.data_quality.age_hours == 27.0 and not result.data_quality.is_stale


async def test_september_is_not_a_spike(cost_tool):
    c = (await cost_tool.run(GetCostDataArgs(period="2026-09"))).comparison
    assert (c.delta_abs, c.delta_pct, c.is_spike) == (-27.27, -3.23, False)
    assert c.top_movers == []


async def test_missing_previous_period_degrades_to_absolute_figures(cost_tool):
    result = await cost_tool.run(GetCostDataArgs(period="2026-08"))
    assert result.comparison.previous_total is None
    assert result.data_quality.warnings == ["no previous period (2026-07) to compare"]


@pytest.mark.parametrize(("period", "kind"), [("2025-01", ErrorKind.not_found), ("Oct", ErrorKind.invalid_arguments)])
async def test_bad_periods(cost_tool, period, kind):
    with pytest.raises(ToolError) as exc:
        await cost_tool.run(GetCostDataArgs(period=period))
    assert exc.value.kind is kind


async def test_stale_export_is_fatal(settings, fixtures_dir):
    late = FixedClock(datetime(2026, 11, 20, tzinfo=timezone.utc))
    with pytest.raises(ToolError) as exc:
        await GetCostDataTool(BillingRepository(fixtures_dir), late, settings).run(GetCostDataArgs())
    assert exc.value.kind is ErrorKind.stale_data and "2026-11-01" in exc.value.message


async def test_resource_details_produce_findings_with_data_savings(details_tool):
    ids = [IDLE, VOL, "asg-web-prod", "i-0c9d8e7f6a5b43210", "ghost"]
    result = await details_tool.run(GetResourceDetailsArgs(resource_ids=ids))
    by_id = {d.resource.resource_id: d for d in result.details}
    assert result.missing == ["ghost"]
    [idle] = by_id[IDLE].findings
    assert (idle.signal, idle.recommended_action, idle.estimated_monthly_saving) == (
        Signal.idle_instance,
        ActionType.stop_instance,
        571.39,
    )
    assert by_id[IDLE].previous_period_cost == 0.0
    [vol] = by_id[VOL].findings
    assert (vol.recommended_action, vol.estimated_monthly_saving) == (ActionType.delete_volume, 160.0)
    assert by_id["asg-web-prod"].findings == []
    [small] = by_id["i-0c9d8e7f6a5b43210"].findings
    assert small.below_threshold and small.recommended_action is None


async def test_resource_details_rejects_too_many_ids(details_tool):
    with pytest.raises(ToolError):
        await details_tool.run(GetResourceDetailsArgs(resource_ids=[f"i-{n}" for n in range(21)]))
