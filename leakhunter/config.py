"""Application settings: the single source of truth for every tunable knob (env prefix ``LEAKHUNTER_``)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FIXTURES_DIR = Path(__file__).parent / "data" / "fixtures"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEAKHUNTER_", env_file=".env", extra="ignore")

    # LLM
    llm_provider: Literal["scripted", "anthropic"] = "scripted"
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_fallbacks: bool = True
    llm_timeout_s: float = 60.0
    llm_max_tokens: int = 8000
    max_steps: int = 6
    max_repair_turns: int = 1

    # Time & data
    now: datetime | None = datetime.fromisoformat("2026-11-02T09:00:00+00:00")
    current_period: str = "2026-10"
    fixtures_dir: Path = FIXTURES_DIR
    stale_after_hours: float = 48.0
    malformed_fail_ratio: float = 0.2

    # Analysis
    idle_cpu_threshold_pct: float = 5.0
    min_action_saving_usd: float = 20.0
    spike_threshold_pct: float = 10.0
    top_movers_limit: int = 5

    # Tools
    tool_timeout_s: float = 5.0
    tool_max_attempts: int = 2
    tool_retry_backoff_s: float = 0.2
    fault_timeout_sleep_s: float | None = None  # defaults to 2 x tool_timeout_s

    # API
    viewer_keys: str = "viewer-dev-key"
    operator_keys: str = "operator-dev-key"

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("now", mode="before")
    @classmethod
    def _empty_now_means_system_clock(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def viewer_key_set(self) -> frozenset[str]:
        return _split(self.viewer_keys)

    @property
    def operator_key_set(self) -> frozenset[str]:
        return _split(self.operator_keys)


def _split(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())
