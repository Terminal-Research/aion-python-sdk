import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, TaskState, TaskStatus
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)

from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.extensions.base import ROUTED_EXTENSION_METADATA_KEY
from aion.server.agent.execution.extensions.errors import ExtensionPreflightError
from aion.server.agent.execution.scope import init_execution_scope


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


class TestTaskForExecution:
    """The gate that decides what a request executes.

    A task is continued whenever it is not terminal: the SDK serialises
    requests per task and treats a follow-up message as the next turn, so an
    active task must not be rejected. A terminal task is skipped rather than
    raised on, because an exception out of execute() fails the whole task in
    the SDK producer instead of rejecting the single request.
    """

    @pytest.mark.anyio
    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_SUBMITTED,
    ])
    async def test_non_terminal_task_is_continued(self, executor, state):
        task = _make_task(state=state)
        ctx = _make_context(task=task)

        assert await executor._get_task_for_execution(ctx) == (task, False)

    @pytest.mark.anyio
    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    ])
    async def test_terminal_task_is_skipped(self, executor, state):
        ctx = _make_context(task=_make_task(state=state))

        assert await executor._get_task_for_execution(ctx) is None

    @pytest.mark.anyio
    async def test_missing_task_is_created(self, executor):
        ctx = _make_context(task=None)
        ctx.message = MagicMock()
        ctx.metadata = None
        init_execution_scope()

        with patch(
            "aion.server.agent.execution.request_executor.new_task_from_user_message"
        ) as make_task:
            make_task.return_value = _make_task(state=TaskState.TASK_STATE_SUBMITTED)
            result = await executor._get_task_for_execution(ctx)

        assert result == (make_task.return_value, True)

    @pytest.mark.anyio
    async def test_execute_on_terminal_task_runs_nothing(self, executor, event_queue):
        """A skipped request must not raise: the SDK reads that as agent failure."""
        executor.agent.stream = MagicMock()
        executor.agent.resume = MagicMock()
        ctx = _make_context(task=_make_task(state=TaskState.TASK_STATE_COMPLETED))

        await executor.execute(ctx, event_queue)

        executor.agent.stream.assert_not_called()
        executor.agent.resume.assert_not_called()
        event_queue.enqueue_event.assert_not_awaited()


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

            agent.cancel.assert_awaited_once_with(context=ctx)
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

            agent.cancel.assert_awaited_once_with(context=ctx)
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

            agent.cancel.assert_awaited_once_with(context=ctx)
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_routed_task_calls_handler_not_framework(self, agent, event_queue):
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_WORKING,
            metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri},
        )
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            handler.cancel.assert_awaited_once_with(context=ctx)
            agent.cancel.assert_not_awaited()
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_routed_task_unsupported_still_emits_canceled(self, agent, event_queue):
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        handler.cancel = AsyncMock(side_effect=UnsupportedOperationError())
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_WORKING,
            metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri},
        )
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            handler.cancel.assert_awaited_once_with(context=ctx)
            agent.cancel.assert_not_awaited()
            updater_instance.cancel.assert_awaited_once()


def _make_real_task(state: TaskState = TaskState.TASK_STATE_INPUT_REQUIRED, metadata: dict | None = None):
    """Build a real a2a.types.Task so protobuf Struct metadata semantics
    (HasField, `in`, item access) behave exactly as in production, unlike a
    MagicMock which cannot emulate them."""
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))
    if metadata:
        for key, value in metadata.items():
            task.metadata[key] = value
    return task


def _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/fake/1.0.0"):
    handler = MagicMock()
    handler.uri = uri
    handler.stream = MagicMock(name="stream")
    handler.resume = MagicMock(name="resume")
    handler.cancel = AsyncMock(name="cancel")
    handler.preflight = AsyncMock(name="preflight", return_value=None)
    return handler


