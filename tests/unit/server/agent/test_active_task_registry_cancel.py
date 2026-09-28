"""Regression tests for two bugs found reviewing the cancellation-signal code.

1. ``cancel_local`` must raise ``TaskNotCancelableError`` rather than return
   an already-terminal task, even in the race where the SDK's own
   ``ActiveTask.cancel`` finds the run has already reached its own outcome
   and silently returns that outcome instead of raising - it awaits
   ``task_manager.get_task()`` before re-checking its finished flag, so a
   run that completes in that window is answered as-is, not as an error.
2. A signaled cancel that fails for a reason other than the run having
   already finished must undo the ownership provider's one-shot delivery
   bookkeeping, or the request is never retried until the much slower
   reaper backstop forces the task closed without a graceful teardown.
"""

import pytest
from a2a.types import Task, TaskState, TaskStatus
from a2a.utils.errors import TaskNotCancelableError
from unittest.mock import AsyncMock, Mock, patch

from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


@pytest.fixture
def execution_scope():
    """Provides the execution scope the registry stores the task manager in."""
    init_execution_scope()
    yield
    clear_execution_scope()


def _task(state: TaskState) -> Task:
    return Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))


async def _registry_holding(state: TaskState):
    """Build a registry with one registered task and a mocked ownership provider.

    ``_is_finished`` is set to "not finished" so ``cancel_local`` reaches the
    SDK-shaped ``.cancel(...)`` call instead of short-circuiting on the
    cheap check - the whole point of these tests is what happens once it does.
    """
    store = AsyncMock()
    store.get.return_value = _task(state)
    ownership = Mock()
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=store,
        push_sender=None,
        ownership_provider=ownership,
    )

    with patch(
        "aion.server.agent.execution.active_task_registry.ActiveTask"
    ) as active_task_cls:
        active_task_cls.return_value.start = AsyncMock()
        active_task_cls.return_value.aclose = AsyncMock()
        active_task_cls.return_value._is_finished.is_set.return_value = False
        await registry.get_or_create(
            TASK_ID,
            call_context=Mock(),
            context_id=CONTEXT_ID,
        )

    return registry, ownership, active_task_cls.return_value


class TestCancelLocalAgainstARaceWithNaturalCompletion:
    @pytest.mark.anyio
    async def test_raises_when_the_sdk_returns_an_already_completed_task(self, execution_scope):
        """The SDK does not raise for this race - the registry must, to keep
        on_cancel_task's contract that an already-terminal task is always an
        error, never inferred from the returned state."""
        registry, _ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        active_task.cancel = AsyncMock(return_value=_task(TaskState.TASK_STATE_COMPLETED))

        with pytest.raises(TaskNotCancelableError):
            await registry.cancel_local(TASK_ID, Mock())

    @pytest.mark.anyio
    async def test_raises_for_every_terminal_state_but_canceled(self, execution_scope):
        registry, _ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        for state in (
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_REJECTED,
        ):
            active_task.cancel = AsyncMock(return_value=_task(state))
            with pytest.raises(TaskNotCancelableError):
                await registry.cancel_local(TASK_ID, Mock())

    @pytest.mark.anyio
    async def test_returns_the_task_when_it_actually_canceled(self, execution_scope):
        """The ordinary, non-racing outcome must still pass straight through."""
        registry, _ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        active_task.cancel = AsyncMock(return_value=_task(TaskState.TASK_STATE_CANCELED))

        result = await registry.cancel_local(TASK_ID, Mock())

        assert result.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_returns_none_when_nothing_is_running_here(self, execution_scope):
        store = AsyncMock()
        registry = AionActiveTaskRegistry(agent_executor=Mock(), task_store=store, push_sender=None)

        assert await registry.cancel_local("some-other-task", Mock()) is None


class TestSignaledCancelRetriesAfterAFailedAttempt:
    @pytest.mark.anyio
    async def test_forgets_the_signal_on_an_unexpected_failure(self, execution_scope):
        """A failure that is not 'the run already finished' must be retriable
        on the very next heartbeat renewal, not left to the reaper's much
        slower, non-graceful CANCEL_TIMEOUT backstop."""
        registry, ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        active_task.cancel = AsyncMock(side_effect=RuntimeError("transient failure"))

        await registry._run_signaled_cancel(TASK_ID, active_task, Mock())

        ownership.forget_control_signal.assert_called_once_with(TASK_ID)

    @pytest.mark.anyio
    async def test_does_not_forget_the_signal_when_the_run_already_finished(self, execution_scope):
        """Nothing is left to retry once the run reached its own outcome."""
        registry, ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        active_task.cancel = AsyncMock(
            side_effect=TaskNotCancelableError(message="already terminal")
        )

        await registry._run_signaled_cancel(TASK_ID, active_task, Mock())

        ownership.forget_control_signal.assert_not_called()

    @pytest.mark.anyio
    async def test_does_not_forget_the_signal_on_success(self, execution_scope):
        registry, ownership, active_task = await _registry_holding(TaskState.TASK_STATE_WORKING)
        active_task.cancel = AsyncMock(return_value=_task(TaskState.TASK_STATE_CANCELED))

        await registry._run_signaled_cancel(TASK_ID, active_task, Mock())

        ownership.forget_control_signal.assert_not_called()
