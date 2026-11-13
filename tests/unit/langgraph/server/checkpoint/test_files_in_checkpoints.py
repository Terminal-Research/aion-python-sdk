"""A graph's checkpoints keep the files of its messages as links to stored files, not as bytes.

The server's checkpointers store an inline file through the turn's files
before a checkpoint or a pending write is saved; a file the turn also sends to
the client is the same stored file. The SDK's models read such a block by the
stored file it names.
"""

from __future__ import annotations

import base64
from typing import Annotated, Optional, TypedDict

import pytest
from a2a.types import Message, Part, Role
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.content import create_file_block
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.langgraph.authoring import inline_files
from aion.langgraph.server.checkpoint.backends.memory import AionInMemorySaver, MemoryBackend
from aion.langgraph.server.converters.a2a_to_lc import A2AToLcConverter
from aion.langgraph.server.converters.lc_to_a2a import LcToA2AConverter
from aion.server.agent.execution.request_executor import public_reason_of
from aion.server.files.storage import FileUploadErrorCode, UploadFailure
from aion.server.files.turn import StateFileNotStoredError
from tests.unit.support.files import NumberingBackend, storing_turn

PDF = b"%PDF-1.7 report"
PDF_B64 = base64.b64encode(PDF).decode()
PNG = b"\x89PNG chart"
PNG_B64 = base64.b64encode(PNG).decode()
LINK = "https://files.test/1?grant=g"
CONFIG = {"configurable": {"thread_id": "thread-1"}}


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    outbox: Optional[A2AOutbox]


def _graph(answer):
    graph = StateGraph(State)
    graph.add_node("answer", answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=AionInMemorySaver())


def _report_block() -> dict:
    return create_file_block(base64=PDF_B64, mime_type="application/pdf", filename="report.pdf")


def _saved(saver: AionInMemorySaver) -> str:
    """Everything the saver holds, as it would reach a database."""
    return repr(saver.storage) + repr(saver.blobs) + repr(saver.writes)


async def _run(answer, backend: NumberingBackend):
    graph = _graph(answer)
    with storing_turn(backend):
        await graph.ainvoke({"messages": [HumanMessage("Make a report")]}, CONFIG)
    return graph


async def test_a_file_a_node_returns_is_saved_as_a_link() -> None:
    returned = AIMessage(content=[{"type": "text", "text": "Here it is."}, _report_block()])
    backend = NumberingBackend()

    graph = await _run(lambda state: {"messages": [returned]}, backend)

    saved = (await graph.aget_state(CONFIG)).values["messages"][-1]
    text, report = saved.content
    assert text["text"] == "Here it is."
    assert "base64" not in report and report["url"] == LINK
    assert (report["type"], report["mime_type"], report["extras"]["filename"]) == ("file", "application/pdf", "report.pdf")
    assert StoredFile.from_metadata(report["extras"][STORED_FILE_KEY]).file_id == "file-1"
    assert PDF_B64 not in _saved(graph.checkpointer)
    assert len(backend.stored) == 1


async def test_the_message_the_graph_holds_keeps_its_content() -> None:
    """What is saved is a copy: the run goes on with the bytes it has."""
    returned = AIMessage(content=[_report_block()])

    await _run(lambda state: {"messages": [returned]}, NumberingBackend())

    assert returned.content[0]["base64"] == PDF_B64


async def test_the_file_the_client_gets_is_the_same_stored_file() -> None:
    """The pipeline stores what the client gets through the same turn: one upload, one link."""
    returned = AIMessage(content=[_report_block()])
    backend = NumberingBackend()
    graph = _graph(lambda state: {"messages": [returned]})

    with storing_turn(backend) as files:
        await graph.ainvoke({"messages": [HumanMessage("Make a report")]}, CONFIG)
        sent = await files.keep_parts(LcToA2AConverter.from_message(returned))

    saved = (await graph.aget_state(CONFIG)).values["messages"][-1].content[0]
    assert len(backend.stored) == 1
    assert sent[0].url == saved["url"]


