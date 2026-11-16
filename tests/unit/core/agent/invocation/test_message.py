"""The inbound message an agent handles: the files attached to it."""

from __future__ import annotations

from a2a.types import Message, Part, Role

from aion.core.a2a import A2AInbox
from aion.core.agent import BaseMessage
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.runtime.context import AionRuntimeContext

STORED = "https://files.test/photo.png"


class _Message(BaseMessage):
    async def react(self, key, *, operation="add", display_value=None) -> None:
        return None


def inbound(*parts: Part) -> _Message:
    message = Message(message_id="m-1", role=Role.ROLE_USER, parts=list(parts))
    return _Message(context=AionRuntimeContext(inbox=A2AInbox(message=message)), thread=None)


async def test_the_files_of_an_inbound_message_are_read_inline_or_from_storage() -> None:
    message = inbound(
        Part(text="Two files"),
        Part(raw=b"inline", filename="note.txt", media_type="text/plain"),
        Part(url=STORED, filename="photo.png", media_type="image/png"),
    )
    note, photo = message.files

    assert (message.text, note.name, photo.name, photo.media_type) == ("Two files", "note.txt", "photo.png", "image/png")
    assert await note.read_text() == "inline"
    with use_conversation(InMemoryConversation(files={STORED: b"png-bytes"})):
        assert await photo.read() == b"png-bytes"
    assert photo.content == b"png-bytes"


def test_a_message_without_attachments_has_no_files() -> None:
    assert inbound(Part(text="Just text")).files == []
