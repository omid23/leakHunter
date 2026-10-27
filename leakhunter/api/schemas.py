"""Request DTOs. Responses are the domain ``RunState`` itself (decision D14)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from leakhunter.tools.faults import FaultKind


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    faults: list[FaultKind] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ids: list[str] = Field(min_length=1, max_length=50)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class FaultInfo(BaseModel):
    id: str
    label: str
    description: str


class Health(BaseModel):
    status: str
    llm_provider: str
    model: str
    prompt_version: str
    clock_now: str
    fixtures_ok: bool
