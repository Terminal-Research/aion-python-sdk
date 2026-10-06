"""a2a-sdk's ``VersionedTaskStore`` over the Aion task schema."""

from __future__ import annotations

import asyncio
import uuid

from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError,
    StoredTask,
    VersionedTaskStore,
)
from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.models import TaskEventModel, TaskVersionModel
from a2a.types import Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from a2a.types import a2a_pb2
from a2a.utils.proto_utils import to_stream_response
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from aion.db.postgres.manager import db_manager
from aion.db.postgres.repositories import TasksRepository
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.files.a2a import strip_inline_file_content
from .postgres_task_store import PostgresTaskStore

_MAX_ATTEMPTS = 5
_RETRY_DELAY_S = 0.02
# Deadlock and serialization failure. psycopg reports these as a generic
# DBAPIError rather than OperationalError.
_TRANSIENT_ERROR_CODES = frozenset({"40P01", "40001"})


def _is_transient(error: DBAPIError) -> bool:
    """Whether ``error`` is database contention that is safe to retry."""
    return (
        isinstance(error, OperationalError)
        or getattr(error.orig, "sqlstate", None) in _TRANSIENT_ERROR_CODES
    )


def _as_int(version: TaskVersion) -> int:
    """The integer a version from this store wraps."""
    value = version._value  # noqa: SLF001 - a2a-sdk's own stores read it the same way
    if not isinstance(value, int):
        raise TypeError(f"PostgresVersionedTaskStore requires integer versions, got {type(value).__name__}")
    return value


