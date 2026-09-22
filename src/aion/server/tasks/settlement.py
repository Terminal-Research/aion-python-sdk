"""Supplying a final state for a task whose execution is gone.

The server writes a task's outcome only where the execution cannot: shutdown
cancelled it, or its owner stopped renewing the task's lease and the reaper
reclaimed it. Both leave a task holding an active state with nothing alive to
advance it, so it would be presented as running forever.

This module builds the settled task; it never writes one. A shutdown settles
its own tasks while it still holds their claims (``ActiveTaskRegistry``), and a
task whose owner is gone is settled by ``ClaimReaper`` once the lease expires.
Both reach the store through a claim, so exactly one writer ever decides a
given task's supplied state.

Such a task is settled terminally — most often as ``FAILED``, since the run
did not finish and nothing can continue it. The alternative — a non-terminal
state, so a client could pick the task back up — is worse than it sounds,
because the task is not waiting for anything. ``RequestContextBuilder`` adopts
the last interrupted task of a context whenever a message arrives without a
task id, and the executor continues any non-terminal task through the
handler's ``resume``. A task settled as ``INPUT_REQUIRED`` would therefore
capture the next message of its context and have it delivered as the answer
to a question no agent asked, resuming from a suspension point that does not
exist in the checkpoint. Terminal is the honest answer, and continuity is not
lost by it: the context keeps the history, so the next message opens a fresh
task that reads it.

``CANCEL_REQUESTED`` and ``CANCEL_TIMEOUT`` are the one pair of reasons that
settle as ``CANCELED`` instead: both mean someone asked for exactly this
outcome (see ``TaskSettlementReason``), so it is not a failure the server
supplies in the run's absence, it is the run's own requested ending arriving
late.

The reason is recorded in metadata under ``A2AMetadataKey.SETTLED_REASON`` so a
client can tell a state the server had to supply from one the agent declared.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import Task, TaskState

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES

__all__ = ["settled_task"]


def settled_task(task: Task, reason: TaskSettlementReason) -> Optional[Task]:
    """Build the settled copy of a task, or decline to settle it.

    Returns ``None`` when the task already carries a state of its own — an
    outcome the agent declared, or an interrupt it is genuinely waiting on.
    Overwriting either would replace the truth with a guess, so the caller has
    nothing to write and the check lives here rather than in each caller.

    Args:
        task: The task as the store currently holds it.
        reason: Why the server, and not the agent, is deciding the state.

    Returns:
        A copy of ``task`` in ``FAILED`` with the reason in metadata, or
        ``None`` if the task needs no state supplied.
    """
    if task.status.state in NON_ACTIVE_TASK_STATES:
        return None

    cancel_reasons = (
        TaskSettlementReason.CANCEL_REQUESTED,
        TaskSettlementReason.CANCEL_TIMEOUT,
    )
    settled = Task()
    settled.CopyFrom(task)
    settled.status.state = (
        TaskState.TASK_STATE_CANCELED
        if reason in cancel_reasons
        else TaskState.TASK_STATE_FAILED
    )
    settled.metadata[A2AMetadataKey.SETTLED_REASON.value] = reason.value
    return settled
