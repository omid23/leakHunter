from __future__ import annotations

import pytest

from leakhunter.config import FIXTURES_DIR, Settings


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_provider="scripted",
        tool_timeout_s=0.05,
        tool_retry_backoff_s=0.0,
        fault_timeout_sleep_s=0.2,
        log_json=False,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def services(settings):
    from leakhunter.bootstrap import build_services

    return build_services(settings)


@pytest.fixture
def service(services):
    return services.service
