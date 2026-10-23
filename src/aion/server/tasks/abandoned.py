"""Settling the tasks whose execution is gone without a word: running, and silent for too long.

A task in ``SUBMITTED`` or ``WORKING`` is presented as running, so something
must be executing it. An execution writes every status and artifact it
produces; when the server running it dies or hangs, the task keeps its active
state and nothing will ever write it again. A task whose last durable write is
older than ``AION_STALE_TASK_TIMEOUT`` is taken for such a task - abandoned -
and settled ``FAILED`` with ``aion:settledReason=task_abandoned``, the
settlement ``aion.server.tasks.settlement`` describes. A task of an
executable extension also gets the error its failed turn closes with, coded
``task_abandoned`` (``aion.core.a2a.codes``), so its client reads it the same
way.

The settlement is a write over the task as it was read: with versions
(PostgreSQL) a write at the version read, without them (the in-memory store)
a write refused when the task was written since. A task that moved in between
- its execution was alive after all - keeps the state it has. The live
execution that later writes over a settled task meets the version conflict
and stops, as it does after a cancel made on another server.

The background sweep runs on every server of the agent, but one at a time:
a sweep takes a lock for its whole length (the candidate query and the
settlements), and a server that finds it held skips that sweep and tries
again at the next interval. With PostgreSQL the lock is an advisory
transaction lock per agent, so a server that dies releases it with its
connection; the in-memory store serves one process and takes none. Settling
the one task a request names takes no lock: the versioned write alone keeps
two servers from settling it twice.

Tasks waiting for input (``INPUT_REQUIRED``, ``AUTH_REQUIRED``) are never
touched: nothing is executing them, by design. Whoever calls
:meth:`AbandonedTasks.settle` decides whether the task's turn runs in its own
process, which is alive by definition; such a task is not settled.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from a2a.server.cluster.event_stream import TaskEventStream, VersionedEvent
from a2a.server.cluster.task_store import ConcurrentTaskModificationError, VersionedTaskStore
from a2a.types import Message, Role, Task, TaskState, TaskStatusUpdateEvent

from aion.core.a2a.enums import A2AMetadataKey, TaskSettlementReason
from aion.core.a2a import codes
from aion.core.a2a.errors import error_parts
from aion.db.postgres import DbManager
from aion.db.postgres.repositories import TasksRepository
from aion.server.a2a.constants import ACTIVE_TASK_STATES
from aion.server.a2a.utils import record_closing_message, routed_extension_of

from .settlement import settled_task
from .stores.in_memory_task_store import InMemoryTaskStore

__all__ = [
    "ABANDONED_MESSAGE",
    "SWEEP_INTERVAL_SECONDS",
    "AbandonedTasks",
    "InMemoryStaleTasks",
    "PostgresStaleTasks",
    "StaleTask",
    "StaleTasks",
    "abandoned_task",
]

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60.0
"""How often a server looks for abandoned tasks; half the timeout when that is shorter."""

ABANDONED_MESSAGE = (
    "The task stopped making progress: the server running it stopped or stopped responding. "
    "Send the request again."
)
"""The error message an executable extension's abandoned task is settled with."""


@dataclass(frozen=True)
class StaleTask:
    """A running task whose last write is older than the timeout, as it was read.

    Attributes:
        task: The task.
        read_as: What the store compares a write over the task with: the
            version it was read at, or the time of its last write.
    """

    task: Task
    read_as: Any


class StaleTasks(Protocol):
    """The store's side of settling abandoned tasks: what is stale, and a write over what was read."""

    def sweep_lock(self) -> AbstractAsyncContextManager[bool]:
        """Take the right to sweep for the length of the ``async with`` block, without waiting for it.

        Yields:
            True when this server holds the right until the block ends, also
            when it ends in an error; False when another server holds it.
        """

    async def candidates(self, timeout: float) -> list[str]:
        """The ids of the agent's running tasks whose last write is older than ``timeout`` seconds."""

    async def read(self, task_id: str, timeout: float) -> Optional[StaleTask]:
        """The task when it is running and its last write is older than ``timeout`` seconds; None otherwise."""

    async def write(self, stale: StaleTask, settled: Task, event: TaskStatusUpdateEvent) -> bool:
        """Write ``settled`` over the task as it was read; False when the task was written since."""


