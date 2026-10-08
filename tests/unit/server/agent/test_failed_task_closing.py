"""Who closes a task when the agent crashes, and when the server must not.

A crashed execution leaves a task that nothing is working on. The JSON-RPC
error reaches the caller of that one request; the stored task is what every
later reader sees, so the server marks it FAILED itself.

The exception is a producer that had already reported its own outcome before
crashing. That outcome is the authoritative one, and a second terminal event
would contradict it. No authoring surface lets an agent report a terminal
state - `a2a_outbox` is read from the final state, which a crash never
reaches - so this case cannot be driven from a scenario agent, and is stated
here instead. It is reachable in production: an extension handler that emits
its own terminal event and then fails.

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
from a2a.utils.errors import InternalError

from aion.core.runtime.context import (
    AionRuntimeContext,
    AionRuntimeContextRegistry,
    ForwardedAttribution,
)
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.event_pipeline import AionEventPipeline
from aion.server.agent.execution.extensions import ROUTED_EXTENSION_METADATA_KEY
from aion.server.agent.execution.request_executor import AionAgentRequestExecutor
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
    set_task_manager,
)
from aion.server.files.a2a import A2AFileTransformer
from aion.server.files.storage import FileUploadErrorCode, FileUploadManager, UploadFailure
from aion.server.tasks.task_manager import AionTaskManager
from tests.unit.support.events import RecordingQueue
from tests.unit.support.files import OutcomeBackend, distribution_payload, principal

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


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

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]


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

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

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

    await AionAgentRequestExecutor._close_failed_task(updater, pipeline)

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


class _Agent:
    """An agent whose turn answers with a report the storage service refuses."""

    def __init__(self) -> None:
        self.reached_the_end = False
        self.discard_undelivered = AsyncMock()

    async def resume(self, *, context):
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


async def _run_refused(agent, queue, *, extension_handlers=(), metadata=None):
    """Run one resumed turn of ``agent`` against storage that refuses every file."""
    backend = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)])
    executor = AionAgentRequestExecutor(
        agent,
        file_transformer=A2AFileTransformer(FileUploadManager(backend)),
        extension_handlers=extension_handlers,
    )
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        metadata=metadata,
    )
    context = RequestContext(ServerCallContext(), task_id=TASK_ID, context_id=CONTEXT_ID, task=task)
    with (
        patch.object(
            AionAgentRequestExecutor,
            "_setup_runtime_context",
            AsyncMock(return_value=ResponseServiceParameters()),
        ),
        pytest.raises(InternalError) as raised,
    ):
        await executor.execute(context, queue)
    return raised.value, context


async def test_a_file_that_could_not_be_stored_fails_the_task(queue, runtime_context, caplog) -> None:
    """The agent's answer goes out, the file does not, and the task is FAILED.

    The caller is told why in the fixed public wording of the failure. What
    the storage service actually answered stays in the log: one WARNING per
    file, and one ERROR line for the failed execution.
    """
    agent = _Agent()

    with caplog.at_level(logging.WARNING):
        error, _ = await _run_refused(agent, queue)

    assert error.message == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    assert not agent.reached_the_end, "the run stops at the file it could not deliver"
    replies = [
        [part.text for part in event.status.message.parts]
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    ]
    assert replies == [["here is the report"]]
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]

    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "report.pdf" in warning.getMessage()
    assert "STORAGE_FORBIDDEN" in warning.getMessage()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "Execution failed: 1 outbound file(s) could not be stored: STORAGE_FORBIDDEN"
    ]
    assert errors[0].exc_info is None


async def test_the_agent_forgets_the_undelivered_event_before_the_task_fails(queue, runtime_context) -> None:
    """A turn that follows the FAILED state finds nothing of the file to read back."""
    agent = _Agent()
    closed_when_discarded = []
    agent.discard_undelivered.side_effect = lambda *args: closed_when_discarded.append(
        _terminal_states(queue)
    )

    _, context = await _run_refused(agent, queue)

    passed_context, event = agent.discard_undelivered.call_args.args
    assert passed_context is context
    assert event.status.message.parts[1] == REPORT, "the event as the agent produced it"
    assert closed_when_discarded == [[]]


async def test_a_failure_to_forget_does_not_change_the_outcome(queue, runtime_context, caplog) -> None:
    agent = _Agent()
    agent.discard_undelivered.side_effect = RuntimeError("artifact service is gone")

    error, _ = await _run_refused(agent, queue)

    assert error.message == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]
    assert "Could not discard" in caplog.text


async def test_an_extension_handler_without_a_copy_is_not_asked(queue, runtime_context) -> None:
    """The task's own handler is asked, and one that keeps nothing is skipped."""

    class _Handler:
        uri = "https://example.com/ext"

        def __init__(self) -> None:
            self.resume = _Agent().resume

    agent = _Agent()

    error, _ = await _run_refused(
        agent,
        queue,
        extension_handlers=[_Handler()],
        metadata={ROUTED_EXTENSION_METADATA_KEY: _Handler.uri},
    )

    assert error.message == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    agent.discard_undelivered.assert_not_called()
    assert _terminal_states(queue) == [TaskState.TASK_STATE_FAILED]