class PostgresVersionedTaskStore(VersionedTaskStore):
    """``VersionedTaskStore`` for :class:`PostgresTaskStore`.

    The semantics are those of a2a-sdk's ``VersionedDatabaseTaskStore``; only
    the task tables differ. The task itself stays in Aion's schema (``tasks``
    with ``task_messages`` and ``task_artifacts``), written by
    ``PostgresTaskStore.write``. The version and the journal are a2a-sdk's own
    tables and models, ``task_versions`` and ``task_events``, so a2a-sdk's
    ``DatabaseTaskEventStream`` reads what this store writes.

    * The version counts writes to the task: the first write sets 1, every
      later one adds 1. A write names the version it was derived from, and a
      stale one raises ``ConcurrentTaskModificationError``.
    * A write moving the task to ``CANCELED`` is not checked against a
      version: it overwrites a task that is not terminal, and is refused for
      one that is terminal or absent.
    * The event that produced the write is appended to ``task_events`` with
      the new version, in the same transaction.
    * Every branch locks the version row before the task row, so concurrent
      writers cannot deadlock on the pair.

    Transient contention - a deadlock or a serialization failure - is retried
    a few times. A version conflict never is: it is the signal the caller
    reloads on.
    """

    def __init__(
            self,
            store: PostgresTaskStore,
            *,
            max_attempts: int = _MAX_ATTEMPTS,
            retry_delay_s: float = _RETRY_DELAY_S,
    ) -> None:
        """Wrap ``store``, which keeps writing the task itself.

        Args:
            store: The store the task rows belong to.
            max_attempts: Attempts per call under transient contention.
            retry_delay_s: Base delay between those attempts.
        """
        self._store = store
        self._max_attempts = max_attempts
        self._retry_delay_s = retry_delay_s

    @property
    def as_task_store(self) -> PostgresTaskStore:
        """The underlying unversioned store, for reads that need no version."""
        return self._store

    @property
    def owner_resolver(self):
        """The owner resolver of the underlying store."""
        return self._store.owner_resolver

    async def _retrying(self, operation):
        attempts = 0
        while True:
            attempts += 1
            try:
                return await operation()
            except DBAPIError as error:
                if not _is_transient(error) or attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def save(
            self,
            task: Task,
            *,
            event: Event | None = None,
            prev: Task | None = None,
            prev_version: TaskVersion,
            context: ServerCallContext,
    ) -> TaskVersion:
        """Persist ``task`` with a compare-and-swap on its version.

        Raises:
            ConcurrentTaskModificationError: If ``prev_version`` is stale, or
                a cancellation finds the task terminal or absent, or the task
                is new and its context is being deleted.
        """
        del prev
        return await self._retrying(
            lambda: self._save_once(task, event=event, prev_version=prev_version, context=context)
        )

    async def _save_once(
            self,
            task: Task,
            *,
            event: Event | None,
            prev_version: TaskVersion,
            context: ServerCallContext | None,
    ) -> TaskVersion:
        task_uuid = uuid.UUID(task.id)
        write_owner = self._store._write_owner(context)  # noqa: SLF001
        async with db_manager.get_session() as session:
            async with session.begin():
                if task.status.state == a2a_pb2.TaskState.TASK_STATE_CANCELED:
                    stored = await self._lock_version(session, task.id)
                    current = await TasksRepository(session).find_by_id_for_update(
                        task_uuid, self._store.agent_id, owner_scope=write_owner
                    )
                    if current is None or current.status.state in TERMINAL_TASK_STATES:
                        raise ConcurrentTaskModificationError(task.id)
                    new_version = (stored or 0) + 1
                    if stored is None:
                        await self._insert_version(session, task.id, write_owner, new_version)
                    else:
                        await self._set_version(session, task.id, new_version)
                elif prev_version.is_missing:
                    new_version = 1
                    await self._insert_version(session, task.id, write_owner, new_version)
                else:
                    new_version = _as_int(prev_version) + 1
                    result = await session.execute(
                        update(TaskVersionModel)
                        .where(
                            TaskVersionModel.task_id == task.id,
                            TaskVersionModel.version == _as_int(prev_version),
                        )
                        .values(version=new_version)
                    )
                    if result.rowcount == 0:
                        raise ConcurrentTaskModificationError(task.id)

                owner = await self._store.write(session, task, context)
                if write_owner is None:
                    # A context-less write keeps the recorded owner, which is
                    # only known once the task row has been read.
                    await session.execute(
                        update(TaskVersionModel)
                        .where(TaskVersionModel.task_id == task.id)
                        .values(owner=owner)
                    )

                if event is not None:
                    await session.execute(
                        insert(TaskEventModel).values(
                            task_id=task.id,
                            owner=owner,
                            task_version=new_version,
                            event_data=to_stream_response(self._persistable_event(event)).SerializeToString(),
                        )
                    )
        return TaskVersion(new_version)

    def _persistable_event(self, event: Event) -> Event:
        """The event as it may be journaled: guarded like the task it produced.

        The journal is persistence too, read back by every replica that
        follows the task, so inline file content the store strips from a task
        is stripped from the event that carries it as well.
        """
        if not self._store.guards_inline_files:
            return event
        if isinstance(event, Task):
            return strip_inline_file_content(event)
        if isinstance(event, TaskStatusUpdateEvent) and event.status.HasField("message"):
            probe = Task(id=event.task_id, status=event.status)
            guarded = strip_inline_file_content(probe)
            if guarded is probe:
                return event
            copy = TaskStatusUpdateEvent()
            copy.CopyFrom(event)
            copy.status.CopyFrom(guarded.status)
            return copy
        if isinstance(event, TaskArtifactUpdateEvent):
            probe = Task(id=event.task_id, artifacts=[event.artifact])
            guarded = strip_inline_file_content(probe)
            if guarded is probe:
                return event
            copy = TaskArtifactUpdateEvent()
            copy.CopyFrom(event)
            copy.artifact.CopyFrom(guarded.artifacts[0])
            return copy
        return event

    @staticmethod
    async def _lock_version(session: AsyncSession, task_id: str) -> int | None:
        return (
            await session.execute(
                select(TaskVersionModel.version)
                .where(TaskVersionModel.task_id == task_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _insert_version(
            session: AsyncSession, task_id: str, owner: str | None, version: int
    ) -> None:
        # The insert is what makes one of two concurrent first writers lose.
        try:
            async with session.begin_nested():
                await session.execute(
                    insert(TaskVersionModel).values(task_id=task_id, owner=owner, version=version)
                )
        except IntegrityError as error:
            raise ConcurrentTaskModificationError(task_id) from error

    @staticmethod
    async def _set_version(session: AsyncSession, task_id: str, version: int) -> None:
        await session.execute(
            update(TaskVersionModel)
            .where(TaskVersionModel.task_id == task_id)
            .values(version=version)
        )

    async def get(
            self, task_id: str, context: ServerCallContext | None
    ) -> StoredTask | None:
        """The task with the version it was read at, or ``None`` if absent.

        The task and its version come from one ``REPEATABLE READ`` snapshot. A
        task written before versions existed has no version row and reads as
        ``TaskVersion.MISSING``.
        """
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return None
        return await self._retrying(lambda: self._get_once(task_uuid, task_id, context))

    async def _get_once(
            self, task_uuid: uuid.UUID, task_id: str, context: ServerCallContext | None
    ) -> StoredTask | None:
        async with db_manager.get_session() as session:
            async with session.begin():
                await self._store._repeatable_read(session)  # noqa: SLF001
                task = await self._store.get_in_session(session, task_uuid, context)
                if task is None:
                    return None
                version = (
                    await session.execute(
                        select(TaskVersionModel.version).where(TaskVersionModel.task_id == task_id)
                    )
                ).scalar_one_or_none()
        return StoredTask(task, TaskVersion(version) if version is not None else TaskVersion.MISSING)

    async def list(
            self,
            params: a2a_pb2.ListTasksRequest,
            context: ServerCallContext,
    ) -> a2a_pb2.ListTasksResponse:
        """List tasks through the underlying store."""
        return await self._store.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Delete a task and its version row through the underlying store."""
        await self._store.delete(task_id, context)
