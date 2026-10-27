import pytest

from leakhunter.data.billing_repository import BillingRepository
from leakhunter.data.resource_repository import ResourceRepository
from leakhunter.domain.models import ErrorKind
from leakhunter.tools.base import ToolRegistry
from leakhunter.tools.cost_tools import GetResourceDetailsTool
from leakhunter.tools.faults import STALE_GENERATED_AT, FaultInjector, FaultKind, SlowTool, fault_catalog
from leakhunter.tools.runtime import ToolRuntime


@pytest.fixture
def repo(fixtures_dir):
    return BillingRepository(fixtures_dir)


def test_catalog_lists_every_fault_once():
    ids = [f["id"] for f in fault_catalog()]
    assert ids == [k.value for k in FaultKind]
    assert all(f["label"] and f["description"] for f in fault_catalog())


def test_no_faults_means_undecorated(repo):
    assert FaultInjector([], 1).billing(repo) is repo


def test_stale_fault_overrides_generated_at(repo):
    source = FaultInjector([FaultKind.stale_billing], 1).billing(repo)
    assert source.load("2026-10").generated_at == STALE_GENERATED_AT


def test_malformed_fault_swaps_current_period_only(repo):
    source = FaultInjector([FaultKind.malformed_rows], 1).billing(repo)
    assert len(source.load("2026-10").row_errors) == 5
    assert source.load("2026-09").row_errors == []


def test_faults_compose(repo):
    source = FaultInjector([FaultKind.malformed_rows, FaultKind.stale_billing], 1).billing(repo)
    export = source.load("2026-10")
    assert export.generated_at == STALE_GENERATED_AT and len(export.row_errors) == 5


async def test_timeout_fault_wraps_only_resource_details(settings, repo, fixtures_dir):
    tool = GetResourceDetailsTool(repo, ResourceRepository(fixtures_dir), settings)
    wrapped = FaultInjector([FaultKind.tool_timeout], 0.2).tool(tool)
    assert isinstance(wrapped, SlowTool) and wrapped.spec is tool.spec
    runtime = ToolRuntime(ToolRegistry([wrapped]), timeout_s=0.05, max_attempts=2, backoff_s=0)
    result = await runtime.call(
        "get_resource_details", {"resource_ids": ["x"], "period": "current"}, step=1, trace=lambda e: None
    )
    assert result.error_kind is ErrorKind.timeout and result.attempts == 2
