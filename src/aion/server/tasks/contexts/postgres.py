"""The context catalog on Postgres: ``context_reservations``, ``context_bindings`` and the task tables."""

from __future__ import annotations

import uuid
from typing import Optional

from a2a.types import TaskStatus
from sqlalchemy import and_, delete, desc, func, literal_column, or_, select, text

from aion.core.a2a import A2AMetadataKey, MessageType
from aion.db.postgres import DbManager
from aion.db.postgres.models import (
    ContextBindingModel,
    ContextReservationModel,
    TaskArtifactModel,
    TaskClaimModel,
    TaskMessageModel,
    TaskRecordModel,
)
from aion.db.postgres.records import ContextReservationRecord
from aion.db.postgres.repositories import ContextReservationsRepository
from aion.server.a2a.constants import TERMINAL_TASK_STATES
from aion.server.tasks.admission import ACTIVE, DELETED, DELETING, holder_of_record

from .catalog import (
    ARTIFACT_WINDOW,
    EXCLUDED_ARTIFACT_IDS,
    ContextActivity,
    ContextPage,
    ContextTaskState,
    DeletionStep,
    DeletionTicket,
    FinishOutcome,
)

__all__ = ["PostgresContextCatalog"]

_PUSH_CONFIGS_TABLE = "push_notification_configs"

_MESSAGE_TYPE = literal_column(
    f"{TaskMessageModel.__tablename__}.payload -> 'metadata' ->> '{A2AMetadataKey.MESSAGE_TYPE.value}'"
)


