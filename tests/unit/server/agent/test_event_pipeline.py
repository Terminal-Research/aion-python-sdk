"""What `AionEventPipeline` promises about every event on its way to a client.

Every event either framework produces passes through this one object, and it
is the object that decides four things nobody else decides: whether the turn
has been announced, whether the event is a duplicate, whether it reaches the
client or only the database, and whether the task has already been closed.

None of that had a test of its own. The pipeline was patched out in the tests
of its own caller, so the caller's tests state that it was constructed and
nothing about what it does. Everything below is written against the real
collaborators - a real task manager over an in-memory store, a real task
updater - so each assertion is about a value that reached a client or a
stored task, not about a call that happened.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.a2a.utils import mark_status_event_ephemeral
from aion.server.agent.execution.event_pipeline import (
    AionEventPipeline,
    OutboundFileNotStoredError,
)
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
    set_task_manager,
)
from aion.server.files.a2a import EventTransform, TransformReport
from aion.server.files.storage import FileUploadErrorCode, UploadFailure
from aion.server.tasks.task_manager import AionTaskManager
from tests.unit.support.events import RecordingQueue

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


class ReplacingTransformer:
    """A file transformer that rewrites the text of every message it sees.

    The real one rewrites inline file parts into URLs; what matters to the
    pipeline is only that it runs, and that what it returns is what travels
    onward.
    """

    def __init__(self) -> None:
        self.seen: list[object] = []

    async def transform_event(self, event):
        self.seen.append(event)
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message"):
            event.status.message.parts[0].text = "transformed"
        return EventTransform(event, TransformReport())


@pytest.fixture(autouse=True)
def _execution_scope():
    """The pipeline reads its task manager out of the per-request scope."""
    init_execution_scope()
    yield
    clear_execution_scope()


@pytest.fixture
def store() -> InMemoryTaskStore:
    return InMemoryTaskStore()


@pytest.fixture
def call_context() -> ServerCallContext:
    return ServerCallContext()


@pytest.fixture
def manager(store: InMemoryTaskStore, call_context: ServerCallContext) -> AionTaskManager:
    """A real task manager, registered in the scope the way execution does."""
    task_manager = AionTaskManager(
        task_store=store,
        context=call_context,
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        initial_message=None,
    )
    set_task_manager(task_manager)
    return task_manager


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


def _pipeline(queue: RecordingQueue, *, task_started: bool = False, transformer=None):
    """A pipeline wired the way `AionRequestExecutor` wires one."""
    updater = TaskUpdater(queue, TASK_ID, CONTEXT_ID)
    return AionEventPipeline(queue, updater, transformer, task_started=task_started)


def _message(text: str, *, message_id: str | None = None) -> Message:
    return Message(
        context_id=CONTEXT_ID,
        task_id=TASK_ID,
        message_id=message_id or str(uuid.uuid4()),
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
    )


def _status(
    text: str | None = None,
    *,
    state: TaskState = TaskState.TASK_STATE_WORKING,
    message: Message | None = None,
    ephemeral: bool = False,
) -> TaskStatusUpdateEvent:
    carried = message if message is not None else (_message(text) if text else None)
    status = TaskStatus(state=state, message=carried) if carried else TaskStatus(state=state)
    event = TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)
    if ephemeral:
        mark_status_event_ephemeral(event)
    return event


def _artifact_update(text: str, *, artifact_id: str = "art-1") -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(artifact_id=artifact_id, name="chunk", parts=[Part(text=text)]),
        append=True,
        last_chunk=False,
    )


def _task(*, state: TaskState = TaskState.TASK_STATE_WORKING, history=()) -> Task:
    return Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=state),
        history=list(history),
    )


def _status_events(queue: RecordingQueue) -> list[TaskStatusUpdateEvent]:
    return [e for e in queue.events if isinstance(e, TaskStatusUpdateEvent)]


# --------------------------------------------------------------------------
# Announcing the turn
# --------------------------------------------------------------------------

async def test_the_first_event_announces_the_work_once(manager, queue) -> None:
    """The client learns the task is working, and learns it exactly once.

    The announcement is the pipeline's job precisely because the agent does
    not make one: an agent that spends a minute before its first token would
    otherwise leave the client with no sign the turn started.
    """
    pipeline = _pipeline(queue)

    await pipeline.process(_status("first"))
    await pipeline.process(_status("second"))

    working = [
        e for e in _status_events(queue)
        if e.status.state == TaskState.TASK_STATE_WORKING and not e.status.HasField("message")
    ]
    assert len(working) == 1
    assert queue.events[0] is working[0], "the announcement precedes the agent's own events"


async def test_a_resumed_task_announces_nothing(manager, queue) -> None:
    """A task resumed after an interrupt was announced before the agent ran.

    `task_started=True` is how the executor says so, and a second
    announcement would tell the client the turn restarted when it did not.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("first"))

    assert len(queue.events) == 1
    assert queue.events[0].status.HasField("message")