async def test_a_tool_message_with_an_image_is_saved_as_a_link() -> None:
    returned = ToolMessage(
        content=[{"type": "image", "base64": PNG_B64, "mime_type": "image/png"}], tool_call_id="call-1"
    )

    graph = await _run(lambda state: {"messages": [returned]}, NumberingBackend())

    saved = (await graph.aget_state(CONFIG)).values["messages"][-1]
    assert saved.content[0]["url"] == LINK and "base64" not in saved.content[0]
    assert PNG_B64 not in _saved(graph.checkpointer)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source_type": "base64", "data": PNG_B64, "mime_type": "image/png"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
    ],
    ids=["legacy", "openai-data-url"],
)
async def test_other_forms_of_an_inline_file_are_saved_as_a_standard_link(block: dict) -> None:
    returned = HumanMessage(content=[{"type": "text", "text": "Look"}, block])

    graph = await _run(lambda state: {"messages": [returned]}, NumberingBackend())

    saved = (await graph.aget_state(CONFIG)).values["messages"][-1].content[1]
    assert (saved["type"], saved["mime_type"], saved["url"]) == ("image", "image/png", LINK)
    assert not {"data", "source_type", "base64", "image_url"} & set(saved)
    assert PNG_B64 not in _saved(graph.checkpointer)


async def test_an_outbox_kept_in_an_ordinary_field_is_saved_with_links() -> None:
    outbox = A2AOutbox(message=Message(message_id="m-1", role=Role.ROLE_AGENT, parts=[Part(raw=PDF, media_type="application/pdf")]))

    graph = await _run(lambda state: {"outbox": outbox}, NumberingBackend())

    saved = (await graph.aget_state(CONFIG)).values["outbox"]
    assert saved.message.parts[0].url == LINK
    assert PDF_B64 not in _saved(graph.checkpointer)


async def test_without_a_storing_turn_the_bytes_are_kept() -> None:
    """No storage backend, no turn files: the checkpoint keeps the file, as the task does."""
    graph = _graph(lambda state: {"messages": [AIMessage(content=[_report_block()])]})

    await graph.ainvoke({"messages": [HumanMessage("Make a report")]}, CONFIG)

    saved = (await graph.aget_state(CONFIG)).values["messages"][-1].content[0]
    assert saved["base64"] == PDF_B64


async def test_a_file_that_cannot_be_stored_fails_the_run_with_a_reason() -> None:
    graph = _graph(lambda state: {"messages": [AIMessage(content=[_report_block()])]})

    with storing_turn(NumberingBackend(refuse=True)):
        with pytest.raises(Exception) as raised:
            await graph.ainvoke({"messages": [HumanMessage("Make a report")]}, CONFIG)

    refusal = UploadFailure(FileUploadErrorCode.STORAGE_REJECTED)
    assert public_reason_of(raised.value) == StateFileNotStoredError([refusal]).public_reason
    assert PDF_B64 not in _saved(graph.checkpointer)


async def test_the_memory_backend_gives_the_saver_that_links_files() -> None:
    assert isinstance(await MemoryBackend().create(), AionInMemorySaver)


class TestReadingALinkedFile:
    """A block of a stored file names the file; the SDK's model reads it by that name."""

    class _Recording(InMemoryConversation):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.read: list[Part] = []

        async def read_file(self, part, *, max_bytes=None):
            self.read.append(part)
            return await super().read_file(part, max_bytes=max_bytes)

    async def test_the_file_is_read_by_the_stored_file_and_the_request_does_not_carry_its_name(self) -> None:
        block = {
            "type": "file",
            "url": LINK,
            "mime_type": "application/pdf",
            "extras": {"filename": "report.pdf", STORED_FILE_KEY: {"fileId": "file-1", "fileVersionId": "ver-1"}},
        }
        conversation = self._Recording(files={LINK: PDF})

        with use_conversation(conversation):
            [message] = await inline_files([AIMessage(content=[block])])

        [read] = conversation.read
        assert StoredFile.of(read) == StoredFile.create("file-1", "ver-1")
        [sent] = message.content
        assert base64.b64decode(sent["base64"]) == PDF
        assert sent["extras"] == {"filename": "report.pdf"}
        assert "url" not in sent

    async def test_a_block_with_nothing_but_the_reference_in_extras_loses_extras(self) -> None:
        block = {"type": "file", "url": LINK, "mime_type": "application/pdf", "extras": {STORED_FILE_KEY: {"fileId": "f", "fileVersionId": "v"}}}

        with use_conversation(self._Recording(files={LINK: PDF})):
            [message] = await inline_files([AIMessage(content=[block])])

        assert "extras" not in message.content[0]

    def test_an_inbound_stored_file_reaches_the_graph_with_its_reference(self) -> None:
        part = Part(url=LINK, media_type="application/pdf", filename="report.pdf")
        StoredFile.create("file-1", "ver-1").write_to(part)

        block = A2AToLcConverter.from_part(part)

        assert StoredFile.from_metadata(block["extras"][STORED_FILE_KEY]) == StoredFile.create("file-1", "ver-1")
        assert StoredFile.of(LcToA2AConverter.from_block(block)) == StoredFile.create("file-1", "ver-1")
