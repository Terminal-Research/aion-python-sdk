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

A stored file's part keeps the file's address - with Aion's storage the
version's protected content address, which works only with authorization -
and names the file under ``aion:file``. The conversation reads such a file
through a download link the server issues for the same file version on
behalf of the turn's sender (``aion.server.files.links``). The storage
service grants one only to the file's owner, the usage attribution it was
stored under: in a shared context a participant's turn sees the files the
others sent, and when the storage service refuses its attribution a link to
one, the read and the link raise ``ConversationFileForbiddenError`` and the
address is not fetched. A file past the retention deadline its part records,
or one the storage service says no longer exists, raises
``ConversationFileRemovedError`` the same way. A storage backend that holds its files in this
process - the stub one, for development - is asked for the file first
(``FileStorageBackend.held``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Optional

from a2a.server.owner_resolver import OwnerResolver
from a2a.types import Message, Part, Task

from aion.core.a2a.files import DEFAULT_MAX_FILE_BYTES
from aion.core.exceptions import ConversationFileTooLargeError
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

    from aion.server.files.storage import FileUploadManager

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
        file_links: Issues a download link to a stored file, on behalf of
            the turn's sender, whose part's address is no link that works;
            omitted, a file is read through the address its part holds.
        storage: The storage files are stored through, asked first for a file
            it holds in this process.
    """

    def __init__(
            self,
            *,
            task_store: "TaskStore",
            context_runs: ContextRuns,
            context_artifacts: ContextArtifacts,
            owner_resolver: OwnerResolver,
            file_links: Optional[StoredFileLinks] = None,
            storage: Optional["FileUploadManager"] = None,
    ) -> None:
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._owner_resolver = owner_resolver
        self._file_links = file_links
        self._storage = storage

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
            storage=self._storage,
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
            storage: Optional["FileUploadManager"] = None,
    ) -> None:
        super().__init__(context_id=context_id, task_id=task_id)
        self._task_store = task_store
        self._context_runs = context_runs
        self._context_artifacts = context_artifacts
        self._holder = holder
        self._file_links = file_links
        self._storage = storage

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

    async def read_file(self, part: Part, *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
        """The file from the storage backend when it holds it in this process, else as any conversation reads it."""
        held = self._storage.held(part.url) if self._storage is not None and part.WhichOneof("content") == "url" else None
        if held is None:
            return await super().read_file(part, max_bytes=max_bytes)
        if max_bytes is not None and len(held) > max_bytes:
            raise ConversationFileTooLargeError(part.filename or "file", max_bytes)
        return held

    async def file_link(self, part: Part) -> str:
        """A link issued for the turn's sender to the file version the part names; the part's address without one.

        The part's address is given as it is when it is itself a link that
        still works, when the part names no stored file, without a storage
        backend, and when no link could be issued for a reason other than
        those raised.

        Raises:
            ConversationFileError: The part carries no address.
            ConversationFileForbiddenError: The storage service refused a link
                to the turn's usage attribution: the file is not available to
                this sender.
            ConversationFileRemovedError: The file is past its retention
                deadline, or the storage service says it no longer exists.
            ConversationFileUnavailableError: The storage service did not
                answer the request for a link - it was unavailable, or not in
                time - and the part's address does not work by itself.
        """
        stored = await super().file_link(part)
        if self._file_links is None:
            return stored
        return await self._file_links.link_for(part) or stored

    async def _task_in_context(self, task_id: str) -> Optional[Task]:
        task = await self._task_store.get(task_id, None)
        return task if task is not None and task.context_id == self.context_id else None
