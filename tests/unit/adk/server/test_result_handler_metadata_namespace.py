"""The platform metadata namespace holds at the one place ADK writes into a task.

An ADK agent returns ``a2a_outbox`` as a Task patch, and that patch carries a
metadata map. Merging it is the single point in the SDK where metadata an agent
authored reaches a task's own metadata, so it is the place the reserved
namespaces have to be enforced: ``aion:network`` is routing and identity the
server owns, and an agent that could overwrite it could redirect its own
delivery.

The rule is one definition, ``aion.core.a2a.metadata`` - the same one the
deduplicator applies to inbound events and the framework adapters apply to
emitted ones. These tests assert it at the ADK merge, from both sides: the task
kept in the request context, and the event that goes out on the wire.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from google.protobuf.struct_pb2 import Struct

from aion.adk.server.execution.result_handler import ADKExecutionResultHandler

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

SERVER_NETWORK = {"distributionId": "dist-server"}


def _struct(values: dict) -> Struct:
    struct = Struct()
    struct.update(values)
    return struct


def _current_task() -> Task:
    """The task as the server holds it, carrying a server-owned routing key."""
    task = Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    task.metadata["aion:network"] = _struct(SERVER_NETWORK)
    return task


def _patch(metadata: dict) -> Task:
    """An outbox Task patch carrying whatever metadata the agent set."""
    patch = Task(id="ignored", context_id="ignored", status=TaskStatus())
    for key, value in metadata.items():
        patch.metadata[key] = _struct(value) if isinstance(value, dict) else value
    return patch


def _merge(patch: Task) -> tuple[Task, list]:
    """Run the outbox-Task merge and return the resulting task and events."""
    context = Mock()
    context.current_task = _current_task()
    events = ADKExecutionResultHandler._handle_outbox_task(
        patch, context, TASK_ID, CONTEXT_ID
    )
    return context.current_task, events


@pytest.mark.parametrize(
    "reserved_key",
    [
        "aion:network",
        "aion:ephemeral",
        "aion:messageType",
        "https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0",
    ],
)
def test_an_agent_cannot_write_a_reserved_key_into_the_task(reserved_key: str) -> None:
    """A patch key in a platform namespace never reaches the task's metadata.

    Asserted on the value rather than the key, so it holds the same way for a
    namespace the server already wrote to and one it has not touched: what the
    agent put in the patch is not in the task afterwards.
    """
    task, _ = _merge(_patch({reserved_key: {"distributionId": "dist-agent"}}))

    assert "dist-agent" not in str(task.metadata)


def test_the_server_keeps_its_own_routing_when_the_agent_patches_it() -> None:
    """The exact case the contract names: aion:network stays the server's."""
    task, _ = _merge(_patch({"aion:network": {"distributionId": "dist-agent"}}))

    assert dict(task.metadata["aion:network"]) == SERVER_NETWORK


def test_a_reserved_key_does_not_go_out_on_the_wire_either() -> None:
    """Dropping it from the task but announcing it in an event would still leak it."""
    _, events = _merge(
        _patch({
            "aion:network": {"distributionId": "dist-agent"},
            "trace": {"span": "abc"},
        })
    )

    status_events = [
        event for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.metadata
    ]
    assert len(status_events) == 1
    assert "aion:network" not in status_events[0].metadata
    assert "trace" in status_events[0].metadata


def test_the_agent_keeps_everything_outside_the_reserved_namespaces() -> None:
    """The rule takes the platform's keys and nothing else."""
    task, _ = _merge(_patch({"trace": {"span": "abc"}, "tenant": {"id": "acme"}}))

    assert dict(task.metadata["trace"]) == {"span": "abc"}
    assert dict(task.metadata["tenant"]) == {"id": "acme"}
    assert dict(task.metadata["aion:network"]) == SERVER_NETWORK


def test_a_patch_with_only_reserved_keys_announces_nothing() -> None:
    """No metadata event at all, rather than an empty one, when nothing survives."""
    _, events = _merge(_patch({"aion:network": {"distributionId": "dist-agent"}}))

    assert not [
        event for event in events
        if isinstance(event, TaskStatusUpdateEvent) and event.metadata
    ]
