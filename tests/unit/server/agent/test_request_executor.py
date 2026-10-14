import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict, MessageToJson
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)

from aion.server.agent.execution import AionAgentRequestExecutor
from aion.core.a2a.enums import A2AMetadataKey
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.agent.execution.extensions import EXTENSION_ROUTE_STATE_KEY, ExtensionRoute
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.server.agent.execution.scope import init_execution_scope
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.files.storage import FileUploadErrorCode, StubFileStorageBackend, UploadFailure
from tests.unit.support.daemon import daemon_payload
from tests.unit.support.events import RecordingQueue
from tests.unit.support.files import OutcomeBackend, StoringTransformer

ROUTED = A2AMetadataKey.ROUTED_EXTENSION.value


@pytest.fixture(autouse=True)
def runtime_provider():
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    yield
    provider.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


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
            mock_context = AionRuntimeContext()
            MockBuilder.from_request_context.return_value = mock_context

            with patch("aion.server.agent.execution.request_executor.AionRuntimeContextRegistry") as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                MockRegistry.aget_current_context = AsyncMock(return_value=None)
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
                        assigned = MockRegistry.aset_current_context.await_args_list
                        assert len(assigned) == 2
                        assert isinstance(assigned[0].args[0], AionRuntimeContext)
                        assert assigned[1].args == (None,)

    @pytest.mark.anyio
    async def test_execute_handles_missing_runtime_context(self):
        """Verify that execute() handles case when context is unavailable."""
        executor = AionAgentRequestExecutor(aion_agent=_make_agent())
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.AionRuntimeContextBuilder") as MockBuilder:
            # Simulate no context available (an agent that never reads one)
            MockBuilder.from_request_context.return_value = None

            with patch("aion.server.agent.execution.request_executor.AionRuntimeContextRegistry") as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                MockRegistry.aget_current_context = AsyncMock(return_value=None)
                with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                    with patch.object(executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))):
                        async def empty_stream(*args, **kwargs):
                            return
                            yield
                        executor.agent.stream = empty_stream

                        await executor.execute(ctx, event_queue)

                        # Verify builder was called
                        MockBuilder.from_request_context.assert_called_once_with(ctx)

                        # An empty inbox still gets explicit direct attribution,
                        # and never inherits the previous execution's caller.
                        assigned = MockRegistry.aset_current_context.await_args_list
                        assert len(assigned) == 2
                        assert assigned[0].args[0].callback_attribution is not None
                        assert assigned[1].args == (None,)


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


SECRET = "sk-configuration-secret"
"""A configuration variable's value, as the daemon payload carries it in plain text."""

TOOLKIT = "https://example.invalid/toolkits/scripted/1.0.0"


def _request_metadata() -> dict:
    """``params.metadata`` of a daemon-driven request that also plants server keys."""
    return {
        DAEMON_EXTENSION_URI_V1: daemon_payload(
            configuration_variables={"MODEL_API_KEY": SECRET},
        ).model_dump(mode="json", by_alias=True, exclude_none=True),
        A2AMetadataKey.SETTLED_REASON.value: "server_shutdown",
        ROUTED: TOOLKIT,
        "aion:custom": "planted",
        "client-note": "for this turn",
    }


def _request_context(metadata: dict, *, extensions: tuple[str, ...] = ()) -> RequestContext:
    """A new request as a2a-sdk hands it to the executor."""
    message = Message(message_id="msg-1", role=Role.ROLE_USER, parts=[Part(text="improve the agent")])
    message.extensions.extend(extensions)
    request = SendMessageRequest(message=message)
    request.metadata.update(metadata)
    return RequestContext(ServerCallContext(), request=request)


@pytest.fixture
def daemon_enabled():
    aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
    yield
    aion_a2a_extension_registry.reset_to_default()


class _ExtensionHandler:
    """An extension handler that finishes its turn without a word."""

    uri = TOOLKIT

    def __init__(self) -> None:
        self.streamed = 0

    async def stream(self, context, admission=None, event_queue=None):
        self.streamed += 1
        return
        yield


