"""The adapter contract, driven against every adapter that ships.

One test or more per numbered guarantee in `contract.py`, parameterized by adapter.
Nothing here branches on the adapter's name: a guarantee that only one of them
can hold is not a guarantee, it is a difference, and differences are declared
in `contract.DIFFERENCES` with their reason instead.
"""

from __future__ import annotations

import uuid

import pytest
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
from google.protobuf.struct_pb2 import Struct

from aion.core.a2a import A2AOutbox, ArtifactId

from .contract import ADAPTERS, DIFFERENCES, UNSUPPORTED, Chunk, Close, Reply, Thought

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

CALLER_TEXT = "what did you change?"
REPLY_TEXT = "the retry helper"

SERVER_NETWORK = {"distributionId": "dist-server"}

pytestmark = pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.name)


def _message(text: str, role=Role.ROLE_AGENT, **ids) -> Message:
    return Message(
        message_id=uuid.uuid4().hex,
        role=role,
        parts=[Part(text=text)],
        **ids,
    )


def _current_task() -> Task:
    """The task the request is running on.

    It already holds what was asked, and one server-owned routing key — the
    thing guarantee 7 exists to protect.
    """
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        history=[_message(CALLER_TEXT, role=Role.ROLE_USER, task_id=TASK_ID, context_id=CONTEXT_ID)],
    )
    task.metadata["aion:network"] = _struct(SERVER_NETWORK)
    return task


def _patch(**kwargs) -> Task:
    """An outbox Task patch. Ids left unset: they are the server's to fill."""
    return Task(status=TaskStatus(state=TaskState.TASK_STATE_WORKING), **kwargs)


def _texts(messages) -> list[str]:
    return ["".join(part.text for part in message.parts if part.text) for message in messages]


def test_a_turn_without_an_outbox_has_no_result_events(adapter) -> None:
    """Guarantee 1. The terminal event still follows; this is what precedes it."""
    assert adapter.result_events() == []


def test_an_outbox_message_comes_back_as_a_message(adapter) -> None:
    """Guarantee 2. A `Message` is persisted into history by the pipeline; a
    status event carrying one is not — it stays pending and the terminal task
    keeps it there."""
    events = adapter.result_events(outbox=A2AOutbox(message=_message(REPLY_TEXT)))

    assert len(events) == 1
    assert isinstance(events[0], Message)
    assert _texts(events) == [REPLY_TEXT]


def test_an_outbox_task_comes_back_as_one_task(adapter) -> None:
    """Guarantee 3. One object, not a fan-out of events per message and
    artifact: the merge downstream is defined for a whole task."""
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)])),
        current_task=_current_task(),
    )

    assert len(events) == 1
    assert isinstance(events[0], Task)


def test_an_outbox_message_carries_the_servers_ids(adapter) -> None:
    """Guarantee 4, for a message. The agent's ids are not the server's."""
    events = adapter.result_events(
        outbox=A2AOutbox(
            message=_message(REPLY_TEXT, task_id="agents-idea", context_id="agents-idea")
        )
    )

    assert events[0].task_id == TASK_ID
    assert events[0].context_id == CONTEXT_ID


def test_an_outbox_task_carries_the_servers_ids(adapter) -> None:
    """Guarantee 4, for a task: on the task and on every message in it.

    The patch leaves them unset, which is how an agent should write one — and
    a patch that set them would still not be believed.
    """
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)])),
        current_task=_current_task(),
    )

    task = events[0]
    assert task.id == TASK_ID
    assert task.context_id == CONTEXT_ID
    assert all(message.task_id == TASK_ID for message in task.history)
    assert all(message.context_id == CONTEXT_ID for message in task.history)


def test_a_task_patch_adds_to_the_turn_rather_than_replacing_it(adapter) -> None:
    """Guarantee 5. The caller's own message survives the patch.

    A task holding only the answer would be a record of an answer to nothing.
    """
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)])),
        current_task=_current_task(),
    )

    assert _texts(events[0].history) == [CALLER_TEXT, REPLY_TEXT]


def test_a_task_patch_adds_its_artifacts(adapter) -> None:
    """Guarantee 5, for artifacts: added to what the task already has."""
    artifact = Artifact(
        artifact_id=uuid.uuid4().hex, name="report", parts=[Part(text="findings")]
    )
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(artifacts=[artifact])),
        current_task=_current_task(),
    )

    assert [a.name for a in events[0].artifacts] == ["report"]