# --------------------------------------------------------------------------
# Who sees what
# --------------------------------------------------------------------------

async def test_a_status_update_reaches_the_client(manager, queue) -> None:
    """Status updates are the stream: they go to the client, not only to the store."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("progress"))

    assert [e.status.message.parts[0].text for e in _status_events(queue)] == ["progress"]


async def test_a_task_is_persisted_without_reaching_the_client(manager, queue, store, call_context) -> None:
    """A whole `Task` is a state correction, not a frame on the stream.

    Sending it to the client would replay history it already has; it is saved
    silently so the next `tasks/get` answers with it.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_task(history=[_message("from the agent")]))

    assert queue.events == []
    stored = await store.get(TASK_ID, call_context)
    assert [m.parts[0].text for m in stored.history] == ["from the agent"]


async def test_a_message_is_persisted_without_reaching_the_client(manager, queue, store, call_context) -> None:
    """A bare `Message` is durable state, not a frame the client is sent twice.

    The task manager wraps it in a working status update, which is what puts
    it on the task; the next status update folds it from `status.message` into
    history. The pipeline's part is only that it went to the store and not to
    the queue.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_message("noted"))

    assert queue.events == []
    stored = await store.get(TASK_ID, call_context)
    assert stored.status.message.parts[0].text == "noted"


async def test_the_client_receives_a_copy_the_producer_cannot_still_change(manager, queue) -> None:
    """What was enqueued is what the client gets, whatever the agent does next.

    The producer keeps a reference to the event it emitted; without a copy, a
    later mutation would rewrite a frame that was already on its way out.
    """
    pipeline = _pipeline(queue, task_started=True)
    event = _status("as emitted")

    await pipeline.process(event)
    event.status.message.parts[0].text = "changed afterwards"

    assert _status_events(queue)[0].status.message.parts[0].text == "as emitted"


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------

async def test_a_message_the_task_already_holds_is_dropped(manager, queue, store, call_context) -> None:
    """Re-emitting an already-recorded message must not duplicate it.

    Agents re-emit: a framework that replays its own state, an extension that
    echoes what it just wrote. The task is the authority on what it already
    contains.
    """
    await manager.process(_task(history=[_message("only once", message_id="m-1")]))
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_message("only once", message_id="m-1"))

    stored = await store.get(TASK_ID, call_context)
    assert [m.message_id for m in stored.history] == ["m-1"]
    assert not stored.status.HasField("message"), "the repeat was stored as a pending message"


async def test_every_artifact_chunk_is_streamed(manager, queue) -> None:
    """Artifact updates are never deduplicated: each one is a further chunk.

    Two chunks of one streamed reply share an artifact id by design, so the
    rule that drops repeats elsewhere would silently truncate the stream.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_artifact_update("Hel"))
    await pipeline.process(_artifact_update("lo"))

    chunks = [e for e in queue.events if isinstance(e, TaskArtifactUpdateEvent)]
    assert [c.artifact.parts[0].text for c in chunks] == ["Hel", "lo"]


