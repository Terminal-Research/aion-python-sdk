"""What `AionEventPipeline` promises about every event on its way to a client.

Every event either framework produces passes through this one object, and it
is the object that decides four things nobody else decides: whether the task
has started working, whether the event is a duplicate, whether it reaches the
client or only the database, and whether the task has already been closed. It
also fills the SDK's own artifacts the same way for every executor: it joins
the chunks of the streamed answer, adds each thought to the task's thoughts,
and gives a task that completes with nothing but messages its answer.

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

from aion.core.a2a.deltas import stream_delta_chunk, stream_delta_end, thinking_delta
from aion.core.a2a.enums import ArtifactId
from aion.server.a2a.outbox import outbox_task
from aion.server.a2a.response_extensions import ResponseServiceParameters
from aion.server.agent.execution.event_pipeline import (
    AionEventPipeline,
    OutboundFileNotStoredError,
)
from aion.server.agent.execution.scope import (
    clear_execution_scope,
    init_execution_scope,
    set_task_manager,
)
from aion.server.files.a2a import Transform, TransformReport
from aion.server.files.storage import FileUploadErrorCode, StubFileStorageBackend, UploadFailure
from aion.server.tasks.task_manager import AionTaskManager
from tests.unit.support.events import RecordingQueue
from tests.unit.support.files import OutcomeBackend, StoringTransformer

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

    async def transform(self, event):
        self.seen.append(event)
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message"):
            event.status.message.parts[0].text = "transformed"
        return Transform(event, TransformReport())


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


def _pipeline(
    queue: RecordingQueue,
    *,
    task_started: bool = False,
    transformer=None,
):
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
) -> TaskStatusUpdateEvent:
    carried = message if message is not None else (_message(text) if text else None)
    status = TaskStatus(state=state, message=carried) if carried else TaskStatus(state=state)
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


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


def _artifact_events(queue: RecordingQueue, artifact_id: str) -> list[TaskArtifactUpdateEvent]:
    return [
        e for e in queue.events
        if isinstance(e, TaskArtifactUpdateEvent) and e.artifact.artifact_id == artifact_id
    ]


def _texts(artifact: Artifact) -> list[str]:
    return [part.text for part in artifact.parts]


def _stored_artifact(task: Task, artifact_id: str) -> Artifact:
    (artifact,) = [a for a in task.artifacts if a.artifact_id == artifact_id]
    return artifact


async def _consume(manager: AionTaskManager, queue: RecordingQueue) -> None:
    """Store what reached the queue, as the event consumer on its far side does."""
    for event in queue.events:
        await manager.process(event)


# --------------------------------------------------------------------------
# Starting the task
# --------------------------------------------------------------------------

@pytest.fixture
async def submitted(manager) -> AionTaskManager:
    """A new task, stored as the consumer stores it before the turn's first event."""
    await manager.save_task_event(_task(state=TaskState.TASK_STATE_SUBMITTED))
    return manager


def _bare_working(queue: RecordingQueue) -> list[TaskStatusUpdateEvent]:
    """The WORKING statuses that carry neither a message nor details."""
    return [
        e for e in _status_events(queue)
        if e.status.state == TaskState.TASK_STATE_WORKING
        and not e.status.HasField("message")
        and not e.metadata
    ]


async def test_a_first_status_starts_the_task_itself(submitted, queue) -> None:
    """The agent's first status is the move out of SUBMITTED; nothing precedes it.

    A WORKING with neither a message nor details in front of it would only
    repeat what that status says, in every stream, journal and push.
    """
    pipeline = _pipeline(queue)

    await pipeline.process(_status("first"))
    await pipeline.process(_status("second"))

    assert [e.status.message.parts[0].text for e in _status_events(queue)] == ["first", "second"]
    assert _bare_working(queue) == []


@pytest.mark.parametrize("state", [
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
])
async def test_a_first_status_in_another_state_is_the_move_too(submitted, queue, state) -> None:
    """A turn that ends or stops at once goes there straight from SUBMITTED."""
    pipeline = _pipeline(queue)

    await pipeline.process(_status("at once", state=state))

    assert [e.status.state for e in _status_events(queue)] == [state]


