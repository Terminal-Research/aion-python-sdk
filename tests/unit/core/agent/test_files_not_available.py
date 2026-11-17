"""A stored file the storage service does not make available to the turn's sender.

The storage service gives access to a stored file only on behalf of its
owner, the usage attribution it was stored under. A conversation whose link
for a file is refused raises ``ConversationFileForbiddenError`` from every
read of the file, and the file's address is not fetched. The model helpers
turn it into ``ModelFileForbiddenError``, which ``inline_files`` replaces
with a note; any other failure to read a file still fails the turn.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus

import aion.core.runtime.context.conversation as conversation_module
from aion.core.a2a.files import ConversationFile, StoredFile
from aion.core.agent.model_files import (
    ModelFileForbiddenError,
    read_file_for_model,
    read_file_for_model_sync,
)
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import (
    ConversationFileError,
    ConversationFileForbiddenError,
    TaskFailedError,
)
from aion.core.runtime.context.conversation import Conversation

ADDRESS = "https://files.test/files/file-1/versions/version-1/content"
REPORT = StoredFile.create("file-1", "version-1")


def _stored_part() -> Part:
    part = Part(url=ADDRESS, filename="a.pdf", media_type="application/pdf")
    REPORT.write_to(part)
    return part


class _Refusing(InMemoryConversation):
    """A conversation whose server refuses every stored file's link to the turn's sender.

    Its reads are those every ``Conversation`` builds on ``file_link``; a
    fetch of an address fails the test.
    """

    read_file = Conversation.read_file

    def __init__(self, *tasks: Task) -> None:
        super().__init__(tasks)
        self.links: list[Part] = []

    async def file_link(self, part: Part) -> str:
        self.links.append(part)
        raise ConversationFileForbiddenError(part.filename or "file", part.media_type or None)


@pytest.fixture(autouse=True)
def no_fetch(monkeypatch) -> list[str]:
    fetched: list[str] = []

    async def fetch(url: str, *, label: str, max_bytes=None) -> bytes:
        fetched.append(url)
        raise AssertionError("the address of a file not available to this sender was fetched")

    monkeypatch.setattr(conversation_module, "fetch_file", fetch)
    return fetched


def test_the_error_names_the_file_and_nothing_else() -> None:
    error = ConversationFileForbiddenError("a.pdf", "application/pdf")

    assert str(error) == "File 'a.pdf' (application/pdf) is not available to this sender."
    assert str(ConversationFileForbiddenError("a.pdf")) == "File 'a.pdf' is not available to this sender."
    assert isinstance(error, ConversationFileError)
    assert error.status_code is None


async def test_a_file_not_available_to_the_sender_is_neither_read_nor_linked(no_fetch) -> None:
    file = ConversationFile(_stored_part())

    with use_conversation(_Refusing()):
        with pytest.raises(ConversationFileForbiddenError, match="not available to this sender"):
            await file.read()
        with pytest.raises(ConversationFileForbiddenError):
            await file.link()

    assert file.content is None
    assert no_fetch == []


async def test_a_read_by_address_finds_the_part_and_is_refused(no_fetch) -> None:
    task = Task(
        id="t-1",
        context_id="conversation",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(artifact_id="a-1", parts=[_stored_part()])],
    )
    conversation = _Refusing(task)

    with use_conversation(conversation), pytest.raises(ConversationFileForbiddenError):
        await conversation.read_file_at(ADDRESS)

    assert StoredFile.of(conversation.links[0]) == REPORT
    assert no_fetch == []


class TestModelHelpers:
    """A model helper reports the file as left out, not as one that could not be read."""

    async def test_a_read_by_the_stored_file(self) -> None:
        with use_conversation(_Refusing()), pytest.raises(ModelFileForbiddenError) as refused:
            await read_file_for_model(ADDRESS, name="a.pdf", stored=REPORT)

        assert refused.value.reason == "File 'a.pdf' is not available to this sender. It could not be sent to the model."
        assert isinstance(refused.value.__cause__, ConversationFileForbiddenError)

    async def test_a_read_by_the_address(self) -> None:
        task = Task(
            id="t-1",
            context_id="conversation",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[Artifact(artifact_id="a-1", parts=[_stored_part()])],
        )

        with use_conversation(_Refusing(task)), pytest.raises(ModelFileForbiddenError):
            await read_file_for_model(ADDRESS, name="a.pdf")

    async def test_a_read_from_a_worker_thread(self) -> None:
        """A model called with ``invoke`` in a worker thread reads on the turn's event loop, with the same outcome."""
        with use_conversation(_Refusing()):
            with pytest.raises(ModelFileForbiddenError):
                await asyncio.to_thread(read_file_for_model_sync, ADDRESS, name="a.pdf", stored=REPORT)

    async def test_any_other_failure_still_fails_the_turn(self) -> None:
        class Failing(InMemoryConversation):
            async def read_file(self, part, *, max_bytes=None):
                raise ConversationFileError("File 'a.pdf' could not be read: its address answered HTTP 503.")

        with use_conversation(Failing()), pytest.raises(TaskFailedError) as failed:
            await read_file_for_model(ADDRESS, name="a.pdf", stored=REPORT)

        assert not isinstance(failed.value, ModelFileForbiddenError)
        assert failed.value.reason.endswith("It could not be sent to the model.")
