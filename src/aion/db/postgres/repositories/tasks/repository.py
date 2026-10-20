"""Task repository implementation."""

from __future__ import annotations

import datetime as _dt
import hashlib
import uuid
from typing import Iterable, List, NamedTuple, Type, Optional

from sqlalchemy import select, func, asc, desc, delete, and_, or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from a2a.types import Artifact, TaskStatus

from aion.db.postgres.constants import ABANDONED_SWEEP_LOCK_CLASS
from aion.db.postgres.records import TaskRecord, resolve_status_timestamp
from aion.db.postgres.repositories.base import BaseRepository
from aion.db.postgres.models import TaskArtifactModel, TaskRecordModel
from aion.db.postgres.types import Pagination, Sorting
from aion.db.postgres.repositories.tasks.selectors import latest_artifacts, artifacts_by_version, all_versions_by_name


STATUS_TIMESTAMP_SORT_KEY = "status.timestamp"
"""Reserved :class:`~aion.db.postgres.types.SortKey` column naming the task's
last state-change time.

The public name remains stable while the repository resolves it to the typed
``status_timestamp`` column. The column is ``NOT NULL``: every row has a real
timestamp, either from its own ``TaskStatus`` or stamped by the application
when one was missing.
"""


class StoredArtifact(NamedTuple):
    """One artifact row of a task, with the task it belongs to.

    Attributes:
        task_id: The task that holds the artifact.
        artifact: The artifact as it was last written.
        updated_at: When the artifact's payload was last replaced.
    """

    task_id: uuid.UUID
    artifact: Artifact
    updated_at: _dt.datetime


