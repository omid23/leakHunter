from datetime import datetime, timezone

import pytest

from leakhunter.domain.models import ActionType, Resource, Signal
from leakhunter.domain.rules import RuleContext, evaluate

CTX = RuleContext(idle_cpu_threshold_pct=5.0, min_action_saving_usd=20.0, period="2026-10")


def resource(**overrides) -> Resource:
    base = dict(
        resource_id="i-1",
        service="ec2",
        resource_type="instance",
        state="running",
        region="us-east-1",
        env="dev",
        name="n",
        avg_cpu_7d_pct=2.0,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Resource(**base)


def test_idle_instance_yields_stop_action_with_period_cost_as_saving():
    [finding] = evaluate(resource(), 571.392, CTX)
    assert finding.signal is Signal.idle_instance
    assert finding.recommended_action is ActionType.stop_instance
    assert finding.estimated_monthly_saving == 571.39
    assert finding.evidence["avg_cpu_7d_pct"] == 2.0
    assert not finding.below_threshold


@pytest.mark.parametrize(
    "overrides",
    [
        {"avg_cpu_7d_pct": 5.0},  # threshold is strict
        {"avg_cpu_7d_pct": 63.0},
        {"state": "stopped"},
        {"avg_cpu_7d_pct": None},
        {"resource_type": "autoscaling_group"},
    ],
)
def test_healthy_or_non_instance_resources_yield_nothing(overrides):
    assert evaluate(resource(**overrides), 500, CTX) == []


def test_unattached_volume_yields_delete_action():
    vol = resource(resource_id="vol-1", resource_type="volume", state="available", avg_cpu_7d_pct=None)
    [finding] = evaluate(vol, 160.0003, CTX)
    assert finding.signal is Signal.unattached_volume
    assert finding.recommended_action is ActionType.delete_volume
    assert finding.estimated_monthly_saving == 160.0


def test_attached_volume_is_fine():
    vol = resource(resource_type="volume", state="in-use", attached_to="i-9", avg_cpu_7d_pct=None)
    assert evaluate(vol, 160, CTX) == []


def test_below_threshold_finding_is_reported_without_action():
    [finding] = evaluate(resource(avg_cpu_7d_pct=0.8), 7.7376, CTX)
    assert finding.below_threshold
    assert finding.recommended_action is None
    assert finding.estimated_monthly_saving == 7.74