class PostgresContextCatalog:
    """Context visibility, reads and deletion for one agent, on the database its task store uses.

    Every statement is scoped to ``agent_id``, so agents sharing a database
    never see each other's contexts. The deletion's last step - removing the
    tasks, their claims and push configurations, and the bindings - is one
    transaction that holds the reservation row, the same lock admission
    takes, so no request is admitted into the context halfway through.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def list_contexts(self, member: str, *, limit: int, offset: int) -> list[ContextActivity]:
        last_activity = func.coalesce(func.max(TaskRecordModel.updated_at), ContextBindingModel.created_at)
        stmt = (
            select(ContextBindingModel.context_id, last_activity.label("last_activity_at"))
            .join(
                ContextReservationModel,
                and_(
                    ContextReservationModel.agent_id == ContextBindingModel.agent_id,
                    ContextReservationModel.context_id == ContextBindingModel.context_id,
                    ContextReservationModel.state == ACTIVE,
                ),
            )
            .outerjoin(
                TaskRecordModel,
                and_(
                    TaskRecordModel.agent_id == ContextBindingModel.agent_id,
                    TaskRecordModel.context_id == ContextBindingModel.context_id,
                ),
            )
            .where(
                ContextBindingModel.agent_id == self._agent_id,
                ContextBindingModel.owner_scope == member,
            )
            .group_by(ContextBindingModel.context_id, ContextBindingModel.created_at)
            .order_by(desc("last_activity_at"), desc(ContextBindingModel.context_id))
            .limit(limit)
            .offset(offset)
        )
        async with self._db_manager.get_session() as session:
            rows = (await session.execute(stmt)).all()
        return [ContextActivity(context_id, last_activity_at) for context_id, last_activity_at in rows]

    async def read_context(
        self,
        context_id: str,
        member: str,
        *,
        history_length: int,
        history_offset: int,
    ) -> Optional[ContextPage]:
        in_context = and_(
            TaskRecordModel.agent_id == self._agent_id,
            TaskRecordModel.context_id == context_id,
        )
        async with self._db_manager.get_session() as session:
            async with session.begin():
                # One snapshot for the visibility check and every read below.
                await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                bound_at = await session.scalar(
                    select(ContextBindingModel.created_at)
                    .join(
                        ContextReservationModel,
                        and_(
                            ContextReservationModel.agent_id == ContextBindingModel.agent_id,
                            ContextReservationModel.context_id == ContextBindingModel.context_id,
                            ContextReservationModel.state == ACTIVE,
                        ),
                    )
                    .where(
                        ContextBindingModel.agent_id == self._agent_id,
                        ContextBindingModel.context_id == context_id,
                        ContextBindingModel.owner_scope == member,
                    )
                )
                if bound_at is None:
                    return None

                latest = (
                    await session.execute(
                        select(TaskRecordModel.status, TaskRecordModel.updated_at)
                        .where(in_context)
                        .order_by(desc(TaskRecordModel.updated_at), desc(TaskRecordModel.id))
                        .limit(1)
                    )
                ).first()

                newest_first = (
                    await session.execute(
                        select(TaskMessageModel.payload)
                        .join(TaskRecordModel, TaskRecordModel.id == TaskMessageModel.task_id)
                        .where(
                            in_context,
                            or_(_MESSAGE_TYPE.is_(None), _MESSAGE_TYPE == MessageType.MESSAGE.value),
                        )
                        .order_by(
                            desc(TaskRecordModel.created_at),
                            desc(TaskRecordModel.id),
                            desc(TaskMessageModel.seq),
                        )
                        .limit(history_length)
                        .offset(history_offset)
                    )
                ).scalars().all()

                artifacts = (
                    await session.execute(
                        select(TaskArtifactModel.task_id, TaskArtifactModel.payload)
                        .join(TaskRecordModel, TaskRecordModel.id == TaskArtifactModel.task_id)
                        .where(in_context, TaskArtifactModel.artifact_id.notin_(EXCLUDED_ARTIFACT_IDS))
                        .order_by(
                            desc(TaskArtifactModel.updated_at),
                            desc(TaskArtifactModel.task_id),
                            desc(TaskArtifactModel.artifact_id),
                        )
                        .limit(ARTIFACT_WINDOW)
                    )
                ).all()

        return ContextPage(
            context_id=context_id,
            history=list(reversed(newest_first)),
            artifacts=[(str(task_id), payload) for task_id, payload in artifacts],
            status=TaskStatus() if latest is None else latest.status,
            last_activity_at=bound_at if latest is None else latest.updated_at,
        )

    async def begin_deletion(self, context_id: str, member: str) -> DeletionTicket:
        async with self._db_manager.get_session() as session:
            async with session.begin():
                reservations = ContextReservationsRepository(session)
                held = await reservations.held_for_update(self._agent_id, context_id)
                if held is None or held.state == DELETED:
                    return DeletionTicket(DeletionStep.NOT_VISIBLE)
                if held.state == DELETING:
                    if held.deletion_requested_by != member:
                        return DeletionTicket(DeletionStep.NOT_VISIBLE)
                    return DeletionTicket(
                        DeletionStep.RESUMED, held.deletion_operation_id, holder_of_record(held)
                    )
                if not await reservations.unbind(self._agent_id, context_id, member):
                    return DeletionTicket(DeletionStep.NOT_VISIBLE)
                if await reservations.has_bindings(self._agent_id, context_id):
                    return DeletionTicket(DeletionStep.UNBOUND)
                operation_id = str(uuid.uuid4())
                await reservations.mark_deleting(self._agent_id, context_id, operation_id, member)
                return DeletionTicket(DeletionStep.STARTED, operation_id, holder_of_record(held))

    async def context_tasks(self, context_id: str) -> list[ContextTaskState]:
        async with self._db_manager.get_session() as session:
            rows = (
                await session.execute(
                    select(TaskRecordModel.id, TaskRecordModel.status).where(
                        TaskRecordModel.agent_id == self._agent_id,
                        TaskRecordModel.context_id == context_id,
                    )
                )
            ).all()
        return [ContextTaskState(str(task_id), status.state) for task_id, status in rows]

    async def finish_deletion(self, context_id: str, operation_id: str) -> tuple[FinishOutcome, list[str]]:
        async with self._db_manager.get_session() as session:
            async with session.begin():
                reservations = ContextReservationsRepository(session)
                held = await reservations.held_for_update(self._agent_id, context_id)
                if held is None or held.state != DELETING or held.deletion_operation_id != operation_id:
                    return FinishOutcome.SUPERSEDED, []

                rows = (
                    await session.execute(
                        select(TaskRecordModel.id, TaskRecordModel.status)
                        .where(
                            TaskRecordModel.agent_id == self._agent_id,
                            TaskRecordModel.context_id == context_id,
                        )
                        .with_for_update()
                    )
                ).all()
                if any(status.state not in TERMINAL_TASK_STATES for _, status in rows):
                    return FinishOutcome.NOT_SETTLED, []

                task_ids = [task_id for task_id, _ in rows]
                if task_ids:
                    # A claim row has no foreign key to its task. Removing it
                    # is what refuses a late write of a removed task: a
                    # fenced write needs a live claim to land.
                    await session.execute(delete(TaskClaimModel).where(TaskClaimModel.task_id.in_(task_ids)))
                    if await session.scalar(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": _PUSH_CONFIGS_TABLE}):
                        await session.execute(
                            text(f"DELETE FROM {_PUSH_CONFIGS_TABLE} WHERE task_id = ANY(:task_ids)"),
                            {"task_ids": [str(task_id) for task_id in task_ids]},
                        )
                    # Messages and artifacts cascade with their task.
                    await session.execute(delete(TaskRecordModel).where(TaskRecordModel.id.in_(task_ids)))
                await reservations.mark_deleted(self._agent_id, context_id)
                return FinishOutcome.FINISHED, [str(task_id) for task_id in task_ids]

    async def abandon_deletion(self, context_id: str, operation_id: str) -> None:
        async with self._db_manager.get_session() as session:
            async with session.begin():
                reservations = ContextReservationsRepository(session)
                held = await reservations.held_for_update(self._agent_id, context_id)
                if held is not None and held.state == DELETING and held.deletion_operation_id == operation_id:
                    await reservations.mark_active(self._agent_id, context_id)

    async def pending_deletions(self) -> list[tuple[str, DeletionTicket]]:
        async with self._db_manager.get_session() as session:
            rows = (
                await session.execute(
                    select(ContextReservationModel).where(
                        ContextReservationModel.agent_id == self._agent_id,
                        ContextReservationModel.state == DELETING,
                    )
                )
            ).scalars().all()
        return [
            (
                row.context_id,
                DeletionTicket(
                    DeletionStep.RESUMED,
                    row.deletion_operation_id,
                    holder_of_record(ContextReservationRecord.model_validate(row, from_attributes=True)),
                ),
            )
            for row in rows
        ]
