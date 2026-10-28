import pytest

from leakhunter.agent.actions import MockActionExecutor
from leakhunter.domain.errors import InvalidTransition, RunNotFound
from leakhunter.domain.models import ActionType, ErrorKind, ProposedAction, RunState, RunStatus, Signal
from leakhunter.state.run_repository import InMemoryRunRepository


@pytest.mark.parametrize(
    "path",
    [
        [RunStatus.refused],
        [RunStatus.running, RunStatus.completed],
        [RunStatus.running, RunStatus.failed],
        [RunStatus.running, RunStatus.awaiting_approval, RunStatus.completed],
        [RunStatus.running, RunStatus.awaiting_approval, RunStatus.executing, RunStatus.completed],
    ],
)
def test_legal_lifecycles(path):
    run = RunState(question="q")
    for status in path:
        run.transition_to(status)
    assert run.status is path[-1]


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([], RunStatus.awaiting_approval),
        ([RunStatus.running, RunStatus.completed], RunStatus.executing),
        ([RunStatus.refused], RunStatus.running),
        ([RunStatus.running], RunStatus.executing),  # approval cannot be skipped
    ],
)
def test_illegal_transitions_raise(path, illegal):
    run = RunState(question="q")
    for status in path:
        run.transition_to(status)
    with pytest.raises(InvalidTransition):
        run.transition_to(illegal)


def test_fail_records_error():
    run = RunState(question="q")
    run.transition_to(RunStatus.running)
    run.fail(ErrorKind.stale_data, "old")
    assert run.status is RunStatus.failed and run.error.kind is ErrorKind.stale_data


async def test_repository_round_trip_isolated_copies_and_ordering():
    repo = InMemoryRunRepository()
    first, second = RunState(question="a"), RunState(question="b")
    await repo.save(first)
    await repo.save(second)
    loaded = await repo.get(first.id)
    loaded.question = "mutated"
    assert (await repo.get(first.id)).question == "a"
    assert [r.id for r in await repo.list()] == [second.id, first.id]
    with pytest.raises(RunNotFound):
        await repo.get("missing")


async def test_mock_executor_never_does_anything_real():
    executor = MockActionExecutor()
    action = ProposedAction(
        id="act_1",
        action=ActionType.delete_volume,
        resource_id="vol-1",
        estimated_monthly_saving=1.0,
        rationale="r",
        signal=Signal.unattached_volume,
    )
    result = await executor.execute(action)
    assert result.status == "deleted (mock)" and executor.executed == [action]
