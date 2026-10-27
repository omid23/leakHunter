"""Composition root: the only place adapters are chosen and wired together."""

from __future__ import annotations

from dataclasses import dataclass

from leakhunter.agent.actions import ActionExecutor, MockActionExecutor
from leakhunter.agent.llm import LLMClient
from leakhunter.agent.orchestrator import CostAgent
from leakhunter.agent.scripted_client import ScriptedLLMClient
from leakhunter.app.service import AgentService
from leakhunter.config import Settings
from leakhunter.data.billing_repository import BillingRepository
from leakhunter.data.clock import Clock, FixedClock, SystemClock
from leakhunter.data.resource_repository import ResourceRepository
from leakhunter.state.run_repository import InMemoryRunRepository
from leakhunter.tools.base import ToolRegistry
from leakhunter.tools.cost_tools import GetCostDataTool, GetResourceDetailsTool
from leakhunter.tools.faults import FaultInjector, FaultKind
from leakhunter.tools.runtime import ToolRuntime


@dataclass
class Services:
    settings: Settings
    clock: Clock
    llm: LLMClient
    executor: ActionExecutor
    service: AgentService


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        from leakhunter.agent.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(settings)
    return ScriptedLLMClient()


def build_tools_factory(settings: Settings, clock: Clock):  # type: ignore[no-untyped-def]
    billing = BillingRepository(settings.fixtures_dir)
    resources = ResourceRepository(settings.fixtures_dir)
    timeout_sleep = settings.fault_timeout_sleep_s or settings.tool_timeout_s * 2

    def tools_for(faults: list[FaultKind]) -> ToolRuntime:
        injector = FaultInjector(faults, timeout_sleep)
        source = injector.billing(billing)
        registry = ToolRegistry(
            [
                injector.tool(GetCostDataTool(source, clock, settings)),
                injector.tool(GetResourceDetailsTool(source, resources, settings)),
            ]
        )
        return ToolRuntime(
            registry,
            timeout_s=settings.tool_timeout_s,
            max_attempts=settings.tool_max_attempts,
            backoff_s=settings.tool_retry_backoff_s,
        )

    return tools_for


def build_services(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    executor: ActionExecutor | None = None,
) -> Services:
    settings = settings or Settings()
    clock: Clock = FixedClock(settings.now) if settings.now else SystemClock()
    llm = llm or build_llm(settings)
    executor = executor or MockActionExecutor()
    agent = CostAgent(llm, settings=settings, clock=clock)
    service = AgentService(agent, InMemoryRunRepository(), executor, build_tools_factory(settings, clock))
    return Services(settings=settings, clock=clock, llm=llm, executor=executor, service=service)