class TestResolve:
    @pytest.mark.anyio
    async def test_resolve_stream_matches_new_task_and_persists_metadata(self):
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task()

        mock_runtime_context = MagicMock()
        mock_runtime_context.extensions = {handler.uri: None}

        with patch(
            "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
        ) as MockRegistry:
            MockRegistry.aget_current_context = AsyncMock(return_value=mock_runtime_context)
            resolved = await executor._resolve(task, "stream")

        assert resolved is handler.stream
        assert task.metadata[ROUTED_EXTENSION_METADATA_KEY] == handler.uri

    @pytest.mark.anyio
    async def test_resolve_stream_falls_back_to_agent_without_match(self):
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task()

        resolved = await executor._resolve(task, "stream")

        assert resolved is agent.stream

    @pytest.mark.anyio
    async def test_resolve_resume_recalls_routed_handler(self):
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri})

        resolved = await executor._resolve(task, "resume")

        assert resolved is handler.resume

    @pytest.mark.anyio
    async def test_resolve_stream_runs_matched_handlers_preflight(self):
        """preflight() must see the live request context, not a cached one -
        that's the whole point of running it per request instead of once at
        executor startup (see ExtensionPreflightError)."""
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task()
        context = MagicMock(spec=RequestContext)

        mock_runtime_context = MagicMock()
        mock_runtime_context.extensions = {handler.uri: None}

        with patch(
            "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
        ) as MockRegistry:
            MockRegistry.aget_current_context = AsyncMock(return_value=mock_runtime_context)
            await executor._resolve(task, "stream", context)

        handler.preflight.assert_awaited_once_with(context)

    @pytest.mark.anyio
    async def test_resolve_cancel_does_not_run_preflight(self):
        """A cancelled task already exists - there is nothing left to gate
        before task creation, so cancel must not require preflight at all."""
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri})

        await executor._resolve(task, "cancel")

        handler.preflight.assert_not_called()

    @pytest.mark.anyio
    async def test_resolve_stream_falls_back_to_agent_skips_preflight(self):
        """The plain framework adapter (`self.agent`) has no preflight - the
        executor must not assume every routed callable is an extension
        handler."""
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task()

        resolved = await executor._resolve(task, "stream", MagicMock(spec=RequestContext))

        assert resolved is agent.stream

    @pytest.mark.anyio
    async def test_resolve_cancel_falls_back_to_agent_without_routing(self):
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task()

        resolved = await executor._resolve(task, "cancel")

        assert resolved is agent.cancel

    @pytest.mark.anyio
    async def test_resolve_cancel_recalls_routed_handler(self):
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri})

        resolved = await executor._resolve(task, "cancel")

        assert resolved is handler.cancel


