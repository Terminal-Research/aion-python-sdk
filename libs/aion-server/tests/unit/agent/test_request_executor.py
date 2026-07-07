import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, TaskState
from a2a.utils.errors import TaskNotCancelableError, TaskNotFoundError, UnsupportedOperationError

from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.scope import init_execution_scope


def _make_task(state: TaskState = TaskState.TASK_STATE_WORKING):
    task = MagicMock()
    task.id = "task-123"
    task.context_id = "ctx-456"
    task.status = MagicMock()
    task.status.state = state
    return task


def _make_context(task=None, message=None, metadata=None):
    ctx = MagicMock(spec=RequestContext)
    ctx.current_task = task
    ctx.task_id = task.id if task else None
    ctx.context_id = task.context_id if task else None
    ctx.message = message or Message(
        message_id="msg-123",
        role=Role.ROLE_USER,
        parts=[Part(text="hello")],
    )
    ctx.metadata = metadata
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


class TestExecuteRuntimeContext:
    @pytest.mark.anyio
    async def test_execute_builds_and_sets_runtime_context(self):
        """Verify that execute() builds AionRuntimeContext and stores in scope."""
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

        # Mock agent stream and AionRuntimeContextBuilder
        executor.agent.stream = AsyncMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))

        # Initialize execution scope for the test
        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.AionRuntimeContextBuilder") as MockBuilder:
            mock_context = MagicMock()
            MockBuilder.from_request_context.return_value = mock_context

            with patch("aion.server.agent.execution.request_executor.AionRuntimeContextRegistry") as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                    # Mock _get_task_for_execution to return our task
                    with patch.object(executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))):
                        async def empty_stream(*args, **kwargs):
                            return
                            yield
                        executor.agent.stream = empty_stream

                        await executor.execute(ctx, event_queue)

                        # Verify context was built from request context
                        MockBuilder.from_request_context.assert_called_once_with(ctx)

                        # Verify context was set via registry
                        MockRegistry.aset_current_context.assert_awaited_once_with(mock_context)

    @pytest.mark.anyio
    async def test_execute_handles_missing_runtime_context(self):
        """Verify that execute() handles case when context is unavailable."""
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.AionRuntimeContextBuilder") as MockBuilder:
            # Simulate no context available (e.g., graph without a2a_inbox)
            MockBuilder.from_request_context.return_value = None

            with patch("aion.server.agent.execution.request_executor.AionRuntimeContextRegistry") as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                    with patch.object(executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))):
                        async def empty_stream(*args, **kwargs):
                            return
                            yield
                        executor.agent.stream = empty_stream

                        await executor.execute(ctx, event_queue)

                        # Verify builder was called
                        MockBuilder.from_request_context.assert_called_once_with(ctx)

                        # Verify set_current_context was NOT called when context is None
                        MockRegistry.aset_current_context.assert_not_awaited()


class TestGetTaskForExecution:
    @pytest.mark.anyio
    async def test_new_task_without_metadata_does_not_assign_none(self):
        """A new task should omit metadata when the request context has none."""
        ctx = _make_context(task=None, metadata=None)
        init_execution_scope()

        task, is_new_task = await AionAgentRequestExecutor._get_task_for_execution(ctx)

        assert is_new_task is True
        assert ctx.current_task == task
        assert not task.HasField("metadata")


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
