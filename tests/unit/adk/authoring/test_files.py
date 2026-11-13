"""A model gets the files of a conversation by their content, not their address.

A stored file is ``file_data`` with an address in the session. No provider
can be relied on to fetch a download link of the deployment's storage, and
ADK's ``LiteLlm`` refuses or drops a document given by address. ``inline_files``
puts the content into the model request and leaves the session as it is.
"""

from __future__ import annotations

import base64

import pytest
from google.adk.models import lite_llm as adk_lite_llm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from aion.adk.authoring import inline_files, inline_files_before_model
from aion.adk.authoring.models import _file_inlining_lite_llm
from aion.core.a2a.file_contents import file_contents
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import TaskFailedError
from aion.core.settings import file_settings

PDF_URL = "https://files.test/report?grant=abc"
PNG_URL = "https://files.test/chart?grant=abc"
PDF, PNG = b"%PDF-1.7 report", b"\x89PNG chart"


def _conversation() -> InMemoryConversation:
    return InMemoryConversation(files={PDF_URL: PDF, PNG_URL: PNG})


def _file(url: str, mime_type: str, name: str) -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=url, mime_type=mime_type, display_name=name))


def _request() -> tuple[LlmRequest, types.Content]:
    question = types.Content(
        role="user",
        parts=[
            types.Part(text="Summarize these."),
            _file(PDF_URL, "application/pdf", "report.pdf"),
            _file(PNG_URL, "image/png", "chart.png"),
        ],
    )
    answer = types.Content(role="model", parts=[types.Part(text="Done.")])
    return LlmRequest(model="gpt-test", contents=[question, answer]), question


async def test_every_file_is_replaced_by_its_content() -> None:
    request, _ = _request()

    with use_conversation(_conversation()):
        await inline_files(request)

    text, pdf, png = request.contents[0].parts
    assert text.text == "Summarize these."
    assert pdf.file_data is None and png.file_data is None
    assert (pdf.inline_data.data, pdf.inline_data.mime_type, pdf.inline_data.display_name) == (
        PDF,
        "application/pdf",
        "report.pdf",
    )
    assert (png.inline_data.data, png.inline_data.mime_type) == (PNG, "image/png")


async def test_the_content_the_request_was_built_from_keeps_the_address() -> None:
    """What the session holds is not changed: content goes into the request only."""
    request, question = _request()
    answer = request.contents[1]

    with use_conversation(_conversation()):
        await inline_files(request)

    assert question.parts[1].file_data.file_uri == PDF_URL and question.parts[1].inline_data is None
    assert request.contents[0] is not question
    assert request.contents[1] is answer


async def test_a_file_this_server_holds_in_memory_is_not_read_again() -> None:
    file_contents().put("file:f-1:v-1", PDF, url=PDF_URL)
    request = LlmRequest(
        model="gpt-test",
        contents=[types.Content(role="user", parts=[_file(PDF_URL, "application/pdf", "report.pdf")])],
    )

    with use_conversation(InMemoryConversation()):
        await inline_files(request)

    assert request.contents[0].parts[0].inline_data.data == PDF


async def test_a_file_larger_than_a_model_request_may_carry_fails_the_task(monkeypatch) -> None:
    monkeypatch.setattr(file_settings, "model_file_max_bytes", 4)
    request, _ = _request()

    with use_conversation(_conversation()), pytest.raises(TaskFailedError, match="report.pdf"):
        await inline_files(request)


async def test_a_file_that_is_not_a_web_address_is_left_to_the_model() -> None:
    """A provider's own file reference - a ``gs://`` object, an uploaded file's id - is not the SDK's to read."""
    part = _file("gs://bucket/report.pdf", "application/pdf", "report.pdf")
    request = LlmRequest(model="gpt-test", contents=[types.Content(role="user", parts=[part])])

    await inline_files(request)

    assert request.contents[0].parts[0].file_data.file_uri == "gs://bucket/report.pdf"


async def test_the_callback_inlines_and_lets_the_model_run() -> None:
    request, _ = _request()

    with use_conversation(_conversation()):
        assert await inline_files_before_model(None, request) is None

    assert request.contents[0].parts[2].inline_data.data == PNG


