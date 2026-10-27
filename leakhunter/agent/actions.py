"""Action execution port and the mock adapter. Nothing here ever touches a real cloud account."""

from __future__ import annotations

from typing import Protocol

from leakhunter.domain.models import ActionType, ExecutionResult, ProposedAction

MOCK_STATUS = {
    ActionType.stop_instance: "stopped (mock)",
    ActionType.delete_volume: "deleted (mock)",
}


class ActionExecutor(Protocol):
    async def execute(self, action: ProposedAction) -> ExecutionResult: ...


class MockActionExecutor:
    def __init__(self) -> None:
        self.executed: list[ProposedAction] = []

    async def execute(self, action: ProposedAction) -> ExecutionResult:
        self.executed.append(action)
        return ExecutionResult(
            action_id=action.id,
            action=action.action,
            resource_id=action.resource_id,
            status=MOCK_STATUS[action.action],
        )