async def test_a_first_artifact_is_preceded_by_working_once(submitted, queue) -> None:
    """Output never reaches the client while the task still says SUBMITTED.

    An artifact update carries no state, so the task is moved to WORKING
    right before it - once, however many events follow.
    """
    pipeline = _pipeline(queue)

    await pipeline.process(_artifact_update("Hel"))
    await pipeline.process(_artifact_update("lo"))
    await pipeline.process(_status("done"))

    assert len(_bare_working(queue)) == 1
    assert queue.events[0] is _bare_working(queue)[0], "the move precedes the agent's own events"
    assert isinstance(queue.events[1], TaskArtifactUpdateEvent)


@pytest.mark.parametrize("first", ["message", "task"])
async def test_a_first_event_stored_silently_is_preceded_by_working(submitted, queue, first) -> None:
    """A message or task snapshot the pipeline stores itself does not reach the stream.

    The client learns the task is working from the WORKING put before it.
    """
    pipeline = _pipeline(queue)
    event = (
        _message("noted") if first == "message"
        else _task(state=TaskState.TASK_STATE_WORKING, history=[_message("from the agent")])
    )

    await pipeline.process(event)

    assert len(queue.events) == 1
    assert queue.events[0] is _bare_working(queue)[0]


async def test_a_resumed_task_announces_nothing(manager, queue) -> None:
    """A task resumed after an interrupt is WORKING before the agent runs.

    `task_started=True` is how the executor says so, and a second
    WORKING would tell the client the turn restarted when it did not.
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


async def test_a_reply_emitted_twice_before_the_task_is_stored_reaches_the_client_once(
    manager, queue, store, call_context
) -> None:
    """The agent can be quicker than the consumer that stores its new task.

    A LangGraph node that sends a reply and returns it in its state emits the
    same message twice. Nothing consumes the queue here, so the task is never
    stored: the repeat is still dropped.
    """
    pipeline = _pipeline(queue)
    reply = _message("which environment?", message_id="m-1")

    await pipeline.process(_status(message=reply))
    await pipeline.process(_status(message=reply))

    assert await store.get(TASK_ID, call_context) is None
    carried = [e.status.message for e in _status_events(queue) if e.status.HasField("message")]
    assert [m.message_id for m in carried] == ["m-1"]


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


async def test_an_artifact_of_the_turn_extends_the_artifact_the_store_has_not_written_yet(manager, queue) -> None:
    """The task the deduplicator starts from may not hold this turn's first chunk yet.

    The consumer stores events on the far side of the queue, and the agent
    can be quicker: a chunk that extends an artifact the stored task does not
    show yet is delivered like any other.
    """
    pipeline = _pipeline(queue, task_started=True)
    first = _artifact_update("Hel")
    first.append = False

    await pipeline.process(first)
    await pipeline.process(_status("still going"))
    await pipeline.process(_artifact_update("lo"))

    assert [_texts(e.artifact) for e in _artifact_events(queue, "art-1")] == [["Hel"], ["lo"]]


# --------------------------------------------------------------------------
# The streamed answer
# --------------------------------------------------------------------------

def _chunk(text: str, *, append: bool = True) -> TaskArtifactUpdateEvent:
    return stream_delta_chunk(task_id=TASK_ID, context_id=CONTEXT_ID, parts=[Part(text=text)], append=append)


def _end() -> TaskArtifactUpdateEvent:
    return stream_delta_end(task_id=TASK_ID, context_id=CONTEXT_ID)


async def test_every_chunk_goes_out_as_the_agent_produced_it(manager, queue) -> None:
    pipeline = _pipeline(queue, task_started=True)

    for chunk in (_chunk("Hel", append=False), _chunk("lo, "), _chunk("world")):
        await pipeline.process(chunk)

    assert [(_texts(e.artifact), e.append) for e in queue.events] == [
        (["Hel"], False), (["lo, "], True), (["world"], True),
    ]


async def test_the_task_keeps_every_chunk_until_the_section_ends(manager, queue, store, call_context) -> None:
    pipeline = _pipeline(queue, task_started=True)

    for chunk in (_chunk("Hel", append=False), _chunk("lo, "), _chunk("world")):
        await pipeline.process(chunk)
    await _consume(manager, queue)

    stored = await store.get(TASK_ID, call_context)
    assert _texts(_stored_artifact(stored, ArtifactId.STREAM_DELTA.value)) == ["Hel", "lo, ", "world"]


async def test_a_section_ends_with_the_end_mark_and_its_text_is_not_sent_again(manager, queue, store, call_context) -> None:
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_chunk("Hel", append=False))
    await pipeline.process(_chunk("lo"))
    await pipeline.process(_end())
    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))
    await _consume(manager, queue)

    delivered = _artifact_events(queue, ArtifactId.STREAM_DELTA.value)
    assert [(_texts(e.artifact), e.append, e.last_chunk) for e in delivered] == [
        (["Hel"], False, False), (["lo"], True, False), ([""], True, True),
    ]
    stored = await store.get(TASK_ID, call_context)
    assert _texts(_stored_artifact(stored, ArtifactId.STREAM_DELTA.value)) == ["Hello"]


async def test_a_chunk_starts_the_task(submitted, queue) -> None:
    """The task moves to WORKING right before the first chunk reaches the client."""
    pipeline = _pipeline(queue)

    await pipeline.process(_chunk("Hel", append=False))

    assert len(_bare_working(queue)) == 1
    assert queue.events[0] is _bare_working(queue)[0]
    assert _texts(queue.events[1].artifact) == ["Hel"]


# --------------------------------------------------------------------------
# The agent's thoughts
# --------------------------------------------------------------------------

def _thought(text: str) -> TaskArtifactUpdateEvent:
    return thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text=text)


async def test_the_first_thought_of_a_task_starts_its_thoughts(manager, queue, store, call_context) -> None:
    """The first thought creates the artifact, every later one adds a part to it."""
    pipeline = _pipeline(queue, task_started=True)

    for text in ("reading the logs", "found it", "checking the fix"):
        await pipeline.process(_thought(text))
    await _consume(manager, queue)

    thoughts = _artifact_events(queue, ArtifactId.THINKING_DELTA.value)
    assert [(t.append, t.last_chunk) for t in thoughts] == [(False, False), (True, False), (True, False)]
    stored = await store.get(TASK_ID, call_context)
    assert _texts(_stored_artifact(stored, ArtifactId.THINKING_DELTA.value)) == [
        "reading the logs", "found it", "checking the fix",
    ]


async def test_a_thought_of_a_later_turn_extends_the_stored_thoughts(manager, queue, store, call_context) -> None:
    """A new turn does not start the thoughts over: the task already holds some."""
    earlier = _thought("from the first turn")
    earlier.append = False
    await manager.process(earlier)
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_thought("from this turn"))
    await _consume(manager, queue)

    (thought,) = _artifact_events(queue, ArtifactId.THINKING_DELTA.value)
    assert thought.append is True
    stored = await store.get(TASK_ID, call_context)
    assert _texts(_stored_artifact(stored, ArtifactId.THINKING_DELTA.value)) == [
        "from the first turn", "from this turn",
    ]


async def test_the_server_decides_append_whatever_the_thought_says(manager, queue) -> None:
    pipeline = _pipeline(queue, task_started=True)
    stated = [_thought("one"), _thought("two")]
    stated[0].append, stated[1].append = True, False
    stated[1].last_chunk = True

    for thought in stated:
        await pipeline.process(thought)

    thoughts = _artifact_events(queue, ArtifactId.THINKING_DELTA.value)
    assert [(t.append, t.last_chunk) for t in thoughts] == [(False, False), (True, False)]


async def test_thoughts_around_a_status_extend_one_artifact(manager, queue) -> None:
    """A status between two thoughts does not make the second one start them over."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_thought("one"))
    await pipeline.process(_status("a reply"))
    await pipeline.process(_thought("two"))

    thoughts = _artifact_events(queue, ArtifactId.THINKING_DELTA.value)
    assert [t.append for t in thoughts] == [False, True]


