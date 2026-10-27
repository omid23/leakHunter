from leakhunter.data.clock import FixedClock, SystemClock
from leakhunter.data.resource_repository import ResourceRepository


def test_inventory_lookup_reports_missing_ids(fixtures_dir):
    repo = ResourceRepository(fixtures_dir)
    found, missing = repo.get_many(["i-0a1b2c3d4e5f67890", "nope"])
    assert [r.name for r in found] == ["ml-batch-worker"]
    assert missing == ["nope"]
    assert len(repo.all()) == 7


def test_clocks_are_timezone_aware():
    from datetime import datetime

    assert FixedClock(datetime(2026, 11, 2, 9)).now().tzinfo is not None
    assert SystemClock().now().tzinfo is not None
