"""The interface of a turn's conversation, which the runtime context carries.

The server implements ``Conversation`` over its stores and installs one
on the runtime context of every turn (``AionRuntimeContext.conversation``);
agents read it through ``aion.core.agent.conversation``.

A conversation has four reads a subclass must give - when the context was
created, one task, the tasks, the versions of an artifact id - and
builds the rest on them: the messages, the artifacts, the content of a file.
A subclass over a store that can answer one of those with a query of its own
overrides it; the functions ``select_tasks``, ``select_messages`` and
``select_artifacts`` are the selection every implementation shares, so the
answers do not depend on the store.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any, Iterable, Iterator, Optional, Sequence
from urllib.parse import urlsplit

from a2a.types import Artifact, Message, Part, Role, Task, TaskArtifactUpdateEvent

from aion.core.a2a.artifacts import ARTIFACT_VERSION_KEY
from aion.core.a2a.content import conversation_messages, data_of, text_of
from aion.core.a2a.enums import ArtifactId
from aion.core.a2a.file_contents import content_key, file_contents
from aion.core.a2a.files import DEFAULT_MAX_FILE_BYTES, ConversationFile, files_of
from aion.core.exceptions import ConversationFileError, ConversationUnavailableError
from aion.core.runtime.context.registry import AionRuntimeContextRegistry

__all__ = [
    "SDK_ARTIFACT_IDS",
    "ArtifactVersion",
    "Conversation",
    "artifact_version",
    "check_role",
    "check_window",
    "current_conversation",
    "fetch_file",
    "select_artifacts",
    "select_messages",
    "select_tasks",
]


SDK_ARTIFACT_IDS = frozenset(artifact_id.value for artifact_id in ArtifactId)
"""The artifacts the SDK fills, one per task."""

FILE_READ_TIMEOUT_SECONDS = 30.0
"""How long a read waits for a file's address to connect and to send each chunk."""

MAX_FILE_REDIRECTS = 5
"""How many redirects a file read follows; each one is checked like the address itself."""

logger = logging.getLogger(__name__)


def current_conversation() -> "Conversation":
    """The conversation the server installed for the turn running here.

    Raises:
        ConversationUnavailableError: No turn is running here.
    """
    context = AionRuntimeContextRegistry.get_current_context()
    conversation = context.conversation if context is not None else None
    if conversation is None:
        raise ConversationUnavailableError("The conversation is read only while the server runs a turn.")
    return conversation


def artifact_version(artifact: Artifact) -> int:
    """The version number in the artifact's metadata; 0 for an artifact that carries none."""
    if artifact.HasField("metadata") and ARTIFACT_VERSION_KEY in artifact.metadata:
        try:
            return int(artifact.metadata[ARTIFACT_VERSION_KEY])
        except (TypeError, ValueError):
            return 0
    return 0


def check_role(role: Optional[int]) -> None:
    """Refuse a role filter that is not a role a message has.

    Raises:
        ValueError: ``role`` is neither ``Role.ROLE_USER`` nor ``Role.ROLE_AGENT``.
    """
    if role is not None and role not in (Role.ROLE_USER, Role.ROLE_AGENT):
        raise ValueError(f"Unknown role {role!r}: expected Role.ROLE_USER or Role.ROLE_AGENT.")


