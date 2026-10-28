from datetime import datetime, timezone

from leakhunter.domain.models import BillingExport, RowError
from leakhunter.domain.quality import age_hours, assess, is_stale, malformed_ratio_exceeded

NOW = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)


def test_freshness_against_pinned_clock():
    fresh = datetime(2026, 11, 1, 6, tzinfo=timezone.utc)
    stale = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)
    assert age_hours(fresh, NOW) == 27.0
    assert not is_stale(fresh, NOW, 48)
    assert is_stale(stale, NOW, 48)


def test_malformed_ratio():
    assert not malformed_ratio_exceeded(5, 222, 0.2)
    assert malformed_ratio_exceeded(3, 10, 0.2)
    assert not malformed_ratio_exceeded(0, 0, 0.2)


def test_assess_reports_skipped_rows_by_reason():
    export = BillingExport(
        period="2026-10",
        generated_at=datetime(2026, 11, 1, 6, tzinfo=timezone.utc),
        items=[],
        row_errors=[RowError(line_no=2, reason="negative_cost", raw="x")] * 2,
        source_file="f.csv",
    )
    quality = assess(export, NOW, 48)
    assert quality.skipped_rows == 2
    assert quality.skipped_reasons == {"negative_cost": 2}
    assert quality.warnings == ["2 malformed rows skipped in f.csv"]
    assert not quality.is_stale
