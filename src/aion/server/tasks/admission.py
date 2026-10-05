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

Admission also **binds** the caller to the context by its owner scope: a
private context ends up with one binding, a shared one with one per
participant. Bindings are what the Context extension lists, reads and
deletes by; see ``aion.server.tasks.contexts``. A context whose last binding
was removed is being deleted, and nothing is admitted into it until the
deletion finishes; once it has, the context is reserved afresh by the next
request, as if it had never existed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional, Protocol

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver

from aion.db.postgres import DbManager
from aion.db.postgres.records import ContextReservationRecord
from aion.db.postgres.repositories import ContextReservationsRepository
from aion.db.postgres.repositories.context_reservations.repository import ACTIVE, DELETED, DELETING
from aion.server.auth import CredentialKind, verified_caller

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE",
    "DELETED",
    "DELETING",
    "ContextAdmission",
    "ContextHolder",
    "InMemoryContextAdmission",
    "InMemoryContextEntry",
    "holder_of_record",
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
    """Reserves a context for its first holder, admits only that holder after, and binds whoever it admits."""

    async def admit(self, context_id: str, holder: ContextHolder, member: Optional[str]) -> bool:
        """Whether ``holder`` may use ``context_id``; reserves it when nobody holds it yet.

        An admitted ``member`` - the caller's owner scope - is bound to the
        context. ``None`` binds nobody: a caller without individual access
        has no owner scope of its own to be bound by.

        A refusal changes nothing. A context being deleted admits nobody.
        """


@dataclass
class InMemoryContextEntry:
    """One context's reservation, lifecycle and bindings, held by this process."""

    holder: ContextHolder
    state: str = ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bindings: dict[str, datetime] = field(default_factory=dict)
    deletion_operation_id: Optional[str] = None
    deletion_requested_by: Optional[str] = None


class InMemoryContextAdmission:
    """Reservations held by this process, for the in-memory task store and framework state."""

    def __init__(self) -> None:
        self.entries: dict[str, InMemoryContextEntry] = {}
        self.lock = asyncio.Lock()

    async def admit(self, context_id: str, holder: ContextHolder, member: Optional[str]) -> bool:
        async with self.lock:
            entry = self.entries.get(context_id)
            if entry is None or entry.state == DELETED:
                entry = self.entries[context_id] = InMemoryContextEntry(holder)
            if entry.holder != holder or entry.state != ACTIVE:
                return False
            if member:
                entry.bindings.setdefault(member, datetime.now(timezone.utc))
            return True


class PostgresContextAdmission:
    """Reservations in ``context_reservations``, shared by every server of the agent on one database.

    A context nobody holds is refused, and stays unreserved, when framework
    state saved before it carried an agent exists for it. The binding is
    written in the same transaction as the reservation it depends on.
    """

    def __init__(self, agent_id: str, db_manager: DbManager) -> None:
        self._agent_id = agent_id
        self._db_manager = db_manager

    async def admit(self, context_id: str, holder: ContextHolder, member: Optional[str]) -> bool:
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
                repository = ContextReservationsRepository(session)
                held = await repository.reserve(wanted)
                if held is None or held.state != ACTIVE or holder_of_record(held) != holder:
                    return False
                if member:
                    await repository.bind(self._agent_id, context_id, member)
                return True


def holder_of_record(record: ContextReservationRecord) -> ContextHolder:
    """The holder a reservation row names."""
    return ContextHolder(
        record.kind,  # type: ignore[arg-type]
        owner_scope=record.owner_scope,
        owner_agent_identity_id=record.owner_agent_identity_id,
        edge_agent_environment_id=record.edge_agent_environment_id,
    )
