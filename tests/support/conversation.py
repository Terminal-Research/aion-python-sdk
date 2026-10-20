"""A turn's conversation history over the in-memory stores, for tests that run an executor themselves."""

from __future__ import annotations

from typing import Optional

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.types import Task

from aion.core.runtime.context.conversation import ConversationHistory
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.server.tasks.admission import ContextHolder, InMemoryContextAdmission
from aion.server.tasks.context_artifacts import InMemoryContextArtifacts
from aion.server.tasks.context_runs import InMemoryContextRuns
from aion.server.tasks.contexts import InMemoryContextCatalog
from aion.server.tasks.conversation import StoredConversations
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore

__all__ = ["InMemoryConversations", "install"]


class InMemoryConversations:
    """The in-memory task store and context reservations, and the histories of turns over them.

    Args:
        owner_resolver: Resolves a call's owner, for the store and the holder.
    """

    def __init__(self, owner_resolver: OwnerResolver = resolve_user_scope) -> None:
        self.store = InMemoryTaskStore(owner_resolver)
        self.admission = InMemoryContextAdmission()
        InMemoryContextCatalog(self.store, self.admission)
        self.runs = InMemoryContextRuns(self.store, self.admission)
        self.artifacts = InMemoryContextArtifacts(self.store, self.admission)
        self.conversations = StoredConversations(
            task_store=self.store,
            context_runs=self.runs,
            context_artifacts=self.artifacts,
            owner_resolver=owner_resolver,
        )

    async def hold(self, context_id: str, user: str) -> None:
        """Admit ``user`` into ``context_id``, as its private holder."""
        await self.admission.admit(context_id, ContextHolder.private(user), user)

    def for_turn(self, call_context: ServerCallContext, task: Task) -> ConversationHistory:
        """The history of the turn of ``task`` its caller runs."""
        return self.conversations.for_turn(call_context, task)


def install(history: Optional[ConversationHistory]) -> None:
    """Make ``history`` the current turn's, keeping the current runtime context's callback attribution.

    The runtime context registry needs a provider, as the server registers at
    startup.
    """
    current = AionRuntimeContextRegistry.get_current_context()
    AionRuntimeContextRegistry.set_current_context(
        AionRuntimeContext(
            callback_attribution=current.callback_attribution if current is not None else None,
            conversation=history,
        )
    )
