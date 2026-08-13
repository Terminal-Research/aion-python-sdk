"""Supplying a final state for a task whose execution is gone.

The server writes a task's outcome only where the execution cannot: it was
cancelled by shutdown, or it died with its process. Both leave a task holding an
active state with nothing alive to advance it, so it would be presented as
running forever.

Such a task is settled as ``FAILED``. It is terminal on purpose: the run did
not finish and nothing can continue it. The alternative — a non-terminal state,
so a client could pick the task back up — is worse than it sounds, because the
task is not waiting for anything. ``RequestContextBuilder`` adopts the last
interrupted task of a context whenever a message arrives without a task id, and
the executor continues any non-terminal task through the handler's ``resume``.
A task settled as ``INPUT_REQUIRED`` would therefore capture the next message
of its context and have it delivered as the answer to a question no agent
asked, resuming from a suspension point that does not exist in the checkpoint.
Terminal is the honest answer, and continuity is not lost by it: the context
keeps the history, so the next message opens a fresh task that reads it.

The reason is recorded in metadata under ``A2AMetadataKey.SETTLED_REASON`` so a
client can tell a state the server had to supply from one the agent declared.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from a2a.types import Task, TaskState

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.server.a2a.constants import NON_ACTIVE_TASK_STATES

if TYPE_CHECKING:
    from aion.server.tasks.stores import BaseTaskStore

logger = logging.getLogger(__name__)

__all__ = ["settled_task", "settle_orphaned_tasks"]


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

    settled = Task()
    settled.CopyFrom(task)
    settled.status.state = TaskState.TASK_STATE_FAILED
    settled.metadata[A2AMetadataKey.SETTLED_REASON.value] = reason.value
    return settled


async def settle_orphaned_tasks(store: BaseTaskStore) -> None:
    """Close out tasks a killed predecessor left running in the store.

    A graceful stop settles its own tasks as it drains them. This covers what
    no shutdown could: a process killed outright — SIGKILL, OOM, a lost
    machine — leaves its tasks active in the store with the execution already
    gone. A durable store carries them into the next process, which is the only
    thing left that can end them.

    Every task the store presents as running is one of those. This process has
    just started and executes nothing yet, and a store whose contents die with
    the process hands back nothing here at all.

    Each task is settled on its own, because one write the store refuses says
    nothing about the rest, and a task left active is exactly the state this
    exists to remove.

    Args:
        store: The task store to reap. Must outlive nothing else — this runs
            before the process serves any request.
    """
    tasks = await store.get_active_tasks()
    if not tasks:
        return

    logger.info("Settling %d task(s) left running by a previous process", len(tasks))

    for task in tasks:
        settled = settled_task(task, TaskSettlementReason.SERVER_RESTART)
        if settled is None:
            continue

        try:
            await store.save(settled)
        except Exception as exc:
            logger.error(
                "Failed to settle task %s left in %s by a previous process",
                task.id,
                TaskState.Name(task.status.state),
                exc_info=exc,
            )
