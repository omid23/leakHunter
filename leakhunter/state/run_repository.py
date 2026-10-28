"""Run persistence port and the in-memory adapter (decision D6: swap for a database without touching callers)."""

from __future__ import annotations

import asyncio
from typing import Protocol

from leakhunter.domain.errors import RunNotFound
from leakhunter.domain.models import RunState


class RunRepository(Protocol):
    async def save(self, run: RunState) -> None: ...

    async def get(self, run_id: str) -> RunState: ...

    async def list(self) -> list[RunState]: ...


class InMemoryRunRepository:
    """Stores deep copies so callers can never mutate persisted state by accident."""

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = asyncio.Lock()

    async def save(self, run: RunState) -> None:
        async with self._lock:
            self._runs[run.id] = run.model_copy(deep=True)

    async def get(self, run_id: str) -> RunState:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            return run.model_copy(deep=True)

    async def list(self) -> list[RunState]:
        """Newest first by insertion order: wall-clock timestamps tie on coarse clocks (Windows ticks every 15 ms)."""
        async with self._lock:
            return [run.model_copy(deep=True) for run in reversed(self._runs.values())]
