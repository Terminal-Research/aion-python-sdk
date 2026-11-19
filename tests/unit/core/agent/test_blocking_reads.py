"""A file read without ``await`` goes through the turn's event loop, and is never waited for on it.

A model called with ``invoke`` reads the files of its request with
``read_file_for_model_sync``. From a worker thread, or from code on an event
loop of its own, the read is handed to the turn's loop. On the turn's loop
itself nothing can be handed there and waited for, and a stored file's
address works only with authorization: the task fails at once, with a reason
that says to ``await`` the model, whether the turn holds the file or not.
"""

from __future__ import annotations

import asyncio

import pytest

from aion.core.a2a.file_contents import file_contents
from aion.core.a2a.files import StoredFile
from aion.core.agent.model_files import on_turn_event_loop, read_file_for_model_sync
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import TaskFailedError

PDF = b"%PDF-1.7 report"
LINK = "https://files.test/report?grant=abc"
REPORT = StoredFile.create("file-1", "ver-1")


async def test_on_the_turns_event_loop_the_task_fails_with_the_reason() -> None:
    # The turn holds the file: the call fails all the same, as it would on a later turn.
    file_contents().put("file:file-1:ver-1", PDF, url=LINK)

    with use_conversation(InMemoryConversation(files={LINK: PDF})):
        assert on_turn_event_loop()
        with pytest.raises(TaskFailedError) as failed:
            read_file_for_model_sync(LINK, name="report.pdf", stored=REPORT)

    assert str(failed.value) == (
        "File 'report.pdf' could not be read: a model called without await, with invoke() or stream(), runs on "
        "the turn's event loop and cannot read it there. Call ainvoke() or astream() in an async node, or call "
        "the model from a synchronous node. It could not be sent to the model."
    )


async def test_a_worker_thread_reads_through_the_turns_event_loop() -> None:
    with use_conversation(InMemoryConversation(files={LINK: PDF})):
        assert await asyncio.to_thread(on_turn_event_loop) is False
        assert await asyncio.to_thread(read_file_for_model_sync, LINK, name="report.pdf") == PDF


async def test_code_on_another_event_loop_reads_through_the_turns_one() -> None:
    def on_a_loop_of_its_own() -> bytes:
        async def read() -> bytes:
            assert not on_turn_event_loop()
            return read_file_for_model_sync(LINK, name="report.pdf")

        return asyncio.run(read())

    with use_conversation(InMemoryConversation(files={LINK: PDF})):
        assert await asyncio.to_thread(on_a_loop_of_its_own) == PDF


async def test_outside_a_turn_no_loop_is_the_turns() -> None:
    assert not on_turn_event_loop()
