import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
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
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
)

from aion.core.a2a.enums import A2AMetadataKey
from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
from aion.core.runtime import aion_a2a_extension_registry
from aion.server.agent.execution.pool import EXTENSION_ROUTE_STATE_KEY
from aion.core.runtime.context.extensions import AionRuntimeExtensions
from aion.server.agent.execution.scope import init_execution_scope
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.tasks.task_manager import AionTaskManager
from tests.support.scripted_executor.executor import ScriptedExecutor
from tests.support.scripted_executor.script import Script
from tests.unit.support.daemon import daemon_payload
from tests.unit.support.events import RecordingQueue
from tests.support.executors import FakeFrameworkExecutor, request_executor

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
    ctx.call_context = ServerCallContext()
    return ctx


async def _nothing(_context):
    return
    yield


@pytest.fixture
def framework():
    return FakeFrameworkExecutor()


@pytest.fixture
def executor(framework):
    return request_executor(framework)


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
        executor = request_executor()
        task = _make_task(state=TaskState.TASK_STATE_WORKING)
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)

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
        executor = request_executor()
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
    async def test_execute_on_terminal_task_runs_nothing(self, executor, framework, event_queue):
        """A skipped request must not raise: the SDK reads that as agent failure."""
        ctx = _make_context(task=_make_task(state=TaskState.TASK_STATE_COMPLETED))

        await executor.execute(ctx, event_queue)

        assert framework.calls == []
        event_queue.enqueue_event.assert_not_awaited()


SECRET = "sk-configuration-secret"
"""A configuration variable's value, as the daemon payload carries it in plain text."""

EXTENSION = "https://example.invalid/extensions/scripted/1.0.0"


def _request_metadata() -> dict:
    """``params.metadata`` of a daemon-driven request that also plants server keys."""
    return {
        DAEMON_EXTENSION_URI_V1: daemon_payload(
            configuration_variables={"MODEL_API_KEY": SECRET},
        ).model_dump(mode="json", by_alias=True, exclude_none=True),
        A2AMetadataKey.SETTLED_REASON.value: "server_shutdown",
        ROUTED: EXTENSION,
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


def _silent_extension() -> ScriptedExecutor:
    """An extension's executor that finishes its turn without a word."""
    return ScriptedExecutor(script=Script(outputs=[]))


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
        extension = _silent_extension()
        executor = request_executor(extensions={EXTENSION: extension})
        context = _request_context(_request_metadata())
        context.call_context.state[EXTENSION_ROUTE_STATE_KEY] = EXTENSION
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)

        announced = queue.events[0]
        assert isinstance(announced, Task)
        assert MessageToDict(announced.metadata) == {ROUTED: EXTENSION}
        assert SECRET not in MessageToJson(announced)
        assert len(extension.turns) == 1

    @pytest.mark.anyio
    async def test_a_route_named_in_the_request_routes_nothing(self, daemon_enabled):
        """Only the server decides which executor a task belongs to."""
        extension = _silent_extension()
        framework = FakeFrameworkExecutor()
        executor = request_executor(framework, extensions={EXTENSION: extension})
        context = _request_context(_request_metadata())
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)

        announced = queue.events[0]
        assert MessageToDict(announced.metadata) == {}
        assert [call[1] for call in framework.calls] == [context]
        assert extension.turns == []


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
    @pytest.mark.parametrize("state", [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_INPUT_REQUIRED])
    async def test_cancel_tells_the_framework_executor_and_emits_canceled(self, event_queue, state):
        stopped = []

        async def stop(config, within):
            stopped.append((config.task_id, within))

        executor = request_executor(FakeFrameworkExecutor(cancel=stop))
        task = _make_real_task(state=state)

        await executor.cancel(_make_context(task=task), event_queue)

        assert stopped == [("task-123", None)]
        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_a_framework_executor_that_cannot_stop_a_turn_still_gets_the_task_canceled(
        self, executor, framework, event_queue
    ):
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING)

        await executor.cancel(_make_context(task=task), event_queue)

        assert len(framework.cancels) == 1
        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_cancel_of_a_routed_task_goes_to_its_extension_not_the_framework(self, framework, event_queue):
        extension = ScriptedExecutor()
        executor = request_executor(framework, extensions={EXTENSION: extension})
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING, metadata={ROUTED: EXTENSION})

        await executor.cancel(_make_context(task=task), event_queue)

        assert extension.cancels == [("task-123", None)]
        assert framework.cancels == []
        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED

    @pytest.mark.anyio
    async def test_a_cancel_activates_none_of_the_declared_extensions(self, daemon_enabled, event_queue):
        """A daemon declared in ``A2A-Extensions`` has no payload on a cancel, and must not fail it."""
        seen = []

        async def stop(config, within):
            seen.append(await AionRuntimeContextRegistry.aget_current_context())

        executor = request_executor(FakeFrameworkExecutor(cancel=stop))
        task = _make_real_task(state=TaskState.TASK_STATE_WORKING)
        call_context = ServerCallContext(requested_extensions={DAEMON_EXTENSION_URI_V1})
        context = RequestContext(call_context, task_id=task.id, context_id=task.context_id, task=task)

        await executor.cancel(context, event_queue)

        [runtime] = seen
        assert runtime.extensions.activated_uris == ()
        assert runtime.callback_attribution is not None
        [canceled] = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert canceled.status.state == TaskState.TASK_STATE_CANCELED


