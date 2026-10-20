"""Who closes a task when the agent crashes, and when the server must not.

A crashed execution leaves a task that nothing is working on. The JSON-RPC
error reaches the caller of that one request; the stored task is what every
later reader sees, so the server marks it FAILED itself.

The exception is a producer that had already reported its own outcome before
crashing. That outcome is the authoritative one, and a second terminal event
would contradict it. No authoring surface lets an agent report a terminal
state - `a2a_outbox` is read from the final state, which a crash never
reaches - so this case cannot be driven from a scenario agent, and is stated
here instead. It is reachable in production: an executable extension's
executor that emits its own terminal event and then fails.

A file the agent produced that could not be stored ends the run the same way:
what the agent said beside it still reaches the client, and the task is
FAILED.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TaskStatusUpdateEvent

from aion.core.runtime.context import (
    AionRuntimeContext,
    AionRuntimeContextRegistry,
    ForwardedAttribution,
)
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.event_pipeline import AionEventPipeline
from aion.server.agent.adapters import AgentFrameworkExecutor
from aion.server.agent.execution.extensions import ExtensionExecutor, ExtensionServices
from aion.server.agent.execution.request_executor import (
    CRASH_REASON,
    AionAgentRequestExecutor,
    public_reason_of,
)
from aion.core.a2a.enums import A2AMetadataKey
from aion.core.exceptions import AionDaemonIdentityRequired, TaskFailedError
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
    set_task_manager,
)
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import FileUploadErrorCode, FileUploadManager, UploadFailure
from aion.server.tasks.task_manager import AionTaskManager
from tests.unit.support.events import RecordingQueue
from tests.support.executors import request_executor
from tests.unit.support.files import OutcomeBackend, distribution_payload, principal

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
FRAMEWORK_TASK = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))


@pytest.fixture(autouse=True)
def _execution_scope():
    init_execution_scope()
    yield
    clear_execution_scope()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def updater(queue: RecordingQueue) -> TaskUpdater:
    return TaskUpdater(queue, TASK_ID, CONTEXT_ID)


@pytest.fixture
def pipeline(queue: RecordingQueue, updater: TaskUpdater) -> AionEventPipeline:
    """A pipeline with a task manager behind it, as execution has."""
    set_task_manager(
        AionTaskManager(
            task_store=InMemoryTaskStore(),
            context=ServerCallContext(),
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            initial_message=None,
        )
    )
    return AionEventPipeline(queue, updater, None, task_started=True)


def _terminal_states(queue: RecordingQueue) -> list[TaskState]:
    return [
        event.status.state
        for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent))
        and event.status.state
        in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED)
    ]


async def test_a_crash_with_nothing_reported_closes_the_task_as_failed(
    queue, updater, pipeline
) -> None:
    """Otherwise the task stays WORKING in the store with nobody working on it."""
    await pipeline.process(
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
    )

    await AionAgentRequestExecutor._close_task(updater, pipeline, FRAMEWORK_TASK)

    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]
    assert not queue.events[-1].status.HasField("message"), "a crash's text is internal"


async def test_a_reported_outcome_is_not_overwritten(queue, updater, pipeline) -> None:
    """A producer that closed the task first has said the authoritative thing.

    It completed its work and then failed on the way out. The task is
    COMPLETED, and a FAILED event after it would tell the client the opposite
    of what it was already told.
    """
    await pipeline.process(
        TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
    )

    await AionAgentRequestExecutor._close_task(updater, pipeline, FRAMEWORK_TASK)

    assert _terminal_states(queue) == [TaskState.TASK_STATE_COMPLETED]


async def test_a_failure_while_closing_is_swallowed(queue, updater, pipeline) -> None:
    """The original exception is what the caller must see, not this one.

    Closing the task needs the store, which is one of the things that may
    have just broken. Raising here would replace the real error with the
    error from reporting it.
    """

    async def _unavailable(*args, **kwargs):
        raise RuntimeError("the store is gone")

    updater.failed = _unavailable

    await AionAgentRequestExecutor._close_task(updater, pipeline, FRAMEWORK_TASK)

    assert _terminal_states(queue) == []


REPORT = Part(raw=b"%PDF", media_type="application/pdf", filename="report.pdf")


@pytest.fixture
def runtime_context():
    """The callback attribution an execution always has, so storage is attempted."""
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(
            distribution_extension_payload=distribution_payload(principal()),
            callback_attribution=ForwardedAttribution("signed"),
        )
    )
    yield
    AionRuntimeContextRegistry.set_current_context(None)
    AionRuntimeContextRegistry.clear_provider()


class _Agent(AgentFrameworkExecutor):
    """A framework executor whose turn answers with a report the storage service refuses."""

    def __init__(self) -> None:
        self.reached_the_end = False
        self.discard_undelivered = AsyncMock()

    async def stream(self, context, config):  # pragma: no cover - only resumed turns run here
        return
        yield

    async def resume(self, context, config):
        message = Message(
            message_id="m-1",
            role=Role.ROLE_AGENT,
            parts=[Part(text="here is the report"), REPORT],
        )
        yield TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=message),
        )
        self.reached_the_end = True
        yield TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )


async def _run_refused(agent, queue, *, extensions=None, metadata=None):
    """Run one resumed turn of ``agent`` against storage that refuses every file.

    Returns the reason the FAILED status gives, and the request context. The
    turn ends without raising: a file that could not be stored is the task's
    outcome, not a crash of the request.
    """
    backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)])
    executor = request_executor(
        agent,
        file_transformer=A2AFileTransformer(FileUploadManager(backend)),
        extensions=extensions,
    )
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        metadata=metadata,
    )
    context = RequestContext(ServerCallContext(), task_id=TASK_ID, context_id=CONTEXT_ID, task=task)
    with patch.object(
        AionAgentRequestExecutor,
        "_setup_runtime_context",
        AsyncMock(return_value=ResponseServiceParameters()),
    ):
        await executor.execute(context, queue)
    (failed,) = [
        event for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent))
        and event.status.state == TaskState.TASK_STATE_FAILED
    ]
    return " ".join(part.text for part in failed.status.message.parts), context


async def test_a_file_that_could_not_be_stored_fails_the_task(queue, runtime_context, caplog) -> None:
    """The agent's answer goes out, the file does not, and the task is FAILED.

    The FAILED status says why in the fixed public wording of the failure,
    and the request itself ends without an error: nothing crashed, so
    nothing is logged as an error. What the storage service actually
    answered stays in the log, one WARNING line per file.
    """
    agent = _Agent()

    with caplog.at_level(logging.WARNING):
        reason, _ = await _run_refused(agent, queue)

    assert reason == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    assert not agent.reached_the_end, "the run stops at the file it could not deliver"
    replies = [
        [part.text for part in event.status.message.parts]
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.status.state == TaskState.TASK_STATE_WORKING
        and event.status.HasField("message")
    ]
    assert replies == [["here is the report"]]
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]
    (failed,) = [e for e in queue.events if e.status.state == TaskState.TASK_STATE_FAILED]
    assert [part.text for part in failed.status.message.parts] == [
        FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    ], "every reader of the task - stream, unary, tasks/get - learns why it failed"

    (warning,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "report.pdf" in warning.getMessage()
    assert "STORAGE_FORBIDDEN" in warning.getMessage()
    assert warning.exc_info is None


async def test_the_agent_forgets_the_undelivered_event_before_the_task_fails(queue, runtime_context) -> None:
    """A turn that follows the FAILED state finds nothing of the file to read back."""
    agent = _Agent()
    closed_when_discarded = []
    agent.discard_undelivered.side_effect = lambda *args: closed_when_discarded.append(
        _terminal_states(queue)
    )

    _, context = await _run_refused(agent, queue)

    config, event = agent.discard_undelivered.call_args.args
    assert (config.task_id, config.context_id) == (TASK_ID, CONTEXT_ID)
    assert event.status.message.parts[1] == REPORT, "the event as the agent produced it"
    assert closed_when_discarded == [[]]


async def test_a_failure_to_forget_does_not_change_the_outcome(queue, runtime_context, caplog) -> None:
    agent = _Agent()
    agent.discard_undelivered.side_effect = RuntimeError("artifact service is gone")

    reason, _ = await _run_refused(agent, queue)

    assert reason == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]
    assert "Could not discard" in caplog.text


async def test_an_extensions_executor_is_not_asked(queue, runtime_context) -> None:
    """Only a framework executor keeps a copy of what it emits; a task of an extension asks nobody."""
    uri = "https://example.com/ext"

    class _Extension(ExtensionExecutor):
        def __init__(self) -> None:
            super().__init__({}, ExtensionServices())
            self._agent = _Agent()

        async def stream(self, context, config):  # pragma: no cover - only resumed turns run here
            return
            yield

        def resume(self, context, config):
            return self._agent.resume(context, config)

    agent = _Agent()

    reason, _ = await _run_refused(
        agent,
        queue,
        extensions={uri: _Extension()},
        metadata={A2AMetadataKey.ROUTED_EXTENSION.value: uri},
    )

    assert reason.strip() == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason, "said in the extension's error shape"
    agent.discard_undelivered.assert_not_called()
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]


# --------------------------------------------------------------------------
# Why a failed task failed
# --------------------------------------------------------------------------

class _FailingAgent(AgentFrameworkExecutor):
    """A framework executor whose turn says something, then raises ``error``."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def stream(self, context, config):  # pragma: no cover - only resumed turns run here
        return
        yield

    async def resume(self, context, config):
        yield TaskStatusUpdateEvent(
            task_id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(text="working on it")]),
            ),
        )
        raise self._error


