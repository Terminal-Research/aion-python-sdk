"""The SDK's chat model leaves out a file its model refuses, and the call answers.

The model class is the one ``aion_chat_openai`` builds. Under it the real
``ChatOpenAI`` builds each request - and raises on a block it cannot send, as
for a video - and only the network call is faked: a stand-in model service
that refuses a request carrying a file of ``REFUSED``.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import patch

import httpx
import openai
import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.messages.content import create_file_block, create_image_block, create_text_block, create_video_block
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from aion.core.a2a.file_contents import FileContents, keeping_file_contents
from aion.core.agent.left_out_files import CHECK_TEXT
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.settings import file_settings
from aion.langgraph.authoring import inline_files
from aion.langgraph.authoring.models import _file_inlining_chat_openai

REFUSED = "application/x-refused"
PDF, PNG, DAT, CLIP = b"%PDF-1.7 report", b"\x89PNG chart", b"\x00data", b"\x00\x00video"


def _block(content: bytes, mime_type: str, name: str) -> dict:
    create = {"image/png": create_image_block, "video/mp4": create_video_block}.get(mime_type, create_file_block)
    return create(base64=base64.b64encode(content).decode(), mime_type=mime_type, filename=name)


def _note(name: str, mime_type: str) -> dict:
    return {"type": "text", "text": f'[File "{name}" ({mime_type}) was not accepted by the model and was left out]'}


def _http_error(cls: type, status: int) -> Exception:
    request = httpx.Request("POST", "https://models.test/v1/chat/completions")
    return cls("refused", response=httpx.Response(status, request=request), body=None)


class _ModelService:
    """Stands in for the model service: refuses a request with a file of ``REFUSED``, otherwise answers "ok".

    Each request is the one the real ``ChatOpenAI`` builds; ``requests`` holds
    the content of its user messages.
    """

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.requests: list[list] = []

    def answer(self, model: ChatOpenAI, messages, stop, kwargs) -> None:
        payload = model._get_request_payload(messages, stop=stop, **kwargs)
        content = [
            part
            for message in payload["messages"]
            if message["role"] == "user"
            for part in (
                [{"type": "text", "text": message["content"]}] if isinstance(message["content"], str) else message["content"]
            )
        ]
        self.requests.append(content)
        if self.failure is not None:
            raise self.failure
        if any(f"data:{REFUSED};" in str(part) for part in content):
            raise _http_error(openai.BadRequestError, 400)

    def patch(self):
        service = self

        async def agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            service.answer(self, messages, stop, kwargs)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

        async def astream(self, messages, stop=None, run_manager=None, **kwargs):
            service.answer(self, messages, stop, kwargs)
            for piece in ("o", "k"):
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
                if run_manager is not None:
                    await run_manager.on_llm_new_token(piece, chunk=chunk)
                yield chunk

        def generate(self, messages, stop=None, run_manager=None, **kwargs):
            service.answer(self, messages, stop, kwargs)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

        def stream(self, messages, stop=None, run_manager=None, **kwargs):
            service.answer(self, messages, stop, kwargs)
            for piece in ("o", "k"):
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))

        return patch.multiple(ChatOpenAI, _agenerate=agenerate, _astream=astream, _generate=generate, _stream=stream)


@pytest.fixture
def service():
    service = _ModelService()
    with service.patch():
        yield service


def _model(**kwargs) -> ChatOpenAI:
    return _file_inlining_chat_openai()(model="gpt-test", api_key="key", **kwargs)


def _question(*files: dict) -> HumanMessage:
    return HumanMessage(content=[create_text_block(text="Look at these."), *files])


def _files_of(request: list) -> list[str]:
    """What each file part of a request is, and every text part but the question."""
    described = []
    for part in request:
        if part["type"] == "text":
            if part["text"] != "Look at these.":
                described.append(part["text"])
        elif part["type"] == "image_url":
            described.append(part["image_url"]["url"].split(";")[0])
        else:
            described.append(part["file"]["filename"])
    return described


async def test_the_refused_file_is_left_out_and_the_others_reach_the_model(service: _ModelService) -> None:
    answer = await _model().ainvoke([
        _question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "data.bin"), _block(PNG, "image/png", "chart.png"))
    ])

    assert answer.content == "ok"
    assert [_files_of(request) for request in service.requests] == [
        ["report.pdf", "data.bin", "data:image/png"],
        ["report.pdf", "data.bin", _note("chart.png", "image/png")["text"]],
        ["report.pdf", _note("data.bin", REFUSED)["text"], "data:image/png"],
    ]


async def test_a_video_langchain_cannot_send_is_left_out(service: _ModelService) -> None:
    """``ChatOpenAI`` raises on a video block before any request: that is the model class refusing it."""
    answer = await _model().ainvoke([_question(_block(PNG, "image/png", "chart.png"), _block(CLIP, "video/mp4", "clip.mp4"))])

    assert answer.content == "ok"
    [request] = service.requests
    assert _files_of(request) == ["data:image/png", _note("clip.mp4", "video/mp4")["text"]]


async def test_a_later_call_leaves_the_file_out_at_the_first_try(service: _ModelService) -> None:
    question = _question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "data.bin"))
    model = _model()
    first = await model.ainvoke([question])
    service.requests.clear()

    await model.ainvoke([question, first, HumanMessage(content="And now?")])

    [request] = service.requests
    assert _files_of(request) == ["report.pdf", _note("data.bin", REFUSED)["text"], "And now?"]


async def test_a_file_the_model_takes_stays_when_two_others_are_refused(service: _ModelService) -> None:
    answer = await _model().ainvoke([
        _question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "one.bin"), _block(DAT + b"2", REFUSED, "two.bin"))
    ])

    assert answer.content == "ok"
    checks = [request for request in service.requests if {"type": "text", "text": CHECK_TEXT} in request]
    assert [_files_of(check) for check in checks] == [["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT]]
    assert _files_of(service.requests[-1]) == ["report.pdf", _note("one.bin", REFUSED)["text"], _note("two.bin", REFUSED)["text"]]


async def test_a_failure_that_is_not_about_a_file_is_raised_after_one_request() -> None:
    service = _ModelService(failure=_http_error(openai.RateLimitError, 429))

    with service.patch(), pytest.raises(openai.RateLimitError):
        await _model().ainvoke([_question(_block(DAT, REFUSED, "data.bin"))])

    assert len(service.requests) == 1


async def test_only_the_try_that_goes_through_reaches_the_callbacks(service: _ModelService) -> None:
    """Chunks, and the tokens a graph streams to its client, come from one try: the others fail before their first."""

    class Tokens(AsyncCallbackHandler):
        def __init__(self) -> None:
            self.tokens: list[str] = []

        async def on_llm_new_token(self, token: str, **kwargs) -> None:
            self.tokens.append(token)

    tokens = Tokens()
    chunks = [
        chunk.content
        async for chunk in _model().astream(
            [_question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "data.bin"))],
            config={"callbacks": [tokens]},
        )
    ]

    # LangChain closes a stream with an empty chunk of its own.
    assert [chunk for chunk in chunks if chunk] == ["o", "k"]
    assert [token for token in tokens.tokens if token] == ["o", "k"]
    assert len(service.requests) == 2


async def test_a_streaming_model_searches_once(service: _ModelService) -> None:
    await _model(streaming=True).ainvoke([_question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "data.bin"))])

    assert [_files_of(request) for request in service.requests] == [
        ["report.pdf", "data.bin"],
        ["report.pdf", _note("data.bin", REFUSED)["text"]],
    ]


def test_a_synchronous_call_leaves_the_file_out_too(service: _ModelService) -> None:
    question = _question(_block(PDF, "application/pdf", "report.pdf"), _block(DAT, REFUSED, "data.bin"))

    assert _model().invoke([question]).content == "ok"
    assert "".join(chunk.content for chunk in _model().stream([question, HumanMessage(content="Again?")])) == "ok"

    assert [_files_of(request) for request in service.requests] == [
        ["report.pdf", "data.bin"],
        ["report.pdf", _note("data.bin", REFUSED)["text"]],
        ["report.pdf", _note("data.bin", REFUSED)["text"], "Again?"],
    ]


class TestFileTooLarge:
    """A file larger than a model request may carry is a note, on every path that reads files for a model."""

    URL = "https://files.test/scan?grant=abc"

    class _Recording(InMemoryConversation):
        def __init__(self) -> None:
            super().__init__(files={TestFileTooLarge.URL: b"%PDF-1.7 a scan of many pages"})
            self.reads = 0

        async def read_file(self, part, *, max_bytes=None):
            self.reads += 1
            return await super().read_file(part, max_bytes=max_bytes)

    def _question(self) -> HumanMessage:
        return _question(create_file_block(url=self.URL, mime_type="application/pdf", filename="scan.pdf"))

    async def test_inline_files_puts_a_note_in_its_place(self, monkeypatch) -> None:
        monkeypatch.setattr(file_settings, "model_file_max_bytes", 8)

        with use_conversation(self._Recording()):
            [message] = await inline_files([self._question()])

        assert message.content[1] == {
            "type": "text",
            "text": '[File "scan.pdf" (application/pdf) is larger than the 8 bytes a model request may carry and was left out]',
        }

    async def test_it_is_not_read_again_in_the_turn(self, monkeypatch) -> None:
        monkeypatch.setattr(file_settings, "model_file_max_bytes", 8)
        conversation = self._Recording()

        with use_conversation(conversation):
            await inline_files([self._question()])
            await inline_files([self._question()])

        assert conversation.reads == 1

    async def test_a_later_turn_reads_a_link_of_its_own_again(self, monkeypatch) -> None:
        """The content behind a link the server did not store may change between turns."""
        monkeypatch.setattr(file_settings, "model_file_max_bytes", 8)
        conversation = self._Recording()

        with use_conversation(conversation):
            for _ in range(2):
                with keeping_file_contents(FileContents()):
                    await inline_files([self._question()])

        assert conversation.reads == 2

    async def test_a_stored_file_whose_size_is_known_is_not_read(self, monkeypatch) -> None:
        monkeypatch.setattr(file_settings, "model_file_max_bytes", 8)
        conversation = self._Recording()
        block = create_file_block(url=self.URL, mime_type="application/pdf", filename="scan.pdf")
        block["extras"] = {**block.get("extras", {}), "aion:file": {"fileId": "f-1", "fileVersionId": "v-1", "byteSize": 9}}

        with use_conversation(conversation):
            [message] = await inline_files([_question(block)])

        assert message.content[1]["text"].startswith('[File "scan.pdf" (application/pdf) is larger than the 8 bytes')
        assert conversation.reads == 0

    async def test_the_model_gets_the_note(self, service: _ModelService, monkeypatch) -> None:
        monkeypatch.setattr(file_settings, "model_file_max_bytes", 8)

        with use_conversation(self._Recording()):
            await _model().ainvoke([self._question()])

        [request] = service.requests
        assert _files_of(request) == [
            '[File "scan.pdf" (application/pdf) is larger than the 8 bytes a model request may carry and was left out]'
        ]


class TestFileNotAvailable:
    """A stored file the storage service does not make available to the turn's sender is a note, and the turn goes on.

    In a shared conversation the file one participant sent stays in the
    history the others' turns send to a model.
    """

    URL = "https://files.test/files/f-1/versions/v-1/content"
    NOTE = '[File "a.pdf" (application/pdf) is not available to this sender and was left out]'

    class _Refusing(InMemoryConversation):
        def __init__(self) -> None:
            super().__init__(files={TestFileNotAvailable.URL: b"%PDF-1.7 not for this sender"})
            self.reads = 0

        async def read_file(self, part, *, max_bytes=None):
            from aion.core.exceptions import ConversationFileForbiddenError

            self.reads += 1
            raise ConversationFileForbiddenError(part.filename or "file", part.media_type or None)

    def _question(self) -> HumanMessage:
        block = create_file_block(url=self.URL, mime_type="application/pdf", filename="a.pdf")
        block["extras"] = {**block.get("extras", {}), "aion:file": {"fileId": "f-1", "fileVersionId": "v-1"}}
        return _question(block, _block(PDF, "application/pdf", "report.pdf"))

    async def test_inline_files_puts_a_note_in_its_place(self) -> None:
        with use_conversation(self._Refusing()):
            [message] = await inline_files([self._question()])

        assert message.content[1] == {"type": "text", "text": self.NOTE}
        assert base64.b64decode(message.content[2]["base64"]) == PDF

    async def test_inline_files_sync_puts_a_note_in_its_place(self) -> None:
        """A synchronous node runs in a worker thread and reads on the turn's event loop."""
        from aion.langgraph.authoring import inline_files_sync

        with use_conversation(self._Refusing()):
            [message] = await asyncio.to_thread(inline_files_sync, [self._question()])

        assert message.content[1] == {"type": "text", "text": self.NOTE}

    async def test_the_model_gets_the_note_and_answers(self, service: _ModelService) -> None:
        with use_conversation(self._Refusing()):
            answer = await _model().ainvoke([self._question()])

        assert answer.content == "ok"
        [request] = service.requests
        assert _files_of(request) == [self.NOTE, "report.pdf"]

    async def test_a_synchronous_model_call_gets_the_note_and_answers(self, service: _ModelService) -> None:
        with use_conversation(self._Refusing()):
            answer = await asyncio.to_thread(_model().invoke, [self._question()])

        assert answer.content == "ok"
        [request] = service.requests
        assert _files_of(request) == [self.NOTE, "report.pdf"]

    async def test_a_file_that_cannot_be_read_otherwise_still_fails_the_turn(self) -> None:
        from aion.core.exceptions import ConversationFileError, TaskFailedError

        class Unavailable(InMemoryConversation):
            async def read_file(self, part, *, max_bytes=None):
                raise ConversationFileError("File 'a.pdf' could not be read: its address answered HTTP 503.")

        with use_conversation(Unavailable()), pytest.raises(TaskFailedError, match="HTTP 503"):
            await inline_files([self._question()])


