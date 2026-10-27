"""Domain-level exceptions shared by every layer."""

from __future__ import annotations


class LeakHunterError(Exception):
    """Base class for expected, user-visible errors."""


class InvalidTransition(LeakHunterError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"cannot move a run from '{current}' to '{target}'")
        self.current = current
        self.target = target


class RunNotFound(LeakHunterError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run '{run_id}' not found")
        self.run_id = run_id


class UnknownActionId(LeakHunterError):
    def __init__(self, action_ids: list[str]) -> None:
        super().__init__(f"unknown action id(s): {', '.join(action_ids)}")
        self.action_ids = action_ids


class PeriodNotFound(LeakHunterError):
    def __init__(self, period: str) -> None:
        super().__init__(f"no billing export for period '{period}'")
        self.period = period
