"""The history of a turn's conversation over the server's stores (``aion.core.agent.conversation``).

``StoredConversations`` is built once with the agent's stores; the request
executor asks it for the history of every turn it runs and installs that on
the turn's runtime context. A turn's history reads the context of its task
for the holder its caller was admitted as (``aion.server.tasks.admission``):
every read first checks that this holder still holds the context, so a
context being deleted, or one another holder presents under the same id,
reads as empty. Within the context nothing is narrowed to the caller's own
tasks: the participants of a shared context share its history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from a2a.server.owner_resolver import OwnerResolver
from a2a.types import Task

from aion.core.runtime.context.conversation import ArtifactVersion, ConversationHistory
from aion.server.tasks.admission import ContextHolder, holder_of
from aion.server.tasks.context_artifacts import ContextArtifacts
from aion.server.tasks.context_runs import ContextRuns

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import TaskStore

__all__ = ["StoredConversations"]


class StoredConversations:
    """Builds the history of each turn over one agent's stores.

    Args:
        task_store: The agent's task store; read regardless of owner.
        context_runs: Who holds a context, its generation and its tasks.
        context_artifacts: The versions of an artifact among a context's tasks.
        owner_resolver: Resolves a call's owner, as the task store does; it
            gives the holder a caller was admitted as.
    """

    def __init__(
            self,
            *,
            task_store: "TaskStore",
            context_runs: ContextRuns,
            context_artifacts: ContextArtifacts,
            owner_resolver: OwnerResolver,
    ) -> None:
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._owner_resolver = owner_resolver

    def for_turn(self, call_context: "ServerCallContext", task: Task) -> ConversationHistory:
        """The history of the turn of ``task`` its caller (``call_context``) runs."""
        return _StoredConversation(
            self._task_store,
            self._context_runs,
            self._context_artifacts,
            context_id=task.context_id,
            task_id=task.id,
            holder=holder_of(call_context, self._owner_resolver),
        )


class _StoredConversation(ConversationHistory):
    """One turn's history: its context, for the holder its caller was admitted as."""

    def __init__(
            self,
            task_store: "TaskStore",
            context_runs: ContextRuns,
            context_artifacts: ContextArtifacts,
            *,
            context_id: str,
            task_id: str,
            holder: Optional[ContextHolder],
    ) -> None:
        super().__init__(context_id=context_id, task_id=task_id)
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._holder = holder

    async def _held(self) -> bool:
        return self._holder is not None and await self._context_runs.holds(self.context_id, self._holder)

    async def generation(self) -> Optional[str]:
        if not await self._held():
            return None
        return await self._context_runs.generation(self.context_id)

    async def get_task(self, task_id: str) -> Optional[Task]:
        if not await self._held():
            return None
        return await self._task_in_context(task_id)

    async def tasks(self, *, unfinished: bool = False) -> list[Task]:
        if not await self._held():
            return []
        found = []
        for task_id in await self._context_runs.task_ids(self.context_id, unfinished=unfinished):
            task = await self._task_in_context(task_id)
            if task is not None:
                found.append(task)
        return found

    async def artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        if self._holder is None:
            return []
        return await self._context_artifacts.versions(self.context_id, artifact_id, self._holder)

    async def _task_in_context(self, task_id: str) -> Optional[Task]:
        task = await self._task_store.get(task_id, None)
        return task if task is not None and task.context_id == self.context_id else None