class TestExecuteViaResolve:
    @pytest.mark.anyio
    async def test_execute_routes_new_task_to_matched_handler_and_persists_metadata(self):
        agent = _make_agent()

        async def agent_stream(*args, **kwargs):
            raise AssertionError("agent.stream must not be called when a handler is matched")
            yield

        agent.stream = agent_stream

        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")

        async def handler_stream(*args, **kwargs):
            return
            yield

        handler.stream = handler_stream

        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task()
        ctx = _make_context(task=None)
        ctx.current_task = None
        ctx.message = MagicMock()
        ctx.metadata = None
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        mock_runtime_context = MagicMock()
        mock_runtime_context.extensions = {handler.uri: None}

        with patch(
            "aion.server.agent.execution.request_executor.new_task_from_user_message",
            return_value=task,
        ):
            with patch(
                "aion.server.agent.execution.request_executor.AionRuntimeContextBuilder"
            ) as MockBuilder:
                MockBuilder.from_request_context.return_value = MagicMock()
                with patch(
                    "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
                ) as MockRegistry:
                    MockRegistry.aset_current_context = AsyncMock()
                    MockRegistry.aget_current_context = AsyncMock(return_value=mock_runtime_context)
                    with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                        await executor.execute(ctx, event_queue)

        assert task.metadata[ROUTED_EXTENSION_METADATA_KEY] == handler.uri
        event_queue.enqueue_event.assert_awaited_once_with(task)

    @pytest.mark.anyio
    async def test_execute_resumes_via_previously_routed_handler(self):
        agent = _make_agent()

        async def agent_resume(*args, **kwargs):
            raise AssertionError("agent.resume must not be called when a handler was previously routed")
            yield

        agent.resume = agent_resume

        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")

        resume_called_with = {}

        async def handler_resume(*args, **kwargs):
            resume_called_with.update(kwargs)
            return
            yield

        handler.resume = handler_resume

        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            metadata={ROUTED_EXTENSION_METADATA_KEY: handler.uri},
        )
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        with patch(
            "aion.server.agent.execution.request_executor.AionRuntimeContextBuilder"
        ) as MockBuilder:
            MockBuilder.from_request_context.return_value = MagicMock()
            with patch(
                "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
            ) as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                    await executor.execute(ctx, event_queue)

        assert resume_called_with.get("context") is ctx
        enqueued = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert task not in enqueued

    @pytest.mark.anyio
    async def test_execute_rejects_via_preflight_before_task_is_persisted(self):
        """A handler that rejects a request in preflight() must stop the
        request at the JSON-RPC level - no task ever reaches the event
        queue, so no FAILED task is left behind for a deployment-config
        problem (see ExtensionPreflightError)."""
        agent = _make_agent()
        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        handler.preflight = AsyncMock(side_effect=ExtensionPreflightError("GITHUB_TOKEN is not set"))

        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task()
        ctx = _make_context(task=None)
        ctx.current_task = None
        ctx.message = MagicMock()
        ctx.metadata = None
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        mock_runtime_context = MagicMock()
        mock_runtime_context.extensions = {handler.uri: None}

        with patch(
            "aion.server.agent.execution.request_executor.new_task_from_user_message",
            return_value=task,
        ):
            with patch(
                "aion.server.agent.execution.request_executor.AionRuntimeContextBuilder"
            ) as MockBuilder:
                MockBuilder.from_request_context.return_value = MagicMock()
                with patch(
                    "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
                ) as MockRegistry:
                    MockRegistry.aset_current_context = AsyncMock()
                    MockRegistry.aget_current_context = AsyncMock(return_value=mock_runtime_context)
                    with pytest.raises(InvalidParamsError):
                        await executor.execute(ctx, event_queue)

        event_queue.enqueue_event.assert_not_awaited()


