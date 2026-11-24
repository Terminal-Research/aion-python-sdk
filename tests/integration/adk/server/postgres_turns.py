"""An agent's context tasks and turn histories over PostgreSQL, for tests of ADK's services.

``PostgresAgent`` builds the task store, the context reservations and the
history of each turn as the server builds them for one agent, so a test can
store a context's tasks and ask a service from inside a turn, the way ADK
asks it while the server runs the agent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus

from tests.integration.server.tasks.postgres_support import stores
from tests.support.conversation import install

from aion.core.principal import Principal
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.db.postgres.manager import db_manager
from aion.server.agent.adapters import StateScope
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.auth import Assurance, AuthenticatedCaller, CallerCredentials, CredentialKind, GatewayCoordinates
from aion.server.tasks.admission import PostgresContextAdmission, holder_of
from aion.server.tasks.context_artifacts import PostgresContextArtifacts
from aion.server.tasks.context_runs import PostgresContextRuns
from aion.server.tasks.conversation import StoredConversations

__all__ = ["PostgresAgent", "participant", "private", "runtime_provider"]

IDENTITY = "6f1c2a40-0d5e-4b8e-9a51-3c1f2e7d9b10"
EDGE = "0b7e4f3a-62d1-4c59-8f0e-a2c9d14b7e21"


class _User(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def private(name: str) -> ServerCallContext:
    """A caller of a private context: the application's own user ``name``."""
    return ServerCallContext(user=_User(name))


def participant(name: str) -> ServerCallContext:
    """A participant ``name`` of the gateway conversation of one agent identity at one edge environment."""
    caller = AuthenticatedCaller(
        Principal("ExternalAnonymous", name),
        credential=CredentialKind.INVOCATION,
        assurance=Assurance.ACCOUNT,
        issuer="aion.io",
        gateway=GatewayCoordinates(IDENTITY, EDGE, "terminal"),
        claims={},
    )
    return ServerCallContext(user=_User(name), state={"auth": CallerCredentials(caller)})


@contextmanager
def runtime_provider() -> Iterator[None]:
    """The runtime context registry's provider, as the server registers it at startup."""
    provider = RequestScopeRuntimeContextProvider()
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    try:
        yield
    finally:
        provider.set_current_context(None)
        AionRuntimeContextRegistry.clear_provider()


class PostgresAgent:
    """One agent's task store, context reservations and turn histories, as the server builds them.

    Args:
        agent_id: The agent's id; a fresh one by default, so tests sharing a
            database never meet.
    """

    def __init__(self, agent_id: str | None = None) -> None:
        self.id = agent_id or f"agent-{uuid.uuid4()}"
        self.store, _ = stores(self.id)
        self.admission = PostgresContextAdmission(self.id, db_manager)
        self.conversations = StoredConversations(
            task_store=self.store,
            context_runs=PostgresContextRuns(self.id, db_manager),
            context_artifacts=PostgresContextArtifacts(self.id, db_manager),
            owner_resolver=resolve_user_scope,
        )

    async def admit(self, context_id: str, caller: ServerCallContext) -> bool:
        """Admit ``caller`` into the context as the server does before a turn; whether it was."""
        return await self.admission.admit(context_id, holder_of(caller, resolve_user_scope), resolve_user_scope(caller))

    async def store_artifact(self, context_id: str, caller: ServerCallContext, name: str, version: int, text: str) -> None:
        """Store a task of ``caller`` holding version ``version`` of the artifact ``name``, as the server stores it."""
        artifact = Artifact(artifact_id=uuid.uuid4().hex, name=name, parts=[Part(text=text)])
        artifact.metadata["version"] = str(version)
        task = Task(
            id=str(uuid.uuid4()),
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[artifact],
        )
        await self.store.save(task, caller)

    def turn(self, context_id: str, caller: ServerCallContext) -> str:
        """Install the history of a new turn of ``caller`` in the context; the ADK ``user_id`` the turn runs as."""
        install(self.conversations.for_turn(caller, Task(id=str(uuid.uuid4()), context_id=context_id)))
        return StateScope.for_call(self.id, caller, resolve_user_scope).state_owner
