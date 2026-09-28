"""Postgres implementation of ``TaskStore``."""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Optional, List

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import Message, Task, TaskState, TaskStatus
from a2a.types import a2a_pb2
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE, MAX_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError, TaskNotCancelableError
from a2a.utils.task import validate_history_length
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from aion.db.postgres.manager import db_manager
from aion.db.postgres.types import Pagination, Sorting, SortKey
from aion.db.postgres.repositories import (
    TaskArtifactsRepository,
    TaskClaimsRepository,
    TaskMessagesRepository,
    TasksRepository,
)
from aion.db.postgres.records import TaskRecord
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.tasks.identifiers import require_task_uuid
from aion.server.tasks.ownership import OwnershipProvider, TaskOwnershipLost
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
      a caller that never touches history or artifacts - the reaper settling
      a task, cancellation - from clearing either table by passing an
      unhydrated ``Task`` through ``save``.
    - Every method that returns a ``Task`` to a caller outside this store
      hydrates it fully from both child tables first, inside a
      ``REPEATABLE READ`` transaction so the head and its children come from
      one consistent snapshot rather than whatever each of several
      ``READ COMMITTED`` statements happened to see.
    """

    def __init__(
            self,
            agent_id: str,
            ownership_provider: OwnershipProvider,
            owner_resolver: OwnerResolver = resolve_user_scope,
            *,
            guard_inline_files: bool = False,
    ) -> None:
        """Initialize the store with its agent identity and ownership provider.

        The provider is required rather than optional: an instance without one
        would be a shared store whose writes are unfenced, which is the exact
        combination the pairing in ``StoreManager`` exists to make unreachable.

        Args:
            agent_id: Identity of the agent this process serves. Every query
                and write below is scoped to it, so several agents can share
                one database without ever seeing each other's tasks.
            ownership_provider: Fences writes against a concurrently running
                incarnation of the same task.
            owner_resolver: Resolves the effective caller into the stable scope
                persisted with each task.
            guard_inline_files: Strip inline file content before writing -
                see :class:`BaseTaskStore`.
        """
        super().__init__(guard_inline_files=guard_inline_files)
        if not agent_id:
            raise ValueError("PostgresTaskStore requires a non-empty agent_id")
        if ownership_provider is None:
            raise ValueError("PostgresTaskStore requires an ownership provider")
        self.agent_id = agent_id
        self.ownership_provider = ownership_provider
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
        """Save a task.

        A task's owner (``owner_scope``) is fixed by its first write. A new
        task takes the owner its context names - a user's context that
        user, an explicit ``ServerCallContext()`` the anonymous owner ``""``
        - and ``None`` cannot create one. An existing task keeps its owner:
        a write with ``None`` updates it as recorded, a write with another
        owner's context is refused. Separately, the write is fenced by this
        process's lease on the task - the lease owner is a server process
        and has nothing to do with the user who owns the task.

        Raises:
            ValueError: If ``task.id`` is not a UUID (see
                :meth:`TaskRecord.from_task`).
            TaskOwnerUndefinedError: If the task is new and ``context`` is
                ``None``.
            TaskOwnerMismatchError: If ``context`` resolves to another owner.
            TaskOwnershipLost: If this process no longer holds the lease.
        """
        # Before TaskRecord.from_task, not between the writes below: the head
        # row carries `status` as JSONB of its own, so a guard placed in front
        # of the message and artifact tables alone would still let raw bytes
        # into it.
        task = self._persistable(task)
        task_uuid = uuid.UUID(task.id)
        write_owner = self._write_owner(context)

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            # The row lock the upsert below takes anyway, taken first so the
            # owner it reads cannot change before the write. Who owns the
            # task is settled before the lease, so a write naming no owner
            # for a new task fails the same way as in the in-memory store.
            recorded = await repository.lock_owner_scope(task_uuid)
            owner = self._owner_of_write(task.id, write_owner, recorded)
            claim = self.ownership_provider.claim_for(task.id)
            if claim is None:
                raise TaskOwnershipLost(task.id)
            entity = TaskRecord.from_task(task, self.agent_id, owner)
            if not await repository.save_owned_locked(entity, claim.owner_token):
                raise TaskOwnershipLost(task.id)
            await TaskMessagesRepository(session).append_new(
                entity.id, self._effective_history(task)
            )
            await TaskArtifactsRepository(session).upsert_batch(entity.id, task.artifacts)
            if entity.status.state in TERMINAL_TASK_STATES:
                # Before the claim is released - the release happens one
                # layer up, in AionTaskManager._save_task, only after this
                # write returns successfully. A pod waiting on this task's
                # cancellation is only woken if it actually asked; see
                # notify_cancel_resolved.
                await TaskClaimsRepository(session).notify_cancel_resolved(entity.id)
            await session.commit()

    async def cancel_with_ownership_revocation(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Cancel a task while holding its task-row mutex.

        Cancellation is the one control-plane path that intentionally does not
        present an owner token.  It locks ``tasks`` first, changes a non-terminal
        task to ``CANCELED``, and removes any claim in the same transaction so a
        running owner discovers the loss on its next heartbeat.

        The already-terminal case is reported from here rather than inferred by
        the caller from the returned state: a successful cancellation also ends
        in a terminal state, so afterwards the two are indistinguishable.

        With a caller's context, only that caller's task is found - its
        ``owner_scope`` is part of the locking query, so another user's task
        is neither locked nor changed and reads as absent. The owner token the
        claim carries names a server process, not a user, and says nothing
        about who may cancel. ``None`` reaches every owner's task, as everywhere
        in this store.

        Returns:
            The canceled task, or ``None`` when no such task exists for this
            caller.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        try:
            task_uuid = require_task_uuid(task_id)
        except InvalidParamsError:
            return None

        owner_scope = self._owner_filter(context)
        async with db_manager.get_session() as session:
            async with session.begin():
                # No REPEATABLE READ here on purpose: find_by_id_for_update's
                # row lock already blocks the only writer that touches this
                # task's messages and artifacts (save_owned takes the same
                # lock first), which gives the hydration below a consistent
                # view without paying for snapshot isolation - and without
                # its serialization failures on the UPDATE further down.
                tasks = TasksRepository(session)
                claims = TaskClaimsRepository(session)
                entity = await tasks.find_by_id_for_update(
                    task_uuid, self.agent_id, owner_scope=owner_scope
                )
                if entity is None:
                    return None

                task = await self._hydrate(session, entity)
                if task.status.state in TERMINAL_TASK_STATES:
                    raise TaskNotCancelableError(
                        message=(
                            "Task cannot be canceled - current state: "
                            f"{TaskState.Name(task.status.state)}"
                        )
                    )

                task.status.state = TaskState.TASK_STATE_CANCELED
                await tasks.update_status(task_uuid, self.agent_id, task.status)
                # Deleted without a token on purpose: removing the lease is how
                # a pod that is not the owner tells the owner it has stopped
                # being one.
                await claims.revoke_unconditionally(task_uuid, self.agent_id)

                return task

    async def request_cancellation(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> Optional[bool]:
        """Ask this task's owner to cancel it, without writing a terminal state.

        The non-owner mirror of :meth:`save`'s fenced write: a pod that does
        not hold this task's claim cannot run its teardown, so instead of
        writing ``CANCELED`` itself it marks the claim and lets the owner
        discover the mark on its next heartbeat renewal and cancel locally -
        see ``PostgresOwnershipProvider._notify_control_signal_once`` and
        ``AionActiveTaskRegistry._on_control_signal``. The eventual terminal
        write, and its notification to whoever is waiting on it, both happen
        over there, not here.

        Locks the task row before marking the claim, in the same transaction,
        for the same reason :meth:`cancel_with_ownership_revocation` does:
        a task that reaches an outcome between the read and the write must
        not have a cancellation request attached to a claim it no longer
        needs. Whichever of the two writers gets the lock first decides the
        race; the loser's transaction observes the outcome the winner already
        committed.

        Returns:
            ``True`` when a live claim was marked - the caller should now
            wait for the terminal write it will trigger. ``False`` when the
            task exists but has no live claim - there is no owner to ask, and
            the caller must close the task out directly instead, exactly as
            :meth:`cancel_with_ownership_revocation` already does. ``None``
            when no such task exists for this caller: as there, the caller's
            ``owner_scope`` is part of the locking query, so another user's
            task is neither locked nor marked.

        Raises:
            TaskNotCancelableError: If the task already has an outcome.
        """
        try:
            task_uuid = require_task_uuid(task_id)
        except InvalidParamsError:
            return None

        owner_scope = self._owner_filter(context)
        async with db_manager.get_session() as session:
            async with session.begin():
                tasks = TasksRepository(session)
                claims = TaskClaimsRepository(session)
                entity = await tasks.find_by_id_for_update(
                    task_uuid, self.agent_id, owner_scope=owner_scope
                )
                if entity is None:
                    return None

                if entity.status.state in TERMINAL_TASK_STATES:
                    raise TaskNotCancelableError(
                        message=(
                            "Task cannot be canceled - current state: "
                            f"{TaskState.Name(entity.status.state)}"
                        )
                    )

                marked = await claims.request_cancel(task_uuid, self.agent_id)
                return marked is not None

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

        owner_scope = self._owner_filter(context)
        async with db_manager.get_session() as session:
            async with session.begin():
                await self._repeatable_read(session)
                repository = TasksRepository(session)
                entity = await repository.find_by_id(
                    task_uuid, self.agent_id, owner_scope=owner_scope
                )

                if not entity:
                    return None

                return await self._hydrate(session, entity)

    async def delete(
            self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Delete a task by ID - the caller's, or any owner's with no context.

        With a context only a task of the caller's owner is deleted; another
        owner's is left as it is, exactly as in the in-memory store. ``None``
        deletes regardless of owner: the store's deliberate unfiltered access,
        which no A2A request path uses. ``task_messages`` and
        ``task_artifacts`` rows cascade with it; there is nothing else here to
        clean up.
        """
        owner_scope = self._owner_filter(context)
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            return

        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            await repository.delete_by_id(task_uuid, self.agent_id, owner_scope=owner_scope)
            await session.commit()

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

    async def get_context_ids(
            self,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: ServerCallContext | None = None,
    ) -> List[str]:
        """Retrieve unique context IDs, capped even when the caller asks for everything.

        ``limit=None`` used to reach the repository unbounded, and
        ``_apply_pagination`` skips a falsy limit - so an omitted limit read
        every context id the table has ever seen. Callers that truly want a
        default get one now instead of an unbounded scan.
        """
        limit = min(limit, MAX_LIST_TASKS_PAGE_SIZE) if limit else DEFAULT_LIST_TASKS_PAGE_SIZE
        async with db_manager.get_session() as session:
            repository = TasksRepository(session)
            return await repository.find_unique_context_ids(
                agent_id=self.agent_id,
                owner_scope=self._owner_filter(context),
                pagination=Pagination(limit=limit, offset=offset),
            )

    async def get_context_tasks(
            self,
            context_id: str,
            offset: Optional[int] = None,
            limit: Optional[int] = None,
            context: ServerCallContext | None = None,
    ) -> List[Task]:
        """Retrieve tasks for a specific context, capped even when the caller asks for everything.

        Same reasoning as :meth:`get_context_ids`: an omitted ``limit``
        previously reached the repository as ``None`` and came back
        unbounded, loading every task - full JSON artifacts and history
        included - that the context has ever had.
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
