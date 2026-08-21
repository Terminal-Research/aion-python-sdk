"""Waking a pod that signaled a cancellation it does not own.

``request_cancellation`` (see ``PostgresTaskStore``) marks a claim and
returns; the owner discovers the mark on its next heartbeat renewal and, on
finishing its own graceful cancellation, notifies
``aion.db.postgres.constants.TASK_TERMINAL_CHANNEL`` with the task id as
payload - conditionally, only when a claim still carries a request (see
``TaskClaimsRepository.notify_terminal_if_cancel_requested``). This module is
the receiving side: one listener per process, dispatching by payload to
whichever in-process wait is registered for that task id.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import psycopg
from psycopg import sql

from aion.db.postgres import db_manager as default_db_manager
from aion.db.postgres.constants import TASK_TERMINAL_CHANNEL

__all__ = ["TaskTerminalListener", "task_terminal_listener"]

logger = logging.getLogger(__name__)

# How long to wait before retrying after the listen connection drops. Short:
# every second spent reconnecting is a second where a terminal notification
# for a waiting request is silently lost - the waiter's own timeout and final
# store read (see AionRequestHandler.on_cancel_task) are what make that loss
# survivable rather than a hang, not a reason to make it common.
_RECONNECT_DELAY_SECONDS = 2.0


class Waiter:
    """A registered interest in one task's terminal notification.

    Obtained from :meth:`TaskTerminalListener.register`, which must be called
    before the caller's own write makes the owner able to see whatever it is
    waiting on - see ``AionRequestHandler.on_cancel_task``, the one caller.
    Splitting registration from the wait is what makes that ordering
    possible: registering only builds the local ``asyncio.Event`` and touches
    no database, so it can happen strictly before the transaction that will
    eventually cause the notification, with nothing in between that could let
    a fast owner's notification arrive before anyone is listening for it.
    """

    __slots__ = ("_listener", "_task_id", "_event")

    def __init__(self, listener: "TaskTerminalListener", task_id: str, event: asyncio.Event) -> None:
        self._listener = listener
        self._task_id = task_id
        self._event = event

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for a notification naming this task.

        Returns:
            ``True`` if a matching notification arrived, ``False`` on
            timeout. ``False`` is not proof nothing happened: the caller
            must still re-read the store, since a notification can be lost
            to a listener reconnect independently of whether the terminal
            write itself succeeded.
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        """Forget this registration. Call exactly once, whether or not waited on."""
        self._listener._release(self._task_id)


class TaskTerminalListener:
    """Dispatches ``TASK_TERMINAL_CHANNEL`` notifications to local waiters.

    Holds exactly one connection for the life of the process, opened
    directly rather than borrowed from ``DbManager``'s pool: a pooled
    connection is returned and reused by an unrelated query, which would
    silently end the subscription the moment that happened. Reconnects and
    re-subscribes on its own after any disruption - a notification sent
    during that gap is lost, which is why every waiter also re-checks the
    store directly once its own wait budget elapses rather than trusting
    this channel alone.

    Registration and dispatch both run without ever awaiting in between
    reading and writing the waiter map, so - despite looking unguarded - they
    cannot interleave with each other on asyncio's single-threaded loop; no
    lock is needed for either.
    """

    def __init__(self, db_manager=default_db_manager) -> None:
        self._db_manager = db_manager
        self._waiters: dict[str, asyncio.Event] = {}
        self._waiter_refcounts: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background listen loop, once per process."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="task-terminal-listener"
            )

    async def stop(self) -> None:
        """Cancel the listen loop and wait for its connection to close."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def register(self, task_id: str) -> Waiter:
        """Register interest in ``task_id``'s next terminal notification.

        Synchronous and side-effect-free beyond the local waiter map -
        callable at any point relative to a database transaction, which is
        what lets a caller register before committing the very write whose
        eventual consequence it is about to wait for.
        """
        event = self._waiters.get(task_id)
        if event is None:
            event = asyncio.Event()
            self._waiters[task_id] = event
        self._waiter_refcounts[task_id] = self._waiter_refcounts.get(task_id, 0) + 1
        return Waiter(self, task_id, event)

    def _release(self, task_id: str) -> None:
        remaining = self._waiter_refcounts.get(task_id, 0) - 1
        if remaining > 0:
            self._waiter_refcounts[task_id] = remaining
        else:
            self._waiter_refcounts.pop(task_id, None)
            self._waiters.pop(task_id, None)

    async def _run(self) -> None:
        """Keep a subscription alive, reconnecting on any disruption."""
        while True:
            try:
                await self._listen_until_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Task-terminal listener connection lost; reconnecting", exc_info=True
                )
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    async def _listen_until_disconnected(self) -> None:
        """Hold one dedicated connection and dispatch until it drops."""
        dsn = self._db_manager.get_dsn()
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(
                sql.SQL("LISTEN {}").format(sql.Identifier(TASK_TERMINAL_CHANNEL))
            )
            logger.info("Task-terminal listener subscribed to %s", TASK_TERMINAL_CHANNEL)
            async for notification in conn.notifies():
                self._dispatch(notification.payload)

    def _dispatch(self, task_id: str) -> None:
        """Wake the local waiter for ``task_id``, if this process has one."""
        event = self._waiters.get(task_id)
        if event is not None:
            event.set()


task_terminal_listener = TaskTerminalListener()