async def test_an_ephemeral_status_does_not_claim_its_message_was_stored(manager, queue) -> None:
    """An ephemeral event never reaches the task, so it cannot mask a later one.

    Folding it into the deduplicator's cache would leave that cache believing
    the task holds a message the database never received - and the durable
    event carrying it would then be dropped as a duplicate.
    """
    pipeline = _pipeline(queue, task_started=True)
    carried = _message("the same message", message_id="m-2")

    await pipeline.process(_status(message=carried, ephemeral=True))
    await pipeline.process(_status(message=_message("the same message", message_id="m-2")))

    delivered = [e for e in _status_events(queue) if e.status.HasField("message")]
    assert len(delivered) == 2, "the durable event was dropped as a duplicate of an ephemeral one"


# --------------------------------------------------------------------------
# Knowing the task is closed
# --------------------------------------------------------------------------

async def test_terminal_seen_is_false_until_something_closes_the_task(manager, queue) -> None:
    """Working updates leave the task open, however many arrive."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("still going"))

    assert pipeline.terminal_seen is False


async def test_a_terminal_status_event_closes_the_task_for_good(manager, queue) -> None:
    """Once closed, the flag stays set: a later event cannot reopen the task.

    The caller reads this to decide whether a crashing producer still owes a
    terminal status. A flag that could fall back to False would produce a
    second terminal event contradicting the first.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status(state=TaskState.TASK_STATE_COMPLETED))
    assert pipeline.terminal_seen is True

    await pipeline.process(_status("something after the end"))
    assert pipeline.terminal_seen is True