class TasksRepository(BaseRepository[TaskRecordModel, TaskRecord]):
    """Repository for Task operations using entities."""

    def __init__(self, session: AsyncSession):
        """Initialize the repository with an active SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session used for all database operations.
        """
        super().__init__(session)

    @property
    def model_class(self) -> Type[TaskRecordModel]:
        """SQLAlchemy ORM model for the tasks table."""
        return TaskRecordModel

    @property
    def entity_class(self) -> Type[TaskRecord]:
        """Pydantic domain entity used as the public return type."""
        return TaskRecord

    async def find_by_id(
            self, id: uuid.UUID, agent_id: str, owner_scope: Optional[str] = None
    ) -> Optional[TaskRecord]:
        """Find a task by id, scoped to ``agent_id`` and, when given, its owner.

        Overrides :meth:`BaseRepository.find_by_id` with a mandatory
        ``agent_id``: unlike the child tables, ``tasks`` is read directly by
        id from outside any already-scoped query, so the scoping has to live
        here rather than be assumed from context. ``owner_scope`` narrows the
        read to one caller's tasks; ``None`` reads regardless of owner, which
        is what the server's own paths - reconciliation, settlement - need.
        """
        stmt = select(self.model_class).where(
            self.model_class.id == id, self.model_class.agent_id == agent_id
        )
        if owner_scope is not None:
            stmt = stmt.where(self.model_class.owner_scope == owner_scope)
        return await self._execute_and_convert(stmt)

    async def delete_by_id(
            self, id: uuid.UUID, agent_id: str, owner_scope: Optional[str] = None
    ) -> bool:
        """Delete a task by id, scoped to ``agent_id`` and, when given, its owner.

        Overrides :meth:`BaseRepository.delete_by_id` for the same reason as
        :meth:`find_by_id`.
        """
        stmt = delete(self.model_class).where(
            self.model_class.id == id, self.model_class.agent_id == agent_id
        )
        if owner_scope is not None:
            stmt = stmt.where(self.model_class.owner_scope == owner_scope)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    def _apply_filter(
            self,
            stmt,
            agent_id: str,
            owner_scope: Optional[str] = None,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
    ):
        """Apply task filter conditions to ``stmt`` and return the updated statement.

        ``agent_id`` is required, not optional like the rest: every caller
        must scope its query to one agent, so there is no filterless case to
        support the way there is for ``task_id`` or ``context_id``.
        """
        stmt = stmt.where(self.model_class.agent_id == agent_id)
        if owner_scope is not None:
            stmt = stmt.where(self.model_class.owner_scope == owner_scope)
        if task_id is not None:
            stmt = stmt.where(self.model_class.id == task_id)
        if context_id is not None:
            stmt = stmt.where(self.model_class.context_id == context_id)
        if status_state is not None:
            stmt = stmt.where(self.model_class.state == status_state)
        if status_timestamp_after is not None:
            stmt = stmt.where(self.model_class.status_timestamp >= status_timestamp_after)
        return stmt

    def _apply_sorting(self, stmt: Select, sorting: Sorting) -> Select:
        """Apply ORDER BY clauses, resolving the task-specific status timestamp.

        Extends the base implementation with :data:`STATUS_TIMESTAMP_SORT_KEY`,
        which resolves to the typed ``status_timestamp`` column.

        Args:
            stmt: Statement to order.
            sorting: Sort keys applied left-to-right.

        Returns:
            The statement with ORDER BY clauses appended.
        """
        for key in sorting.keys:
            if key.column == STATUS_TIMESTAMP_SORT_KEY:
                column = self.model_class.status_timestamp
                stmt = stmt.order_by(desc(column) if key.descending else asc(column))
                continue
            column = getattr(self.model_class, key.column)
            stmt = stmt.order_by(desc(column) if key.descending else asc(column))
        return stmt

    async def find_ids(
            self,
            agent_id: str,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
            pagination: Optional[Pagination] = None,
            sorting: Optional[Sorting] = None,
    ) -> List[str]:
        """Find the ids of matching tasks, in the requested order.

        The identifier-only counterpart of :meth:`find`, for callers that need
        the shape of a result set — its size, or the position of one task
        within it — without paying to load and deserialize every task's JSON
        payload. Accepts the same filters, ordering and pagination.

        Args:
            agent_id: Restrict to this agent's tasks.
            task_id: Restrict to a single task id.
            context_id: Restrict to one context.
            status_state: Restrict to tasks in this state.
            status_timestamp_after: Restrict to tasks stamped at or after this
                timezone-aware datetime.
            pagination: Offset/limit window over the ordered result.
            sorting: Sort keys applied left-to-right.

        Returns:
            Matching task ids as strings, in the requested order.
        """
        stmt = select(self.model_class.id)
        stmt = self._apply_filter(
            stmt,
            agent_id=agent_id,
            task_id=task_id,
            context_id=context_id,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )

        if sorting is not None:
            stmt = self._apply_sorting(stmt, sorting)
        if pagination is not None:
            stmt = self._apply_pagination(stmt, pagination)

        result = await self._session.execute(stmt)
        return [str(row[0]) for row in result.fetchall()]

    async def count(
            self,
            agent_id: str,
            owner_scope: Optional[str] = None,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
    ) -> int:
        """Count matching tasks without loading a single row.

        The result set's size for a caller that only wants ``total_size`` -
        keeping it a single aggregate query is what makes that number
        affordable on a large table, unlike materializing every matching id.
        """
        stmt = select(func.count(self.model_class.id))
        stmt = self._apply_filter(
            stmt,
            agent_id=agent_id,
            owner_scope=owner_scope,
            task_id=task_id,
            context_id=context_id,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def find_page(
            self,
            *,
            agent_id: str,
            owner_scope: Optional[str] = None,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
            after: Optional[tuple[_dt.datetime, str]] = None,
            limit: int,
    ) -> List[TaskRecord]:
        """Return up to ``limit`` tasks ordered by status_timestamp desc, id desc.

        This is the keyset counterpart of ``find`` with an offset
        :class:`Pagination`: instead of asking the database to skip
        ``offset`` rows of a full sort, ``after`` restricts the scan to rows
        strictly past a known position in the same order, so each page costs
        the same regardless of how deep into the result set it is.

        Args:
            agent_id: Restrict to this agent's tasks.
            owner_scope: Restrict to one caller's tasks; ``None`` lists every
                owner's, for the server's own paths.
            after: The ``(status_timestamp, id)`` of the last row already
                returned, decoded straight from the caller's page token.
                ``None`` starts from the first page.
            limit: Maximum rows to return.

        Returns:
            Matching tasks in stable order, at most ``limit`` of them.
        """
        stmt = select(self.model_class)
        stmt = self._apply_filter(
            stmt,
            agent_id=agent_id,
            owner_scope=owner_scope,
            task_id=task_id,
            context_id=context_id,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )

        if after is not None:
            cursor_ts, cursor_id = after
            ts_col = self.model_class.status_timestamp
            id_col = self.model_class.id
            stmt = stmt.where(
                or_(
                    ts_col < cursor_ts,
                    and_(ts_col == cursor_ts, id_col < cursor_id),
                )
            )

        stmt = stmt.order_by(
            desc(self.model_class.status_timestamp),
            desc(self.model_class.id),
        ).limit(limit)

        return await self._execute_and_convert_many(stmt)

    async def save(self, entity: TaskRecord) -> None:
        """Insert or update a task atomically by its identifier.

        ``created_at`` is deliberately absent from both the insert values and
        the conflict update. The database owns it and an entity constructed
        from an incoming A2A task must never be able to reset it. Callers lock
        the row first (:meth:`lock_owner_scope`) and decide who may write;
        this statement only writes.
        """
        stmt = insert(self.model_class).values(
            id=entity.id,
            agent_id=entity.agent_id,
            owner_scope=entity.owner_scope,
            context_id=entity.context_id,
            status=entity.status,
            status_timestamp=entity.status_timestamp,
            task_metadata=entity.task_metadata,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[self.model_class.id],
            set_={
                "context_id": stmt.excluded.context_id,
                "status": stmt.excluded.status,
                "status_timestamp": stmt.excluded.status_timestamp,
                "metadata": stmt.excluded.metadata,
                "updated_at": func.clock_timestamp(),
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def lock_owner_scope(self, task_id: uuid.UUID) -> Optional[str]:
        """Lock the task row and return its recorded owner, or None if there is no row.

        A caller that goes on to :meth:`save` pays for one lock and learns
        whose task it is writing - a task's owner is fixed by its first write,
        and the upsert never changes it.
        """
        result = await self._session.execute(
            select(self.model_class.owner_scope)
            .where(self.model_class.id == task_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def find_by_id_for_update(
            self,
            task_id: uuid.UUID,
            agent_id: str,
            *,
            owner_scope: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Find and lock a task row until the surrounding transaction ends.

        The repository does not open or commit a transaction; the caller must
        keep the same session and transaction for every dependent write.

        Args:
            task_id: The task to lock.
            agent_id: Restrict to this agent's tasks - a task id that exists
                but belongs to a different agent is reported as not found,
                the same as one that does not exist at all.
            owner_scope: Restrict to one caller's tasks - another user's task
                is reported as not found and is not locked. ``None`` for the
                server's own paths, which act for no user.
        """
        conditions = [self.model_class.id == task_id, self.model_class.agent_id == agent_id]
        if owner_scope is not None:
            conditions.append(self.model_class.owner_scope == owner_scope)
        stmt = select(self.model_class).where(*conditions).with_for_update()
        return await self._execute_and_convert(stmt)

    async def find(
            self,
            agent_id: str,
            owner_scope: Optional[str] = None,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
            pagination: Optional[Pagination] = None,
            sorting: Optional[Sorting] = None,
    ) -> List[TaskRecord]:
        """Find tasks matching the given filter."""
        stmt = select(self.model_class)
        stmt = self._apply_filter(
            stmt,
            agent_id=agent_id,
            owner_scope=owner_scope,
            task_id=task_id,
            context_id=context_id,
            status_state=status_state,
            status_timestamp_after=status_timestamp_after,
        )

        if sorting is not None:
            stmt = self._apply_sorting(stmt, sorting)
        if pagination is not None:
            stmt = self._apply_pagination(stmt, pagination)

        return await self._execute_and_convert_many(stmt)

    async def find_artifacts(
            self,
            agent_id: str,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            artifact_name: Optional[str] = None,
            artifact_version: Optional[str] = None,
            owner_scope: Optional[str] = None,
            artifact_id: Optional[str] = None,
    ) -> List[Artifact]:
        """Find artifacts matching the given criteria.

        Either ``task_id`` or ``context_id`` must be provided to scope the search.
        ``owner_scope`` narrows it to one user's tasks - a ``context_id`` is
        chosen by clients, and two users may share one. ``artifact_id`` keeps
        only the artifacts with that id, before the matrix below applies.

        Behaviour matrix:
        - name=None,  version=None   > latest version of each artifact (by task creation order)
        - name=given, version=None   > all versions of the named artifact
        - name=None,  version="-1"   > latest version of each artifact (explicit sentinel)
        - name=given, version="-1"   > latest version of the named artifact
        - name=None,  version=given  > all artifacts matching that version
        - name=given, version=given  > all artifacts matching both name and version
        """
        if task_id is None and context_id is None:
            raise ValueError("Either 'task_id' or 'context_id' must be provided.")

        want_latest = artifact_version == "-1"
        effective_version = None if want_latest else artifact_version

        stmt = (
            select(self.model_class.id, TaskArtifactModel.payload)
            .select_from(self.model_class)
            .join(TaskArtifactModel, TaskArtifactModel.task_id == self.model_class.id)
            .order_by(desc(self.model_class.created_at), self.model_class.id)
        )
        stmt = self._apply_filter(
            stmt,
            agent_id=agent_id,
            owner_scope=owner_scope,
            task_id=task_id,
            context_id=context_id,
        )

        if artifact_id is not None:
            stmt = stmt.where(TaskArtifactModel.artifact_id == artifact_id)
        if artifact_name is not None:
            stmt = stmt.where(TaskArtifactModel.payload["name"].astext == artifact_name)
        if effective_version is not None:
            stmt = stmt.where(
                TaskArtifactModel.payload["metadata"]["version"].astext == effective_version
            )

        result = await self._session.execute(stmt)

        # Regrouped by task, one list per task in the same newest-task-first
        # order the query already produced - the shape latest_artifacts() and
        # friends below expect, since they were written against one row per
        # task holding that task's whole artifact list.
        grouped: dict[uuid.UUID, list[Artifact]] = {}
        order: list[uuid.UUID] = []
        for row_task_id, payload in result.all():
            if row_task_id not in grouped:
                grouped[row_task_id] = []
                order.append(row_task_id)
            grouped[row_task_id].append(payload)
        rows = [grouped[tid] for tid in order]

        if artifact_name is not None and want_latest:
            # `rows` is already newest-task-first; latest_artifacts() only
            # ever looks at the first occurrence, so nothing past the first
            # task's group could change the answer.
            rows = rows[:1]

        if effective_version is not None:
            return artifacts_by_version(rows, effective_version, artifact_name)

        if artifact_name is not None and not want_latest:
            return all_versions_by_name(rows, artifact_name)

        return latest_artifacts(rows, artifact_name)

    async def find_artifact_versions(
            self, agent_id: str, context_id: str, artifact_id: str
    ) -> List[StoredArtifact]:
        """Every artifact with ``artifact_id`` among the tasks of a context, newest task first.

        An artifact id repeated in several tasks of a context is one artifact
        in several versions. The search is not narrowed to an owner: who may
        see the context's tasks is decided before it is made (context
        admission).

        Args:
            agent_id: Restrict to this agent's tasks.
            context_id: The context to search.
            artifact_id: The artifact's ``artifact_id``.

        Returns:
            One row per task holding the artifact, newest task first.
        """
        stmt = (
            select(self.model_class.id, TaskArtifactModel.payload, TaskArtifactModel.updated_at)
            .select_from(self.model_class)
            .join(TaskArtifactModel, TaskArtifactModel.task_id == self.model_class.id)
            .where(TaskArtifactModel.artifact_id == artifact_id)
            .order_by(desc(self.model_class.created_at), desc(self.model_class.id))
        )
        stmt = self._apply_filter(stmt, agent_id=agent_id, context_id=context_id)
        result = await self._session.execute(stmt)
        return [StoredArtifact(task_id, payload, updated_at) for task_id, payload, updated_at in result.all()]

    async def find_latest_tagged(
            self,
            agent_id: str,
            context_id: str,
            metadata_key: str,
            metadata_value: Optional[str],
            owner_scope: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """The newest task of a context whose ``metadata[metadata_key]`` is ``metadata_value``.

        Args:
            agent_id: Restrict to this agent's tasks.
            context_id: The context to search.
            metadata_key: A top-level key of the task's metadata, read as text.
            metadata_value: The value it must hold; None matches a task whose
                metadata lacks the key.
            owner_scope: Restrict to one owner's tasks; None searches every
                owner's.

        Returns:
            The newest matching task by ``created_at``, or None.
        """
        tag = self.model_class.task_metadata[metadata_key].astext
        stmt = select(self.model_class).where(tag.is_(None) if metadata_value is None else tag == metadata_value)
        stmt = self._apply_filter(stmt, agent_id=agent_id, owner_scope=owner_scope, context_id=context_id)
        stmt = stmt.order_by(desc(self.model_class.created_at), desc(self.model_class.id)).limit(1)
        return await self._execute_and_convert(stmt)

    async def find_idle_ids(
            self,
            agent_id: str,
            states: Iterable[str],
            idle_seconds: float,
            task_id: Optional[uuid.UUID] = None,
    ) -> list[uuid.UUID]:
        """The ids of the agent's tasks in ``states`` whose last write is older than ``idle_seconds``.

        Every owner's tasks are searched. The age is measured on the
        database's clock, which stamped ``updated_at``, so the servers' own
        clocks do not matter.

        Args:
            agent_id: Restrict to this agent's tasks.
            states: States, by their protobuf name, a task must be in.
            idle_seconds: How long ago the last write must be.
            task_id: Check this one task only.

        Returns:
            The matching ids, the longest idle first.
        """
        stmt = select(self.model_class.id).where(
            self.model_class.agent_id == agent_id,
            self.model_class.state.in_(list(states)),
            self.model_class.updated_at < func.now() - _dt.timedelta(seconds=idle_seconds),
        )
        if task_id is not None:
            stmt = stmt.where(self.model_class.id == task_id)
        stmt = stmt.order_by(asc(self.model_class.updated_at))
        return list((await self._session.execute(stmt)).scalars().all())

    async def try_lock_abandoned_sweep(self, agent_id: str) -> bool:
        """Try to take the agent's abandoned-task sweep lock, without waiting.

        The lock is a transaction-level advisory lock: it lives as long as
        this repository's session holds its transaction, and ends with the
        commit, the rollback, the session's close or the lost connection. Its
        keys are a fixed class and a stable hash of ``agent_id``, so each
        agent of a shared database has its own lock.

        Args:
            agent_id: The agent whose sweep is locked.

        Returns:
            True when the lock is held now; False when another session holds it.
        """
        agent_key = int.from_bytes(hashlib.sha256(agent_id.encode()).digest()[:4], "big", signed=True)
        acquired = await self._session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_class, :agent_key)"),
            {"lock_class": ABANDONED_SWEEP_LOCK_CLASS, "agent_key": agent_key},
        )
        return bool(acquired)

    async def find_context_task_ids(
            self,
            agent_id: str,
            context_id: str,
            excluded_states: Iterable[str] = (),
    ) -> List[str]:
        """The ids of every owner's tasks of a context, oldest first.

        Every owner's tasks are listed: who may read the context is decided
        before the search is made (context admission).

        Args:
            agent_id: Restrict to this agent's tasks.
            context_id: The context to list.
            excluded_states: States, by their protobuf name, a listed task
                must not be in.

        Returns:
            The task ids as strings, by ``created_at``.
        """
        stmt = select(self.model_class.id)
        excluded = list(excluded_states)
        if excluded:
            stmt = stmt.where(self.model_class.state.not_in(excluded))
        stmt = self._apply_filter(stmt, agent_id=agent_id, context_id=context_id)
        stmt = stmt.order_by(asc(self.model_class.created_at), asc(self.model_class.id))
        return [str(task_id) for task_id in (await self._session.execute(stmt)).scalars().all()]
