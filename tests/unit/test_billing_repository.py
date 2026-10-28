from datetime import datetime, timezone

import pytest

from leakhunter.data.billing_repository import BillingRepository
from leakhunter.domain.errors import PeriodNotFound
from leakhunter.domain.quality import malformed_ratio_exceeded


@pytest.fixture
def repo(fixtures_dir):
    return BillingRepository(fixtures_dir)


def test_clean_export_parses_every_row(repo):
    export = repo.load("2026-10")
    assert len(export.items) == 217 and export.row_errors == []
    assert export.generated_at == datetime(2026, 11, 1, 6, tzinfo=timezone.utc)
    assert repo.periods() == ["2026-08", "2026-09", "2026-10"]


def test_malformed_export_skips_five_rows_with_reasons(repo):
    export = repo.load_variant("malformed")
    assert len(export.items) == 217
    assert sorted(e.reason for e in export.row_errors) == [
        "invalid_cost",
        "invalid_date",
        "missing_resource_id",
        "negative_cost",
        "wrong_column_count",
    ]
    assert all(e.line_no > 1 and e.raw for e in export.row_errors)
    assert not malformed_ratio_exceeded(len(export.row_errors), export.total_rows, 0.2)


def test_missing_period_raises(repo):
    with pytest.raises(PeriodNotFound):
        repo.load("2025-01")


def test_period_mismatch_rows_are_rejected(tmp_path, fixtures_dir):
    (tmp_path / "manifest.json").write_text(
        '{"exports": {"2026-10": {"file": "b.csv", "generated_at": "2026-11-01T06:00:00Z"}}, "variants": {}}'
    )
    (tmp_path / "b.csv").write_text(
        "bill_period,usage_date,service,usage_type,resource_id,usage_amount,unblended_cost,env\n"
        "2026-10,2026-09-30,AmazonEC2,x,i-1,1,1.0,dev\n"
        "2026-10,2026-10-01,AmazonEC2,x,i-1,abc,1.0,dev\n"
        "2026-10,2026-10-01,AmazonEC2,x,i-1,1,1.5,\n"
    )
    export = BillingRepository(tmp_path).load("2026-10")
    assert [e.reason for e in export.row_errors] == ["period_mismatch", "invalid_usage_amount"]
    assert export.items[0].env is None and export.items[0].unblended_cost == 1.5
