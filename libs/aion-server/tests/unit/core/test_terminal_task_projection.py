"""Tests for TerminalTaskProjection.

The projection owns the outbound contract: a task must leave the active states
through exactly one event — the full Task — never through a status update.
"""

import asyncio
import pytest
from a2a.types import (
    Message,
    Role,
    SendMessageConfiguration,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InternalError
from a2a.utils.task import apply_history_length
from unittest.mock import AsyncMock, Mock

from aion.server.core.app.handlers.terminal_task_projection import TerminalTaskProjection

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

NON_ACTIVE_STATES = [
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
]


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _message(text: str = "SUMMER", message_id: str = "msg-1") -> Message:
    return Message(
        message_id=message_id,
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        role=Role.ROLE_AGENT,
    )


def _task(state: TaskState, history: list[Message] | None = None, task_id: str = TASK_ID) -> Task:
    task = Task(id=task_id, context_id=CONTEXT_ID, status=TaskStatus(state=state))
    if history:
        task.history.extend(history)
    return task


def _status(state: TaskState, message: Message | None = None) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


async def _stream(*events):
    for event in events:
        yield event


def _store(task: Task | None) -> Mock:
    store = Mock()
    store.get = AsyncMock(return_value=task)
    store.save = AsyncMock()
    return store


async def _collect(projection: TerminalTaskProjection, *events) -> list:
    return [event async for event in projection.project(_stream(*events))]


def _saved(store: Mock) -> Task:
    store.save.assert_awaited_once()
    return store.save.await_args.args[0]


class TestTerminalTaskProjection:
    """The projection replaces non-active status updates with the stored Task."""

    @pytest.mark.parametrize("state", NON_ACTIVE_STATES)
    @pytest.mark.anyio
    async def test_non_active_status_is_replaced_by_task(self, state):
        """Every non-active state is delivered as a Task, never as a status update."""
        stored = _task(state, history=[_message()])
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(
            projection,
            _task(TaskState.TASK_STATE_SUBMITTED),
            _status(TaskState.TASK_STATE_WORKING),
            _status(state, _message()),
        )

        assert [type(event) for event in result] == [Task, TaskStatusUpdateEvent, Task]
        assert result[-1] is stored
        assert result[-1].status.state == state
        assert not any(
            isinstance(event, TaskStatusUpdateEvent)
            and event.status.state == state
            for event in result
        )

    @pytest.mark.anyio
    async def test_working_events_pass_through_untouched(self):
        """Active-state events and artifacts are streamed as they arrive."""
        working = _status(TaskState.TASK_STATE_WORKING, _message())
        artifact = TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID)
        projection = TerminalTaskProjection(
            task_store=_store(_task(TaskState.TASK_STATE_COMPLETED)),
            call_context=Mock(),
        )

        result = await _collect(projection, working, artifact)

        assert result[0] is working
        assert result[1] is artifact

    @pytest.mark.anyio
    async def test_task_id_is_tracked_from_the_stream(self):
        """A server-generated task id is discovered from the outbound events."""
        store = _store(_task(TaskState.TASK_STATE_COMPLETED))
        call_context = Mock()
        projection = TerminalTaskProjection(task_store=store, call_context=call_context)

        await _collect(
            projection,
            _task(TaskState.TASK_STATE_SUBMITTED),
            _status(TaskState.TASK_STATE_COMPLETED),
        )

        store.get.assert_awaited_once_with(TASK_ID, call_context)

    @pytest.mark.anyio
    async def test_seeded_task_id_is_used_without_task_events(self):
        """Resubscribe passes the task id explicitly, so no Task event is needed."""
        store = _store(_task(TaskState.TASK_STATE_COMPLETED))
        projection = TerminalTaskProjection(
            task_store=store,
            call_context=Mock(),
            task_id=TASK_ID,
        )

        result = await _collect(projection, _status(TaskState.TASK_STATE_COMPLETED))

        assert [type(event) for event in result] == [Task]

    @pytest.mark.anyio
    async def test_history_length_is_applied_to_the_final_task(self):
        """The final Task honours request-level history_length."""
        stored = _task(TaskState.TASK_STATE_COMPLETED, history=[_message()])
        configuration = SendMessageConfiguration(history_length=0)
        projection = TerminalTaskProjection(
            task_store=_store(stored),
            call_context=Mock(),
            task_transform=lambda task: apply_history_length(task, configuration),
        )

        result = await _collect(projection, _status(TaskState.TASK_STATE_COMPLETED))

        assert len(result[-1].history) == 0
        assert len(stored.history) == 1

    @pytest.mark.anyio
    async def test_missing_snapshot_fails_the_stream(self):
        """A task that produced events and vanished is a store defect, not a result."""
        projection = TerminalTaskProjection(task_store=_store(None), call_context=Mock())

        with pytest.raises(InternalError):
            await _collect(projection, _status(TaskState.TASK_STATE_COMPLETED, _message()))

    @pytest.mark.anyio
    async def test_missing_snapshot_fails_even_without_a_terminal_status(self):
        """The same defect is reported when the agent never declared an outcome."""
        projection = TerminalTaskProjection(task_store=_store(None), call_context=Mock())

        with pytest.raises(InternalError):
            await _collect(projection, _status(TaskState.TASK_STATE_WORKING, _message()))

    @pytest.mark.anyio
    async def test_stale_active_snapshot_is_settled_before_it_is_emitted(self):
        """A snapshot still in an active state is closed rather than emitted as is."""
        store = _store(_task(TaskState.TASK_STATE_WORKING))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = await _collect(projection, _status(TaskState.TASK_STATE_COMPLETED))

        # The active snapshot also opens the stream, hence two Tasks.
        assert [type(event) for event in result] == [Task, Task]
        assert result[0].status.state == TaskState.TASK_STATE_WORKING
        assert result[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert _saved(store).status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.anyio
    async def test_event_after_non_active_status_releases_it_in_order(self):
        """A misbehaving agent emitting past a terminal state loses no events."""
        terminal = _status(TaskState.TASK_STATE_COMPLETED)
        trailing = TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID)
        stored = _task(TaskState.TASK_STATE_COMPLETED)
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(projection, terminal, trailing)

        assert result == [terminal, trailing, stored]

    @pytest.mark.anyio
    async def test_messages_are_passed_through(self):
        """A Message is not a task event and the projection leaves it alone.

        Aion's executor announces a Task first, so the SDK consumer is in task
        mode and a Message never reaches a real stream; if one ever did, the
        consumer raises and the failure path closes the stream with a Task.
        """
        message = Message(message_id="msg-1", context_id=CONTEXT_ID, role=Role.ROLE_AGENT)
        artifact = TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID)
        stored = _task(TaskState.TASK_STATE_COMPLETED)
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(projection, message, artifact)

        assert result == [message, artifact, stored]