@pytest.mark.parametrize(
    "reserved_key",
    [
        "aion:network",
        "aion:unassigned",
        "aion:messageType",
        "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0",
    ],
)
def test_a_task_patch_cannot_write_a_platform_metadata_key(adapter, reserved_key: str) -> None:
    """Guarantee 6. Asserted on the value, so it holds the same way for a
    namespace the server has written to and one it has not touched."""
    patch = _patch()
    patch.metadata[reserved_key] = _struct({"distributionId": "dist-agent"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert "dist-agent" not in str(events[0].metadata)


def test_a_task_patch_keeps_the_metadata_that_is_the_agents(adapter) -> None:
    """Guarantee 6, the other half: the rule takes the platform's keys only."""
    patch = _patch()
    patch.metadata["trace"] = _struct({"span": "abc"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert dict(events[0].metadata["trace"]) == {"span": "abc"}


def test_the_server_keeps_its_own_routing_when_the_agent_patches_it(adapter) -> None:
    """Guarantee 6, the case it is named for: `aion:network` stays the
    server's. An agent able to rewrite it could redirect its own delivery."""
    patch = _patch()
    patch.metadata["aion:network"] = _struct({"distributionId": "dist-agent"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert dict(events[0].metadata["aion:network"]) == SERVER_NETWORK


def test_an_unreadable_outbox_produces_no_result_events(adapter) -> None:
    """Guarantee 7. Something in the state under that key that is not an
    outbox is not a reason to fail the turn."""
    assert adapter.result_events(outbox={"not": "an outbox"}) == []


def test_an_empty_outbox_produces_no_result_events(adapter) -> None:
    """Guarantee 7, for the outbox an agent left behind without filling in.

    Readable, valid, and carrying neither a message nor a task: the turn
    answers with what it streamed and sent while it ran.
    """
    assert adapter.result_events(outbox=A2AOutbox()) == []


def test_an_outbox_carrying_both_delivers_the_message(adapter) -> None:
    """Guarantee 8. One answer per turn, resolved the same way everywhere."""
    events = adapter.result_events(
        outbox=A2AOutbox(
            message=_message(REPLY_TEXT), task=_patch(history=[_message("also this")])
        ),
        current_task=_current_task(),
    )

    assert len(events) == 1
    assert isinstance(events[0], Message)
    assert _texts(events) == [REPLY_TEXT]


def test_an_outbox_saved_by_an_earlier_turn_is_not_applied_again(adapter) -> None:
    """Guarantee 9. The saved state outlives the turn; the outbox does not.

    The earlier turn's message is still in the checkpoint or the session when
    the next turn of the context finishes. Answering with it would put the
    same reply into every later task of the conversation.
    """
    events = adapter.result_events(saved_outbox=A2AOutbox(message=_message("said last turn")))

    assert events == []


def test_an_adapter_with_no_current_task_still_applies_the_patch(adapter) -> None:
    """The degenerate case the merge has to survive: nothing to merge onto.

    An adapter driven outside a request has no current task. The patch then
    stands alone — which is all that can be said — rather than raising.
    """
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)]))
    )

    assert _texts(events[0].history) == [REPLY_TEXT]
    assert events[0].id == TASK_ID


def _stream_deltas(events) -> list[tuple[str, bool, bool]]:
    """Each aion:stream-delta event as (text, append, last_chunk)."""
    return [
        ("".join(part.text for part in event.artifact.parts), event.append, event.last_chunk)
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
    ]


def _thoughts(events) -> list[TaskArtifactUpdateEvent]:
    return [
        event
        for event in events
        if isinstance(event, TaskArtifactUpdateEvent) and event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
    ]


def _status_messages(events) -> list[str]:
    """The text of every status update that carries a message."""
    return [
        "".join(part.text for part in event.status.message.parts if part.text)
        for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    ]


async def test_a_streamed_section_ends_with_the_end_mark(adapter) -> None:
    """Guarantee 10. The chunks, then one empty part that ends the section; the text is not sent again."""
    events = await adapter.run_events(Chunk("It "), Chunk("is sunny."), Close("It is sunny."))

    assert _stream_deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("", True, True),
    ]
    end = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)][-1]
    assert [part.text for part in end.artifact.parts] == [""]
    assert not end.artifact.HasField("metadata")


