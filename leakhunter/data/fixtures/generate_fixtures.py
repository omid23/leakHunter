"""Reproducible generator for the committed billing fixtures (``python generate_fixtures.py``).

The CSVs are committed; tests never depend on this script. It encodes the story in PLAN.md §7:
a new idle EC2 instance appears in October, an EBS volume has been unattached since 2026-10-01,
and the production autoscaling group grew legitimately.
"""

from __future__ import annotations

import calendar
import csv
import json
from pathlib import Path

from leakhunter.data.billing_repository import COLUMNS as HEADER

HERE = Path(__file__).parent
GENERATED_AT = "2026-11-01T06:00:00Z"

# resource_id -> (service, usage_type, env, daily usage amount, {period: daily cost})
RESOURCES: dict[str, tuple[str, str, str, float, dict[str, str]]] = {
    "i-0a1b2c3d4e5f67890": (
        "AmazonEC2",
        "BoxUsage:m5.4xlarge",
        "dev",
        24,
        {"2026-10": "18.4320"},
    ),
    "vol-0f1e2d3c4b5a69788": (
        "AmazonEC2",
        "EBS:VolumeUsage.gp3",
        "dev",
        64.5161,
        {"2026-08": "5.1613", "2026-09": "5.1613", "2026-10": "5.1613"},
    ),
    "asg-web-prod": (
        "AmazonEC2",
        "BoxUsage:m5.large",
        "prod",
        48,
        {"2026-08": "8.1600", "2026-09": "8.1600", "2026-10": "12.2400"},
    ),
    "db-prod-main": (
        "AmazonRDS",
        "InstanceUsage:db.r5.large",
        "prod",
        24,
        {"2026-08": "8.4000", "2026-09": "8.4000", "2026-10": "8.4000"},
    ),
    "s3-data-lake": (
        "AmazonS3",
        "TimedStorage-ByteHrs",
        "prod",
        6000,
        {"2026-08": "4.2000", "2026-09": "4.2000", "2026-10": "4.2000"},
    ),
    "i-0c9d8e7f6a5b43210": (
        "AmazonEC2",
        "BoxUsage:t3.micro",
        "dev",
        24,
        {"2026-08": "0.2496", "2026-09": "0.2496", "2026-10": "0.2496"},
    ),
    "nat-0123456789abcdef0": (
        "AmazonVPC",
        "NatGateway-Hours",
        "prod",
        24,
        {"2026-08": "1.1000", "2026-09": "1.1000", "2026-10": "1.1000"},
    ),
}

PERIODS = ["2026-08", "2026-09", "2026-10"]

# The five deliberately broken rows appended to the malformed October export.
MALFORMED_ROWS = [
    ["2026-10", "2026-10-15", "AmazonEC2", "BoxUsage:m5.large", "asg-web-prod", "48", "-12.2400", "prod"],
    ["2026-10", "2026-10-16", "AmazonRDS", "InstanceUsage:db.r5.large", "db-prod-main", "24", "N/A", "prod"],
    ["2026-10", "2026-10-17", "AmazonS3", "TimedStorage-ByteHrs", "", "6000", "4.2000", "prod"],
    [
        "2026-10",
        "2026-13-45",
        "AmazonVPC",
        "NatGateway-Hours",
        "nat-0123456789abcdef0",
        "24",
        "1.1000",
        "prod",
    ],
    ["2026-10", "2026-10-18", "AmazonEC2", "BoxUsage:t3.micro", "i-0c9d8e7f6a5b43210", "24"],
]


def rows_for(period: str) -> list[list[str]]:
    year, month = (int(part) for part in period.split("-"))
    days = calendar.monthrange(year, month)[1]
    rows = []
    for day in range(1, days + 1):
        for resource_id, (service, usage_type, env, amount, costs) in RESOURCES.items():
            if period in costs:
                usage_date = f"{period}-{day:02d}"
                rows.append([period, usage_date, service, usage_type, resource_id, str(amount), costs[period], env])
    return rows


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


def main() -> None:
    manifest: dict[str, dict[str, dict[str, str]]] = {"exports": {}, "variants": {}}
    for period in PERIODS:
        name = f"billing_{period}.csv"
        write_csv(HERE / name, rows_for(period))
        manifest["exports"][period] = {"file": name, "generated_at": GENERATED_AT}
    malformed = "billing_2026-10_malformed.csv"
    write_csv(HERE / malformed, rows_for("2026-10") + MALFORMED_ROWS)
    manifest["variants"]["malformed"] = {"period": "2026-10", "file": malformed, "generated_at": GENERATED_AT}
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
