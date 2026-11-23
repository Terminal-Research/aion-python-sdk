"""A graph's checkpoints keep the files of its messages as links to stored files, not as bytes.

The server's checkpointers store an inline file through the turn's files
before a checkpoint or a pending write is saved; a file the turn also sends to
the client is the same stored file. The SDK's models read such a block by the
stored file it names.
"""

from __future__ import annotations

import base64
import logging
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Annotated, Any, NamedTuple, Optional, TypedDict
from unittest.mock import AsyncMock, Mock, patch

import pytest
from a2a.types import Message, Part, Role
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.messages.content import create_file_block
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from pydantic import BaseModel

import aion.core.runtime.context.conversation as conversation_module
from aion.core.a2a.file_contents import FileContents, keeping_file_contents
from aion.core.a2a.files import STORED_FILE_KEY, StoredFile
from aion.core.a2a.models import A2AOutbox
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.langgraph.authoring import inline_files
from aion.langgraph.server.adapter import LangGraphAdapter
from aion.langgraph.server.checkpoint.backends.memory import AionInMemorySaver, MemoryBackend
from aion.langgraph.server.checkpoint.files import linking_files, with_linked_files
from aion.langgraph.server.converters.a2a_to_lc import A2AToLcConverter
from aion.core.runtime.context.conversation import Conversation
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


class TestAGraphThatReturnsTheInlinedMessages:
    """A graph that returns into its state the messages ``inline_files`` gave it does not store their files again.

    The copies carry the file's content in place of its address; the
    checkpoint links the stored file the turn read them from, with its own
    reference and retention, on every turn.
    """

    ADDRESS = "https://files.test/files/inbound/versions/v-1/content"
    INBOUND = StoredFile.create("inbound", "v-1", retention_expires_at="2026-12-18T00:00:00Z", byte_size=len(PDF))

    class _Reading(InMemoryConversation):
        """Reads as the server's conversation does: through the turn's memory and the part's address."""

        read_file = Conversation.read_file

    @pytest.fixture(autouse=True)
    def downloads(self, monkeypatch) -> list[str]:
        fetched: list[str] = []

        async def fetch(url: str, *, label: str, max_bytes=None) -> bytes:
            fetched.append(url)
            return PDF

        monkeypatch.setattr(conversation_module, "fetch_file", fetch)
        return fetched

    def _question(self) -> HumanMessage:
        part = Part(url=self.ADDRESS, media_type="application/pdf", filename="report.pdf")
        self.INBOUND.write_to(part)
        return HumanMessage(content=A2AToLcConverter.from_parts([Part(text="Summarize the report"), part]))

    async def test_the_checkpoint_keeps_the_stored_file_turn_after_turn(self, downloads) -> None:
        async def answer(state):
            inlined = await inline_files(state["messages"])
            return {"messages": [*inlined, AIMessage("Summary.")]}

        graph = _graph(answer)
        backend = NumberingBackend()
        for message in (self._question(), HumanMessage("And the totals?")):
            with storing_turn(backend), keeping_file_contents(FileContents()), use_conversation(self._Reading()):
                await graph.ainvoke({"messages": [message]}, CONFIG)

        report = (await graph.aget_state(CONFIG)).values["messages"][0].content[1]
        assert backend.stored == []
        assert report["url"] == self.ADDRESS and "base64" not in report
        assert StoredFile.from_metadata(report["extras"][STORED_FILE_KEY]) == self.INBOUND
        assert PDF_B64 not in _saved(graph.checkpointer)
        assert downloads == [self.ADDRESS, self.ADDRESS]


class Report(BaseModel):
    title: str = "report"
    messages: list[AnyMessage] = []


@dataclass
class Box:
    messages: list = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FrozenBox:
    messages: tuple = ()


class Pair(NamedTuple):
    title: str
    messages: list


class Messages(list):
    pass


class HeldState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    held: Any
    note: str


def _chart() -> AIMessage:
    return AIMessage(content=[{"type": "text", "text": "The chart."}, {"type": "image", "base64": PNG_B64, "mime_type": "image/png"}])


def _held_graph(builder: StateGraph):
    return builder.compile(checkpointer=AionInMemorySaver())