class TestFileRemoved:
    """A stored file that is no longer stored is a note, and the turn goes on.

    The SDK stores a conversation's files for 30 days from their upload; the
    message with the file stays in the state every later turn sends.
    """

    URL = "https://files.test/files/f-1/versions/v-1/content"
    NOTE = '[File "a.pdf" (application/pdf) is no longer available and was left out]'

    class _Removed(InMemoryConversation):
        async def read_file(self, part, *, max_bytes=None):
            from aion.core.exceptions import ConversationFileRemovedError

            raise ConversationFileRemovedError(part.filename or "file", part.media_type or None)

    def _question(self) -> HumanMessage:
        block = create_file_block(url=self.URL, mime_type="application/pdf", filename="a.pdf")
        block["extras"] = {**block.get("extras", {}), "aion:file": {"fileId": "f-1", "fileVersionId": "v-1"}}
        return _question(block, _block(PDF, "application/pdf", "report.pdf"))

    async def test_inline_files_puts_a_note_in_its_place(self) -> None:
        with use_conversation(self._Removed()):
            [message] = await inline_files([self._question()])

        assert message.content[1] == {"type": "text", "text": self.NOTE}
        assert base64.b64decode(message.content[2]["base64"]) == PDF

    async def test_the_model_gets_the_note_and_answers(self, service: _ModelService) -> None:
        with use_conversation(self._Removed()):
            answer = await _model().ainvoke([self._question()])

        assert answer.content == "ok"
        [request] = service.requests
        assert _files_of(request) == [self.NOTE, "report.pdf"]

    async def test_a_synchronous_model_call_gets_the_note_and_answers(self, service: _ModelService) -> None:
        with use_conversation(self._Removed()):
            answer = await asyncio.to_thread(_model().invoke, [self._question()])

        assert answer.content == "ok"
        [request] = service.requests
        assert _files_of(request) == [self.NOTE, "report.pdf"]


