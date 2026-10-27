"""AgentService: the application use cases. The API and the eval runner both call this one entry point."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from leakhunter.agent.actions import ActionExecutor
from leakhunter.agent.orchestrator import CostAgent
from leakhunter.domain.models import RunState
from leakhunter.state.run_repository import RunRepository
from leakhunter.tools.faults import FaultKind
from leakhunter.tools.runtime import ToolRuntime

ToolRuntimeFactory = Callable[[list[FaultKind]], ToolRuntime]


class AgentService:
    def __init__(
        self,
        agent: CostAgent,
        runs: RunRepository,
        executor: ActionExecutor,
        tools_for: ToolRuntimeFactory,
    ) -> None:
        self.agent = agent
        self.runs = runs
        self.executor = executor
        self.tools_for = tools_for
        self._decision_lock = asyncio.Lock()  # one approval/rejection at a time: no double execution

    async def run_question(self, question: str, faults: list[FaultKind] | None = None) -> RunState:
        faults = list(dict.fromkeys(faults or []))
        run = RunState(question=question, faults=[f.value for f in faults])
        await self.runs.save(run)
        await self.agent.run(run, self.tools_for(faults))
        await self.runs.save(run)
        return run

    async def approve(self, run_id: str, action_ids: list[str], actor: str) -> RunState:
        async with self._decision_lock:
            run = await self.runs.get(run_id)
            await self.agent.approve(run, action_ids, actor, self.executor)
            await self.runs.save(run)
            return run

    async def reject(self, run_id: str, reason: str | None, actor: str) -> RunState:
        async with self._decision_lock:
            run = await self.runs.get(run_id)
            self.agent.reject(run, reason, actor)
            await self.runs.save(run)
            return run

    async def get(self, run_id: str) -> RunState:
        return await self.runs.get(run_id)

    async def list(self) -> list[RunState]:
        return await self.runs.list()