class TestCreateFactory:
    @pytest.mark.anyio
    async def test_create_keeps_only_available_handlers(self):
        from aion.server.agent.execution.extensions import ExtensionAvailability

        available_handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/evolution/1.0.0")
        available_handler.availability = AsyncMock(return_value=ExtensionAvailability.ok())

        unavailable_handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/other/1.0.0")
        unavailable_handler.availability = AsyncMock(
            return_value=ExtensionAvailability.unavailable("the other toolkit is not installed")
        )

        agent = _make_agent()
        agent.config = MagicMock()

        with patch(
            "aion.server.agent.execution.request_executor.discover_extension_task_handlers",
            return_value=[available_handler, unavailable_handler],
        ):
            executor = await AionAgentRequestExecutor.create(aion_agent=agent)

        assert executor._extension_handlers == {available_handler.uri: available_handler}
        available_handler.availability.assert_awaited_once_with(agent.config)
        unavailable_handler.availability.assert_awaited_once_with(agent.config)

    @pytest.mark.anyio
    async def test_create_marks_enabled_unavailable_handler_in_registry(self):
        """Availability is a registry concern: an enabled extension whose
        handler cannot run is recorded via mark_unavailable() so the shared
        verification pipeline rejects requests with the handler's own reason."""
        from types import SimpleNamespace
        from aion.server.agent.execution.extensions import ExtensionAvailability

        unavailable_handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/other/1.0.0")
        unavailable_handler.availability = AsyncMock(
            return_value=ExtensionAvailability.unavailable("the other toolkit is not installed")
        )

        agent = _make_agent()
        agent.config = SimpleNamespace(enabled_extensions=[unavailable_handler.uri])

        with patch(
            "aion.server.agent.execution.request_executor.discover_extension_task_handlers",
            return_value=[unavailable_handler],
        ), patch(
            "aion.server.agent.execution.request_executor.aion_a2a_extension_registry"
        ) as mock_registry:
            executor = await AionAgentRequestExecutor.create(aion_agent=agent)

        assert executor._extension_handlers == {}
        mock_registry.mark_unavailable.assert_called_once_with(
            unavailable_handler.uri, "the other toolkit is not installed"
        )

    @pytest.mark.anyio
    async def test_create_does_not_mark_not_enabled_handler(self):
        """A handler that is unavailable merely because the agent didn't opt
        in must not be marked - the registry already rejects such requests
        as not enabled for this agent."""
        from types import SimpleNamespace
        from aion.server.agent.execution.extensions import ExtensionAvailability

        handler = _make_handler(uri="https://docs.aion.to/a2a/extensions/aion/other/1.0.0")
        handler.availability = AsyncMock(
            return_value=ExtensionAvailability.unavailable("not enabled")
        )
        agent = _make_agent()
        agent.config = SimpleNamespace(enabled_extensions=[])

        with patch(
            "aion.server.agent.execution.request_executor.discover_extension_task_handlers",
            return_value=[handler],
        ), patch(
            "aion.server.agent.execution.request_executor.aion_a2a_extension_registry"
        ) as mock_registry:
            await AionAgentRequestExecutor.create(aion_agent=agent)

        mock_registry.mark_unavailable.assert_not_called()


class TestFailureClosesTheTask:
    """A crashing producer must leave the task in a terminal state.

    The JSON-RPC error only reaches the caller of that one request; without a
    terminal status the task stays WORKING in the store forever, and any client
    that reconnects or polls sees work that never ends.
    """

    @staticmethod
    def _boom(*args, **kwargs):
        async def _gen():
            raise RuntimeError("producer exploded")
            yield  # pragma: no cover - makes this an async generator

        return _gen()

    @pytest.mark.anyio
    async def test_producer_crash_marks_task_failed(self):
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        executor.agent.stream = self._boom

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock()
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = False
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    with pytest.raises(InternalError):
                        await executor.execute(ctx, event_queue)

            updater.failed.assert_awaited_once()

    @pytest.mark.anyio
    async def test_producer_that_already_reported_its_outcome_is_not_overridden(self):
        """A producer that emitted a terminal state and then crashed has already
        said how the task ended; a second terminal event would contradict it."""
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        executor.agent.stream = self._boom

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock()
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = True
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    with pytest.raises(InternalError):
                        await executor.execute(ctx, event_queue)

            updater.failed.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failure_to_mark_failed_does_not_mask_the_original_error(self):
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        executor.agent.stream = self._boom

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock(side_effect=ConnectionError("store is down"))
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = False
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    # InternalError, not ConnectionError.
                    with pytest.raises(InternalError):
                        await executor.execute(ctx, event_queue)

    @pytest.mark.anyio
    async def test_updater_is_per_execution_not_shared_state(self):
        """One executor instance serves every concurrent request, so the updater
        must not live on the instance where a later execution would clobber it."""
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        init_execution_scope()

        async def empty_stream(*args, **kwargs):
            return
            yield

        executor.agent.stream = empty_stream
        event_queue = AsyncMock(spec=EventQueue)

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                for task_id in ("task-a", "task-b"):
                    task = _make_task()
                    task.id = task_id
                    with patch.object(
                        executor,
                        "_get_task_for_execution",
                        new=AsyncMock(return_value=(task, True)),
                    ):
                        await executor.execute(_make_context(task=task), event_queue)

        assert [call.args[1] for call in MockUpdater.call_args_list] == ["task-a", "task-b"]
        assert not hasattr(executor, "_task_updater")