async def test_the_closing_message_of_a_section_is_not_a_message(adapter) -> None:
    """Guarantee 11, for a section the framework closes with its own message."""
    events = await adapter.run_events(Chunk("It "), Chunk("is sunny."), Close("It is sunny."))

    assert _status_messages(events) == []


async def test_a_section_the_run_leaves_open_is_closed_with_the_end_mark(adapter) -> None:
    """Guarantees 11 and 12. The run ends mid-section: the section still ends,
    and its text stays the artifact rather than a message."""
    events = await adapter.run_events(Chunk("It "), Chunk("is sunny."))

    assert _stream_deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("", True, True),
    ]
    assert _status_messages(events) == []


async def test_an_open_section_is_closed_before_the_result_and_the_terminal_status(adapter) -> None:
    """Guarantee 12. The outbox answer and the terminal status come after the section's end."""
    events = await adapter.run_events(
        Chunk("It is sunny."),
        outbox=A2AOutbox(message=_message(REPLY_TEXT)),
    )

    final, result, terminal = events[-3:]
    assert isinstance(final, TaskArtifactUpdateEvent)
    assert final.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
    assert final.last_chunk is True
    assert isinstance(result, Message)
    assert _texts([result]) == [REPLY_TEXT]
    assert terminal.status.state == TaskState.TASK_STATE_COMPLETED
    assert not terminal.status.HasField("message")


async def test_the_next_section_starts_the_artifact_over(adapter) -> None:
    """Guarantee 13."""
    events = await adapter.run_events(Chunk("Checking."), Close("Checking."), Chunk("Sunny."))

    assert _stream_deltas(events) == [
        ("Checking.", False, False),
        ("", True, True),
        ("Sunny.", False, False),
        ("", True, True),
    ]


async def test_a_thought_is_one_text_part_of_the_thinking_artifact(adapter) -> None:
    """Guarantee 14. A thought is neither a message nor the streamed answer."""
    events = await adapter.run_events(Thought("Looking up the forecast"))

    thoughts = _thoughts(events)
    assert len(thoughts) == 1
    assert [part.text for part in thoughts[0].artifact.parts] == ["Looking up the forecast"]
    assert _stream_deltas(events) == []
    assert _status_messages(events) == []


async def test_a_thought_does_not_end_the_streaming_section(adapter) -> None:
    """Guarantee 14. The chunk after the thought extends the same section."""
    events = await adapter.run_events(
        Chunk("It "), Thought("Looking up the forecast"), Chunk("is sunny."), Close("It is sunny.")
    )

    assert _stream_deltas(events) == [
        ("It ", False, False),
        ("is sunny.", True, False),
        ("", True, True),
    ]
    artifact_ids = [event.artifact.artifact_id for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert artifact_ids == [
        ArtifactId.STREAM_DELTA.value,
        ArtifactId.THINKING_DELTA.value,
        ArtifactId.STREAM_DELTA.value,
        ArtifactId.STREAM_DELTA.value,
    ]


async def test_a_reply_that_did_not_stream_is_a_message(adapter) -> None:
    """Guarantee 15."""
    events = await adapter.run_events(Reply("It is sunny."))

    assert _status_messages(events) == ["It is sunny."]
    message_update = next(
        event for event in events if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message")
    )
    assert message_update.status.state == TaskState.TASK_STATE_WORKING
    assert _stream_deltas(events) == []


def _struct(values: dict) -> Struct:
    struct = Struct()
    struct.update(values)
    return struct


@pytest.mark.parametrize("difference_key", sorted(DIFFERENCES))
def test_every_declared_difference_states_a_reason(adapter, difference_key: str) -> None:
    """A difference without a reason is an undiagnosed defect wearing a label.

    Parameterized by adapter like everything else here, so the declarations are
    read on every adapter's run rather than only on the first.
    """
    assert DIFFERENCES[difference_key].strip()


def test_unsupported_capabilities_are_declared_per_adapter(adapter) -> None:
    """An adapter names what it has no equivalent of, with the reason.

    An empty entry would let "we never wrote that test" pass for "the framework
    cannot do this".
    """
    for (name, capability), reason in UNSUPPORTED.items():
        assert name in {a.name for a in ADAPTERS}, f"{name} is not an adapter under test"
        assert capability and reason.strip()
