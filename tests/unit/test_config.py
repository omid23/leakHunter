import pytest
from pydantic import ValidationError

from leakhunter.config import Settings
from leakhunter.tools.base import ToolRegistry
from leakhunter.tools.runtime import ToolRuntime


@pytest.mark.parametrize(
    "bad",
    [
        {"tool_max_attempts": 0},
        {"tool_timeout_s": 0},
        {"tool_timeout_s": -1},
        {"max_steps": 0},
        {"malformed_fail_ratio": 1.5},
        {"top_movers_limit": 0},
        {"current_period": "Oct 2026"},
        {"fault_timeout_sleep_s": 0},
    ],
)
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **bad)


def test_empty_now_means_system_clock():
    assert Settings(_env_file=None, now="").now is None
    assert Settings(_env_file=None, now="2026-11-02T09:00:00Z").now.tzinfo is not None


def test_api_keys_are_split_and_trimmed():
    settings = Settings(_env_file=None, viewer_keys=" a , b ,,", operator_keys="op")
    assert settings.viewer_key_set == {"a", "b"} and settings.operator_key_set == {"op"}


def test_runtime_rejects_zero_attempts():
    with pytest.raises(ValueError):
        ToolRuntime(ToolRegistry([]), timeout_s=1, max_attempts=0, backoff_s=0)