class InMemoryStaleTasks:
    """Reads and writes ``InMemoryTaskStore``, this process's own."""

    def __init__(self, store: InMemoryTaskStore) -> None:
        self._store = store

    @asynccontextmanager
    async def sweep_lock(self) -> AsyncIterator[bool]:
        yield True

    async def candidates(self, timeout: float) -> list[str]:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=timeout)
        return [
            task.id
            for task, written_at in await self._store.write_times()
            if task.status.state in ACTIVE_TASK_STATES and written_at < cutoff
        ]

    async def read(self, task_id: str, timeout: float) -> Optional[StaleTask]:
        found = await self._store.last_write(task_id)
        if found is None:
            return None
        task, written_at = found
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=timeout)
        if task.status.state not in ACTIVE_TASK_STATES or written_at >= cutoff:
            return None
        return StaleTask(task, written_at)

    async def write(self, stale: StaleTask, settled: Task, event: TaskStatusUpdateEvent) -> bool:
        return await self._store.replace_if_last_written_at(settled, stale.read_as)


class PostgresStaleTasks:
    """Reads ``tasks`` of one agent and writes through a2a-sdk's versioned store.

    The age of a task is measured on the database's clock, which stamped its
    last write. The settlement goes through ``versioned_store`` as a2a-sdk's
    own remote cancel does: at the version read, with its event in the task's
    journal, so a subscriber on any server sees the task end.

    The sweep lock is a transaction-level advisory lock on the agent: the
    session that took it stays open for the sweep, and closing it rolls the
    transaction back, which releases the lock whether the sweep ended or
    failed. A server that dies loses its connection and with it the lock.
    """

    def __init__(
            self,
            agent_id: str,
            db_manager: DbManager,
            versioned_store: VersionedTaskStore,
            event_stream: Optional[TaskEventStream] = None,
    ) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager
        self._versioned_store = versioned_store
        self._event_stream = event_stream

    @asynccontextmanager
    async def sweep_lock(self) -> AsyncIterator[bool]:
        async with self._db_manager.get_session() as session:
            yield await TasksRepository(session).try_lock_abandoned_sweep(self._agent_id)

    async def _idle(self, timeout: float, task_id: Optional[uuid.UUID] = None) -> list[uuid.UUID]:
        async with self._db_manager.get_session() as session:
            return await TasksRepository(session).find_idle_ids(
                self._agent_id,
                [TaskState.Name(state) for state in ACTIVE_TASK_STATES],
                timeout,
                task_id=task_id,
            )

    async def candidates(self, timeout: float) -> list[str]:
        return [str(task_id) for task_id in await self._idle(timeout)]

    async def read(self, task_id: str, timeout: float) -> Optional[StaleTask]:
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return None
        # The version first, the age second: a write between the two reads
        # makes the task young, and a write after both moves the version the
        # settlement is written at.
        stored = await self._versioned_store.get(task_id, None)
        if stored is None or stored.task.status.state not in ACTIVE_TASK_STATES:
            return None
        if not await self._idle(timeout, task_uuid):
            return None
        return StaleTask(stored.task, stored.version)

    async def write(self, stale: StaleTask, settled: Task, event: TaskStatusUpdateEvent) -> bool:
        try:
            version = await self._versioned_store.save(
                settled, event=event, prev=stale.task, prev_version=stale.read_as, context=None
            )
        except ConcurrentTaskModificationError:
            return False
        if self._event_stream is not None:
            await self._event_stream.publish(settled.id, VersionedEvent(event=event, version=version))
        return True


def abandoned_task(task: Task) -> Optional[tuple[Task, TaskStatusUpdateEvent]]:
    """The settled copy of an abandoned task and the event that records it; None for a task not running.

    The copy is ``FAILED`` with ``aion:settledReason=task_abandoned``. A task
    routed to an executable extension (``aion:routedExtension``) also gets,
    as its status message, the error its failed turn closes with: code
    ``task_abandoned``, retryable.
    """
    settled = settled_task(task, TaskSettlementReason.TASK_ABANDONED)
    if settled is None:
        return None
    uri = routed_extension_of(task)
    if uri is not None:
        settled.status.message.CopyFrom(
            Message(
                message_id=uuid.uuid4().hex,
                task_id=task.id,
                context_id=task.context_id,
                role=Role.ROLE_AGENT,
                parts=error_parts(
                    uri, codes.TASK_ABANDONED, ABANDONED_MESSAGE, codes.RETRYABLE[codes.TASK_ABANDONED]
                ),
            )
        )
        record_closing_message(settled)
    event = TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=settled.status)
    event.metadata[A2AMetadataKey.SETTLED_REASON.value] = TaskSettlementReason.TASK_ABANDONED.value
    return settled, event


