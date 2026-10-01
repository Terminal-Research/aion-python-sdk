"""Context reservation repository implementation."""

from __future__ import annotations

from typing import Type

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.models import ContextReservationModel
from aion.db.postgres.records import ContextReservationRecord
from aion.db.postgres.repositories.base import BaseRepository

_COLUMNS = (
    ContextReservationModel.agent_id,
    ContextReservationModel.context_id,
    ContextReservationModel.kind,
    ContextReservationModel.owner_scope,
    ContextReservationModel.owner_agent_identity_id,
    ContextReservationModel.edge_agent_environment_id,
    ContextReservationModel.created_at,
)


class ContextReservationsRepository(BaseRepository[ContextReservationModel, ContextReservationRecord]):
    """Repository for ``context_reservations``: insert once, then only read.

    The table is keyed by ``(agent_id, context_id)`` rather than ``id``, so the
    base class's id-keyed helpers do not apply.
    """

    @property
    def model_class(self) -> Type[ContextReservationModel]:
        return ContextReservationModel

    @property
    def entity_class(self) -> Type[ContextReservationRecord]:
        return ContextReservationRecord

    async def reserve(self, reservation: ContextReservationRecord) -> ContextReservationRecord:
        """Write ``reservation`` unless the context already has one, and return the one that holds it.

        ``INSERT ... ON CONFLICT DO NOTHING`` waits for a concurrent insert of
        the same key to commit or roll back, so of two first requests exactly
        one writes the row, and the other reads it back. Call it inside a
        transaction and compare the result with what was asked for: anything
        else means another holder got there first.
        """
        values = reservation.model_dump(exclude={"created_at"})
        await self._session.execute(
            pg_insert(ContextReservationModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[ContextReservationModel.agent_id, ContextReservationModel.context_id]
            )
        )
        row = (
            await self._session.execute(
                select(*_COLUMNS).where(
                    ContextReservationModel.agent_id == reservation.agent_id,
                    ContextReservationModel.context_id == reservation.context_id,
                )
            )
        ).mappings().one()
        return ContextReservationRecord(**row)
