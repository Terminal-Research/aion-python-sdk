"""A file a framework's state links to is read by the stored file, however old its link.

The state keeps a stored file's link; the link expires long before the file
is removed. A model helper that has the reference to the file reads the file
by it. One that has the address alone finds the part that carries it among
the conversation's tasks - a message or an artifact the agent sent - and reads
that part, so its link can be issued again.
"""

from __future__ import annotations

from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus

from aion.core.a2a.file_contents import file_contents
from aion.core.a2a.files import StoredFile
from aion.core.agent.model_files import read_file_for_model, read_file_for_model_sync
from aion.core.agent.testing import InMemoryConversation, use_conversation

PDF = b"%PDF-1.7 report"
OLD_LINK = "https://files.test/1?grant=expired"
REPORT = StoredFile.create("file-1", "ver-1")


def _stored_part() -> Part:
    part = Part(url=OLD_LINK, media_type="application/pdf", filename="report.pdf")
    REPORT.write_to(part)
    return part


class _Recording(InMemoryConversation):
    """A conversation that answers every stored file with the report and records the part it read."""

    def __init__(self, *tasks: Task) -> None:
        super().__init__(tasks)
        self.read: list[Part] = []

    async def read_file(self, part, *, max_bytes=None):
        self.read.append(part)
        return PDF


async def test_an_address_in_an_artifact_the_agent_sent_is_read_by_its_part() -> None:
    task = Task(
        id="t-1",
        context_id="conversation",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(artifact_id="a-1", parts=[_stored_part()])],
    )
    conversation = _Recording(task)

    with use_conversation(conversation):
        assert await conversation.read_file_at(OLD_LINK) == PDF

    assert StoredFile.of(conversation.read[0]) == REPORT


async def test_an_address_in_a_status_message_is_read_by_its_part() -> None:
    task = Task(
        id="t-1",
        context_id="conversation",
        status=TaskStatus(
            state=TaskState.TASK_STATE_INPUT_REQUIRED,
            message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[_stored_part()]),
        ),
    )
    conversation = _Recording(task)

    with use_conversation(conversation):
        await conversation.read_file_at(OLD_LINK)

    assert StoredFile.of(conversation.read[0]) == REPORT


async def test_a_model_read_with_the_reference_reads_the_stored_file() -> None:
    """A file only the state holds - a tool's image, say - is found by its reference, not by the tasks."""
    file_contents().clear()
    conversation = _Recording()

    with use_conversation(conversation):
        assert await read_file_for_model(OLD_LINK, name="report.pdf", stored=REPORT) == PDF

    [read] = conversation.read
    assert StoredFile.of(read) == REPORT and read.url == OLD_LINK and read.filename == "report.pdf"


async def test_outside_a_turn_the_reference_finds_the_kept_content() -> None:
    file_contents().clear()
    file_contents().put("file:file-1:ver-1", PDF, url="https://files.test/1?grant=fresh")

    assert await read_file_for_model(OLD_LINK, stored=REPORT) == PDF


def test_a_blocking_read_with_the_reference_finds_the_kept_content() -> None:
    file_contents().clear()
    file_contents().put("file:file-1:ver-1", PDF, url="https://files.test/1?grant=fresh")

    assert read_file_for_model_sync(OLD_LINK, stored=REPORT) == PDF