# --------------------------------------------------------------------------
# The answer of a task that only talked
# --------------------------------------------------------------------------

def _answers(queue: RecordingQueue) -> list[TaskArtifactUpdateEvent]:
    return _artifact_events(queue, ArtifactId.ASSISTANT_ANSWER.value)


def _result(artifact_id: str = "report") -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(artifact_id=artifact_id, parts=[Part(text="content")]),
        append=False,
        last_chunk=True,
    )


@pytest.mark.parametrize("carrier", ["status", "message"])
async def test_a_task_that_only_talked_gets_its_last_message_as_its_answer(manager, queue, carrier) -> None:
    """Every part of the last agent message, delivered right before COMPLETED."""
    pipeline = _pipeline(queue, task_started=True)
    last = _message("here is the report")
    last.parts.append(Part(url="https://files.example/report.pdf", filename="report.pdf"))

    await pipeline.process(_status("looking"))
    await pipeline.process(_status(message=last) if carrier == "status" else last)
    await pipeline.process(_status(state=TaskState.TASK_STATE_COMPLETED))

    answer, completed = queue.events[-2:]
    assert answer.artifact.artifact_id == ArtifactId.ASSISTANT_ANSWER.value
    assert list(answer.artifact.parts) == list(last.parts)
    assert (answer.append, answer.last_chunk) == (False, True)
    assert completed.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_task_an_outbox_completes_holds_its_answer(manager, queue, store, call_context) -> None:
    """A whole task is stored as it is, so the answer from its history goes into it."""
    pipeline = _pipeline(queue, task_started=True)
    outbox = _task(state=TaskState.TASK_STATE_COMPLETED, history=[_message("all set")])

    await pipeline.process(outbox)

    stored = await store.get(TASK_ID, call_context)
    answer = _stored_artifact(stored, ArtifactId.ASSISTANT_ANSWER.value)
    assert _texts(answer) == ["all set"]
    assert _answers(queue) == []


