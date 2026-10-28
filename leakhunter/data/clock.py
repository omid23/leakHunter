"""Injectable clock so freshness checks are reproducible (decision D13)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._instant


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
