"""Failure injection by decoration (decision D8): tools and repositories stay fault-agnostic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

from leakhunter.data.billing_repository import BillingRepository, BillingSource
from leakhunter.domain.models import BillingExport
from leakhunter.tools.base import Tool, ToolSpec

STALE_GENERATED_AT = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)


class FaultKind(str, Enum):
    stale_billing = "stale_billing"
    malformed_rows = "malformed_rows"
    tool_timeout = "tool_timeout"


FAULT_INFO: dict[FaultKind, tuple[str, str]] = {
    FaultKind.stale_billing: (
        "Stale billing export",
        "The October export reports generated_at 2026-10-20 (13 days old); the agent must refuse to analyse it.",
    ),
    FaultKind.malformed_rows: (
        "Malformed billing rows",
        "Five broken rows (negative/N/A cost, missing id, bad date, wrong column count) are mixed into October.",
    ),
    FaultKind.tool_timeout: (
        "Resource tool timeout",
        "get_resource_details hangs past its timeout; the agent must retry, then answer without proposing actions.",
    ),
}


def fault_catalog() -> list[dict[str, str]]:
    return [{"id": kind.value, "label": label, "description": text} for kind, (label, text) in FAULT_INFO.items()]


class StaleBillingSource:
    def __init__(self, inner: BillingSource, generated_at: datetime = STALE_GENERATED_AT) -> None:
        self._inner = inner
        self._generated_at = generated_at

    def periods(self) -> list[str]:
        return self._inner.periods()

    def load(self, period: str) -> BillingExport:
        return self._inner.load(period).model_copy(update={"generated_at": self._generated_at})


class MalformedBillingSource:
    def __init__(self, inner: BillingRepository) -> None:
        self._inner = inner
        self._period = inner.variant_period("malformed")

    def periods(self) -> list[str]:
        return self._inner.periods()

    def load(self, period: str) -> BillingExport:
        return self._inner.load_variant("malformed") if period == self._period else self._inner.load(period)


class SlowTool:
    """Wraps a tool so every call hangs for ``sleep_s`` before delegating."""

    def __init__(self, inner: Tool, sleep_s: float) -> None:
        self._inner = inner
        self._sleep_s = sleep_s
        self.spec: ToolSpec = inner.spec

    async def run(self, args: Any) -> BaseModel:
        await asyncio.sleep(self._sleep_s)
        return await self._inner.run(args)


class FaultInjector:
    def __init__(self, faults: list[FaultKind], timeout_sleep_s: float) -> None:
        self.faults = frozenset(faults)
        self._timeout_sleep_s = timeout_sleep_s

    def billing(self, repository: BillingRepository) -> BillingSource:
        source: BillingSource = repository
        if FaultKind.malformed_rows in self.faults:
            source = MalformedBillingSource(repository)
        if FaultKind.stale_billing in self.faults:
            source = StaleBillingSource(source)
        return source

    def tool(self, tool: Tool) -> Tool:
        if FaultKind.tool_timeout in self.faults and tool.spec.name == "get_resource_details":
            return SlowTool(tool, self._timeout_sleep_s)
        return tool
