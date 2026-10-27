"""JSON resource inventory -> ``Resource`` models."""

from __future__ import annotations

import json
from pathlib import Path

from leakhunter.domain.models import Resource


class ResourceRepository:
    def __init__(self, fixtures_dir: Path) -> None:
        raw = json.loads((Path(fixtures_dir) / "resources.json").read_text(encoding="utf-8"))
        self._resources = {r["resource_id"]: Resource.model_validate(r) for r in raw["resources"]}

    def all(self) -> list[Resource]:
        return list(self._resources.values())

    def get_many(self, resource_ids: list[str]) -> tuple[list[Resource], list[str]]:
        found = [self._resources[rid] for rid in resource_ids if rid in self._resources]
        missing = [rid for rid in resource_ids if rid not in self._resources]
        return found, missing
