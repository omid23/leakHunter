"""HTTP routes under /api."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from leakhunter.agent.prompts import PROMPT_VERSION
from leakhunter.api.auth import Principal, Role, require_role
from leakhunter.api.schemas import ApproveRequest, CreateRunRequest, FaultInfo, Health, RejectRequest
from leakhunter.app.service import AgentService
from leakhunter.bootstrap import Services
from leakhunter.domain.models import RunState
from leakhunter.tools.faults import fault_catalog

router = APIRouter(prefix="/api")

SUMMARY_EXCLUDE = {"messages", "evidence", "trace"}


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_service(request: Request) -> AgentService:
    return get_services(request).service


@router.get("/health", response_model=Health)
async def health(services: Services = Depends(get_services)) -> Health:
    fixtures = services.settings.fixtures_dir
    return Health(
        status="ok",
        llm_provider=services.llm.name,
        model=services.llm.model,
        prompt_version=PROMPT_VERSION,
        clock_now=services.clock.now().isoformat(),
        fixtures_ok=(fixtures / "manifest.json").exists() and (fixtures / "resources.json").exists(),
    )


@router.get("/faults", response_model=list[FaultInfo])
async def faults(_: Principal = Depends(require_role(Role.viewer))) -> list[dict[str, str]]:
    return fault_catalog()


@router.post("/runs", response_model=RunState, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: CreateRunRequest,
    _: Principal = Depends(require_role(Role.viewer)),
    service: AgentService = Depends(get_service),
) -> RunState:
    return await service.run_question(body.question, body.faults)


@router.get("/runs")
async def list_runs(
    _: Principal = Depends(require_role(Role.viewer)),
    service: AgentService = Depends(get_service),
) -> list[dict[str, Any]]:
    """Run summaries, newest first (the heavy fields are only on GET /api/runs/{id})."""
    return [run.model_dump(mode="json", exclude=SUMMARY_EXCLUDE) for run in await service.list()]


@router.get("/runs/{run_id}", response_model=RunState)
async def get_run(
    run_id: str,
    _: Principal = Depends(require_role(Role.viewer)),
    service: AgentService = Depends(get_service),
) -> RunState:
    return await service.get(run_id)


@router.post("/runs/{run_id}/approve", response_model=RunState)
async def approve_run(
    run_id: str,
    body: ApproveRequest,
    principal: Principal = Depends(require_role(Role.operator)),
    service: AgentService = Depends(get_service),
) -> RunState:
    return await service.approve(run_id, body.action_ids, principal.actor)


@router.post("/runs/{run_id}/reject", response_model=RunState)
async def reject_run(
    run_id: str,
    body: RejectRequest,
    principal: Principal = Depends(require_role(Role.operator)),
    service: AgentService = Depends(get_service),
) -> RunState:
    return await service.reject(run_id, body.reason, principal.actor)