class TestOpeningSnapshot:
    """A task lifecycle stream begins with a Task, whether or not it is new."""

    @pytest.mark.anyio
    async def test_resumed_stream_is_opened_with_the_stored_task(self):
        """A resumed task emits no Task of its own, so the store supplies one."""
        store = _store(None)
        store.get = AsyncMock(side_effect=[
            _task(TaskState.TASK_STATE_WORKING, history=[_message()]),
            _task(TaskState.TASK_STATE_INPUT_REQUIRED),
        ])
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = await _collect(
            projection,
            _status(TaskState.TASK_STATE_WORKING),
            _status(TaskState.TASK_STATE_INPUT_REQUIRED, _message("Q", "msg-q")),
        )

        # The working status update that opened the turn says nothing the
        # snapshot has not already said, so it is dropped.
        assert [type(event) for event in result] == [Task, Task]
        assert result[0].status.state == TaskState.TASK_STATE_WORKING
        assert [m.message_id for m in result[0].history] == ["msg-1"]
        assert result[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.anyio
    async def test_opening_event_is_kept_when_it_carries_a_message(self):
        """Only an empty restatement is dropped, never content."""
        stored = _task(TaskState.TASK_STATE_WORKING)
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(
            projection,
            _status(TaskState.TASK_STATE_WORKING, _message()),
            _status(TaskState.TASK_STATE_COMPLETED),
        )

        assert [type(event) for event in result] == [Task, TaskStatusUpdateEvent, Task]

    @pytest.mark.anyio
    async def test_opening_event_is_kept_when_it_changes_state(self):
        """A real transition out of the snapshot's state is not a restatement."""
        stored = _task(TaskState.TASK_STATE_WORKING)
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(
            projection,
            TaskArtifactUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID),
            _status(TaskState.TASK_STATE_COMPLETED),
        )

        assert [type(event) for event in result] == [Task, TaskArtifactUpdateEvent, Task]

    @pytest.mark.anyio
    async def test_stream_that_already_opens_with_a_task_is_left_alone(self):
        """A new task announces itself, so no snapshot is prepended."""
        store = _store(_task(TaskState.TASK_STATE_COMPLETED))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = await _collect(
            projection,
            _task(TaskState.TASK_STATE_SUBMITTED),
            _status(TaskState.TASK_STATE_COMPLETED),
        )

        assert [type(event) for event in result] == [Task, Task]
        store.get.assert_awaited_once()

    @pytest.mark.anyio
    async def test_non_active_snapshot_does_not_open_the_stream(self):
        """An opening Task in a non-active state would read as a terminal one."""
        projection = TerminalTaskProjection(
            task_store=_store(_task(TaskState.TASK_STATE_INPUT_REQUIRED)),
            call_context=Mock(),
        )

        result = await _collect(projection, TaskArtifactUpdateEvent(task_id=TASK_ID))

        assert [type(event) for event in result] == [TaskArtifactUpdateEvent, Task]

    @pytest.mark.anyio
    async def test_opening_snapshot_honours_history_length(self):
        """Truncation applies to both ends of the stream."""
        configuration = SendMessageConfiguration(history_length=0)
        projection = TerminalTaskProjection(
            task_store=_store(_task(TaskState.TASK_STATE_WORKING, history=[_message()])),
            call_context=Mock(),
            task_transform=lambda task: apply_history_length(task, configuration),
        )

        result = await _collect(projection, _status(TaskState.TASK_STATE_WORKING))

        assert len(result[0].history) == 0