class AbandonedTasks:
    """Settles the agent's abandoned tasks: one on request, or all of them by a periodic sweep.

    Args:
        stale_tasks: The store's side (``StoreManager.get_stale_tasks``).
        timeout: ``AION_STALE_TASK_TIMEOUT``: seconds without a durable write
            after which a running task is abandoned; 0 settles nothing.
        push_sender: Sends the settled task's push notification: no
            execution is left to send it.
    """

    def __init__(self, stale_tasks: StaleTasks, *, timeout: float, push_sender: Any = None) -> None:
        self._stale_tasks = stale_tasks
        self.timeout = timeout
        self._push_sender = push_sender

    @property
    def enabled(self) -> bool:
        """Whether any task is ever settled: a timeout of 0 turns it off."""
        return self.timeout > 0

    @property
    def interval(self) -> float:
        """Seconds between two sweeps: ``SWEEP_INTERVAL_SECONDS``, or half the timeout when that is shorter."""
        return min(SWEEP_INTERVAL_SECONDS, self.timeout / 2)

    async def settle(self, task_id: str) -> Optional[Task]:
        """Settle the task if it is abandoned now.

        The caller has made sure the task's turn does not run in its own
        process.

        Returns:
            The settled task; None when the task is not running, was written
            within the timeout, or was written while it was being settled.
        """
        if not self.enabled:
            return None
        stale = await self._stale_tasks.read(task_id, self.timeout)
        if stale is None:
            return None
        built = abandoned_task(stale.task)
        if built is None:
            return None
        settled, event = built
        if not await self._stale_tasks.write(stale, settled, event):
            logger.info("Task %s was written while it was being settled as abandoned; it is left as it is", task_id)
            return None
        logger.warning(
            "Settled task %s as FAILED (task_abandoned): it was %s without a write for over %gs",
            task_id,
            TaskState.Name(stale.task.status.state),
            self.timeout,
        )
        if self._push_sender is not None:
            try:
                await self._push_sender.send_notification(task_id, settled)
            except Exception:
                logger.exception("Could not push the settlement of task %s", task_id)
        return settled

    async def sweep(self, running_here: Callable[[str], Awaitable[bool]]) -> list[str]:
        """Settle every abandoned task of the agent whose turn does not run in this process.

        A task whose turn runs here is alive in this process, however long
        it has been silent: it is logged and left.

        Only one server sweeps the agent at a time. A sweep takes the store's
        sweep lock without waiting, and holds it until the sweep ends; when
        another server holds it, this sweep does nothing.

        Args:
            running_here: Whether a task's turn runs in this process.

        Returns:
            The ids of the tasks settled; empty when another server was
            sweeping.
        """
        if not self.enabled:
            return []
        async with self._stale_tasks.sweep_lock() as acquired:
            if not acquired:
                logger.debug("Another server is sweeping the abandoned tasks; this sweep is skipped")
                return []
            settled: list[str] = []
            for task_id in await self._stale_tasks.candidates(self.timeout):
                if await running_here(task_id):
                    logger.warning(
                        "Task %s has written nothing for over %gs, but its turn runs in this process; "
                        "it is not settled",
                        task_id,
                        self.timeout,
                    )
                    continue
                try:
                    if await self.settle(task_id) is not None:
                        settled.append(task_id)
                except Exception:
                    logger.exception("Could not settle the abandoned task %s", task_id)
            return settled

    async def run(self, running_here: Callable[[str], Awaitable[bool]]) -> None:
        """Sweep every ``interval`` until cancelled; a failed or skipped sweep is followed by the next one."""
        if not self.enabled:
            return
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.sweep(running_here)
            except Exception:
                logger.exception("The sweep for abandoned tasks failed")
