"""Data-quality policy: export freshness and the malformed-row ratio."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from leakhunter.domain.models import BillingExport, DataQuality


def age_hours(generated_at: datetime, now: datetime) -> float:
    return round((now - generated_at).total_seconds() / 3600, 1)


def is_stale(generated_at: datetime, now: datetime, stale_after_hours: float) -> bool:
    return age_hours(generated_at, now) > stale_after_hours


def malformed_ratio_exceeded(skipped: int, total: int, fail_ratio: float) -> bool:
    return total > 0 and skipped / total > fail_ratio


def assess(export: BillingExport, now: datetime, stale_after_hours: float) -> DataQuality:
    age = age_hours(export.generated_at, now)
    skipped = len(export.row_errors)
    warnings = []
    if skipped:
        warnings.append(f"{skipped} malformed rows skipped in {export.source_file}")
    return DataQuality(
        source_file=export.source_file,
        generated_at=export.generated_at,
        age_hours=age,
        is_stale=age > stale_after_hours,
        total_rows=export.total_rows,
        skipped_rows=skipped,
        skipped_reasons=dict(Counter(error.reason for error in export.row_errors)),
        warnings=warnings,
    )
