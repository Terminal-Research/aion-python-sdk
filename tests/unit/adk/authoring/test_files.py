"""A model gets the files of a conversation by their content, not their address.

A stored file is ``file_data`` with an address in the session. No provider
can be relied on to fetch a download link of the deployment's storage, and
ADK's ``LiteLlm`` refuses or drops a document given by address. ``inline_files``
puts the content into the model request and leaves the session as it is.
"""

from __future__ import annotations

import pytest
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