class TestRequestMetadataStaysOnTheRequest:
    """A new task does not take the request's metadata, as in a2a-sdk.

    The task is stored, journaled and returned to whoever reads it - GetTask,
    ListTasks, stream snapshots, push notifications - while the daemon payload
    in ``params.metadata`` carries the environment's configuration variables in
    plain text. The agent reads the request's metadata from the request.
    """

    @pytest.mark.anyio
    async def test_a_new_task_carries_none_of_the_request_metadata(self, executor):
        context = _request_context(_request_metadata())
        init_execution_scope()

        task, is_new_task = await executor._get_task_for_execution(context)

        assert is_new_task
        assert MessageToDict(task.metadata) == {}
        assert SECRET not in MessageToJson(task)
        daemon = context.metadata[DAEMON_EXTENSION_URI_V1]
        assert daemon["environment"]["configurationVariables"] == {"MODEL_API_KEY": SECRET}

    @pytest.mark.anyio
    async def test_an_extension_task_carries_only_the_route_the_server_wrote(self, daemon_enabled):
        handler = _ExtensionHandler()
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        context = _request_context(_request_metadata())
        context.call_context.state[EXTENSION_ROUTE_STATE_KEY] = ExtensionRoute(TOOLKIT)
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)

        announced = queue.events[0]
        assert isinstance(announced, Task)
        assert MessageToDict(announced.metadata) == {ROUTED: TOOLKIT}
        assert SECRET not in MessageToJson(announced)
        assert handler.streamed == 1

    @pytest.mark.anyio
    async def test_a_route_named_in_the_request_routes_nothing(self, daemon_enabled):
        """Only the server decides which handler a task belongs to."""
        handler = _ExtensionHandler()
        agent = _make_agent()
        streamed = []

        async def agent_stream(context):
            streamed.append(context)
            return
            yield

        agent.stream = agent_stream
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        context = _request_context(_request_metadata())
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)

        announced = queue.events[0]
        assert MessageToDict(announced.metadata) == {}
        assert executor._routed_handler(announced) is agent
        assert streamed == [context]
        assert handler.streamed == 0


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

            agent.cancel.assert_awaited_once_with(context=ctx, event_queue=event_queue)
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

            agent.cancel.assert_awaited_once_with(context=ctx, event_queue=event_queue)
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

            agent.cancel.assert_awaited_once_with(context=ctx, event_queue=event_queue)
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_routed_task_calls_handler_not_framework(self, agent, event_queue):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_WORKING,
            metadata={ROUTED: handler.uri},
        )
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            handler.cancel.assert_awaited_once_with(context=ctx, event_queue=event_queue)
            agent.cancel.assert_not_awaited()
            updater_instance.cancel.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cancel_routed_task_unsupported_still_emits_canceled(self, agent, event_queue):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        handler.cancel = AsyncMock(side_effect=UnsupportedOperationError())
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_WORKING,
            metadata={ROUTED: handler.uri},
        )
        ctx = _make_context(task=task)

        with patch(
            "aion.server.agent.execution.request_executor.TaskUpdater"
        ) as MockUpdater:
            updater_instance = AsyncMock()
            MockUpdater.return_value = updater_instance

            await executor.cancel(ctx, event_queue)

            handler.cancel.assert_awaited_once_with(context=ctx, event_queue=event_queue)
            agent.cancel.assert_not_awaited()
            updater_instance.cancel.assert_awaited_once()


class TestCancelWritesOneOutcome:
    """A routed task's terminal state has one writer: its own turn, or the executor when the turn did not close it."""

    @pytest.mark.anyio
    async def test_a_turn_that_closed_the_task_leaves_nothing_to_write(self, agent, event_queue):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        handler.cancel = AsyncMock(return_value=True)
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING, metadata={ROUTED: handler.uri})

        await executor.cancel(_make_context(task=task), event_queue)

        event_queue.enqueue_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_a_turn_that_did_not_close_the_task_is_closed_canceled_in_the_runs_queue(self, agent, event_queue):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING, metadata={ROUTED: handler.uri})

        await executor.cancel(_make_context(task=task), event_queue)

        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_a_cancel_activates_none_of_the_declared_extensions(self, daemon_enabled, event_queue):
        """A daemon declared in ``A2A-Extensions`` has no payload on a cancel, and must not fail it."""
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent)
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING)
        call_context = ServerCallContext(requested_extensions={DAEMON_EXTENSION_URI_V1})
        context = RequestContext(call_context, task_id=task.id, context_id=task.context_id, task=task)
        seen = []

        async def cancel(context, event_queue):
            seen.append(await AionRuntimeContextRegistry.aget_current_context())

        agent.cancel = cancel

        await executor.cancel(context, event_queue)

        [runtime] = seen
        assert runtime.extensions.activated_uris == ()
        assert runtime.callback_attribution is not None
        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED


