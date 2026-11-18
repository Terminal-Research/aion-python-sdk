"""A stored file that is no longer stored is left out of a model request, and the turn goes on.

The SDK stores a conversation's files for 30 days from their upload, and the
message with a file stays in the history every later turn sends to a model.
The turn's conversation raises ``ConversationFileRemovedError`` for such a
file; the model helpers turn it into ``ModelFileRemovedError``, which
``inline_files`` replaces with a note. The turn remembers the file and logs
it once.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from a2a.types import Part

from aion.core.a2a.file_contents import FileContents, keeping_file_contents
from aion.core.a2a.files import StoredFile
from aion.core.agent.left_out_files import removed_note
from aion.core.agent.model_files import ModelFileRemovedError, read_file_for_model, read_file_for_model_sync
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import ConversationFileError, ConversationFileRemovedError

ADDRESS = "https://files.test/files/file-1/versions/version-1/content"
REPORT = StoredFile.create("file-1", "version-1")


class _Removed(InMemoryConversation):
    """A conversation whose storage no longer holds any stored file; it counts the reads."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def read_file(self, part: Part, *, max_bytes=None) -> bytes:
        self.reads += 1
        raise ConversationFileRemovedError(part.filename or "file", part.media_type or None)


def test_the_error_and_the_note_name_the_file_and_nothing_else() -> None:
    error = ConversationFileRemovedError("a.pdf", "application/pdf")

    assert str(error) == "File 'a.pdf' (application/pdf) is no longer available."
    assert str(ConversationFileRemovedError("a.pdf")) == "File 'a.pdf' is no longer available."
    assert isinstance(error, ConversationFileError)
    assert removed_note("a.pdf", "application/pdf") == (
        '[File "a.pdf" (application/pdf) is no longer available and was left out]'
    )


async def test_the_model_helper_reports_the_file_as_left_out() -> None:
    with use_conversation(_Removed()), pytest.raises(ModelFileRemovedError) as removed:
        await read_file_for_model(ADDRESS, name="a.pdf", stored=REPORT)

    assert removed.value.reason == "File 'a.pdf' is no longer available. It could not be sent to the model."
    assert isinstance(removed.value.__cause__, ConversationFileRemovedError)


async def test_the_turn_asks_once_and_logs_once(caplog) -> None:
    conversation = _Removed()

    with use_conversation(conversation), keeping_file_contents(FileContents()):
        with caplog.at_level(logging.WARNING, logger="aion.core.agent.model_files"):
            for _ in range(3):
                with pytest.raises(ModelFileRemovedError):
                    await read_file_for_model(ADDRESS, name="a.pdf", stored=REPORT)

    assert conversation.reads == 1
    [warning] = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warning.getMessage() == (
        "File 'a.pdf' (file file-1 version version-1) is no longer stored; "
        "a note takes its place in the model request."
    )


async def test_a_later_turn_asks_again() -> None:
    conversation = _Removed()

    with use_conversation(conversation):
        for _ in range(2):
            with keeping_file_contents(FileContents()), pytest.raises(ModelFileRemovedError):
                await read_file_for_model(ADDRESS, name="a.pdf", stored=REPORT)

    assert conversation.reads == 2


async def test_a_read_from_a_worker_thread() -> None:
    """A model called with ``invoke`` in a worker thread reads on the turn's event loop, with the same outcome."""
    with use_conversation(_Removed()), pytest.raises(ModelFileRemovedError):
        await asyncio.to_thread(read_file_for_model_sync, ADDRESS, name="a.pdf", stored=REPORT)
