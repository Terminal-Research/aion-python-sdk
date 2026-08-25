"""Typed payloads for the ``LISTEN``/``NOTIFY`` task-event channel.

One channel (``TASK_EVENT_CHANNEL``, see ``aion.db.postgres.constants``)
carries every task event a pod might care about across the process boundary.
``TaskEvent.kind`` is what a listener filters and dispatches on - see
``aion.server.tasks.notifications.TaskEventListener``, the one reader of this
model.

A shared channel with a typed payload, rather than one channel per event
kind, is what keeps adding a third kind a one-line enum change instead of a
new constant, a new ``LISTEN``, and a rolling-deploy question about whether
every pod already subscribes to it. The cost of that flexibility is paid once
here: every publisher already builds this model before calling ``pg_notify``,
so a kind nothing recognizes deserializes fine and is simply skipped by a
listener's dispatch loop rather than mis-delivered to the wrong waiter.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel

__all__ = ["TaskEvent", "TaskEventKind"]


class TaskEventKind(str, enum.Enum):
    """One member per distinct cross-pod task event.

    ``str`` mixin keeps ``model_dump_json()`` emitting the bare value
    (``"cancel_requested"``), not an enum-wrapped one, so the wire payload
    stays stable independent of how this type is spelled internally.
    """

    CANCEL_REQUESTED = "cancel_requested"
    """A non-owner marked a live claim's ``cancel_requested_at`` - see
    ``TaskClaimsRepository.request_cancel``. Consumed by the owner, which
    reacts the same way it would on discovering the mark at its next
    heartbeat renewal - this is a latency shortcut, not a second mechanism."""

    CANCEL_RESOLVED = "cancel_resolved"
    """A task with a pending cancellation request reached a terminal state -
    canceled, or something else if it finished first in the race with the
    request. See ``TaskClaimsRepository.notify_cancel_resolved``. Consumed by
    whichever pod is waiting on this task's outcome after having marked the
    request; never emitted for a task nothing asked to cancel."""


class TaskEvent(BaseModel):
    """The wire payload for ``TASK_EVENT_CHANNEL``.

    ``task_id`` is a plain string, not ``uuid.UUID``: it travels through
    Postgres's ``NOTIFY`` as text regardless, and a listener's only use for
    it is an equality/dict-key comparison against another string it already
    has - parsing it back into a UUID would buy nothing.
    """

    kind: TaskEventKind
    task_id: str