class TestCancelFiles:
    """What a cancel hook hands back reaches the client with its files stored.

    The framework adapter's hook publishes straight to the request's queue - a
    rescued bundle, for instance - and closes the CANCELED with a message of its
    own; neither passes through the event pipeline, and neither may carry
    inline bytes.
    """

    @staticmethod
    def _handing_back_files():
        bundle = TaskArtifactUpdateEvent(
            task_id="task-123",
            context_id="ctx-456",
            artifact=Artifact(
                artifact_id="bundle",
                name="bundle",
                parts=[Part(raw=b"git bundle", media_type="application/octet-stream",
                            filename="rescue.bundle")],
            ),
        )
        closing = Message(
            message_id="m-cancel",
            task_id="task-123",
            context_id="ctx-456",
            role=Role.ROLE_AGENT,
            parts=[Part(text="canceled"),
                   Part(raw=b"log", media_type="text/plain", filename="run.log")],
        )

        async def cancel(*, context, event_queue):
            await event_queue.enqueue_event(bundle)
            return closing

        return AsyncMock(side_effect=cancel)

    async def _cancel(self, agent, transformer) -> RecordingQueue:
        agent.cancel = self._handing_back_files()
        executor = AionAgentRequestExecutor(aion_agent=agent, file_transformer=transformer)
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING)
        queue = RecordingQueue()
        await executor.cancel(_make_context(task=task), queue)
        return queue

    @pytest.mark.anyio
    async def test_files_a_cancel_hook_hands_back_leave_as_urls(self, agent):
        queue = await self._cancel(agent, StoringTransformer())

        bundle, canceled = queue.events
        assert isinstance(bundle, TaskArtifactUpdateEvent)
        assert bundle.artifact.parts[0].url.startswith(StubFileStorageBackend.BASE_URI)
        assert not bundle.artifact.parts[0].raw
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED
        text, log = canceled.status.message.parts
        assert text.text == "canceled"
        assert log.url.startswith(StubFileStorageBackend.BASE_URI) and not log.raw

    @pytest.mark.anyio
    async def test_a_file_that_could_not_be_stored_does_not_stop_the_cancel(self, agent):
        """Cancellation is the outcome the caller asked for; the file is dropped."""
        refused = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)])

        queue = await self._cancel(agent, StoringTransformer(refused))

        (canceled,) = queue.events
        assert isinstance(canceled, TaskStatusUpdateEvent)
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED
        assert [part.text for part in canceled.status.message.parts] == ["canceled"]

    @pytest.mark.anyio
    async def test_without_a_backend_the_hook_gets_the_request_queue_itself(self, agent):
        queue = await self._cancel(agent, None)

        bundle, canceled = queue.events
        assert bundle.artifact.parts[0].raw == b"git bundle"
        assert canceled.status.message.parts[1].raw == b"log"


def _make_real_task(state: TaskState = TaskState.TASK_STATE_INPUT_REQUIRED, metadata: dict | None = None):
    """Build a real a2a.types.Task so protobuf Struct metadata semantics
    (HasField, `in`, item access) behave exactly as in production, unlike a
    MagicMock which cannot emulate them."""
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))
    if metadata:
        for key, value in metadata.items():
            task.metadata[key] = value
    return task


def _make_handler(uri="https://example.invalid/toolkits/fake/1.0.0"):
    handler = MagicMock()
    handler.uri = uri
    handler.stream = MagicMock(name="stream")
    handler.resume = MagicMock(name="resume")
    handler.cancel = AsyncMock(name="cancel", return_value=False)
    handler.admit = AsyncMock(name="admit")
    return handler


def _routed_context(route: ExtensionRoute | None) -> MagicMock:
    """A request context whose call context carries the route the request handler admitted."""
    context = MagicMock(spec=RequestContext)
    context.call_context = MagicMock()
    context.call_context.state = {EXTENSION_ROUTE_STATE_KEY: route} if route is not None else {}
    return context


