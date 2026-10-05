"""Context reservation repository implementation."""

from __future__ import annotations

from typing import Optional, Type

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aion.db.postgres.constants import ADK_LEGACY_USER_ID, ADK_SCHEMA, LANGGRAPH_SCHEMA
from aion.db.postgres.models import ContextBindingModel, ContextReservationModel
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
    ContextReservationModel.state,
    ContextReservationModel.deletion_operation_id,
    ContextReservationModel.deletion_requested_by,
    ContextReservationModel.deleted_at,
)

ACTIVE = "active"
DELETING = "deleting"
DELETED = "deleted"

BLOCKED = "blocked"

_LANGGRAPH_TABLES = tuple(
    f"{LANGGRAPH_SCHEMA}.{table}" for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
)
_ADK_SESSIONS = f"{ADK_SCHEMA}.sessions"


class ContextReservationsRepository(BaseRepository[ContextReservationModel, ContextReservationRecord]):
    """Repository for ``context_reservations`` and the ``context_bindings`` that hang off them.

    A reservation is written by the first request into a context and changes
    only through the deletion lifecycle - see :class:`ContextReservationModel`.
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
        there first, and a ``deleting`` state means no work is admitted.

        An existing row is read under ``FOR UPDATE``, so admission and the
        start of a deletion of the same context are serialized. A ``deleted``
        context left nothing behind - no tasks, bindings or framework state -
        so it is reserved afresh, for whoever asks first. A ``blocked`` one is
        the exception: it has no holder its framework state could be deleted
        under, so that state may still exist and the context stays closed.
        """
        held = await self._held(reservation.agent_id, reservation.context_id, for_update=True)
        if held is not None and held.state == DELETED and held.kind != BLOCKED:
            await self._session.execute(
                update(ContextReservationModel)
                .where(
                    ContextReservationModel.agent_id == reservation.agent_id,
                    ContextReservationModel.context_id == reservation.context_id,
                )
                .values(
                    kind=reservation.kind,
                    owner_scope=reservation.owner_scope,
                    owner_agent_identity_id=reservation.owner_agent_identity_id,
                    edge_agent_environment_id=reservation.edge_agent_environment_id,
                    created_at=func.clock_timestamp(),
                    state=ACTIVE,
                    deletion_operation_id=None,
                    deletion_requested_by=None,
                    deleted_at=None,
                )
            )
            return await self._held(reservation.agent_id, reservation.context_id)
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

    async def admits_new_task(self, agent_id: str, context_id: str) -> bool:
        """Whether a new task may be created in the context, holding its reservation until the transaction ends.

        A context without a reservation was never admitted into - a task
        written by Python code that holds the store - and is not fenced. A
        reserved one takes new tasks only while ``active``. ``FOR SHARE``
        lets concurrent task writes through together while making the end of
        a deletion, which locks the row ``FOR UPDATE``, wait for them - or
        them for it.
        """
        state = await self._session.scalar(
            select(ContextReservationModel.state)
            .where(
                ContextReservationModel.agent_id == agent_id,
                ContextReservationModel.context_id == context_id,
            )
            .with_for_update(read=True)
        )
        return state is None or state == ACTIVE

    async def bind(self, agent_id: str, context_id: str, owner_scope: str) -> None:
        """Bind a caller to a context it was admitted into; a repeated binding is kept as it is."""
        await self._session.execute(
            pg_insert(ContextBindingModel)
            .values(agent_id=agent_id, context_id=context_id, owner_scope=owner_scope)
            .on_conflict_do_nothing()
        )

    async def unbind(self, agent_id: str, context_id: str, owner_scope: str) -> bool:
        """Remove a caller's binding; whether there was one."""
        result = await self._session.execute(
            delete(ContextBindingModel).where(
                ContextBindingModel.agent_id == agent_id,
                ContextBindingModel.context_id == context_id,
                ContextBindingModel.owner_scope == owner_scope,
            )
        )
        return bool(result.rowcount)

    async def is_bound(self, agent_id: str, context_id: str, owner_scope: str) -> bool:
        """Whether the caller has a binding to the context."""
        found = await self._session.execute(
            select(ContextBindingModel.owner_scope).where(
                ContextBindingModel.agent_id == agent_id,
                ContextBindingModel.context_id == context_id,
                ContextBindingModel.owner_scope == owner_scope,
            )
        )
        return found.first() is not None

    async def has_bindings(self, agent_id: str, context_id: str) -> bool:
        """Whether any caller is still bound to the context."""
        found = await self._session.execute(
            select(ContextBindingModel.owner_scope)
            .where(
                ContextBindingModel.agent_id == agent_id,
                ContextBindingModel.context_id == context_id,
            )
            .limit(1)
        )
        return found.first() is not None

    async def held_for_update(self, agent_id: str, context_id: str) -> Optional[ContextReservationRecord]:
        """The context's reservation, locked until the transaction ends."""
        return await self._held(agent_id, context_id, for_update=True)

    async def mark_deleting(
        self, agent_id: str, context_id: str, operation_id: str, requested_by: str
    ) -> None:
        """Fence the context: from now on nothing new is admitted into it."""
        await self._set_state(
            agent_id,
            context_id,
            state=DELETING,
            deletion_operation_id=operation_id,
            deletion_requested_by=requested_by,
        )

    async def mark_active(self, agent_id: str, context_id: str) -> None:
        """Lift the fence of a deletion that was refused."""
        await self._set_state(
            agent_id,
            context_id,
            state=ACTIVE,
            deletion_operation_id=None,
            deletion_requested_by=None,
        )

    async def mark_deleted(self, agent_id: str, context_id: str) -> None:
        """Record that nothing of the context remains; its bindings go with it."""
        await self._session.execute(
            delete(ContextBindingModel).where(
                ContextBindingModel.agent_id == agent_id,
                ContextBindingModel.context_id == context_id,
            )
        )
        await self._set_state(agent_id, context_id, state=DELETED, deleted_at=func.clock_timestamp())

    async def _set_state(self, agent_id: str, context_id: str, **values) -> None:
        await self._session.execute(
            update(ContextReservationModel)
            .where(
                ContextReservationModel.agent_id == agent_id,
                ContextReservationModel.context_id == context_id,
            )
            .values(**values)
        )

    async def _held(
        self, agent_id: str, context_id: str, *, for_update: bool = False
    ) -> Optional[ContextReservationRecord]:
        stmt = select(*_COLUMNS).where(
            ContextReservationModel.agent_id == agent_id,
            ContextReservationModel.context_id == context_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        return ContextReservationRecord(**row) if row is not None else None

    async def _existing(self, tables: tuple[str, ...]) -> list[str]:
        found = await self._session.execute(
            text("SELECT name FROM unnest(CAST(:tables AS text[])) AS name WHERE to_regclass(name) IS NOT NULL"),
            {"tables": list(tables)},
        )
        return [name for (name,) in found]