def _make_real_task(state: TaskState = TaskState.TASK_STATE_INPUT_REQUIRED, metadata: dict | None = None):
    """Build a real a2a.types.Task so protobuf Struct metadata semantics
    (HasField, `in`, item access) behave exactly as in production, unlike a
    MagicMock which cannot emulate them."""
    task = Task(id="task-123", context_id="ctx-456", status=TaskStatus(state=state))
    if metadata:
        for key, value in metadata.items():
            task.metadata[key] = value
    return task


def _routed_context(uri: str | None) -> MagicMock:
    """A request context whose call context carries the route the request handler admitted."""
    context = MagicMock(spec=RequestContext)
    context.call_context = ServerCallContext()
    if uri is not None:
        context.call_context.state[EXTENSION_ROUTE_STATE_KEY] = uri
    return context


class TestRoute:
    def test_a_new_task_goes_to_the_executor_of_an_extension_it_activates(self):
        first, second = "https://example.invalid/extensions/a/1.0.0", "https://example.invalid/extensions/b/1.0.0"
        executor = request_executor(extensions={second: ScriptedExecutor(), first: ScriptedExecutor()})

        assert executor.route(AionRuntimeExtensions({first: None})) == first
        assert executor.route(AionRuntimeExtensions({})) is None


class TestAdmitContinuation:
    """A message into a routed task is served by the task's own executor, or refused."""

    def test_a_task_of_an_extension_not_served_here_is_refused_and_left_alone(self):
        executor = request_executor()
        task = _make_real_task(metadata={ROUTED: "https://example.invalid/extensions/gone/1.0.0"})
        before = Task()
        before.CopyFrom(task)

        with pytest.raises(InvalidParamsError) as error:
            executor.admit_continuation(task)

        assert error.value.data == {"code": "extension_unavailable", "retryable": "true"}
        assert task == before

    def test_a_task_of_an_extension_waiting_for_input_is_admitted(self):
        executor = request_executor(extensions={EXTENSION: ScriptedExecutor()})

        executor.admit_continuation(_make_real_task(metadata={ROUTED: EXTENSION}))

    def test_a_task_of_the_framework_executor_is_always_admitted(self):
        executor = request_executor(extensions={EXTENSION: ScriptedExecutor()})

        executor.admit_continuation(_make_real_task(state=TaskState.TASK_STATE_WORKING))