class TestResolve:
    @pytest.mark.anyio
    async def test_resolve_stream_takes_the_admitted_route_and_persists_it(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task()
        admission = object()

        queue = object()

        resolved = await executor._resolve(
            task, "stream", _routed_context(ExtensionRoute(handler.uri, admission)), queue
        )

        assert resolved.func is handler.stream
        assert resolved.keywords == {"admission": admission, "event_queue": queue}
        assert task.metadata[ROUTED] == handler.uri

    @pytest.mark.anyio
    async def test_resolve_stream_falls_back_to_agent_without_a_route(self):
        agent = _make_agent()
        handler = _make_handler()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task()

        resolved = await executor._resolve(task, "stream", _routed_context(None))

        assert resolved is agent.stream
        assert ROUTED not in task.metadata

    @pytest.mark.anyio
    async def test_resolve_stream_ignores_a_route_to_a_handler_it_does_not_have(self):
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task()

        resolved = await executor._resolve(task, "stream", _routed_context(ExtensionRoute("https://x.invalid/t")))

        assert resolved is agent.stream

    @pytest.mark.anyio
    async def test_resolve_resume_recalls_routed_handler(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED: handler.uri})
        queue = object()

        resolved = await executor._resolve(task, "resume", event_queue=queue)

        assert resolved.func is handler.resume
        assert resolved.keywords == {"event_queue": queue}

    @pytest.mark.anyio
    async def test_resolve_cancel_falls_back_to_agent_without_routing(self):
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task()

        resolved = await executor._resolve(task, "cancel")

        assert resolved is agent.cancel

    @pytest.mark.anyio
    async def test_resolve_cancel_recalls_routed_handler(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED: handler.uri})

        resolved = await executor._resolve(task, "cancel")

        assert resolved is handler.cancel


class TestAdmitNewTask:
    """The route of a new task is decided and admitted before the task exists."""

    @staticmethod
    def _call_context():
        context = MagicMock()
        context.state = {}
        return context

    def test_a_new_task_goes_to_the_handler_of_an_extension_it_activates(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/a/1.0.0")
        other = _make_handler(uri="https://example.invalid/toolkits/b/1.0.0")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[other, handler])

        assert executor.routed_extension(AionRuntimeExtensions({handler.uri: None})) == handler.uri
        assert executor.routed_extension(AionRuntimeExtensions({})) is None

    @pytest.mark.anyio
    async def test_the_admitted_route_is_kept_for_execute(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/a/1.0.0")
        handler.admit = AsyncMock(return_value="admitted")
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        extensions = AionRuntimeExtensions({handler.uri: None})
        message = MagicMock()
        call_context = self._call_context()

        await executor.admit_new_task(handler.uri, message, extensions, call_context)

        handler.admit.assert_awaited_once_with(message, extensions, call_context)
        assert call_context.state[EXTENSION_ROUTE_STATE_KEY] == ExtensionRoute(handler.uri, "admitted")

    @pytest.mark.anyio
    async def test_a_refusal_leaves_no_route(self):
        handler = _make_handler()
        handler.admit = AsyncMock(side_effect=InvalidParamsError(message="refused"))
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        call_context = self._call_context()

        with pytest.raises(InvalidParamsError):
            await executor.admit_new_task(
                handler.uri, MagicMock(), AionRuntimeExtensions({handler.uri: None}), call_context
            )

        assert EXTENSION_ROUTE_STATE_KEY not in call_context.state


class TestAdmitContinuation:
    """A message into a routed task is served by the task's own handler, or refused."""

    def test_a_task_of_an_extension_not_served_here_is_refused_and_left_alone(self):
        agent = _make_agent()
        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[])
        task = _make_real_task(metadata={ROUTED: "https://example.invalid/toolkits/gone/1.0.0"})
        before = Task()
        before.CopyFrom(task)

        with pytest.raises(InvalidParamsError) as error:
            executor.admit_continuation(task)

        assert error.value.data == {"code": "extension_unavailable", "retryable": "true"}
        assert task == before

    def test_the_tasks_handler_checks_the_message(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/a/1.0.0")
        handler.admit_continuation = MagicMock()
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])
        task = _make_real_task(metadata={ROUTED: handler.uri})

        executor.admit_continuation(task)

        handler.admit_continuation.assert_called_once_with(task)

    def test_a_task_of_the_framework_adapter_is_always_admitted(self):
        handler = _make_handler()
        handler.admit_continuation = MagicMock()
        executor = AionAgentRequestExecutor(aion_agent=_make_agent(), extension_handlers=[handler])

        executor.admit_continuation(_make_real_task())

        handler.admit_continuation.assert_not_called()