class TestReading:
    """How the files of a request are read."""

    URL = "https://files.test/report?grant=abc"

    class _Recording(InMemoryConversation):
        def __init__(self, files: dict[str, bytes], *, together: int = 0) -> None:
            super().__init__(files=files)
            self.reads: list[str] = []
            self._together = together
            self._started = asyncio.Event()

        async def read_file(self, part, *, max_bytes=None):
            self.reads.append(part.url)
            if self._together and len(self.reads) >= self._together:
                self._started.set()
            if self._together:
                await asyncio.wait_for(self._started.wait(), timeout=2)
            return await super().read_file(part, max_bytes=max_bytes)

    async def test_the_files_of_a_request_are_read_at_once(self) -> None:
        """Each read here waits until the other has started: one after another, they would wait forever."""
        conversation = self._Recording({"https://files.test/a": PDF, "https://files.test/b": PNG}, together=2)
        question = _question(
            create_file_block(url="https://files.test/a", mime_type="application/pdf", filename="a.pdf"),
            create_image_block(url="https://files.test/b", mime_type="image/png", filename="b.png"),
        )

        with use_conversation(conversation):
            [message] = await inline_files([question])

        assert base64.b64decode(message.content[1]["base64"]) == PDF
        assert base64.b64decode(message.content[2]["base64"]) == PNG

    async def test_a_stored_file_the_model_refused_is_not_read_in_a_later_turn(self, service: _ModelService) -> None:
        conversation = self._Recording({self.URL: DAT})
        block = create_file_block(url=self.URL, mime_type=REFUSED, filename="data.bin")
        block["extras"] = {**block.get("extras", {}), "aion:file": {"fileId": "f-1", "fileVersionId": "v-1"}}
        model = _model()

        with use_conversation(conversation):
            with keeping_file_contents(FileContents()):
                await model.ainvoke([_question(block)])
            service.requests.clear()
            with keeping_file_contents(FileContents()):
                await model.ainvoke([_question(block)])

        assert conversation.reads == [self.URL]
        [request] = service.requests
        assert _files_of(request) == [_note("data.bin", REFUSED)["text"]]