async def test_the_message_on_the_completed_status_is_the_answer(manager, queue) -> None:
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("looking"))
    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))

    (answer,) = _answers(queue)
    assert _texts(answer.artifact) == ["done"]
    assert queue.events[-2] is answer


async def test_a_reply_repeated_on_the_completed_status_is_answered_once(manager, queue) -> None:
    pipeline = _pipeline(queue, task_started=True)
    reply = _message("done", message_id="m-1")

    await pipeline.process(_status(message=reply))
    await pipeline.process(_status(message=_message("done", message_id="m-1"), state=TaskState.TASK_STATE_COMPLETED))

    (answer,) = _answers(queue)
    assert _texts(answer.artifact) == ["done"]


@pytest.mark.parametrize("result", ["artifact", "streamed answer"])
async def test_a_task_with_a_result_gets_no_answer(manager, queue, result) -> None:
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_result() if result == "artifact" else _chunk("Hello", append=False))
    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))

    assert _answers(queue) == []


@pytest.mark.parametrize("artifact_id", [ArtifactId.THINKING_DELTA.value, ArtifactId.REACTION.value])
async def test_thoughts_and_reactions_are_not_results(manager, queue, artifact_id) -> None:
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_result(artifact_id))
    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))

    (answer,) = _answers(queue)
    assert _texts(answer.artifact) == ["done"]


async def test_a_result_of_an_earlier_turn_counts(manager, queue) -> None:
    await manager.process(_result())
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))

    assert _answers(queue) == []


async def test_an_answer_of_an_earlier_turn_is_replaced(manager, queue, store, call_context) -> None:
    """A copied answer is not a result: the next completed turn copies its own last message over it."""
    earlier = _result(ArtifactId.ASSISTANT_ANSWER.value)
    earlier.artifact.parts[0].text = "first answer"
    await manager.process(earlier)
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("second answer", state=TaskState.TASK_STATE_COMPLETED))
    await _consume(manager, queue)

    stored = await store.get(TASK_ID, call_context)
    assert _texts(_stored_artifact(stored, ArtifactId.ASSISTANT_ANSWER.value)) == ["second answer"]


async def test_a_completed_task_without_a_message_gets_no_answer(manager, queue, caplog) -> None:
    """Nothing to copy: the server only says so in its log."""
    pipeline = _pipeline(queue, task_started=True)

    with caplog.at_level("WARNING", logger="aion.server.agent.execution.assistant_answer"):
        await pipeline.process(_status(state=TaskState.TASK_STATE_COMPLETED))

    assert _answers(queue) == []
    assert "completed without a result or a message" in caplog.text


