"""The adapter contract, driven against every adapter that ships.

One test per numbered guarantee in `contract.py`, parameterized by adapter.
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
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.struct_pb2 import Struct

from aion.core.a2a import A2AOutbox

from .contract import ADAPTERS, DIFFERENCES, UNSUPPORTED

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


def test_accumulated_text_becomes_one_working_status_update(adapter) -> None:
    """Guarantee 1. The ordinary path: the agent spoke, the SDK frames it."""
    events = adapter.result_events(delta_text=REPLY_TEXT)

    assert len(events) == 1
    assert isinstance(events[0], TaskStatusUpdateEvent)
    assert events[0].status.state == TaskState.TASK_STATE_WORKING
    assert events[0].status.message.parts[0].text == REPLY_TEXT


def test_a_turn_that_produced_nothing_announces_nothing(adapter) -> None:
    """Guarantee 2. The terminal event still follows; this is what precedes it."""
    assert adapter.result_events() == []


def test_an_outbox_message_comes_back_as_a_message(adapter) -> None:
    """Guarantee 3. A `Message` is persisted into history by the pipeline; a
    status event carrying one is not — it stays pending and the terminal task
    keeps it there."""
    events = adapter.result_events(outbox=A2AOutbox(message=_message(REPLY_TEXT)))

    assert len(events) == 1
    assert isinstance(events[0], Message)
    assert _texts(events) == [REPLY_TEXT]


def test_an_outbox_task_comes_back_as_one_task(adapter) -> None:
    """Guarantee 4. One object, not a fan-out of events per message and
    artifact: the merge downstream is defined for a whole task."""
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)])),
        current_task=_current_task(),
    )

    assert len(events) == 1
    assert isinstance(events[0], Task)


def test_an_outbox_message_carries_the_servers_ids(adapter) -> None:
    """Guarantee 5, for a message. The agent's ids are not the server's."""
    events = adapter.result_events(
        outbox=A2AOutbox(
            message=_message(REPLY_TEXT, task_id="agents-idea", context_id="agents-idea")
        )
    )

    assert events[0].task_id == TASK_ID
    assert events[0].context_id == CONTEXT_ID


def test_an_outbox_task_carries_the_servers_ids(adapter) -> None:
    """Guarantee 5, for a task: on the task and on every message in it.

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
    """Guarantee 6. The caller's own message survives the patch.

    A task holding only the answer would be a record of an answer to nothing.
    """
    events = adapter.result_events(
        outbox=A2AOutbox(task=_patch(history=[_message(REPLY_TEXT)])),
        current_task=_current_task(),
    )

    assert _texts(events[0].history) == [CALLER_TEXT, REPLY_TEXT]


def test_a_task_patch_adds_its_artifacts(adapter) -> None:
    """Guarantee 6, for artifacts: added to what the task already has."""
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
        "aion:ephemeral",
        "aion:messageType",
        "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0",
    ],
)
def test_a_task_patch_cannot_write_a_platform_metadata_key(adapter, reserved_key: str) -> None:
    """Guarantee 7. Asserted on the value, so it holds the same way for a
    namespace the server has written to and one it has not touched."""
    patch = _patch()
    patch.metadata[reserved_key] = _struct({"distributionId": "dist-agent"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert "dist-agent" not in str(events[0].metadata)


def test_a_task_patch_keeps_the_metadata_that_is_the_agents(adapter) -> None:
    """Guarantee 7, the other half: the rule takes the platform's keys only."""
    patch = _patch()
    patch.metadata["trace"] = _struct({"span": "abc"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert dict(events[0].metadata["trace"]) == {"span": "abc"}


def test_the_server_keeps_its_own_routing_when_the_agent_patches_it(adapter) -> None:
    """Guarantee 7, the case it is named for: `aion:network` stays the
    server's. An agent able to rewrite it could redirect its own delivery."""
    patch = _patch()
    patch.metadata["aion:network"] = _struct({"distributionId": "dist-agent"})

    events = adapter.result_events(outbox=A2AOutbox(task=patch), current_task=_current_task())

    assert dict(events[0].metadata["aion:network"]) == SERVER_NETWORK


def test_an_unreadable_outbox_falls_through_to_the_text(adapter) -> None:
    """Guarantee 8. Something in the state under that key that is not an
    outbox is not a reason to answer nothing."""
    events = adapter.result_events(outbox={"not": "an outbox"}, delta_text=REPLY_TEXT)

    assert len(events) == 1
    assert isinstance(events[0], TaskStatusUpdateEvent)
    assert events[0].status.message.parts[0].text == REPLY_TEXT


def test_an_empty_outbox_falls_through_to_the_text(adapter) -> None:
    """Guarantee 8, for the outbox an agent left behind without filling in.

    Readable, valid, and carrying neither a message nor a task — the turn is
    answered by what it streamed, not by nothing.
    """
    events = adapter.result_events(outbox=A2AOutbox(), delta_text=REPLY_TEXT)

    assert len(events) == 1
    assert isinstance(events[0], TaskStatusUpdateEvent)
    assert events[0].status.message.parts[0].text == REPLY_TEXT


def test_an_outbox_carrying_both_delivers_the_message(adapter) -> None:
    """Guarantee 9. One answer per turn, resolved the same way everywhere."""
    events = adapter.result_events(
        outbox=A2AOutbox(
            message=_message(REPLY_TEXT), task=_patch(history=[_message("also this")])
        ),
        current_task=_current_task(),
    )

    assert len(events) == 1
    assert isinstance(events[0], Message)
    assert _texts(events) == [REPLY_TEXT]


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
