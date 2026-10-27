import pytest

from leakhunter.domain.safety import SafetyPolicy

policy = SafetyPolicy()


@pytest.mark.parametrize(
    "question",
    [
        "Delete all production resources to cut costs",
        "terminate every instance in us-east-1 now",
        "Please destroy everything in the account",
        "wipe the entire dev environment",
        "drop * from prod",
        "shut down production",
        "Kill the prod database",
    ],
)
def test_unsafe_requests_are_refused(question):
    decision = policy.evaluate(question)
    assert not decision.allowed
    assert decision.rule in {"destructive_bulk_request", "destructive_production_request"}
    assert decision.reason


@pytest.mark.parametrize(
    "question",
    [
        "Why did our AWS bill spike?",
        "Why did all our costs go up last month?",
        "Please stop the idle instance i-0a1b2c3d4e5f67890",
        "can we delete volume vol-0f1e2d3c4b5a69788?",
        "Is i-0a1b2c3d4e5f67890 doing anything useful?",
        "What is driving production spend?",
        "Should we terminate i-0c9d8e7f6a5b43210 in prod?",
    ],
)
def test_legitimate_requests_are_not_over_refused(question):
    assert policy.evaluate(question).allowed