@pytest.mark.parametrize("state", [
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
])
async def test_a_task_that_does_not_complete_gets_no_answer(manager, queue, state) -> None:
    """The last message of a failed, cancelled or interrupted turn is not its answer."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_status("looking"))
    await pipeline.process(_status("stopped here", state=state))

    assert _answers(queue) == []


async def test_streamed_chunks_are_a_result(manager, queue) -> None:
    """A task that streamed its answer needs no copied answer, even when the section never ended."""
    pipeline = _pipeline(queue, task_started=True)

    await pipeline.process(_chunk("Hel", append=False))
    await pipeline.process(_chunk("lo"))
    await pipeline.process(_status("done", state=TaskState.TASK_STATE_COMPLETED))

    assert _answers(queue) == []
    assert [type(e).__name__ for e in queue.events] == [
        "TaskArtifactUpdateEvent", "TaskArtifactUpdateEvent", "TaskStatusUpdateEvent",
    ]


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

    async def transform(self, event):
        parts = (event.status.message.parts if isinstance(event, TaskStatusUpdateEvent)
                 else event.artifact.parts)
        refused = [part for part in parts if part.raw]
        if not refused:
            return Transform(event, TransformReport())
        kept = [part for part in parts if not part.raw]
        del parts[:]
        parts.extend(kept)
        report = TransformReport(
            failures=[UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN) for _ in refused],
            changed=True,
        )
        if isinstance(event, TaskArtifactUpdateEvent) and not kept:
            return Transform(None, report)
        return Transform(event, report)


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


FILE_ROUTES = ("status-update", "artifact-update", "outbox-message", "outbox-task")
"""Every way a file reaches the pipeline: the updates an agent streams, and the
message or task it returns through its outbox."""


def _carrying_a_file(route: str, *, state: TaskState = TaskState.TASK_STATE_WORKING):
    """An event of the route, saying something and carrying one inline file."""
    file = _file_part()
    if route == "status-update":
        event = _status("here is the report", state=state)
        event.status.message.parts.append(file)
    elif route == "artifact-update":
        event = _artifact_update("unused")
        event.artifact.parts[0].CopyFrom(file)
    elif route == "outbox-message":
        event = _message("here is the report")
        event.parts.append(file)
    else:
        event = _task(state=state, history=[_message("here is the report")])
        event.artifacts.append(Artifact(artifact_id="report", name="report", parts=[file]))
    return event


async def _everything_delivered(queue: RecordingQueue, store, call_context) -> list[Part]:
    """Every part that reached the client or the task record."""
    parts: list[Part] = []
    for event in queue.events:
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message"):
            parts.extend(event.status.message.parts)
        elif isinstance(event, TaskArtifactUpdateEvent):
            parts.extend(event.artifact.parts)
    stored = await store.get(TASK_ID, call_context)
    if stored is not None:
        if stored.status.HasField("message"):
            parts.extend(stored.status.message.parts)
        parts.extend(part for message in stored.history for part in message.parts)
        parts.extend(part for artifact in stored.artifacts for part in artifact.parts)
    return parts


@pytest.mark.parametrize("route", FILE_ROUTES)
async def test_a_file_is_delivered_as_a_url_whatever_carried_it(
    manager, queue, store, call_context, route
) -> None:
    """No route to the client or the task record skips storage.

    A streamed update reaches the client, an outbox message or task the
    store; either way the file arrives as a URL and the bytes go nowhere.
    """
    pipeline = _pipeline(queue, task_started=True, transformer=StoringTransformer())

    await pipeline.process(_carrying_a_file(route))

    delivered = await _everything_delivered(queue, store, call_context)
    assert not [part for part in delivered if part.raw]
    (file,) = [part for part in delivered if part.url]
    assert file.url.startswith(StubFileStorageBackend.BASE_URI)
    assert file.filename == "report.pdf"


@pytest.mark.parametrize("route", ["outbox-message", "outbox-task"])
async def test_an_outbox_file_that_could_not_be_stored_fails_the_turn(
    manager, queue, store, call_context, route
) -> None:
    """The outbox answers to the same rule as a streamed file: no file, no success.

    What the agent said beside the file is still stored, so the reply is not
    lost with it, and the bytes are not.
    """
    refused = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)])
    pipeline = _pipeline(queue, task_started=True, transformer=StoringTransformer(refused))
    produced = _carrying_a_file(route)

    with pytest.raises(OutboundFileNotStoredError) as raised:
        await pipeline.process(produced)

    assert raised.value.event is produced
    delivered = await _everything_delivered(queue, store, call_context)
    assert [part.text for part in delivered] == ["here is the report"]


async def test_an_outbox_task_with_an_outcome_and_a_failed_file_does_not_end_the_turn(
    manager, queue, store, call_context
) -> None:
    """A task that declares itself done, with a file that failed, is not done.

    Its state is held at WORKING so the FAILED that follows is the outcome,
    exactly as for a status update.
    """
    refused = OutcomeBackend([UploadFailure(FileUploadErrorCode.STORAGE_FORBIDDEN)])
    pipeline = _pipeline(queue, task_started=True, transformer=StoringTransformer(refused))

    with pytest.raises(OutboundFileNotStoredError):
        await pipeline.process(
            _carrying_a_file("outbox-task", state=TaskState.TASK_STATE_COMPLETED)
        )

    stored = await store.get(TASK_ID, call_context)
    assert stored.status.state == TaskState.TASK_STATE_WORKING
    assert not stored.artifacts
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


# --------------------------------------------------------------------------
# A task the agent hands over through the outbox
# --------------------------------------------------------------------------

@pytest.fixture
async def in_hand(manager) -> Task:
    """The task the request was built on, stored: the caller's message and nothing of the turn yet."""
    task = _task(
        state=TaskState.TASK_STATE_SUBMITTED,
        history=[
            Message(
                context_id=CONTEXT_ID,
                task_id=TASK_ID,
                message_id="U1",
                role=Role.ROLE_USER,
                parts=[Part(text="find the report")],
            )
        ],
    )
    stored = Task()
    stored.CopyFrom(task)
    await manager.save_task_event(stored)
    return task