class TestBytesOfAStoredFile:
    """A block that carries the bytes of a stored file the turn read is known by that stored file.

    A graph that hands a model again the blocks ``inline_files`` gave it sends
    the file by its content, without the reference; a later turn has the
    block from the state by its address (``aion.server.files.turn``).
    """

    URL = "https://files.test/files/f-1/versions/v-1/content"

    def _turn_memory(self, media_type: str) -> FileContents:
        from aion.core.a2a.files import StoredFile

        contents = FileContents()
        contents.put("file:f-1:v-1", DAT, url=self.URL, stored=StoredFile.create("f-1", "v-1"), media_type=media_type)
        return contents

    async def test_refused_in_this_turn_it_is_left_out_unread_in_the_next(self, service: _ModelService, caplog) -> None:
        import logging

        reads: list[str] = []

        class Recording(InMemoryConversation):
            async def read_file(self, part, *, max_bytes=None):
                reads.append(part.url)
                return await super().read_file(part, max_bytes=max_bytes)

        stored = create_file_block(url=self.URL, mime_type=REFUSED, filename="data.bin")
        stored["extras"] = {**stored.get("extras", {}), "aion:file": {"fileId": "f-1", "fileVersionId": "v-1"}}
        model = _model()

        with use_conversation(Recording(files={self.URL: DAT})):
            with keeping_file_contents(self._turn_memory(REFUSED)):
                with caplog.at_level(logging.WARNING, logger="aion.core.agent.left_out_files"):
                    await model.ainvoke([_question(_block(DAT, REFUSED, "data.bin"))])
            service.requests.clear()
            with keeping_file_contents(FileContents()):
                await model.ainvoke([_question(stored)])

        [warning] = [record.getMessage() for record in caplog.records]
        assert f"({REFUSED}, file f-1 version v-1)" in warning
        assert reads == []
        [request] = service.requests
        assert _files_of(request) == [_note("data.bin", REFUSED)["text"]]

    async def test_with_another_media_type_it_is_another_file(self, service: _ModelService, caplog) -> None:
        import logging

        with keeping_file_contents(self._turn_memory("application/octet-stream")):
            with caplog.at_level(logging.WARNING, logger="aion.core.agent.left_out_files"):
                await _model().ainvoke([_question(_block(DAT, REFUSED, "data.bin"))])

        [warning] = [record.getMessage() for record in caplog.records]
        assert f"({REFUSED}, not stored)" in warning
