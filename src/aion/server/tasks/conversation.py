"""The conversation of a turn over the server's stores (``aion.core.agent.conversation``).

``StoredConversations`` is built once with the agent's stores; the request
executor asks it for the conversation of every turn it runs and installs
that on the turn's runtime context. A turn's conversation reads the context
of its task
for the holder its caller was admitted as (``aion.server.tasks.admission``):
every read first checks that this holder still holds the context, so a
context being deleted, or one another holder presents under the same id,
reads as empty. Within the context nothing is narrowed to the caller's own
tasks: the participants of a shared context share the conversation.

A stored file of the conversation is read through a download link that works
now: when the link its part was stored with has expired, a new one is issued
for the same file version (``aion.server.files.links``), in the scope of the
turn that reads it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Optional

from a2a.server.owner_resolver import OwnerResolver
from a2a.types import Message, Part, Task

from aion.core.runtime.context.conversation import (
    ArtifactVersion,
    Conversation,
    check_role,
    check_window,
)
from aion.server.files.links import StoredFileLinks
from aion.server.tasks.admission import ContextHolder, holder_of
from aion.server.tasks.context_artifacts import ContextArtifacts
from aion.server.tasks.context_runs import ContextRuns

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import TaskStore

__all__ = ["StoredConversations"]


class StoredConversations:
    """Builds the conversation of each turn over one agent's stores.

    Args:
        task_store: The agent's task store; read regardless of owner.
        context_runs: Who holds a context, when it was created, its tasks and
            its messages.
        context_artifacts: The artifacts of a context's tasks and the
            versions of one.
        owner_resolver: Resolves a call's owner, as the task store does; it
            gives the holder a caller was admitted as.
        file_links: Issues a download link to a stored file again once the
            one its part holds has expired; omitted, a file is read through
            the link it was stored with.
    """

    def __init__(
            self,
            *,
            task_store: "TaskStore",
            context_runs: ContextRuns,
            context_artifacts: ContextArtifacts,
            owner_resolver: OwnerResolver,
            file_links: Optional[StoredFileLinks] = None,
    ) -> None:
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._owner_resolver = owner_resolver
        self._file_links = file_links

    def for_turn(self, call_context: "ServerCallContext", task: Task) -> Conversation:
        """The conversation of the turn of ``task`` its caller (``call_context``) runs."""
        return _StoredConversation(
            self._task_store,
            self._context_runs,
            self._context_artifacts,
            context_id=task.context_id,
            task_id=task.id,
            holder=holder_of(call_context, self._owner_resolver),
            file_links=self._file_links,
        )


class _StoredConversation(Conversation):
    """One turn's conversation: its context, for the holder its caller was admitted as."""

    def __init__(
            self,
            task_store: "TaskStore",
            context_runs: ContextRuns,
            context_artifacts: ContextArtifacts,
            *,
            context_id: str,
            task_id: str,
            holder: Optional[ContextHolder],
            file_links: Optional[StoredFileLinks] = None,
    ) -> None:
        super().__init__(context_id=context_id, task_id=task_id)
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._holder = holder
        self._file_links = file_links

    async def _held(self) -> bool:
        return self._holder is not None and await self._context_runs.holds(self.context_id, self._holder)

    async def get_created_at(self) -> Optional[datetime]:
        if not await self._held():
            return None
        return await self._context_runs.created_at(self.context_id)

    async def get_task(self, task_id: str) -> Optional[Task]:
        if not await self._held():
            return None
        return await self._task_in_context(task_id)

    async def list_tasks(
            self,
            *,
            states: Optional[Iterable[int]] = None,
            limit: Optional[int] = None,
    ) -> list[Task]:
        check_window(limit)
        if not await self._held():
            return []
        return await self._context_runs.tasks(
            self.context_id, states=None if states is None else frozenset(states), limit=limit
        )

    async def list_messages(
            self,
            *,
            role: Optional[int] = None,
            task_id: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> list[Message]:
        check_role(role)
        check_window(limit, offset)
        if not await self._held():
            return []
        return await self._context_runs.messages(
            self.context_id, role=role, task_id=task_id, limit=limit, offset=offset
        )

    async def list_artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        if self._holder is None:
            return []
        return await self._context_artifacts.versions(self.context_id, artifact_id, self._holder)

    async def list_artifacts(
            self,
            *,
            name: Optional[str] = None,
            task_id: Optional[str] = None,
            all_versions: bool = False,
            include_sdk: bool = False,
            limit: Optional[int] = None,
    ) -> list[ArtifactVersion]:
        check_window(limit)
        if self._holder is None:
            return []
        found = await self._context_artifacts.artifacts(
            self.context_id,
            self._holder,
            name=name,
            task_id=task_id,
            all_versions=all_versions,
            include_sdk=include_sdk,
        )
        return found if limit is None else found[:limit]

    async def file_link(self, part: Part) -> str:
        """The part's own link while it works, otherwise one issued anew for the same file version."""
        stored = await super().file_link(part)
        if self._file_links is None:
            return stored
        return await self._file_links.link_for(part) or stored

    async def _task_in_context(self, task_id: str) -> Optional[Task]:
        task = await self._task_store.get(task_id, None)
        return task if task is not None and task.context_id == self.context_id else None
