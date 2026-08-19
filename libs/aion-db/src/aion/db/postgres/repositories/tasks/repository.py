"""Task repository implementation."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import List, Type, Optional

from sqlalchemy import select, func, asc, desc, literal, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

try:
    from a2a.types import Artifact, TaskStatus
except Exception as exc:
    raise ImportError("The 'a2a-sdk' package is required to use this repository") from exc

from aion.db.postgres.records import TaskRecord
from aion.db.postgres.repositories.base import BaseRepository
from aion.db.postgres.repositories.task_claims import TaskClaimsRepository
from aion.db.postgres.models import TaskRecordModel
from aion.db.postgres.types import Pagination, Sorting
from aion.db.postgres.repositories.tasks.selectors import latest_artifacts, artifacts_by_version, all_versions_by_name


STATUS_TIMESTAMP_SORT_KEY = "status.timestamp"
"""Reserved :class:`~aion.db.postgres.types.SortKey` column naming the task's
last state-change time.

The public name remains stable while the repository resolves it to the generated
``status_timestamp`` column. Rows with no timestamp sort last in both directions
— an absent timestamp means "unknown", not "oldest".
"""


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

    def _apply_filter(
            self,
            stmt,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            status_state: Optional[str] = None,
            status_timestamp_after: Optional[_dt.datetime] = None,
    ):
        """Apply task filter conditions to ``stmt`` and return the updated statement."""
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
        which resolves to the generated ``status_timestamp`` column. Missing
        timestamps are ordered last regardless of direction, so tasks whose
        state was never stamped never displace tasks that carry a real time.

        Args:
            stmt: Statement to order.
            sorting: Sort keys applied left-to-right.

        Returns:
            The statement with ORDER BY clauses appended.
        """
        for key in sorting.keys:
            if key.column == STATUS_TIMESTAMP_SORT_KEY:
                column = self.model_class.status_timestamp
                ordering = desc(column) if key.descending else asc(column)
                stmt = stmt.order_by(ordering.nullslast())
                continue
            column = getattr(self.model_class, key.column)
            stmt = stmt.order_by(desc(column) if key.descending else asc(column))
        return stmt

    async def find_ids(
            self,
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

    async def save(self, entity: TaskRecord) -> None:
        """Insert or update a task atomically by its identifier.

        ``created_at`` is deliberately absent from both the insert values and
        the conflict update. The database owns it and an entity constructed
        from an incoming A2A task must never be able to reset it.
        """
        stmt = insert(self.model_class).values(
            id=entity.id,
            context_id=entity.context_id,
            status=entity.status,
            artifacts=entity.artifacts,
            history=entity.history,
            task_metadata=entity.task_metadata,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[self.model_class.id],
            set_={
                "context_id": stmt.excluded.context_id,
                "status": stmt.excluded.status,
                "artifacts": stmt.excluded.artifacts,
                "history": stmt.excluded.history,
                "metadata": stmt.excluded.metadata,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def save_owned(self, entity: TaskRecord, owner_token: uuid.UUID) -> bool:
        """Fenced upsert a task only while ``owner_token`` is current.

        The ownership predicate is the source relation of the upsert itself.
        Consequently an absent or replaced claim produces zero rows in both the
        insert and conflict-update paths; callers can treat ``False`` as a
        definitive ownership loss without a check-then-write race.

        Args:
            entity: Complete task snapshot to persist.
            owner_token: Process-local claim token, passed only to SQL.

        Returns:
            ``True`` when the row was inserted or updated, ``False`` when the
            claim predicate produced no source row.
        """
        # Normal writes and claim acquisition both take the task row first.  A
        # new task has no row yet, so the SELECT is intentionally allowed to
        # return zero rows; the claim predicate below remains authoritative.
        await self._session.execute(
            select(self.model_class.id)
            .where(self.model_class.id == entity.id)
            .with_for_update()
        )

        # The claim check is the source relation of the insert: a row is
        # produced to insert only while ``owned`` yields one. Literals carry
        # the model's own column types so ProtobufType serializes them the
        # same way a plain ``.values()`` insert would.
        owned = TaskClaimsRepository.owned_cte(entity.id, owner_token)
        source = select(
            literal(entity.id, type_=self.model_class.id.type),
            literal(entity.context_id, type_=self.model_class.context_id.type),
            literal(entity.status, type_=self.model_class.status.type),
            literal(entity.artifacts, type_=self.model_class.artifacts.type),
            literal(entity.history, type_=self.model_class.history.type),
            literal(entity.task_metadata, type_=self.model_class.task_metadata.type),
        ).select_from(owned)

        insert_stmt = insert(self.model_class).from_select(
            ["id", "context_id", "status", "artifacts", "history", "metadata"],
            source,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[self.model_class.id],
            set_={
                "context_id": insert_stmt.excluded.context_id,
                "status": insert_stmt.excluded.status,
                "artifacts": insert_stmt.excluded.artifacts,
                "history": insert_stmt.excluded.history,
                "metadata": insert_stmt.excluded.metadata,
                "updated_at": func.clock_timestamp(),
            },
        ).returning(self.model_class.id)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.first() is not None

    async def find_by_id_for_update(self, task_id: uuid.UUID) -> Optional[TaskRecord]:
        """Find and lock a task row until the surrounding transaction ends.

        Callers use this as the stable mutex for operations that coordinate a
        task row with its optional ownership claim.  The repository does not
        open or commit a transaction; the caller must keep the same session
        and transaction for every dependent write.
        """
        stmt = (
            select(self.model_class)
            .where(self.model_class.id == task_id)
            .with_for_update()
        )
        return await self._execute_and_convert(stmt)

    async def update_status(self, task_id: uuid.UUID, status: TaskStatus) -> None:
        """Update only a task's status inside the caller's transaction.

        A targeted update avoids replacing artifacts, history, or metadata
        from a stale full-task snapshot.  ``ProtobufType`` on the ORM model
        performs the JSONB serialization.

        Nothing is reported back about how many rows changed: the caller holds
        the row lock from :meth:`find_by_id_for_update`, so the row it just
        read cannot disappear before this statement runs.
        """
        stmt = (
            update(self.model_class)
            .where(self.model_class.id == task_id)
            .values(status=status, updated_at=func.clock_timestamp())
        )
        await self._session.execute(stmt)

    async def find(
            self,
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

    async def find_unique_context_ids(
            self,
            pagination: Optional[Pagination] = None,
    ) -> List[str]:
        """Find all unique context_id values ordered by latest task creation."""
        stmt = (
            select(self.model_class.context_id)
            .group_by(self.model_class.context_id)
            .order_by(desc(func.max(self.model_class.created_at)))
        )

        if pagination is not None:
            stmt = self._apply_pagination(stmt, pagination)

        result = await self._session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    async def find_artifacts(
            self,
            task_id: Optional[str] = None,
            context_id: Optional[str] = None,
            artifact_name: Optional[str] = None,
            artifact_version: Optional[str] = None,
    ) -> List[Artifact]:
        """Find artifacts matching the given criteria.

        Either ``task_id`` or ``context_id`` must be provided to scope the search.

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
            select(self.model_class.artifacts)
            .where(self.model_class.artifacts.isnot(None))
            .order_by(desc(self.model_class.created_at))
        )
        stmt = self._apply_filter(stmt, task_id=task_id, context_id=context_id)

        if artifact_name is not None:
            stmt = stmt.where(self.model_class.artifacts.contains([{"name": artifact_name}]))
        if effective_version is not None:
            stmt = stmt.where(self.model_class.artifacts.contains([{"metadata": {"version": effective_version}}]))

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        if effective_version is not None:
            return artifacts_by_version(rows, effective_version, artifact_name)

        if artifact_name is not None and not want_latest:
            return all_versions_by_name(rows, artifact_name)

        return latest_artifacts(rows, artifact_name)
