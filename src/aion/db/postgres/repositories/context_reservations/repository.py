"""Context reservation repository implementation."""

from __future__ import annotations

from typing import Optional, Type

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.constants import ADK_LEGACY_USER_ID, ADK_SCHEMA, LANGGRAPH_SCHEMA
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

_LANGGRAPH_TABLES = tuple(
    f"{LANGGRAPH_SCHEMA}.{table}" for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
)
_ADK_SESSIONS = f"{ADK_SCHEMA}.sessions"


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

    async def reserve(self, reservation: ContextReservationRecord) -> Optional[ContextReservationRecord]:
        """Write ``reservation`` unless the context already has one, and return the one that holds it.

        A context nobody holds yet is refused - ``None``, and nothing written
        - when state saved before it carried an agent exists for it
        (``unattributable_state_exists``). Otherwise ``INSERT ... ON CONFLICT
        DO NOTHING`` waits for a concurrent insert of the same key to commit or
        roll back, so of two first requests exactly one writes the row, and
        the other reads it back. Call it inside a transaction and compare the
        result with what was asked for: anything else means another holder got
        there first.
        """
        held = await self._held(reservation.agent_id, reservation.context_id)
        if held is not None:
            return held
        if await self.unattributable_state_exists(reservation.context_id):
            return None
        values = reservation.model_dump(exclude={"created_at"})
        await self._session.execute(
            pg_insert(ContextReservationModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[ContextReservationModel.agent_id, ContextReservationModel.context_id]
            )
        )
        return await self._held(reservation.agent_id, reservation.context_id)

    async def unattributable_state_exists(self, context_id: str) -> bool:
        """Whether framework state saved before it carried an agent exists for ``context_id``.

        A LangGraph checkpoint whose ``thread_id`` is the context ID alone, or
        an ADK session saved under the shared legacy user: either may belong
        to any agent on this database and any of its callers, so the context
        cannot be given to anyone. Nothing written since can take that shape
        for a context nobody holds, so the answer cannot change under a
        concurrent first request. A framework whose tables are not in this
        database has none.
        """
        tables = await self._existing(_LANGGRAPH_TABLES + (_ADK_SESSIONS,))
        for table in tables:
            if table == _ADK_SESSIONS:
                query = f"SELECT 1 FROM {table} WHERE user_id = :legacy_user AND id = :context_id LIMIT 1"
            else:
                query = f"SELECT 1 FROM {table} WHERE thread_id = :context_id LIMIT 1"
            found = await self._session.execute(
                text(query), {"context_id": context_id, "legacy_user": ADK_LEGACY_USER_ID}
            )
            if found.first() is not None:
                return True
        return False

    async def _held(self, agent_id: str, context_id: str) -> Optional[ContextReservationRecord]:
        row = (
            await self._session.execute(
                select(*_COLUMNS).where(
                    ContextReservationModel.agent_id == agent_id,
                    ContextReservationModel.context_id == context_id,
                )
            )
        ).mappings().one_or_none()
        return ContextReservationRecord(**row) if row is not None else None

    async def _existing(self, tables: tuple[str, ...]) -> list[str]:
        found = await self._session.execute(
            text("SELECT name FROM unnest(CAST(:tables AS text[])) AS name WHERE to_regclass(name) IS NOT NULL"),
            {"tables": list(tables)},
        )
        return [name for (name,) in found]