def check_window(limit: Optional[int], offset: int = 0) -> None:
    """Refuse a ``limit`` or an ``offset`` that is negative.

    Raises:
        ValueError: Either is negative.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative.")
    if offset < 0:
        raise ValueError("offset must not be negative.")


def _window(items: Sequence[Any], limit: Optional[int], offset: int = 0) -> list[Any]:
    """The newest ``limit`` of chronological ``items`` after skipping the ``offset`` newest; still chronological."""
    check_window(limit, offset)
    end = len(items) - offset
    if end <= 0:
        return []
    start = 0 if limit is None else max(0, end - limit)
    return list(items[start:end])


def _file_parts_of(task: Task) -> Iterator[Part]:
    """The parts of a task that carry a file by its address: in its messages and its artifacts."""
    messages = list(task.history)
    if task.status.HasField("message"):
        messages.append(task.status.message)
    for parts in [message.parts for message in messages] + [artifact.parts for artifact in task.artifacts]:
        for part in parts:
            if part.WhichOneof("content") == "url":
                yield part


def select_tasks(
        tasks: Sequence[Task], *, states: Optional[Iterable[int]] = None, limit: Optional[int] = None
) -> list[Task]:
    """The newest ``limit`` of ``tasks`` (oldest first) that are in ``states``; still oldest first."""
    wanted = None if states is None else frozenset(states)
    matching = [task for task in tasks if wanted is None or task.status.state in wanted]
    return _window(matching, limit)


def select_messages(
        tasks: Sequence[Task],
        *,
        role: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
) -> list[Message]:
    """The conversation messages of ``tasks`` (oldest first), chronological.

    Selection is from the newest end: the ``offset`` newest matching messages
    are skipped, then up to ``limit`` are taken.
    """
    check_role(role)
    messages = [
        message
        for task in tasks
        for message in conversation_messages(task)
        if role is None or message.role == role
    ]
    return _window(messages, limit, offset)


def select_artifacts(
        versions: Iterable["ArtifactVersion"],
        *,
        name: Optional[str] = None,
        all_versions: bool = False,
        include_sdk: bool = False,
        limit: Optional[int] = None,
) -> list["ArtifactVersion"]:
    """Narrow ``versions`` (newest first) to the artifacts a read asks for; still newest first.

    Args:
        versions: Every artifact of the tasks searched, newest first.
        name: Keep only the artifacts with this ``name``.
        all_versions: Keep every version of an artifact id; by default only
            the newest one.
        include_sdk: Keep the artifacts the SDK fills itself as well.
        limit: Keep only the newest ``limit`` of the matching artifacts.

    Raises:
        ValueError: ``limit`` is negative.
    """
    check_window(limit)
    found = []
    seen: set[str] = set()
    for version in versions:
        artifact_id = version.artifact.artifact_id
        if not include_sdk and artifact_id in SDK_ARTIFACT_IDS:
            continue
        if name is not None and version.artifact.name != name:
            continue
        if not all_versions:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
        found.append(version)
    return found if limit is None else found[:limit]


async def fetch_file(url: str, *, label: str, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
    """Download a file from its address into memory.

    The address is fetched as it is stored, with no credentials of the
    agent's added: a stored file's address is a download link that carries
    its own access. Only public addresses are fetched, every redirect
    included (``aion.core.http.file_transport``): the address may come from
    whoever sent the message, and the server does not reach into its own
    network for them. Errors name the file by ``label``, never by its
    address, which may be a credential.

    Args:
        url: The file's ``http`` or ``https`` address.
        label: How errors name the file.
        max_bytes: The largest file to take; None for no limit.

    Raises:
        ConversationFileError: The address is not ``http``/``https`` or not
            a public one, it answered with an error or not at all, or the
            file is larger than ``max_bytes``.
    """
    import httpx

    from aion.core.http import file_transport

    if urlsplit(url).scheme.lower() not in ("http", "https"):
        raise ConversationFileError(f"File {label!r} has an address that is not http or https.")
    chunks: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(
                transport=file_transport.async_file_transport(),
                trust_env=False,
                timeout=FILE_READ_TIMEOUT_SECONDS,
                follow_redirects=True,
                max_redirects=MAX_FILE_REDIRECTS,
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    expired = (
                        " Its download link may have expired."
                        if response.status_code in (401, 403, 404, 410)
                        else ""
                    )
                    raise ConversationFileError(
                        f"File {label!r} could not be read: its address answered HTTP {response.status_code}.{expired}",
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ConversationFileError(
                            f"File {label!r} is larger than the {max_bytes} bytes this read allows."
                        )
                    chunks.append(chunk)
    except file_transport.AddressNotPublicError as error:
        logger.warning("File %r was not downloaded: %s", label, error)
        raise ConversationFileError(
            f"File {label!r} could not be read: its address is not a public one, "
            "and files are downloaded from public addresses only."
        ) from error
    except httpx.HTTPError as error:
        raise ConversationFileError(
            f"File {label!r} could not be read: {type(error).__name__}."
        ) from error
    # The host only: the address of a stored file is a download link that
    # carries its own access.
    logger.debug("Downloaded file %r (%d bytes) from %s", label, size, urlsplit(url).hostname)
    return b"".join(chunks)


@dataclass(frozen=True)
class ArtifactVersion:
    """One version of an artifact, as the task that wrote it holds it.

    Attributes:
        task_id: The task that wrote this version.
        artifact: The artifact as that task last wrote it.
        version: Its number, from the artifact's ``metadata["version"]``; 0
            for an artifact that carries none.
        updated_at: When the version was last written, when the store keeps
            that per artifact; otherwise when its task was. None when the
            conversation was not read from a store that keeps either.
    """

    task_id: str
    artifact: Artifact
    version: int
    updated_at: Optional[datetime] = None

    @property
    def artifact_id(self) -> str:
        """The artifact's id."""
        return self.artifact.artifact_id

    @property
    def name(self) -> str:
        """The artifact's name."""
        return self.artifact.name

    @property
    def text(self) -> str:
        """The text of the artifact's text parts, joined; empty without one."""
        return text_of(self.artifact)

    @property
    def data(self) -> Any:
        """The value of the artifact's first structured-data part; None without one.

        ``aion.core.a2a.content.data_of`` gives every data part.
        """
        values = data_of(self.artifact)
        return values[0] if values else None

    @cached_property
    def files(self) -> tuple[ConversationFile, ...]:
        """The artifact's files, in order; each keeps its content once read."""
        return tuple(files_of(self.artifact))

    async def read(self, *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
        """The artifact's content as bytes: its first file, or its text when it has no file.

        Raises:
            ConversationFileError: The artifact has neither a file nor text,
                or the file could not be read.
            ConversationUnavailableError: The file is stored and no turn is
                running here.
        """
        if self.files:
            return await self.files[0].read(max_bytes=max_bytes)
        text = self.text
        if not text:
            raise ConversationFileError(f"Artifact {self.artifact_id!r} has neither a file nor text to read.")
        return text.encode("utf-8")

    async def read_text(
            self, encoding: str = "utf-8", *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES
    ) -> str:
        """The artifact's content as text: its first file decoded, or its text parts; see :meth:`read`."""
        if self.files:
            return await self.files[0].read_text(encoding, max_bytes=max_bytes)
        return (await self.read(max_bytes=max_bytes)).decode("utf-8")


class Conversation(ABC):
    """The conversation of one context, as the caller of one turn may read it.

    The server builds one for every turn; an agent reads it through the
    functions of ``aion.core.agent.conversation``. Every read goes to the
    task store, so any server of the agent answers from what the store holds;
    what the turn has yielded but the store has not saved yet is not there.

    Tasks and messages are returned oldest first, as a conversation reads;
    artifacts newest first, as versions are looked up. A ``limit`` always
    counts from the newest end.

    Args:
        context_id: The context of the turn's task.
        task_id: The turn's task.
    """

    def __init__(self, *, context_id: str, task_id: str) -> None:
        self.context_id = context_id
        self.task_id = task_id
        self._numbers: dict[str, int] = {}
        self._file_parts: Optional[dict[str, Part]] = None
        # The event loop the turn runs on: code of the turn that runs in a
        # worker thread - a synchronous graph node, say - hands its reads to it.
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    # -- what a subclass reads from its store ---------------------------------

    @abstractmethod
    async def get_created_at(self) -> Optional[datetime]:
        """When this context was created: the time the server reserved its id; None when it does not know.

        A context that is deleted and taken again under the same id is
        created anew, so the time differs: state kept outside the server
        under the context's id (a branch, a file) can tell an earlier use of
        the id from the current one by it.
        """

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[Task]:
        """A task of this context, with its history and artifacts; None for any other task."""

    @abstractmethod
    async def list_tasks(
            self,
            *,
            states: Optional[Iterable[int]] = None,
            limit: Optional[int] = None,
    ) -> list[Task]:
        """The tasks of this context, oldest first.

        Args:
            states: Keep only the tasks in these ``a2a.types.TaskState``
                values; by default every task. ``aion.core.a2a`` names the
                usual groups, such as ``UNFINISHED_TASK_STATES``.
            limit: Keep only the newest ``limit`` of the matching tasks.
        """

    @abstractmethod
    async def list_artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        """Every version of ``artifact_id`` in this context, newest task first."""

    # -- reads built on them ---------------------------------------------------

    async def get_current_task(self) -> Optional[Task]:
        """The turn's task as the store holds it; None before it is first saved."""
        return await self.get_task(self.task_id)

    async def list_messages(
            self,
            *,
            role: Optional[int] = None,
            task_id: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
    ) -> list[Message]:
        """The messages of this context, oldest first.

        The messages are those of the context's tasks - every participant's
        in a shared context, as ``list_tasks`` reads them - without the events an
        agent keeps for itself (``is_conversation_message``). An answer the
        agent streamed is an artifact of its task, not a message:
        ``aion.core.a2a.content.answer_text`` reads a task's answer.

        Args:
            role: Keep only the messages of this ``a2a.types.Role``:
                ``Role.ROLE_USER`` or ``Role.ROLE_AGENT``.
            task_id: Keep only the messages of this task of the context.
            limit: Keep only the newest ``limit`` of the matching messages.
            offset: Skip this many of the newest matching messages first, to
                page backwards through a long conversation.

        Raises:
            ValueError: ``role`` is neither of those roles, or ``limit`` or
                ``offset`` is negative.
        """
        if task_id is not None:
            task = await self.get_task(task_id)
            tasks = [task] if task is not None else []
        else:
            tasks = await self.list_tasks()
        return select_messages(tasks, role=role, limit=limit, offset=offset)

    async def list_artifacts(
            self,
            *,
            name: Optional[str] = None,
            task_id: Optional[str] = None,
            all_versions: bool = False,
            include_sdk: bool = False,
            limit: Optional[int] = None,
    ) -> list[ArtifactVersion]:
        """The artifacts of this context, newest first: the latest version of each.

        An artifact is identified by its ``artifact_id``: the same id in
        several tasks is one artifact in several versions, and the newest
        task's is the latest.

        Args:
            name: Keep only the artifacts with this ``name``.
            task_id: Keep only the artifacts of this task of the context.
            all_versions: Return every version of each artifact instead of
                the latest one.
            include_sdk: Include the artifacts the SDK fills itself for every
                task - the streamed answer, the thoughts, the copied answer,
                the reaction - which are left out by default.
            limit: Keep only the newest ``limit`` of the matching artifacts.

        Raises:
            ValueError: ``limit`` is negative.
        """
        if task_id is not None:
            task = await self.get_task(task_id)
            tasks = [task] if task is not None else []
        else:
            tasks = await self.list_tasks()
        versions = (
            ArtifactVersion(task.id, artifact, artifact_version(artifact))
            for task in reversed(tasks)
            for artifact in reversed(task.artifacts)
        )
        return select_artifacts(
            versions, name=name, all_versions=all_versions, include_sdk=include_sdk, limit=limit
        )

    async def get_artifact(self, artifact_id: str, *, version: Optional[int] = None) -> Optional[ArtifactVersion]:
        """One artifact of this context by its id; None without one.

        Args:
            artifact_id: The artifact's id.
            version: The number of the version to return; by default the
                latest one, which the newest task wrote under this id. When
                several tasks hold the number - an artifact written without
                one reads as version 0 in each - the newest of them is
                returned.
        """
        versions = await self.list_artifact_versions(artifact_id)
        if version is not None:
            versions = [found for found in versions if found.version == version]
        return versions[0] if versions else None

    async def file_link(self, part: Part) -> str:
        """A download link to the stored file of ``part`` that works now.

        The address a part holds is a download link with its own access and
        a limited lifetime. A conversation read from a server gives the part's
        own link while it works and a link issued anew for the same file
        afterwards, for as long as the file is stored; this default knows
        only the part and gives its address.

        Args:
            part: A part that carries a file by its address (``url``).

        Raises:
            ConversationFileError: The part carries no address.
        """
        if part.WhichOneof("content") != "url":
            raise ConversationFileError("The part carries no address of a stored file.")
        return part.url

    async def read_file(self, part: Part, *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
        """The bytes of a file part of a message or an artifact.

        An inline file is returned as it is. A stored file is downloaded
        through :meth:`file_link`: the link its part holds, or a new one
        once that has expired. A file that is no longer stored, or one whose
        link cannot be issued again, cannot be read. What was downloaded, and
        what this server stored itself, is kept in memory for a while
        (``aion.core.a2a.file_contents``), so the file is downloaded once.

        Args:
            part: A part that carries a file, ``raw`` or ``url``.
            max_bytes: The largest file to download; None for no limit.

        Raises:
            ConversationFileError: The part carries no file, its address
                answered with an error or not at all, or the file is larger
                than ``max_bytes``.
        """
        kind = part.WhichOneof("content")
        if kind == "raw":
            return bytes(part.raw)
        if kind != "url":
            raise ConversationFileError("The part carries no file to read.")
        label = part.filename or "file"

        async def download() -> bytes:
            return await fetch_file(await self.file_link(part), label=label, max_bytes=max_bytes)

        content = await file_contents().load(content_key(part), download, url=part.url)
        if max_bytes is not None and len(content) > max_bytes:
            raise ConversationFileError(f"File {label!r} is larger than the {max_bytes} bytes this read allows.")
        return content

    async def read_file_at(
            self, url: str, *, name: Optional[str] = None, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES
    ) -> bytes:
        """The bytes of the file a message of the conversation carries at ``url``.

        For a reader that holds only the address - a model request names a
        file that way. The address is that of a file part of one of the
        conversation's tasks - a message, the caller's or the agent's, or an
        artifact the agent sent; the part is what is read (:meth:`read_file`),
        so an address whose link has expired still reads the file. An address
        no task carries is fetched as it is.

        Args:
            url: The address a file part holds.
            name: How errors name the file when no part gives it a name.
            max_bytes: The largest file to take; None for no limit.

        Raises:
            ConversationFileError: As :meth:`read_file`.
        """
        content = file_contents().get_by_url(url)
        if content is None:
            if self._file_parts is None:
                self._file_parts = {}
                for task in await self.list_tasks():
                    for part in _file_parts_of(task):
                        self._file_parts.setdefault(part.url, part)
            part = self._file_parts.get(url) or Part(url=url, filename=name or "")
            return await self.read_file(part, max_bytes=max_bytes)
        if max_bytes is not None and len(content) > max_bytes:
            raise ConversationFileError(
                f"File {name or 'file'!r} is larger than the {max_bytes} bytes this read allows."
            )
        return content

    # -- numbering the artifacts a task writes --------------------------------

    async def artifact_version_to_write(self, artifact_id: str) -> int:
        """The version number the turn's task writes ``artifact_id`` under.

        A task that already holds the artifact keeps its number, so a
        replacement or a later turn does not renumber it; otherwise the number
        follows the newest version in the context, or is 0 for the first.
        Nothing locks the context between this read and the write: two tasks
        writing the same artifact at once can take the same number.
        """
        versions = await self.list_artifact_versions(artifact_id)
        for version in versions:
            if version.task_id == self.task_id:
                return version.version
        return versions[0].version + 1 if versions else 0

    async def stamp_artifact_version(self, event: TaskArtifactUpdateEvent) -> None:
        """Write the number the task writes the event's artifact under into its ``metadata["version"]``.

        Every chunk and replacement of an artifact id in the turn carries the
        same number; the store is asked once per id. The artifacts the SDK
        fills itself - the streamed answer, the thoughts - are one per task,
        not versions of an artifact in the context, and get no number.
        """
        artifact_id = event.artifact.artifact_id
        if not artifact_id or artifact_id in SDK_ARTIFACT_IDS:
            return
        if artifact_id not in self._numbers:
            self._numbers[artifact_id] = await self.artifact_version_to_write(artifact_id)
        event.artifact.metadata[ARTIFACT_VERSION_KEY] = str(self._numbers[artifact_id])