class TestExecuteViaResolve:
    @pytest.mark.anyio
    async def test_execute_routes_new_task_to_the_admitted_handler_and_persists_metadata(self):
        agent = _make_agent()

        async def agent_stream(*args, **kwargs):
            raise AssertionError("agent.stream must not be called when a handler is routed")
            yield

        agent.stream = agent_stream

        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")
        streamed_with = {}

        async def handler_stream(*args, **kwargs):
            streamed_with.update(kwargs)
            return
            yield

        handler.stream = handler_stream

        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task()
        ctx = _routed_context(ExtensionRoute(handler.uri, "admitted"))
        ctx.current_task = None
        ctx.message = MagicMock()
        ctx.metadata = None
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        with patch(
            "aion.server.agent.execution.request_executor.new_task_from_user_message",
            return_value=task,
        ):
            with patch(
                "aion.server.agent.execution.request_executor.AionRuntimeContextBuilder"
            ) as MockBuilder:
                MockBuilder.from_request_context.return_value = AionRuntimeContext()
                with patch(
                    "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
                ) as MockRegistry:
                    MockRegistry.aset_current_context = AsyncMock()
                    MockRegistry.aget_current_context = AsyncMock(return_value=None)
                    with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                        await executor.execute(ctx, event_queue)

        assert task.metadata[ROUTED] == handler.uri
        assert streamed_with == {"context": ctx, "admission": "admitted", "event_queue": event_queue}
        event_queue.enqueue_event.assert_awaited_once_with(task)

    @pytest.mark.anyio
    async def test_execute_resumes_via_previously_routed_handler(self):
        agent = _make_agent()

        async def agent_resume(*args, **kwargs):
            raise AssertionError("agent.resume must not be called when a handler was previously routed")
            yield

        agent.resume = agent_resume

        handler = _make_handler(uri="https://example.invalid/toolkits/scripted/1.0.0")

        resume_called_with = {}

        async def handler_resume(*args, **kwargs):
            resume_called_with.update(kwargs)
            return
            yield

        handler.resume = handler_resume

        executor = AionAgentRequestExecutor(aion_agent=agent, extension_handlers=[handler])
        task = _make_real_task(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            metadata={ROUTED: handler.uri},
        )
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

        init_execution_scope()

        with patch(
            "aion.server.agent.execution.request_executor.AionRuntimeContextBuilder"
        ) as MockBuilder:
            MockBuilder.from_request_context.return_value = AionRuntimeContext()
            with patch(
                "aion.server.agent.execution.request_executor.AionRuntimeContextRegistry"
            ) as MockRegistry:
                MockRegistry.aset_current_context = AsyncMock()
                MockRegistry.aget_current_context = AsyncMock(return_value=None)
                with patch("aion.server.agent.execution.request_executor.AionEventPipeline"):
                    await executor.execute(ctx, event_queue)

        assert resume_called_with.get("context") is ctx
        enqueued = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert task not in enqueued


class TestCreateFactory:
    @pytest.mark.anyio
    async def test_create_serves_every_toolkit_discovery_returns(self):
        handler = _make_handler(uri="https://example.invalid/toolkits/a/1.0.0")
        agent = _make_agent()
        agent.config = MagicMock()

        context_artifacts = MagicMock()
        context_runs = MagicMock()
        with patch(
            "aion.server.agent.execution.request_executor.load_toolkit_handlers",
            new=AsyncMock(return_value=[handler]),
        ) as load:
            executor = await AionAgentRequestExecutor.create(
                aion_agent=agent, context_artifacts=context_artifacts, context_runs=context_runs
            )

        load.assert_awaited_once_with(
            agent.config,
            context_artifacts=context_artifacts,
            context_runs=context_runs,
            owner_resolver=agent.owner_resolver,
        )
        assert executor._extension_handlers == {handler.uri: handler}


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