async def test_the_aion_model_inlines_before_it_builds_its_request() -> None:
    seen: list = []

    class _Base:
        async def generate_content_async(self, llm_request, stream=False):
            seen.append(llm_request.contents[0].parts[2].inline_data)
            yield "response"

    request, _ = _request()
    model = _file_inlining_lite_llm(_Base)()

    with use_conversation(_conversation()):
        responses = [response async for response in model.generate_content_async(request)]

    assert responses == ["response"]
    assert seen[0].data == PNG


class TestToolMedia:
    """A tool's media the session keeps by its address goes to the model by its content.

    ADK's ``LiteLlm`` sends a tool's media only as ``inline_data`` of the
    function response, so the content is put back there.
    """

    @staticmethod
    def _tool_answer(url: str = PNG_URL) -> types.Content:
        media = types.FunctionResponsePart(
            file_data=types.FunctionResponseFileData(file_uri=url, mime_type="image/png", display_name="chart.png")
        )
        response = types.FunctionResponse(id="call-1", name="snapshot", response={}, parts=[media])
        return types.Content(role="user", parts=[types.Part(function_response=response)])

    async def test_a_tools_media_is_given_back_its_content(self) -> None:
        request = LlmRequest(model="gpt-test", contents=[self._tool_answer()])

        with use_conversation(_conversation()):
            await inline_files(request)

        (media,) = request.contents[0].parts[0].function_response.parts
        assert media.file_data is None
        assert (media.inline_data.data, media.inline_data.mime_type, media.inline_data.display_name) == (
            PNG,
            "image/png",
            "chart.png",
        )

    async def test_the_session_keeps_the_address(self) -> None:
        answer = self._tool_answer()
        request = LlmRequest(model="gpt-test", contents=[answer])

        with use_conversation(_conversation()):
            await inline_files(request)

        assert answer.parts[0].function_response.parts[0].file_data.file_uri == PNG_URL

    @pytest.mark.skipif(
        not hasattr(adk_lite_llm, "_function_response_media_parts"),
        reason="this google-adk's LiteLlm sends no media of a tool to a model",
    )
    async def test_litellm_sends_the_tools_image_after_the_tools_answer(self) -> None:
        """What LiteLlm builds from the inlined request: the tool's answer, then the image as the user's."""
        request = LlmRequest(model="gpt-test", contents=[self._tool_answer()])
        with use_conversation(_conversation()):
            await inline_files(request)

        tool, user = await adk_lite_llm._content_to_message_param(request.contents[0])

        assert tool["role"] == "tool"
        assert user["content"] == [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(PNG).decode()}"}}
        ]


class TestReadingByTheStoredFile:
    """The session names the stored file behind an address; the file is read by that name."""

    class _Recording(InMemoryConversation):
        def __init__(self) -> None:
            super().__init__(files={PNG_URL: PNG})
            self.read = []

        async def read_file(self, part, *, max_bytes=None):
            self.read.append(part)
            return await super().read_file(part, max_bytes=max_bytes)

    @staticmethod
    def _session_naming(url: str):
        from types import SimpleNamespace

        from google.adk.events import Event

        from aion.adk.authoring.files import STORED_FILES_KEY

        event = Event(author="agent", custom_metadata={STORED_FILES_KEY: {url: {"fileId": "file-1", "fileVersionId": "ver-1"}}})
        return SimpleNamespace(session=SimpleNamespace(events=[event]))

    @pytest.mark.parametrize("where", ["message", "tool"])
    async def test_a_file_is_read_by_the_stored_file_the_session_names(self, where: str) -> None:
        from aion.adk.authoring.invocation import reset_adk_ctx, set_adk_ctx
        from aion.core.a2a.files import StoredFile

        content = (
            TestToolMedia._tool_answer()
            if where == "tool"
            else types.Content(role="user", parts=[_file(PNG_URL, "image/png", "chart.png")])
        )
        conversation = self._Recording()
        token = set_adk_ctx(self._session_naming(PNG_URL))
        try:
            with use_conversation(conversation):
                await inline_files(LlmRequest(model="gpt-test", contents=[content]))
        finally:
            reset_adk_ctx(token)

        [read] = conversation.read
        assert StoredFile.of(read) == StoredFile.create("file-1", "ver-1")
