"""Who may use a context: the holder admitted into it first, and nobody else.

A context ID is chosen by the client, but the framework state keyed by it -
LangGraph checkpoints, ADK sessions - outlives every task in it. Filtering
tasks by owner does not stop a second caller from presenting the same ID and
running into that state. So every message is admitted into its context before
anything happens: the first request reserves the context for its holder, and
any later request from another holder is refused.

A holder is private or shared, never both. A verified Aion invocation holds a
**shared** context - the gateway conversation of one receiving agent identity
at one edge environment, whose participants share the agent's memory while
each still owns only their own tasks. Every other caller - an anonymous
session, the application's own user - holds a **private** context by its
owner scope.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver

from aion.db.postgres import DbManager
from aion.db.postgres.records import ContextReservationRecord
from aion.db.postgres.repositories import ContextReservationsRepository
from aion.server.auth import CredentialKind, verified_caller

logger = logging.getLogger(__name__)

__all__ = [
    "ContextAdmission",
    "ContextHolder",
    "InMemoryContextAdmission",
    "PostgresContextAdmission",
    "holder_of",
]


@dataclass(frozen=True)
class ContextHolder:
    """Who a context is reserved for.

    ``blocked`` is no caller: a context that already had data when
    reservations were introduced is reserved for nobody, so it is never
    admitted into again.
    """

    kind: Literal["private", "shared", "blocked"]
    owner_scope: Optional[str] = None
    owner_agent_identity_id: Optional[str] = None
    edge_agent_environment_id: Optional[str] = None

    @classmethod
    def private(cls, owner_scope: str) -> ContextHolder:
        return cls("private", owner_scope=owner_scope)

    @classmethod
    def shared(cls, owner_agent_identity_id: str, edge_agent_environment_id: str) -> ContextHolder:
        return cls(
            "shared",
            owner_agent_identity_id=owner_agent_identity_id,
            edge_agent_environment_id=edge_agent_environment_id,
        )


def holder_of(call_context: ServerCallContext, owner_resolver: OwnerResolver) -> Optional[ContextHolder]:
    """The holder a request would use its context as; ``None`` when it has no owner to hold one by.

    Only the verified credential decides a shared holder - never metadata,
    and never ``is_authenticated`` alone.
    """
    caller = verified_caller(call_context)
    if caller is not None and caller.credential is CredentialKind.INVOCATION:
        gateway = caller.gateway
        return ContextHolder.shared(gateway.owner_agent_identity_id, gateway.edge_agent_environment_id)
    owner_scope = owner_resolver(call_context)
    if not owner_scope:
        return None
    return ContextHolder.private(owner_scope)


class ContextAdmission(Protocol):
    """Reserves a context for its first holder and admits only that holder after."""

    async def admit(self, context_id: str, holder: ContextHolder) -> bool:
        """Whether ``holder`` may use ``context_id``; reserves it when nobody holds it yet.

        A refusal changes nothing. An admission is permanent: the
        reservation is never released, whatever happens to the request.
        """


class InMemoryContextAdmission:
    """Reservations held by this process, for the in-memory task store and framework state."""

    def __init__(self) -> None:
        self._holders: dict[str, ContextHolder] = {}
        self._lock = asyncio.Lock()

    async def admit(self, context_id: str, holder: ContextHolder) -> bool:
        async with self._lock:
            return self._holders.setdefault(context_id, holder) == holder


class PostgresContextAdmission:
    """Reservations in ``context_reservations``, shared by every server of the agent on one database.

    A context nobody holds is refused, and stays unreserved, when framework
    state saved before it carried an agent exists for it.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def admit(self, context_id: str, holder: ContextHolder) -> bool:
        wanted = ContextReservationRecord(
            agent_id=self._agent_id,
            context_id=context_id,
            kind=holder.kind,
            owner_scope=holder.owner_scope,
            owner_agent_identity_id=holder.owner_agent_identity_id,
            edge_agent_environment_id=holder.edge_agent_environment_id,
        )
        async with self._db_manager.get_session() as session:
            async with session.begin():
                held = await ContextReservationsRepository(session).reserve(wanted)
        if held is None:
            return False
        return ContextHolder(
            held.kind,  # type: ignore[arg-type]
            owner_scope=held.owner_scope,
            owner_agent_identity_id=held.owner_agent_identity_id,
            edge_agent_environment_id=held.edge_agent_environment_id,
        ) == holder
