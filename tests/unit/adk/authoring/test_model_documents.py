"""A document reaches the model in the request's body, through ADK's real ``LiteLlm``.

For an OpenAI model, ADK's ``LiteLlm`` uploads a document to the provider's
Files API and names it in the request by id, on every request. Behind Aion's
model service that upload has nowhere to go, and it would leave a copy at the
provider each time. ``aion_lite_llm`` hands ``LiteLlm`` a text mark instead
and its client puts the document into the finished request.

These tests run the real ``LiteLlm`` and stop at ``litellm.acompletion``: what
is asserted is the request that would have been sent.
"""

from __future__ import annotations

import base64

import litellm
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from aion.adk.authoring import models
from aion.adk.authoring.models import aion_lite_llm
from aion.core.agent.model_files import HeldDocuments
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import TaskFailedError

PDF_URL = "https://files.test/report?grant=abc"
PNG_URL = "https://files.test/chart?grant=abc"
PDF, PNG = b"%PDF-1.7 report", b"\x89PNG chart"
PDF_PART = {
    "type": "file",
    "file": {"filename": "report.pdf", "file_data": f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}"},
}


class _Sent(Exception):
    """Raised in place of the network call, once the request is captured."""


class _Config:
    def litellm_kwargs(self):
        return {"api_base": "https://models.test/v1", "api_key": "placeholder"}


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """The requests ``litellm.acompletion`` was called with; any file upload fails the test."""
    requests: list[dict] = []

    async def acompletion(**kwargs):
        requests.append(kwargs)
        raise _Sent()

    async def acreate_file(**kwargs):
        raise AssertionError("a document was uploaded to the provider's Files API")

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    monkeypatch.setattr(litellm, "acreate_file", acreate_file)
    monkeypatch.setattr(models, "aion_openai_config", lambda: _Config())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "jwt")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda headers=None: {})
    return requests


def _file(url: str, mime_type: str, name: str) -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=url, mime_type=mime_type, display_name=name))


async def _send(*parts: types.Part, stream: bool = False) -> None:
    request = LlmRequest(model="openai/gpt-test", contents=[types.Content(role="user", parts=list(parts))])
    model = aion_lite_llm("openai/gpt-test")
    with use_conversation(InMemoryConversation(files={PDF_URL: PDF, PNG_URL: PNG})):
        with pytest.raises(_Sent):
            async for _ in model.generate_content_async(request, stream=stream):
                pass


def _user_content(sent: list[dict]) -> list:
    [request] = sent
    return [message for message in request["messages"] if message["role"] == "user"][-1]["content"]


@pytest.mark.parametrize("stream", [False, True])
async def test_a_stored_document_is_in_the_body_of_the_request(sent, stream) -> None:
    """Text, document and image of one message, in order; the image as ADK sends it."""
    await _send(
        types.Part(text="Summarize these."),
        _file(PDF_URL, "application/pdf", "report.pdf"),
        _file(PNG_URL, "image/png", "chart.png"),
        stream=stream,
    )

    assert _user_content(sent) == [
        {"type": "text", "text": "Summarize these."},
        PDF_PART,
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(PNG).decode()}"}},
    ]


async def test_a_message_that_is_only_a_document_carries_it_too(sent) -> None:
    """ADK sends a one-part message as a plain string; the document still takes the mark's place."""
    await _send(_file(PDF_URL, "application/pdf", "report.pdf"))

    assert _user_content(sent) == [PDF_PART]


async def test_a_document_sent_inline_is_not_uploaded_either(sent) -> None:
    """Without a file storage the document is in the session as bytes; it travels the same way."""
    await _send(
        types.Part(text="Summarize this."),
        types.Part(inline_data=types.Blob(data=PDF, mime_type="application/pdf", display_name="report.pdf")),
    )

    assert _user_content(sent) == [{"type": "text", "text": "Summarize this."}, PDF_PART]


async def test_a_text_file_is_left_to_adk(sent) -> None:
    """ADK puts a text file into the request as text; it is not a document to hold."""
    await _send(
        types.Part(text="Read this."),
        types.Part(inline_data=types.Blob(data=b"plain notes", mime_type="text/plain")),
    )

    assert _user_content(sent) == [{"type": "text", "text": "Read this."}, {"type": "text", "text": "plain notes"}]


def test_a_document_whose_mark_is_missing_fails_the_task() -> None:
    """A request that lost the mark would reach the model without the file: it is not sent."""
    held = HeldDocuments()
    held.hold(PDF, mime_type="application/pdf", name="report.pdf")

    with pytest.raises(TaskFailedError, match="report.pdf"):
        held.place([{"role": "user", "content": "Summarize this."}])


def test_a_mark_inside_other_text_keeps_the_text_around_it() -> None:
    held = HeldDocuments()
    mark = held.hold(PDF, mime_type="application/pdf", name="report.pdf")

    [message] = held.place([{"role": "user", "content": f"Before. {mark} After."}])

    assert message["content"] == [
        {"type": "text", "text": "Before. "},
        PDF_PART,
        {"type": "text", "text": " After."},
    ]


def test_messages_are_untouched_when_nothing_is_held() -> None:
    messages = [{"role": "user", "content": "hello"}]

    assert HeldDocuments().place(messages) is messages
