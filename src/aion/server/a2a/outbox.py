"""Applying an agent's `a2a_outbox` to the task the request is running on.

The outbox is the door an agent uses to hand the server a finished A2A object
instead of speaking through the thread: one `Message` or one `Task` patch. What
the server does with it is not a framework's business, so it is defined once
here and both framework adapters call it — a second adapter that grows its own
reading of the outbox is how the two drifted apart before.

Two decisions live in this module.

*The object is handed on as itself.* A `Message` stays a `Message` and a `Task`
stays a `Task`, because the execution pipeline saves those silently, into the
task record, where a later `tasks/get` finds them. Unpacking the payload into
status events instead leaves the last message in `task.status.message`, which
the next status update would fold into history — and the terminal task, which
carries no message of its own, is not one. The reply then reaches no reader at
all.

*The patch is applied to the task in hand.* `current` is the task the request
was built on: it already holds the caller's own message, and a turn that
answered a question it no longer records being asked is not a record of
anything. Merging here rather than leaving it to the store is what makes that
independent of timing — the stored copy is still being written by the event
consumer while an agent that answers immediately is already handing its patch
back.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import Message, Task

from aion.core.a2a.metadata import agent_metadata

__all__ = ["outbox_message", "outbox_task"]


def outbox_message(message: Message, *, task_id: str, context_id: str) -> Message:
    """Return the outbox `Message` with the server's own ids enforced.

    Args:
        message: The message the agent put in the outbox.
        task_id: The task this turn belongs to.
        context_id: The context this turn belongs to.

    Returns:
        A copy carrying the server's ids. The agent's object is left alone -
        it belongs to the agent's own state, which the server does not edit.
    """
    stamped = Message()
    stamped.CopyFrom(message)
    stamped.task_id = task_id
    stamped.context_id = context_id
    return stamped


def outbox_task(
    patch: Task,
    *,
    current: Optional[Task],
    task_id: str,
    context_id: str,
) -> Task:
    """Return the current task with the outbox `Task` patch applied.

    Args:
        patch: The task the agent put in the outbox. Read, never modified.
        current: The task the request is running on, or None when the caller
            has none to hand (an adapter driven outside a request). Without it
            the patch stands alone, which is all that can be said.
        task_id: The task id the result must carry.
        context_id: The context id the result must carry.

    Returns:
        A new `Task`, merged as follows:

            id, context_id  — the server's, whatever the patch says or omits.
                              A patch that could set them could point the
                              answer at another task.
            status          — the patch's when it set one, otherwise the
                              current one. The turn's terminal event follows
                              and has the last word either way.
            history         — current, then the patch's, each of the patch's
                              messages re-stamped with the server's ids.
            artifacts       — current, then the patch's.
            metadata        — the agent-writable keys of the patch, layered
                              over the server's own. Keys in a platform
                              namespace are dropped, so an outbox Task cannot
                              rewrite `aion:network` or any other server-owned
                              key; see `aion.core.a2a.metadata`.
    """
    merged = Task()
    if current is not None:
        merged.CopyFrom(current)
    merged.id = task_id
    merged.context_id = context_id
    if patch.HasField("status"):
        merged.status.CopyFrom(patch.status)

    for message in patch.history:
        appended = merged.history.add()
        appended.CopyFrom(message)
        appended.task_id = task_id
        appended.context_id = context_id

    merged.artifacts.extend(patch.artifacts)

    for key, value in agent_metadata(patch.metadata).items():
        merged.metadata[key] = value

    return merged