def _wrapped(error: BaseException) -> BaseException:
    """``error`` as a framework adapter hands it on: as the cause of its own."""
    try:
        try:
            raise error
        except BaseException as inner:
            raise RuntimeError(f"Failed to stream agent: {inner}") from inner
    except RuntimeError as outer:
        return outer


async def _run_failing(error: BaseException, queue: RecordingQueue):
    """Run one resumed turn that ends on ``error``; return what it raised, if anything."""
    executor = request_executor(_FailingAgent(error))
    task = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    context = RequestContext(ServerCallContext(), task_id=TASK_ID, context_id=CONTEXT_ID, task=task)
    with patch.object(
        AionAgentRequestExecutor,
        "_setup_runtime_context",
        AsyncMock(return_value=ResponseServiceParameters()),
    ):
        try:
            await executor.execute(context, queue)
        except Exception as raised:  # noqa: BLE001 - what the request ends on is the subject
            return raised
    return None


def _failed_reason(queue: RecordingQueue) -> str:
    (failed,) = [
        event for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent))
        and event.status.state == TaskState.TASK_STATE_FAILED
    ]
    return " ".join(part.text for part in failed.status.message.parts)


@pytest.mark.parametrize("error", [
    TaskFailedError("The weather service is unavailable."),
    _wrapped(TaskFailedError("The weather service is unavailable.")),
], ids=["raised", "wrapped-by-the-framework"])
async def test_an_agents_own_reason_is_what_the_failed_task_says(queue, runtime_context, caplog, error) -> None:
    """The agent put the failure in words for its caller; the task carries them.

    Not a crash: the request ends normally, so a2a-sdk logs no traceback,
    and the log has one WARNING line saying the task failed.
    """
    with caplog.at_level(logging.WARNING):
        raised = await _run_failing(error, queue)

    assert raised is None
    assert _failed_reason(queue) == "The weather service is unavailable."
    (line,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert line.levelno == logging.WARNING and line.exc_info is None


async def test_an_sdk_error_naming_what_is_missing_says_so_without_ids(queue, runtime_context) -> None:
    """A deployment without a daemon identity is told what to fix, not which resource id."""
    raised = await _run_failing(_wrapped(AionDaemonIdentityRequired("AgentEnvironment", "01a13ac7")), queue)

    assert raised is None
    reason = _failed_reason(queue)
    assert "AgentEnvironment" in reason and "Identity tab" in reason
    assert "01a13ac7" not in reason


async def test_a_crash_says_it_crashed_and_where_to_look(queue, runtime_context, caplog) -> None:
    """The exception's text stays in the log; the task names the crash and its task id."""
    with caplog.at_level(logging.ERROR):
        raised = await _run_failing(RuntimeError("secret at https://internal.host"), queue)

    assert raised is None, "a raised error would have a2a-sdk write a FAILED of its own"
    reason = _failed_reason(queue)
    assert reason == CRASH_REASON.format(task_id=TASK_ID)
    assert "internal.host" not in reason
    crash, = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert "internal.host" in crash.getMessage() and crash.exc_info is not None


@pytest.mark.parametrize("error,expected", [
    (TaskFailedError("why"), "why"),
    (_wrapped(TaskFailedError("why")), "why"),
    (RuntimeError("no reason here"), None),
    (_wrapped(RuntimeError("nor here")), None),
], ids=["direct", "as-cause", "plain", "plain-as-cause"])
def test_the_reason_is_found_along_the_chain_of_causes(error, expected) -> None:
    assert public_reason_of(error) == expected


def test_a_suppressed_context_is_not_searched() -> None:
    """``raise X from None`` says the earlier error is not this one's story."""
    try:
        try:
            raise TaskFailedError("hidden")
        except TaskFailedError:
            raise RuntimeError("unrelated") from None
    except RuntimeError as error:
        assert public_reason_of(error) is None