class TestFailureClosing:
    """A failing stream still closes with a Task, unless there is no task at all."""

    @staticmethod
    async def _failing_stream():
        yield _status(TaskState.TASK_STATE_WORKING, _message())
        raise RuntimeError("boom")

    @pytest.mark.anyio
    async def test_failure_closes_the_task_and_ends_the_stream(self):
        """The client is told the task failed instead of getting a broken stream."""
        store = _store(_task(TaskState.TASK_STATE_WORKING))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = [event async for event in projection.project(self._failing_stream())]

        assert [type(event) for event in result] == [Task, TaskStatusUpdateEvent, Task]
        assert result[-1].status.state == TaskState.TASK_STATE_FAILED
        assert _saved(store).status.state == TaskState.TASK_STATE_FAILED

    @pytest.mark.anyio
    async def test_failure_keeps_a_state_the_agent_already_declared(self):
        """A task the consumer already settled is emitted as it stands."""
        stored = _task(TaskState.TASK_STATE_FAILED)
        store = _store(stored)
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = [event async for event in projection.project(self._failing_stream())]

        assert result[-1] is stored
        store.save.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failure_without_a_task_propagates(self):
        """A request that never created a task has no terminal state to report."""
        store = _store(None)
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        with pytest.raises(RuntimeError, match="boom"):
            [event async for event in projection.project(self._failing_stream())]

    @pytest.mark.anyio
    async def test_cancellation_is_never_swallowed(self):
        """Client disconnects must not be reported as a failed task."""
        store = _store(_task(TaskState.TASK_STATE_WORKING))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        async def canceled_stream():
            yield _status(TaskState.TASK_STATE_WORKING)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            [event async for event in projection.project(canceled_stream())]

        store.save.assert_not_awaited()


class TestUnsettledStream:
    """A stream that ends without an outcome is settled as COMPLETED."""

    @pytest.mark.anyio
    async def test_active_task_is_completed_when_the_stream_ends(self):
        """Nothing reported a failure, so the task is closed as COMPLETED."""
        store = _store(_task(TaskState.TASK_STATE_WORKING))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        result = await _collect(projection, _status(TaskState.TASK_STATE_WORKING, _message()))

        assert [type(event) for event in result] == [Task, TaskStatusUpdateEvent, Task]
        assert result[-1].status.state == TaskState.TASK_STATE_COMPLETED
        assert _saved(store).status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.anyio
    async def test_settling_invents_no_message(self):
        """A silent stream completes without a fabricated answer."""
        stored = _task(TaskState.TASK_STATE_WORKING)
        projection = TerminalTaskProjection(task_store=_store(stored), call_context=Mock())

        result = await _collect(projection, _status(TaskState.TASK_STATE_WORKING))

        assert not result[-1].status.HasField("message")

    @pytest.mark.anyio
    async def test_empty_stream_without_a_task_id_emits_nothing(self):
        """With no task and no events there is nothing to close."""
        store = _store(_task(TaskState.TASK_STATE_COMPLETED))
        projection = TerminalTaskProjection(task_store=store, call_context=Mock())

        assert await _collect(projection) == []
        store.get.assert_not_awaited()
