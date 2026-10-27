"""Live smoke test against the real model; skipped unless ANTHROPIC_API_KEY is set (scheduled CI only)."""

import os

import pytest

from leakhunter.bootstrap import build_services
from leakhunter.domain.models import RunStatus
from tests.conftest import make_settings

pytestmark = [
    pytest.mark.anthropic,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


async def test_real_model_produces_grounded_proposals():
    settings = make_settings(llm_provider="anthropic", tool_timeout_s=5.0)
    run = await build_services(settings).service.run_question("Why did our AWS bill spike?")
    assert run.status is RunStatus.awaiting_approval, run.error
    assert run.grounding.ok, run.grounding
    assert {a.resource_id for a in run.proposed_actions} <= {"i-0a1b2c3d4e5f67890", "vol-0f1e2d3c4b5a69788"}
