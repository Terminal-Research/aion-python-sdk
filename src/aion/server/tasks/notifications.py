"""Cross-pod task events, delivered faster than a heartbeat cycle allows.

``TASK_EVENT_CHANNEL`` (see ``aion.db.postgres.constants``) carries every
event a pod might publish about a task it does not exclusively own:

- ``CANCEL_REQUESTED`` - a non-owner marked a live claim's
  ``cancel_requested_at`` (``TaskClaimsRepository.request_cancel``) and wants
  the owner to notice before its next heartbeat renewal would surface it.
- ``CANCEL_RESOLVED`` - a task with a pending cancellation request reached a
  terminal state (``TaskClaimsRepository.notify_cancel_resolved``), and
  whoever marked the request is waiting on exactly this.

This module is the one receiving side, shared by both directions: one
listener per process, dispatching by ``kind`` to whichever local waiter or
subscriber cares about it. Neither direction ever trusts this channel alone -
the heartbeat still reads ``cancel_requested_at`` on every renewal, and a
waiter still re-reads the store after its own timeout - so a notification
lost to a reconnect degrades delivery to the pre-existing polling cadence
rather than losing it outright. See ``aion.server.tasks.ownership.postgres
.PostgresOwnershipProvider`` for the owner-side subscriber and
``AionRequestHandler.on_cancel_task`` for the waiter-side caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterable
from itertools import count

import psycopg
from psycopg import sql
from pydantic import ValidationError

from aion.db.postgres import db_manager as default_db_manager
from aion.db.postgres.constants import TASK_EVENT_CHANNEL
from aion.db.postgres.events import TaskEvent, TaskEventKind

__all__ = ["TaskEventCallback", "TaskEventListener", "Subscription", "Waiter", "task_event_listener"]

logger = logging.getLogger(__name__)

# How long to wait before retrying after the listen connection drops. Short:
# every second spent reconnecting is a second where a notification for a
# waiting request or a subscribed owner is silently lost - each side's own
# fallback (the waiter's timeout and final store read, the owner's own
# heartbeat renewal) is what makes that loss survivable rather than a hang or
# a missed cancellation, not a reason to make it common.
_RECONNECT_DELAY_SECONDS = 2.0

TaskEventCallback = Callable[[TaskEventKind, str], None]
"""Called with ``(kind, task_id)`` for a subscribed event kind. Synchronous:
schedule follow-up work, do not await it here - see :meth:`TaskEventListener
.subscribe`."""


class Waiter:
    """A registered interest in one task's next event of one kind.

    Obtained from :meth:`TaskEventListener.register`, which must be called
    before the caller's own write makes the other side able to produce
    whatever this is waiting on - see ``AionRequestHandler.on_cancel_task``,
    the one caller today. Splitting registration from the wait is what makes
    that ordering possible: registering only builds a local ``asyncio.Event``
    and touches no database, so it can happen strictly before the transaction
    that will eventually cause the notification, with nothing in between that
    could let a fast responder's notification arrive before anyone is
    listening for it.
    """

    __slots__ = ("_listener", "_key", "_event")

    def __init__(self, listener: "TaskEventListener", key: tuple[TaskEventKind, str], event: asyncio.Event) -> None:
        self._listener = listener
        self._key = key
        self._event = event

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for a matching event.

        Returns:
            ``True`` if a matching event arrived, ``False`` on timeout.
            ``False`` is not proof nothing happened: the caller must still
            re-read the store, since a notification can be lost to a
            listener reconnect independently of whether the underlying write
            itself succeeded.
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        """Forget this registration. Call exactly once, whether or not waited on."""
        self._listener._release_waiter(self._key)


class Subscription:
    """A registered callback for every future event of one or more kinds."""

    __slots__ = ("_listener", "_token", "_kinds")

    def __init__(self, listener: "TaskEventListener", token: int, kinds: frozenset[TaskEventKind]) -> None:
        self._listener = listener
        self._token = token
        self._kinds = kinds

    def cancel(self) -> None:
        """Stop receiving events. Call exactly once."""
        self._listener._release_subscription(self._token, self._kinds)


class TaskEventListener:
    """Dispatches ``TASK_EVENT_CHANNEL`` notifications to local consumers.

    Two independent registration styles share one connection and one
    dispatch loop:

    - :meth:`register` for a one-shot wait on a single ``(kind, task_id)`` -
      the shape a request handler needs when it marked a request and wants
      to know the moment it resolves.
    - :meth:`subscribe` for an ongoing callback over one or more kinds,
      filtered by the subscriber itself rather than by task id - the shape
      an owner needs, since it already tracks its own claims and only needs
      to know a kind occurred, not to pre-register every task it might ever
      hold.

    Holds exactly one connection for the life of the process, opened
    directly rather than borrowed from ``DbManager``'s pool: a pooled
    connection is returned and reused by an unrelated query, which would
    silently end the subscription the moment that happened. Reconnects and
    re-subscribes on its own after any disruption - see the module docstring
    for why a notification lost to that gap is survivable on both sides.

    Registration and dispatch both run without ever awaiting in between
    reading and writing the waiter/subscriber maps, so - despite looking
    unguarded - they cannot interleave with each other on asyncio's
    single-threaded loop; no lock is needed for either.
    """

    def __init__(self, db_manager=default_db_manager) -> None:
        self._db_manager = db_manager
        self._waiters: dict[tuple[TaskEventKind, str], asyncio.Event] = {}
        self._waiter_refcounts: dict[tuple[TaskEventKind, str], int] = {}
        self._subscribers: dict[TaskEventKind, dict[int, TaskEventCallback]] = {}
        self._subscription_tokens = count()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background listen loop, once per process."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="task-event-listener"
            )

    async def stop(self) -> None:
        """Cancel the listen loop and wait for its connection to close."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def register(self, kind: TaskEventKind, task_id: str) -> Waiter:
        """Register interest in the next ``kind`` event naming ``task_id``.

        Synchronous and side-effect-free beyond the local waiter map -
        callable at any point relative to a database transaction, which is
        what lets a caller register before committing the very write whose
        eventual consequence it is about to wait for.
        """
        key = (kind, task_id)
        event = self._waiters.get(key)
        if event is None:
            event = asyncio.Event()
            self._waiters[key] = event
        self._waiter_refcounts[key] = self._waiter_refcounts.get(key, 0) + 1
        return Waiter(self, key, event)

    def subscribe(self, kinds: Iterable[TaskEventKind], callback: TaskEventCallback) -> Subscription:
        """Call ``callback(kind, task_id)`` for every future event of any of ``kinds``.

        No wildcard form is offered: a subscriber that has not been told
        which kinds it understands would start receiving future kinds it has
        no logic for the moment this listener learns them, silently
        contradicting the "unrecognized kind is skipped" discipline
        :meth:`_dispatch` otherwise guarantees.

        Args:
            kinds: One or more kinds to receive. Never empty - a subscription
                to nothing can never fire, which is always a caller mistake
                worth catching here rather than by way of a callback that
                silently never runs.
            callback: Invoked synchronously from the listener's own dispatch
                loop, once per matching event, for as many kinds as this
                subscription names - the same discipline
                ``PostgresOwnershipProvider``'s other callbacks already
                follow: schedule work, do not await it here.

        Raises:
            ValueError: If ``kinds`` is empty.
        """
        frozen = frozenset(kinds)
        if not frozen:
            raise ValueError("subscribe() requires at least one TaskEventKind")
        token = next(self._subscription_tokens)
        for kind in frozen:
            self._subscribers.setdefault(kind, {})[token] = callback
        return Subscription(self, token, frozen)

    def _release_waiter(self, key: tuple[TaskEventKind, str]) -> None:
        remaining = self._waiter_refcounts.get(key, 0) - 1
        if remaining > 0:
            self._waiter_refcounts[key] = remaining
        else:
            self._waiter_refcounts.pop(key, None)
            self._waiters.pop(key, None)

    def _release_subscription(self, token: int, kinds: frozenset[TaskEventKind]) -> None:
        for kind in kinds:
            callbacks = self._subscribers.get(kind)
            if callbacks is not None:
                callbacks.pop(token, None)
                if not callbacks:
                    self._subscribers.pop(kind, None)

    async def _run(self) -> None:
        """Keep a subscription alive, reconnecting on any disruption."""
        while True:
            try:
                await self._listen_until_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Task-event listener connection lost; reconnecting", exc_info=True
                )
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    async def _listen_until_disconnected(self) -> None:
        """Hold one dedicated connection and dispatch until it drops."""
        dsn = self._db_manager.get_dsn()
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(
                sql.SQL("LISTEN {}").format(sql.Identifier(TASK_EVENT_CHANNEL))
            )
            logger.info("Task-event listener subscribed to %s", TASK_EVENT_CHANNEL)
            async for notification in conn.notifies():
                self._dispatch(notification.payload)

    def _dispatch(self, payload: str) -> None:
        """Route one notification payload to its waiter and subscribers.

        A payload this process's model cannot parse - an older or newer kind
        than this deployment knows about, mid-rollout - is skipped with a
        debug log rather than raised: one unrecognized event must not take
        down the listen loop, which would silently stop delivery for every
        other task this process cares about.
        """
        try:
            event = TaskEvent.model_validate_json(payload)
        except ValidationError:
            logger.debug("Skipping unrecognized task event payload: %s", payload)
            return

        waiter = self._waiters.get((event.kind, event.task_id))
        if waiter is not None:
            waiter.set()

        for callback in list(self._subscribers.get(event.kind, {}).values()):
            try:
                callback(event.kind, event.task_id)
            except Exception:
                logger.exception(
                    "Task-event subscriber failed for %s (%s)", event.task_id, event.kind
                )


task_event_listener = TaskEventListener()
