"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Optional, List

from a2a.server.cluster.task_store import ConcurrentTaskModificationError
from a2a.server.context import ServerCallContext
from a2a.server.models import TaskVersionModel
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import Message, Task, TaskState, TaskStatus
from a2a.types import a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE, MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.task import validate_history_length
from opentelemetry import trace
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.db.postgres.manager import db_manager
from aion.db.postgres.types import Pagination, Sorting, SortKey
from aion.db.postgres.repositories import (
    ContextReservationsRepository,
    TaskArtifactsRepository,
    TaskMessagesRepository,
    TasksRepository,
)
from aion.db.postgres.records import TaskRecord
from .base_task_store import BaseTaskStore
from .page_token import PageCursor, decode_page_token, encode_page_token

_tracer = trace.get_tracer(__name__)


class PostgresTaskStore(BaseTaskStore):
    """Store tasks in a Postgres database using repository pattern.

    ``tasks`` holds each task's compact current-state head; history and
    artifacts live in ``task_messages``/``task_artifacts`` instead, one row
    per entry. Every method here still speaks the same ``save(full_task)`` /
    ``get(task_id)`` contract ``a2a.server.tasks.TaskManager`` expects - the
    normalization is entirely behind that contract:

    - ``save`` always receives the caller's complete, current ``Task``
      (that is how ``TaskManager`` works; it keeps one in-memory ``Task`` and
      hands the whole thing to ``save`` on every event). It writes the head,
      then asks the message and artifact repositories to reconcile their
      tables against ``task.history`` (plus ``status.message``, still
      unpromoted - see :meth:`_effective_history`) and ``task.artifacts`` -
      inserting only a new tail of messages, upserting only artifacts whose
      content changed. Both are no-ops on an empty list, which is what keeps
      a caller that never touches history or artifacts from clearing either
      table by passing an unhydrated ``Task`` through ``save``.
    - Every method that returns a ``Task`` to a caller outside this store
      hydrates it fully from both child tables first, inside a
      ``REPEATABLE READ`` transaction so the head and its children come from
      one consistent snapshot rather than whatever each of several
      ``READ COMMITTED`` statements happened to see.

    This store does not order concurrent writers. a2a-sdk's cluster mode does
    that with a version per task, and :class:`PostgresVersionedTaskStore`
    adds it around this store: its compare-and-swap and this store's write
    share one transaction (:meth:`write`).
    """

    def __init__(
            self,
            agent_id: str,
            owner_resolver: OwnerResolver = resolve_user_scope,
            *,
            guard_inline_files: bool = False,
    ) -> None:
        """Initialize the store with its agent identity.

        Args:
            agent_id: Identity of the agent this process serves. Every query
                and write below is scoped to it, so several agents can share
                one database without ever seeing each other's tasks.
            owner_resolver: Resolves the effective caller into the stable scope
                persisted with each task.
            guard_inline_files: Strip inline file content before writing -
                see :class:`BaseTaskStore`.
        """
        super().__init__(guard_inline_files=guard_inline_files)
        if not agent_id:
            raise ValueError("PostgresTaskStore requires a non-empty agent_id")
        self.agent_id = agent_id
        self.owner_resolver = owner_resolver

    @staticmethod
    async def _repeatable_read(session: AsyncSession) -> None:
        """Start a snapshot that every query in this transaction shares.

        Must be the first statement of the transaction. Without it, each
        query below sees its own ``READ COMMITTED`` snapshot, and a
        concurrent write between the head read and the children reads could
        hand back a status that does not match the history or artifacts
        assembled alongside it.
        """
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    @staticmethod
    def _without_unpromoted_status_message(
            status: TaskStatus, history: List[Message]
    ) -> List[Message]:
        """Drop ``history``'s last entry when it is still only ``status.message``.

        ``save`` writes the current ``status.message`` into ``task_messages``
        as a provisional tail entry (see :meth:`_effective_history`), so a
        task that ends on it - a terminal state has no next event to promote
        it - does not lose it. Reassembling a ``Task`` from ``status`` plus
        that same stored entry would otherwise present the message twice:
        once as ``status.message``, once as ``history[-1]``.

        Comparing against the *current* status is what tells "still
        provisional" apart from "genuinely promoted": once a later save moves
        the message on and ``status`` holds something else, the stored entry
        no longer matches and is real history that stays.
        """
        if not history or not status.HasField('message'):
            return history
        last, current = history[-1], status.message
        is_same = (
            last.message_id == current.message_id
            if last.message_id or current.message_id
            else last == current
        )
        return history[:-1] if is_same else history

    @staticmethod
    async def _hydrate(
            session: AsyncSession,
            entity: TaskRecord,
            *,
            history_limit: Optional[int] = None,
            include_artifacts: bool = True,
    ) -> Task:
        """Assemble one full A2A ``Task`` from a head entity and its children."""
        history = await TaskMessagesRepository(session).find_by_task_id(
            entity.id, limit=history_limit
        )
        history = PostgresTaskStore._without_unpromoted_status_message(
            entity.status, history
        )
        artifacts = (
            await TaskArtifactsRepository(session).find_by_task_id(entity.id)
            if include_artifacts
            else None
        )
        return entity.to_task(str(entity.id), history=history, artifacts=artifacts)

    @staticmethod
    async def _hydrate_many(
            session: AsyncSession, entities: List[TaskRecord]
    ) -> List[Task]:
        """Assemble full A2A ``Task``s for many heads in two bulk queries.

        The multi-task counterpart of :meth:`_hydrate`: one query for every
        entity's history and one for every entity's artifacts, rather than
        two per entity.
        """
        if not entities:
            return []
        ids = [entity.id for entity in entities]
        history_by_id = await TaskMessagesRepository(session).find_by_task_ids(ids)
        artifacts_by_id = await TaskArtifactsRepository(session).find_by_task_ids(ids)
        return [
            entity.to_task(
                str(entity.id),
                history=PostgresTaskStore._without_unpromoted_status_message(
                    entity.status, history_by_id[entity.id]
                ),
                artifacts=artifacts_by_id[entity.id],
            )
            for entity in entities
        ]

    @staticmethod
    def _effective_history(task: Task) -> List[Message]:
        """``task.history`` plus the message still attached to ``status``, if any.

        ``task_messages`` is meant to hold every message the task has ever
        carried, not just the ones ``TaskManager`` has gotten around to
        promoting into ``history``. ``TaskManager.save_task_event`` only
        moves ``status.message`` into ``history`` when the *next*
        ``TaskStatusUpdateEvent`` arrives - so a task that ends on a message
        attached to its final status (a terminal state has no next event)
        would otherwise never write that message anywhere but the ``status``
        JSONB on the head row.

        Treating the current ``status.message`` as history's provisional
        next entry is safe to repeat: when a later event does promote it,
        it lands at the same append-only position it already occupies here,
        so :meth:`TaskMessagesRepository.append_new`'s position-based diff
        recognizes it as already stored rather than inserting it twice. The
        read side undoes this the same way it was done - see
        :meth:`_without_unpromoted_status_message`.
        """
        if task.status.HasField('message'):
            return list(task.history) + [task.status.message]
        return list(task.history)

    async def save(
            self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Save a task in a transaction of its own.

        See :meth:`write` for what is written and when a write is refused.
        """
        async with db_manager.get_session() as session:
            async with session.begin():
                await self.write(session, task, context)

    async def write(
            self,
            session: AsyncSession,
            task: Task,
            context: ServerCallContext | None = None,
    ) -> str:
        """Write a task inside the caller's transaction.

        A task's owner (``owner_scope``) is fixed by its first write. A new
        task takes the owner its context names - a user's context that
        user, an explicit ``ServerCallContext()`` the anonymous owner ``""``
        - and ``None`` cannot create one. An existing task keeps its owner:
        a write with ``None`` updates it as recorded, a write with another
        owner's context is refused.

        A new task is refused while its context is being deleted or once it
        was: admitted before the deletion started and created after it, the
        task would outlive its context. The refusal is
        ``ConcurrentTaskModificationError``, the error a2a-sdk's task manager
        already handles for a write another writer has overtaken.

        Returns:
            The owner the task is written under.

        Raises:
            ValueError: If ``task.id`` is not a UUID (see
                :meth:`TaskRecord.from_task`).
            TaskOwnerUndefinedError: If the task is new and ``context`` is
                ``None``.
            TaskOwnerMismatchError: If ``context`` resolves to another owner.
            ConcurrentTaskModificationError: If the task is new and its
                context is being deleted or was.
        """
        # Before TaskRecord.from_task, not between the writes below: the head
        # row carries `status` as JSONB of its own, so a guard placed in front
        # of the message and artifact tables alone would still let raw bytes
        # into it.
        task = self._persistable(task)
        task_uuid = uuid.UUID(task.id)
        write_owner = self._write_owner(context)

        repository = TasksRepository(session)
        # The row lock the upsert below takes anyway, taken first so the
        # owner it reads cannot change before the write.
        recorded = await repository.lock_owner_scope(task_uuid)
        owner = self._owner_of_write(task.id, write_owner, recorded)
        if recorded is None and task.context_id and not await ContextReservationsRepository(
            session
        ).admits_new_task(self.agent_id, task.context_id):
            raise ConcurrentTaskModificationError(task.id)
        entity = TaskRecord.from_task(task, self.agent_id, owner)
        await repository.save(entity)
        await TaskMessagesRepository(session).append_new(
            entity.id, self._effective_history(task)
        )
        await TaskArtifactsRepository(session).upsert_batch(entity.id, task.artifacts)
        return owner

    async def get_in_session(
            self,
            session: AsyncSession,
            task_id: uuid.UUID,
            context: ServerCallContext | None = None,
    ) -> Task | None:
        """Read one fully hydrated task inside the caller's transaction."""
        entity = await TasksRepository(session).find_by_id(
            task_id, self.agent_id, owner_scope=self._owner_filter(context)
        )
        if not entity:
            return None
        return await self._hydrate(session, entity)

    async def get(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Get a task by ID, with its full history and artifacts.

        With a caller's context, only that caller's task is found: another
        owner's task reads as absent, as it does in the in-memory store. This
        is the check the registry relies on before it hands a live task to a
        subscriber or cancels one. ``None`` reads regardless of owner: the
        store's deliberate unfiltered access, which no A2A request path uses.
        """
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return None

        async with db_manager.get_session() as session:
            async with session.begin():
                await self._repeatable_read(session)
                return await self.get_in_session(session, task_uuid, context)

    async def delete(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Delete a task by ID - the caller's, or any owner's with no context.

        With a context only a task of the caller's owner is deleted; another
        owner's is left as it is, exactly as in the in-memory store. ``None``
        deletes regardless of owner: the store's deliberate unfiltered access,
        which no A2A request path uses. ``task_messages`` and
        ``task_artifacts`` rows cascade with it. The task's version row goes
        in the same transaction, as a2a-sdk's versioned store removes it; its
        events stay in ``task_events``, which a2a-sdk never prunes.
        """
        owner_scope = self._owner_filter(context)
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return

        async with db_manager.get_session() as session:
            async with session.begin():
                # The version row before the task row, in the order every
                # versioned write takes them, so a delete and a write of the
                # same task cannot deadlock.
                await session.execute(
                    select(TaskVersionModel.task_id)
                    .where(TaskVersionModel.task_id == task_id)
                    .with_for_update()
                )
                deleted = await TasksRepository(session).delete_by_id(
                    task_uuid, self.agent_id, owner_scope=owner_scope
                )
                if deleted:
                    await session.execute(
                        delete(TaskVersionModel).where(TaskVersionModel.task_id == task_id)
                    )

    async def list(
            self,
            params: a2a_pb2.ListTasksRequest,
            context: ServerCallContext | None = None,
    ) -> a2a_pb2.ListTasksResponse:
        """List tasks with optional filtering and keyset pagination.

        ``total_size`` comes from a single ``COUNT(*)``, and a page is found
        by keeping only rows past the previous page's last position in the
        same order - never by loading every matching id or asking the
        database to skip ``OFFSET`` rows of a full sort. Both would cost the
        same as scanning the whole filtered result on a large table; this
        costs one page.

        The page token carries its keyset position - ``status_timestamp`` and
        ``id`` - directly, plus a fingerprint of the filters it was issued
        under. That removes the extra point lookup a bare task id would need
        to resolve back into a position, and rejects a token replayed against
        a different query instead of silently paging the wrong listing.

        History and artifacts are read only when the request can use them:
        ``history_length=0`` skips the messages query entirely, and
        ``include_artifacts=false`` skips the artifacts query entirely,
        rather than loading either in full and trimming the result.

        Only the caller's own tasks are listed and counted: with a context,
        ``owner_scope`` is part of both queries, so another user's tasks, their
        ids and their history never appear, and ``total_size`` does not count
        them. The page token needs no owner of its own for that - the filter
        is applied to every page, so a token can only position the cursor
        within the listing of whoever presents it.

        Args:
            params: Filter, page size, and page token of the request.
            context: Server call context; its owner, resolved by this
                store's ``owner_resolver``, selects the owner scope. ``None``
                lists every owner's tasks.

        Returns:
            The requested page, the total number of matching tasks, and a token
            for the next page when one exists.

        Raises:
            InvalidParamsError: If the page token is malformed, unversioned,
                or was issued under different filters than this request.
        """
        validate_history_length(params)

        status_state = TaskState.Name(params.status) if params.status else None
        status_timestamp_after = (
            params.status_timestamp_after.ToDatetime(tzinfo=timezone.utc)
            if params.HasField('status_timestamp_after')
            else None
        )
        filters = dict(
            context_id=params.context_id or None,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        page_size = min(page_size, MAX_LIST_TASKS_PAGE_SIZE)

        after = None
        if params.page_token:
            after = decode_page_token(params.page_token, **filters)

        history_limit: Optional[int] = None
        skip_history = params.HasField('history_length') and params.history_length == 0
        if params.HasField('history_length') and params.history_length > 0:
            history_limit = params.history_length

        owner_scope = self._owner_filter(context)
        async with db_manager.get_session() as session:
            async with session.begin():
                await self._repeatable_read(session)
                repository = TasksRepository(session)

                # A separate span so the p95 cost of the exact COUNT is visible
                # apart from the page query it accompanies on every request.
                with _tracer.start_as_current_span("tasks.list.count"):
                    total_size = await repository.count(
                        agent_id=self.agent_id, owner_scope=owner_scope, **filters
                    )

                # One extra row reveals whether a next page exists without a
                # second round trip or an approximate `has_more` from the count.
                entities = await repository.find_page(
                    agent_id=self.agent_id,
                    owner_scope=owner_scope,
                    after=after,
                    limit=page_size + 1,
                    **filters,
                )

                has_next = len(entities) > page_size
                entities = entities[:page_size]

                ids = [entity.id for entity in entities]
                history_by_id = (
                    {task_id: [] for task_id in ids}
                    if skip_history
                    else await TaskMessagesRepository(session).find_by_task_ids(
                        ids, limit=history_limit
                    )
                )
                artifacts_by_id = (
                    {task_id: [] for task_id in ids}
                    if not params.include_artifacts
                    else await TaskArtifactsRepository(session).find_by_task_ids(ids)
                )

        tasks = [
            entity.to_task(
                str(entity.id),
                history=self._without_unpromoted_status_message(
                    entity.status, history_by_id[entity.id]
                ),
                artifacts=artifacts_by_id[entity.id],
            )
            for entity in entities
        ]

        next_page_token = ""
        if has_next:
            last = entities[-1]
            cursor = PageCursor(status_timestamp=last.status_timestamp, id=str(last.id))
            next_page_token = encode_page_token(cursor, **filters)

        return a2a_pb2.ListTasksResponse(
            tasks=tasks,
            total_size=total_size,
            page_size=page_size,
            next_page_token=next_page_token,
        )

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: ServerCallContext | None = None,
    ) -> List[Task]:
        """Retrieve tasks for a specific context, capped even when the caller asks for everything.

        An omitted ``limit`` falls back to the default page size rather than
        reaching the repository as ``None``, which would load every task -
        full JSON artifacts and history included - that the context has ever
        had.
        """
        limit = min(limit, MAX_LIST_TASKS_PAGE_SIZE) if limit else DEFAULT_LIST_TASKS_PAGE_SIZE
        async with db_manager.get_session() as session:
            async with session.begin():
                await self._repeatable_read(session)
                repository = TasksRepository(session)
                records = await repository.find(
                    agent_id=self.agent_id,
                    owner_scope=self._owner_filter(context),
                    context_id=context_id,
                    pagination=Pagination(limit=limit, offset=offset),
                    sorting=Sorting(SortKey(column="created_at")),
                )
                return await self._hydrate_many(session, records)

    async def get_context_last_task(
            self,
            context_id: str,
            context: ServerCallContext | None = None,
    ) -> Optional[Task]:
        """Retrieve the most recent task for a specific context.

        Only an empty context yields ``None``. Database failures propagate:
        auto-discovery of a resumable task is built on this call, and a
        connection error reported as "no previous task" would silently start a
        fresh task over work that already exists.

        Args:
            context_id: Context whose most recent task is wanted.

        Returns:
            The most recently created task of the context, or ``None`` when the
            context holds no tasks.
        """
        tasks = await self.get_context_tasks(
            context_id=context_id,
            limit=1,
            context=context,
        )
        return tasks[0] if tasks else None
