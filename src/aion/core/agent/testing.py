"""A conversation for the tests of agent code that reads ``aion.core.agent.conversation``.

Outside ``aion serve`` no turn is running, so the functions of
``aion.core.agent.conversation`` raise ``ConversationUnavailableError``.
``InMemoryConversation`` is a conversation a test fills itself, and
``use_conversation`` makes it the current one for a block of code, with no
server and no network.

Example:
    from a2a.types import Role

    from aion.core.a2a import text_artifact
    from aion.core.agent import conversation
    from aion.core.agent.testing import InMemoryConversation, use_conversation

    async def test_node_reads_the_draft():
        earlier = InMemoryConversation()
        earlier.add_task(
            messages=["Write a report", (Role.ROLE_AGENT, "Here is the draft.")],
            artifacts=[text_artifact("Draft", name="report.md")],
        )
        with use_conversation(earlier):
            (draft,) = await conversation.list_artifacts(name="report.md")
            assert draft.text == "Draft"
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Iterable, Iterator, Mapping, Optional, Sequence, Union
from uuid import uuid4

from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus

from aion.core.exceptions import ConversationFileError
from aion.core.a2a.files import DEFAULT_MAX_FILE_BYTES
from aion.core.runtime.context.conversation import (
    ArtifactVersion,
    Conversation,
    artifact_version,
    check_role,
    select_tasks,
)
from aion.core.runtime.context.models import AionRuntimeContext
from aion.core.runtime.context.registry import AionRuntimeContextProvider, AionRuntimeContextRegistry

__all__ = ["InMemoryConversation", "use_conversation"]

MessageInput = Union[Message, str, tuple[int, str]]
"""A message of a test task: an A2A ``Message``, a ``(role, text)`` pair, or the text of a user message."""


class InMemoryConversation(Conversation):
    """A conversation held in memory, which a test fills with tasks.

    Args:
        tasks: The tasks the conversation starts with, oldest first.
        context_id: The id of the conversation's context.
        task_id: The id of the task of the turn that reads the conversation.
        created_at: What ``get_created_at()`` answers.
        files: The content of stored files by their address, which
            a file's ``read()`` is answered from instead of the network.
    """

    def __init__(
            self,
            tasks: Iterable[Task] = (),
            *,
            context_id: str = "conversation",
            task_id: str = "current-task",
            created_at: Optional[datetime] = None,
            files: Optional[Mapping[str, bytes]] = None,
    ) -> None:
        super().__init__(context_id=context_id, task_id=task_id)
        self._tasks: list[Task] = list(tasks)
        self._created_at = created_at
        self.files: dict[str, bytes] = dict(files or {})

    def add_task(
            self,
            *,
            messages: Sequence[MessageInput] = (),
            artifacts: Sequence[Artifact] = (),
            state: int = TaskState.TASK_STATE_COMPLETED,
            task_id: Optional[str] = None,
    ) -> Task:
        """Add a task to the end of the conversation and return it.

        Args:
            messages: The task's history, in order: A2A messages,
                ``(role, text)`` pairs with the role ``Role.ROLE_USER`` or
                ``Role.ROLE_AGENT``, or plain strings for user messages.
            artifacts: The task's artifacts.
            state: The task's state.
            task_id: The task's id; a new one by default.
        """
        task_id = task_id or str(uuid4())
        task = Task(
            id=task_id,
            context_id=self.context_id,
            status=TaskStatus(state=state),
            history=[self._message(entry, task_id) for entry in messages],
            artifacts=list(artifacts),
        )
        self._tasks.append(task)
        return task

    def _message(self, entry: MessageInput, task_id: str) -> Message:
        if isinstance(entry, Message):
            return entry
        role, text = (Role.ROLE_USER, entry) if isinstance(entry, str) else entry
        check_role(role)
        return Message(
            message_id=str(uuid4()),
            context_id=self.context_id,
            task_id=task_id,
            role=role,
            parts=[Part(text=text)],
        )

    async def get_created_at(self) -> Optional[datetime]:
        return self._created_at

    async def get_task(self, task_id: str) -> Optional[Task]:
        return next((task for task in self._tasks if task.id == task_id), None)

    async def list_tasks(
            self,
            *,
            states: Optional[Iterable[int]] = None,
            limit: Optional[int] = None,
    ) -> list[Task]:
        return select_tasks(self._tasks, states=states, limit=limit)

    async def list_artifact_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        return [
            ArtifactVersion(task.id, artifact, artifact_version(artifact))
            for task in reversed(self._tasks)
            for artifact in task.artifacts
            if artifact.artifact_id == artifact_id
        ]

    async def read_file(self, part: Part, *, max_bytes: Optional[int] = DEFAULT_MAX_FILE_BYTES) -> bytes:
        """The file's bytes: an inline file's own, or what ``files`` holds for a stored file's address.

        Raises:
            ConversationFileError: The part carries no file, ``files`` has
                nothing for its address, or the file is larger than
                ``max_bytes``.
        """
        kind = part.WhichOneof("content")
        if kind == "raw":
            return bytes(part.raw)
        if kind != "url":
            raise ConversationFileError("The part carries no file to read.")
        label = part.filename or "file"
        content = self.files.get(part.url)
        if content is None:
            raise ConversationFileError(
                f"File {label!r} has no content in this test conversation: add it to `files` by its address."
            )
        if max_bytes is not None and len(content) > max_bytes:
            raise ConversationFileError(f"File {label!r} is larger than the {max_bytes} bytes this read allows.")
        return content


class _TestContextProvider(AionRuntimeContextProvider):
    """Keeps the runtime context of a test in a context variable, as the server keeps a turn's."""

    def __init__(self) -> None:
        self._context: ContextVar[Optional[AionRuntimeContext]] = ContextVar("aion_test_runtime_context", default=None)

    def get_current_context(self) -> Optional[AionRuntimeContext]:
        return self._context.get()

    async def aget_current_context(self) -> Optional[AionRuntimeContext]:
        return self._context.get()

    def set_current_context(self, context: Optional[AionRuntimeContext]) -> None:
        self._context.set(context)

    async def aset_current_context(self, context: Optional[AionRuntimeContext]) -> None:
        self._context.set(context)


@contextmanager
def use_conversation(conversation: Optional[Conversation]) -> Iterator[Optional[Conversation]]:
    """Make ``conversation`` the current turn's for the block, then restore what was there.

    The rest of the current runtime context, if there is one, is kept. With
    None the block runs as code outside a turn does.

    Args:
        conversation: The conversation ``aion.core.agent.conversation`` reads
            inside the block.

    Yields:
        ``conversation``.
    """
    registered = AionRuntimeContextRegistry._provider
    if registered is None:
        AionRuntimeContextRegistry.set_provider(_TestContextProvider())
    previous = AionRuntimeContextRegistry.get_current_context()
    installed = (
        previous.replace(conversation=conversation)
        if previous is not None
        else AionRuntimeContext(conversation=conversation)
    )
    AionRuntimeContextRegistry.set_current_context(installed)
    try:
        yield conversation
    finally:
        AionRuntimeContextRegistry.set_current_context(previous)
        if registered is None:
            AionRuntimeContextRegistry.clear_provider()