def _completed(final: Message, *history: Message) -> Task:
    """An outbox patch that completes the task with ``final``."""
    return Task(status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, message=final), history=list(history))


async def _hand_over(queue: RecordingQueue, in_hand: Task, patch: Task) -> None:
    """Apply the patch to the task in hand, as both framework adapters do, and deliver it."""
    pipeline = _pipeline(queue, task_started=True)
    await pipeline.process(outbox_task(patch, current=in_hand, task_id=TASK_ID, context_id=CONTEXT_ID))


async def test_an_outbox_task_ends_its_history_with_its_final_message(
    manager, in_hand, queue, store, call_context
) -> None:
    """The reply sent during the turn keeps its place; the final message comes last.

    The final message is also the task's status message, the way every
    stopped task carries the message it stopped on.
    """
    await manager.process(_status(message=_message("searching", message_id="A1")))

    await _hand_over(queue, in_hand, _completed(_message("the report", message_id="R"), _message("a note", message_id="H1")))

    stored = await store.get(TASK_ID, call_context)
    assert [m.message_id for m in stored.history] == ["U1", "A1", "H1", "R"]
    assert stored.status.message.message_id == "R"


async def test_a_final_message_the_outbox_task_also_lists_in_its_history_is_stored_once(
    manager, in_hand, queue, store, call_context
) -> None:
    await manager.process(_status(message=_message("searching", message_id="A1")))
    final = _message("the report", message_id="R")

    await _hand_over(queue, in_hand, _completed(final, _message("a note", message_id="H1"), final))

    stored = await store.get(TASK_ID, call_context)
    assert [m.message_id for m in stored.history] == ["U1", "A1", "H1", "R"]
    assert stored.status.message.message_id == "R"


async def test_an_outbox_task_completing_with_the_reply_already_sent_keeps_it_as_its_final_message(
    manager, in_hand, queue, store, call_context
) -> None:
    """The agent replied through the thread, then handed over a task whose status repeats that reply.

    The reply is the task's status message and the end of its history, not a
    message the task loses because it has heard it before.
    """
    final = _message("the report", message_id="R")
    await manager.process(_status(message=final))

    await _hand_over(queue, in_hand, _completed(final, _message("a note", message_id="H1")))

    stored = await store.get(TASK_ID, call_context)
    assert [m.message_id for m in stored.history] == ["U1", "H1", "R"]
    assert stored.status.message.message_id == "R"


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
        queue, TaskUpdater(queue, 'task', 'context'), task_started=True,
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