async def test_a_terminal_task_closes_the_task_too(manager, queue) -> None:
    """The outcome counts however it arrived - as an event or as a whole task."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_task(state=TaskState.TASK_STATE_FAILED))

    assert pipeline.terminal_seen is True


# --------------------------------------------------------------------------
# Collaborators that may be absent
# --------------------------------------------------------------------------

async def test_the_transformer_runs_before_the_event_is_routed(manager, queue) -> None:
    """What the client receives is the transformed event, not the original.

    Inline file bytes are rewritten into URLs here; a transform applied after
    routing would put the raw bytes on the wire.
    """
    transformer = ReplacingTransformer()
    pipeline = _pipeline(queue, task_started=True, transformer=transformer)

    await pipeline.process(_status("original"))

    assert _status_events(queue)[0].status.message.parts[0].text == "transformed"


class RefusingTransformer:
    """A file transformer whose storage refuses every inline file.

    Like the real one, it takes the files out of the event, reports each one,
    and leaves an artifact with nothing else in it unsent.
    """

    async def transform_event(self, event):
        parts = (event.status.message.parts if isinstance(event, TaskStatusUpdateEvent)
                 else event.artifact.parts)
        refused = [part for part in parts if part.raw]
        if not refused:
            return EventTransform(event, TransformReport())
        kept = [part for part in parts if not part.raw]
        del parts[:]
        parts.extend(kept)
        report = TransformReport(
            failures=[UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN) for _ in refused],
            changed=True,
        )
        if isinstance(event, TaskArtifactUpdateEvent) and not kept:
            return EventTransform(None, report)
        return EventTransform(event, report)


def _file_part() -> Part:
    return Part(raw=b"%PDF", media_type="application/pdf", filename="report.pdf")


async def test_a_file_that_could_not_be_stored_fails_the_turn(manager, queue) -> None:
    """The pipeline raises, and the executor turns that into a FAILED task."""
    pipeline = _pipeline(queue, task_started=True, transformer=RefusingTransformer())
    event = _artifact_update("unused")
    event.artifact.parts[0].CopyFrom(_file_part())

    with pytest.raises(OutboundFileNotStoredError) as raised:
        await pipeline.process(event)

    assert not queue.events, "an artifact with no part left is not sent hollow"
    assert raised.value.public_reason == FileUploadErrorCode.STORAGE_FORBIDDEN.public_reason
    assert "STORAGE_FORBIDDEN" in str(raised.value)


async def test_an_answer_sent_with_the_file_reaches_the_client_first(manager, queue) -> None:
    """The text beside a file that failed is delivered before the turn fails."""
    pipeline = _pipeline(queue, task_started=True, transformer=RefusingTransformer())
    event = _status("here is the report")
    event.status.message.parts.append(_file_part())

    with pytest.raises(OutboundFileNotStoredError):
        await pipeline.process(event)

    (delivered,) = _status_events(queue)
    assert [part.text for part in delivered.status.message.parts] == ["here is the report"]


@pytest.mark.parametrize("state", [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_INPUT_REQUIRED])
async def test_an_event_with_a_file_that_failed_does_not_end_the_turn(manager, queue, state) -> None:
    """Its text still goes out, but the outcome is the FAILED that follows."""
    pipeline = _pipeline(queue, task_started=True, transformer=RefusingTransformer())
    event = _status("done", state=state)
    event.status.message.parts.append(_file_part())

    with pytest.raises(OutboundFileNotStoredError):
        await pipeline.process(event)

    (delivered,) = _status_events(queue)
    assert delivered.status.state == TaskState.TASK_STATE_WORKING
    assert delivered.status.message.parts[0].text == "done"
    assert pipeline.terminal_seen is False


async def test_without_a_task_manager_the_stream_still_works(queue) -> None:
    """No scope-registered task manager means nothing is stored - and nothing raises.

    This is the shape of a request that failed before the manager was
    registered. Losing the durable copy is bad; taking the client's stream
    down with it is worse.
    """
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_message("nowhere to store this"))
    await pipeline.process(_status("but this still streams"))

    assert [e.status.message.parts[0].text for e in _status_events(queue)] == [
        "but this still streams"
    ]


async def test_a_pending_status_message_survives_a_task_saved_over_it(
    manager, queue, store, call_context
) -> None:
    """A message held in `status.message` is not lost when a whole task lands.

    Status messages are folded into history by the next status update. A
    `Task` saved directly skips that chain, so the pipeline folds the pending
    one in itself - otherwise the agent's last reply disappears between the
    status that carried it and the task that replaced it.

    The status update is persisted here the way the real server persists one:
    by the consumer on the far side of the queue, not by the pipeline.
    """
    await manager.process(_status("carried in status"))
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_task(history=[_message("from the incoming task")]))

    stored = await store.get(TASK_ID, call_context)
    assert [m.parts[0].text for m in stored.history] == [
        "carried in status",
        "from the incoming task",
    ]


def message(id, role=Role.ROLE_AGENT, extensions=()):
    return Message(message_id=id, role=role, extensions=extensions)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['task', 'message', 'status'])
async def test_annotates_new_agent_messages_before_storage_or_delivery(kind):
    new = message('new', extensions=['urn:existing', 'urn:cron'])
    old = message('old', extensions=['urn:old'])
    user = message('user', Role.ROLE_USER)
    status = TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=new)
    if kind == 'task':
        event = Task(id='task', history=[old, user, new], status=status)
    elif kind == 'status':
        event = TaskStatusUpdateEvent(task_id='task', status=status)
    else:
        event = new
    before = event.SerializeToString()
    queue, manager = AsyncMock(), AsyncMock()
    manager.get_task.return_value = None
    pipeline = AionEventPipeline(
        queue, AsyncMock(), task_started=True,
        response_parameters=ResponseServiceParameters(('urn:cron', 'urn:verified')),
        prior_message_ids=frozenset({'old', 'user'}),
    )
    with patch('aion.server.agent.execution.event_pipeline.exec_scope_get_task_manager', return_value=manager):
        await pipeline.process(event)
    output = (queue.enqueue_event if kind == 'status' else manager.process).call_args.args[0]
    actual = output if kind == 'message' else output.status.message
    assert list(actual.extensions) == ['urn:existing', 'urn:cron', 'urn:verified']
    if kind == 'task':
        assert list(output.history[0].extensions) == ['urn:old']
        assert not output.history[1].extensions
    assert event.SerializeToString() == before


@pytest.mark.parametrize('role,id', [(Role.ROLE_USER, 'new'), (Role.ROLE_AGENT, 'old')])
def test_old_status_and_user_messages_keep_their_provenance(role, id):
    event = TaskStatusUpdateEvent(status=TaskStatus(message=message(id, role)))
    result = ResponseServiceParameters(('urn:cron',)).annotate(event, frozenset({'old'}))
    assert not result.status.message.extensions


def test_empty_task_does_not_fabricate_a_message():
    result = ResponseServiceParameters(('urn:cron',)).annotate(Task(id='task'), frozenset())
    assert not result.history
    assert not result.status.HasField('message')
