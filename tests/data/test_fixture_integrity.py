"""Fixture integrity, checked independently of the application code with the stdlib csv module."""

import csv
import json
from collections import Counter

import pytest

from leakhunter.config import FIXTURES_DIR
from leakhunter.data.billing_repository import COLUMNS

CLEAN = {"2026-08": 186, "2026-09": 180, "2026-10": 217}


def rows(name: str) -> list[list[str]]:
    with (FIXTURES_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


@pytest.mark.parametrize(("period", "count"), CLEAN.items())
def test_clean_exports_have_expected_columns_rows_and_unique_keys(period, count):
    data = rows(f"billing_{period}.csv")
    assert data[0] == COLUMNS
    body = data[1:]
    assert len(body) == count
    keys = Counter((r[1], r[4]) for r in body)
    assert max(keys.values()) == 1  # one row per resource per day
    assert {r[0] for r in body} == {period}


def test_every_billed_resource_is_in_the_inventory():
    inventory = {
        r["resource_id"] for r in json.loads((FIXTURES_DIR / "resources.json").read_text())["resources"]
    }
    billed = {r[4] for period in CLEAN for r in rows(f"billing_{period}.csv")[1:]}
    assert billed <= inventory


def test_malformed_variant_is_clean_october_plus_five_bad_rows():
    malformed = rows("billing_2026-10_malformed.csv")
    clean = rows("billing_2026-10.csv")
    assert malformed[: len(clean)] == clean
    assert len(malformed) - len(clean) == 5


def test_manifest_lists_every_file():
    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text())
    files = {e["file"] for e in manifest["exports"].values()} | {
        e["file"] for e in manifest["variants"].values()
    }
    assert all((FIXTURES_DIR / f).exists() for f in files)