class TestExecuteViaThePool:
    @pytest.mark.anyio
    async def test_execute_routes_new_task_to_the_admitted_extension_and_persists_metadata(self, framework):
        extension = _silent_extension()
        executor = request_executor(framework, extensions={EXTENSION: extension})
        task = _make_real_task()
        ctx = _routed_context(EXTENSION)
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

        assert task.metadata[ROUTED] == EXTENSION
        [(kind, context, config)] = extension.turns
        assert (kind, context, config.task_id) == ("start", ctx, "task-123")
        assert framework.calls == []
        event_queue.enqueue_event.assert_awaited_once_with(task)

    @pytest.mark.anyio
    async def test_execute_resumes_via_the_previously_routed_extension(self, framework):
        extension = _silent_extension()
        executor = request_executor(framework, extensions={EXTENSION: extension})
        task = _make_real_task(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            metadata={ROUTED: EXTENSION},
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

        assert [(kind, context) for kind, context, _config in extension.turns] == [("resume", ctx)]
        assert framework.calls == []
        enqueued = [call.args[0] for call in event_queue.enqueue_event.await_args_list]
        assert task not in enqueued


class TestFailureClosesTheTask:
    """A crashing producer must leave the task in a terminal state.

    Without a terminal status the task stays WORKING in the store forever, and
    any client that reconnects or polls sees work that never ends. The crash
    ends the request normally: the FAILED is its answer.
    """

    @staticmethod
    def _boom(*args, **kwargs):
        async def _gen():
            raise RuntimeError("producer exploded")
            yield  # pragma: no cover - makes this an async generator

        return _gen()

    @pytest.mark.anyio
    async def test_producer_crash_marks_task_failed(self):
        executor = request_executor(FakeFrameworkExecutor(stream=self._boom))
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock()
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = False
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    await executor.execute(ctx, event_queue)

            updater.failed.assert_awaited_once()

    @pytest.mark.anyio
    async def test_producer_that_already_reported_its_outcome_is_not_overridden(self):
        """A producer that emitted a terminal state and then crashed has already
        said how the task ended; a second terminal event would contradict it."""
        executor = request_executor(FakeFrameworkExecutor(stream=self._boom))
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock()
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = True
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    await executor.execute(ctx, event_queue)

            updater.failed.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failure_to_mark_failed_does_not_escape(self):
        executor = request_executor(FakeFrameworkExecutor(stream=self._boom))
        task = _make_task()
        ctx = _make_context(task=task)
        event_queue = AsyncMock(spec=EventQueue)
        init_execution_scope()

        with patch("aion.server.agent.execution.request_executor.TaskUpdater") as MockUpdater:
            updater = MockUpdater.return_value
            updater.failed = AsyncMock(side_effect=ConnectionError("store is down"))
            with patch("aion.server.agent.execution.request_executor.AionEventPipeline") as MockPipeline:
                MockPipeline.return_value.terminal_seen = False
                with patch.object(
                    executor, "_get_task_for_execution", new=AsyncMock(return_value=(task, True))
                ):
                    # The crash is already logged; the ConnectionError is too.
                    await executor.execute(ctx, event_queue)

    @pytest.mark.anyio
    async def test_updater_is_per_execution_not_shared_state(self):
        """One executor instance serves every concurrent request, so the updater
        must not live on the instance where a later execution would clobber it."""
        executor = request_executor()
        init_execution_scope()
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


class TestStatusTimesFollowTheTurn:
    """A new task's statuses carry times in the order they happened.

    The producer makes the turn's events and the event consumer stores them
    concurrently: the opening Task may be stored after the producer has
    already made the first WORKING, stamped as ``TaskUpdater`` stamps it,
    when it is made. The times must not depend on when the consumer gets to
    an event.
    """

    @staticmethod
    async def _consume(context: RequestContext, events: list) -> list[int]:
        """Store the events as the event consumer does, after the producer made them all.

        Returns:
            The status time of each Task and status update, as it was stored
            and delivered.
        """
        task = events[0]
        manager = AionTaskManager(
            task_store=InMemoryTaskStore(),
            context=context.call_context,
            task_id=task.id,
            context_id=task.context_id,
            initial_message=None,
        )
        await manager.save_task_event(task)
        times = [task.status.timestamp.ToNanoseconds()]
        for event in events[1:]:
            processed = await manager.process(event)
            if isinstance(processed, TaskStatusUpdateEvent):
                times.append(processed.status.timestamp.ToNanoseconds())
        return times

    @pytest.mark.anyio
    async def test_submitted_is_not_later_than_the_working_that_follows(self):
        async def agent_stream(context):
            task = context.current_task
            yield TaskArtifactUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                artifact=Artifact(artifact_id="report", parts=[Part(text="findings")]),
            )
            yield TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )

        executor = request_executor(FakeFrameworkExecutor(stream=agent_stream))
        context = _request_context({})
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)
        assert isinstance(queue.events[0], Task)
        assert queue.events[0].status.state == TaskState.TASK_STATE_SUBMITTED
        assert queue.events[1].status.state == TaskState.TASK_STATE_WORKING

        times = await self._consume(context, queue.events)

        assert len(times) == 3
        assert all(times)
        assert times == sorted(times)

    @pytest.mark.anyio
    async def test_a_turn_that_opens_with_a_status_has_nothing_before_it(self):
        """The agent's own WORKING moves the task, stamped when the agent made it."""
        async def agent_stream(context):
            task = context.current_task
            reply = Message(
                message_id="msg-reply",
                role=Role.ROLE_AGENT,
                task_id=task.id,
                context_id=task.context_id,
                parts=[Part(text="done")],
            )
            working = TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=reply),
            )
            working.status.timestamp.GetCurrentTime()
            yield working
            yield TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )

        executor = request_executor(FakeFrameworkExecutor(stream=agent_stream))
        context = _request_context({})
        queue = RecordingQueue()
        init_execution_scope()

        await executor.execute(context, queue)

        assert [type(event) for event in queue.events] == [Task, TaskStatusUpdateEvent, TaskStatusUpdateEvent]
        assert queue.events[1].status.message.message_id == "msg-reply"
        times = await self._consume(context, queue.events)
        assert times == sorted(times)
