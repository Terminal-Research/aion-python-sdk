import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState
from a2a.utils.errors import TaskNotCancelableError, TaskNotFoundError, UnsupportedOperationError

from aion.server.agent.execution import AionAgentRequestExecutor


def _make_task(state: TaskState = TaskState.TASK_STATE_WORKING):
    task = MagicMock()
    task.id = "task-123"
    task.context_id = "ctx-456"
    task.status = MagicMock()
    task.status.state = state
    return task


def _make_context(task=None):
    ctx = MagicMock(spec=RequestContext)
    ctx.current_task = task
    ctx.task_id = task.id if task else None
    ctx.context_id = task.context_id if task else None
    return ctx


def _make_agent(cancel_side_effect=None):
    """Create a mock AionAgent with configurable cancel behavior."""
    agent = MagicMock()
    if cancel_side_effect is not None:
        agent.cancel = AsyncMock(side_effect=cancel_side_effect)
    else:
        agent.cancel = AsyncMock()
    return agent


@pytest.fixture
def agent():
    return _make_agent()


@pytest.fixture
def executor(agent):
    return AionAgentRequestExecutor(aion_agent=agent)


@pytest.fixture
def event_queue():
    return AsyncMock(spec=EventQueue)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestCancel:
    @pytest.mark.anyio
    async def test_cancel_missing_task_raises_not_found(self, executor, event_queue):
        ctx = _make_context(task=None)
        with pytest.raises(TaskNotFoundError):
            await executor.cancel(ctx, event_queue)

    @pytest.mark.anyio
    @pytest.mark.parametrize("terminal_state", [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    ])
    async def test_cancel_terminal_task_raises_not_cancelable(
        self, executor, event_queue, terminal_state
    ):
        task = _make_task(state=terminal_state)
        ctx = _make_context(task=task)
        with pytest.raises(TaskNotCancelableError):
            await executor.cancel(ctx, event_queue)

    @pytest.mark.anyio
    async def test_cancel_active_task_calls_framework_hook_and_emits_canceled(
        self, agent, event_queue
    ):
        executor = AionAgentRequestExecutor(aion_agent=agent)
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            agent.cancel.assert_awaited_once_with(ctx)
            MockUpdater.assert_called_once_with(event_queue, task.id, task.context_id)
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_unsupported_framework_still_emits_canceled(self, event_queue):
        agent = _make_agent(cancel_side_effect=UnsupportedOperationError())
        executor = AionAgentRequestExecutor(aion_agent=agent)
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            agent.cancel.assert_awaited_once_with(ctx)
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_input_required_task_is_cancelable(self, agent, event_queue):
        executor = AionAgentRequestExecutor(aion_agent=agent)
        task = _make_task(state=TaskState.TASK_STATE_INPUT_REQUIRED)
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            agent.cancel.assert_awaited_once_with(ctx)
            updater_instance.cancel.assert_awaited_once()