def _one_node(answer) -> StateGraph:
    builder = StateGraph(HeldState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder


SHAPES = {
    "pydantic-model": lambda: Report(messages=[_chart()]),
    "dataclass": lambda: Box(messages=[_chart()]),
    "frozen-slots-dataclass": lambda: FrozenBox(messages=(_chart(),)),
    "ordered-dict": lambda: OrderedDict(messages=[_chart()]),
    "defaultdict": lambda: defaultdict(list, messages=[_chart()]),
    "named-tuple": lambda: Pair("charts", [_chart()]),
    "list-subclass": lambda: Messages([_chart()]),
}


class TestFilesWhereverTheStateKeepsThem:
    """A message is saved from wherever LangGraph saves it, and its file is linked there too."""

    @pytest.mark.parametrize("make", SHAPES.values(), ids=SHAPES.keys())
    async def test_a_file_in_a_container_the_state_holds_is_saved_as_a_link(self, make) -> None:
        graph = _held_graph(_one_node(lambda state: {"held": make()}))
        backend = NumberingBackend()

        with storing_turn(backend):
            await graph.ainvoke({"messages": [HumanMessage("Draw it")]}, CONFIG)

        assert PNG_B64 not in _saved(graph.checkpointer)
        assert len(backend.stored) == 1

    async def test_a_file_a_send_carries_to_a_node_is_saved_as_a_link(self) -> None:
        builder = StateGraph(HeldState)
        builder.add_node("plan", lambda state: {"note": "planned"})
        builder.add_node("draw", lambda state: {"note": "drawn"})
        builder.add_edge(START, "plan")
        builder.add_conditional_edges("plan", lambda state: [Send("draw", {"messages": [_chart()]})], ["draw"])
        builder.add_edge("draw", END)
        graph = _held_graph(builder)
        backend = NumberingBackend()

        with storing_turn(backend):
            await graph.ainvoke({"messages": [HumanMessage("Draw it")]}, CONFIG)

        assert PNG_B64 not in _saved(graph.checkpointer)
        assert len(backend.stored) == 1

    async def test_a_file_an_interrupt_asks_with_is_saved_as_a_link(self) -> None:
        graph = _held_graph(_one_node(lambda state: {"note": interrupt(_chart())}))
        backend = NumberingBackend()

        with storing_turn(backend):
            await graph.ainvoke({"messages": [HumanMessage("Draw it")]}, CONFIG)

        assert (await graph.aget_state(CONFIG)).interrupts
        assert PNG_B64 not in _saved(graph.checkpointer)
        assert len(backend.stored) == 1


class TestTheCopiesThatAreSaved:
    """What is saved is a copy of the graph's object, of the same type."""

    @pytest.mark.parametrize("make", SHAPES.values(), ids=SHAPES.keys())
    async def test_a_container_with_a_linked_file_is_copied_with_its_type(self, make) -> None:
        held = make()

        with storing_turn(NumberingBackend()) as files:
            saved = await with_linked_files(held, files)

        assert saved is not held and type(saved) is type(held)
        assert PNG_B64 not in repr(saved)
        assert PNG_B64 in repr(held)

    async def test_a_defaultdict_keeps_its_factory(self) -> None:
        with storing_turn(NumberingBackend()) as files:
            saved = await with_linked_files(defaultdict(list, messages=[_chart()]), files)

        assert saved.default_factory is list

    async def test_a_send_keeps_its_node(self) -> None:
        with storing_turn(NumberingBackend()) as files:
            saved = await with_linked_files(Send("draw", {"messages": [_chart()]}), files)

        assert saved.node == "draw"
        assert saved.arg["messages"][0].content[1]["url"] == LINK

    @pytest.mark.parametrize("make", SHAPES.values(), ids=SHAPES.keys())
    async def test_a_container_without_an_inline_file_is_saved_as_it_is(self, make) -> None:
        held = make()
        with storing_turn(NumberingBackend()) as files:
            linked = await with_linked_files(held, files)
            assert await with_linked_files(linked, files) is linked


class TestAGraphCompiledWithItsOwnCheckpointer:
    """The server serves a copy of the graph whose checkpointer links files and keeps the same storage."""

    async def _served(self, compiled):
        adapter = LangGraphAdapter()
        with patch.object(adapter, "_get_checkpointer", new=AsyncMock()) as server:
            graph = await adapter._compile_graph(compiled, Mock())
        server.assert_not_awaited()
        return graph

    async def test_its_checkpoints_keep_files_as_links(self) -> None:
        own = InMemorySaver()
        compiled = _one_node(lambda state: {"messages": [_chart()]}).compile(checkpointer=own)
        graph = await self._served(compiled)
        backend = NumberingBackend()

        with storing_turn(backend):
            await graph.ainvoke({"messages": [HumanMessage("Draw it")]}, CONFIG)

        assert own.storage and PNG_B64 not in _saved(own)
        assert len(backend.stored) == 1
        saved = (await compiled.aget_state(CONFIG)).values["messages"][-1].content[1]
        assert saved["url"] == LINK

    async def test_the_graph_and_the_checkpointer_it_was_given_are_not_changed(self) -> None:
        own = InMemorySaver()
        compiled = _one_node(lambda state: {"note": "done"}).compile(checkpointer=own)

        graph = await self._served(compiled)

        assert compiled.checkpointer is own and type(own) is InMemorySaver
        assert isinstance(graph.checkpointer, InMemorySaver) and graph.checkpointer is not own

    async def test_the_servers_checkpointer_is_not_copied_again(self) -> None:
        saver = AionInMemorySaver()

        assert linking_files(saver) is saver

    @pytest.mark.parametrize("given", [True, False, None])
    def test_a_checkpointer_setting_that_is_no_saver_is_left_as_it_is(self, given) -> None:
        assert linking_files(given) is given

    def test_a_checkpointer_that_cannot_be_copied_is_kept_with_a_warning(self, caplog) -> None:
        class Uncopyable(InMemorySaver):
            def __reduce_ex__(self, protocol):
                raise TypeError("not copyable")

        own = Uncopyable()

        with caplog.at_level(logging.WARNING, logger="aion.langgraph.server.checkpoint.files"):
            assert linking_files(own) is own

        assert "keep the bytes of the files the graph makes" in caplog.text
