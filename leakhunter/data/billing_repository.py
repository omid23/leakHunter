"""CSV billing exports -> validated ``BillingExport`` (good rows + ``RowError`` per rejected row)."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from leakhunter.domain.errors import PeriodNotFound
from leakhunter.domain.models import BillingExport, CostLineItem, RowError

COLUMNS = [
    "bill_period",
    "usage_date",
    "service",
    "usage_type",
    "resource_id",
    "usage_amount",
    "unblended_cost",
    "env",
]


class BillingSource(Protocol):
    def periods(self) -> list[str]: ...

    def load(self, period: str) -> BillingExport: ...


class BillingRepository:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = Path(fixtures_dir)
        self._manifest = json.loads((self._dir / "manifest.json").read_text(encoding="utf-8"))

    def periods(self) -> list[str]:
        return sorted(self._manifest["exports"])

    def load(self, period: str) -> BillingExport:
        entry = self._manifest["exports"].get(period)
        if entry is None:
            raise PeriodNotFound(period)
        return self.load_file(period, entry["file"], _parse_ts(entry["generated_at"]))

    def load_variant(self, name: str) -> BillingExport:
        entry = self._manifest["variants"][name]
        return self.load_file(entry["period"], entry["file"], _parse_ts(entry["generated_at"]))

    def variant_period(self, name: str) -> str:
        return str(self._manifest["variants"][name]["period"])

    def load_file(self, period: str, filename: str, generated_at: datetime) -> BillingExport:
        items: list[CostLineItem] = []
        errors: list[RowError] = []
        with (self._dir / filename).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if header != COLUMNS:
                raise ValueError(f"{filename}: unexpected header {header}")
            for row in reader:
                line_no = reader.line_num
                item, reason = _parse_row(row, period)
                if item is not None:
                    items.append(item)
                else:
                    errors.append(RowError(line_no=line_no, reason=reason or "invalid", raw=",".join(row)))
        return BillingExport(
            period=period, generated_at=generated_at, items=items, row_errors=errors, source_file=filename
        )


def _parse_row(row: list[str], period: str) -> tuple[CostLineItem | None, str | None]:
    if len(row) != len(COLUMNS):
        return None, "wrong_column_count"
    record = dict(zip(COLUMNS, row, strict=True))
    if not record["resource_id"].strip():
        return None, "missing_resource_id"
    try:
        usage_date = date.fromisoformat(record["usage_date"])
    except ValueError:
        return None, "invalid_date"
    if record["bill_period"] != period or usage_date.strftime("%Y-%m") != period:
        return None, "period_mismatch"
    try:
        cost = float(record["unblended_cost"])
    except ValueError:
        return None, "invalid_cost"
    if cost < 0:
        return None, "negative_cost"
    try:
        amount = float(record["usage_amount"])
    except ValueError:
        return None, "invalid_usage_amount"
    return (
        CostLineItem(
            usage_date=usage_date,
            service=record["service"],
            usage_type=record["usage_type"],
            resource_id=record["resource_id"].strip(),
            usage_amount=amount,
            unblended_cost=cost,
            env=record["env"] or None,
        ),
        None,
    )


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
