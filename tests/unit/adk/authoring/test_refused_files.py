"""The SDK's ADK model leaves out a file its model refuses, and the request answers.

The model is the one ``aion_lite_llm`` builds. The real ``LiteLlm`` builds
each request, and the client of ``aion_lite_llm`` puts documents into its
body; only ``litellm.acompletion`` is faked, by a stand-in model service
that refuses a request carrying a file of ``REFUSED``. What ADK itself makes
of the responses - a streamed one, a tool's media - is taken from a stand-in
model class under the SDK's.
"""

from __future__ import annotations

import base64

import litellm
import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from aion.adk.authoring import models
from aion.adk.authoring.models import _file_inlining_lite_llm, aion_lite_llm
from aion.core.agent.left_out_files import CHECK_TEXT

REFUSED = "application/x-refused"
REFUSED_INLINE = "video/x-refused"
"""A refused type ``LiteLlm`` puts into the request itself: the stand-in model class sees it as ``inline_data``."""
PDF, PNG, DAT = b"%PDF-1.7 report", b"\x89PNG chart", b"\x00data"


def _note(name: str, mime_type: str) -> str:
    return f'[File "{name}" ({mime_type}) was not accepted by the model and was left out]'


def _inline(content: bytes, mime_type: str, name: str) -> types.Part:
    return types.Part(inline_data=types.Blob(data=content, mime_type=mime_type, display_name=name))


class _ModelService:
    """Stands in for LiteLLM's ``acompletion``: refuses a request with a file of ``REFUSED``, otherwise answers "ok"."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.requests: list[list] = []

    async def __call__(self, **kwargs):
        content = [
            part
            for message in kwargs["messages"]
            if message["role"] == "user"
            for part in ([{"type": "text", "text": message["content"]}] if isinstance(message["content"], str) else message["content"])
        ]
        self.requests.append(content)
        if self.failure is not None:
            raise self.failure
        if any(f"data:{REFUSED};" in str(part) for part in content):
            raise litellm.BadRequestError(message="Invalid file data", model=kwargs["model"], llm_provider="openai")
        if kwargs.get("stream"):
            return _streamed("ok")
        return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "ok"}}])


async def _streamed(text: str):
    yield litellm.ModelResponseStream(
        choices=[{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
    )


class _Config:
    def litellm_kwargs(self):
        return {"api_base": "https://models.test/v1", "api_key": "placeholder"}


def _serve(monkeypatch, service: _ModelService) -> _ModelService:
    async def acreate_file(**kwargs):
        raise AssertionError("a document was uploaded to the provider's Files API")

    monkeypatch.setattr(litellm, "acompletion", service)
    monkeypatch.setattr(litellm, "acreate_file", acreate_file)
    monkeypatch.setattr(models, "aion_openai_config", lambda: _Config())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "jwt")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda headers=None: {})
    return service


@pytest.fixture
def service(monkeypatch) -> _ModelService:
    return _serve(monkeypatch, _ModelService())


async def _ask(*parts: types.Part) -> list[LlmResponse]:
    request = LlmRequest(model="openai/gpt-test", contents=[types.Content(role="user", parts=list(parts))])
    return [response async for response in aion_lite_llm("openai/gpt-test").generate_content_async(request)]


def _described(request: list) -> list[str]:
    """Each part of a request: a file by its name or type, a text by its text."""
    described = []
    for part in request:
        if part["type"] == "text":
            described.append(part["text"])
        elif part["type"] == "image_url":
            described.append(part["image_url"]["url"].split(";")[0])
        else:
            described.append(part["file"]["filename"])
    return described


async def test_the_refused_file_is_left_out_and_the_others_reach_the_model(service: _ModelService) -> None:
    [response] = await _ask(
        types.Part(text="Look at these."),
        _inline(PDF, "application/pdf", "report.pdf"),
        _inline(DAT, REFUSED, "data.bin"),
        _inline(PNG, "image/png", "chart.png"),
    )

    assert response.content.parts[0].text == "ok"
    assert [_described(request) for request in service.requests] == [
        ["Look at these.", "report.pdf", "data.bin", "data:image/png"],
        ["Look at these.", "report.pdf", "data.bin", _note("chart.png", "image/png")],
        ["Look at these.", "report.pdf", _note("data.bin", REFUSED), "data:image/png"],
    ]


async def test_a_later_request_leaves_the_file_out_at_the_first_try(service: _ModelService) -> None:
    await _ask(types.Part(text="Look."), _inline(DAT, REFUSED, "data.bin"))
    service.requests.clear()

    await _ask(types.Part(text="Again."), _inline(DAT, REFUSED, "data.bin"))

    [request] = service.requests
    assert _described(request) == ["Again.", _note("data.bin", REFUSED)]


async def test_a_file_the_model_takes_stays_when_two_others_are_refused(service: _ModelService) -> None:
    [response] = await _ask(
        types.Part(text="Look at these."),
        _inline(PDF, "application/pdf", "report.pdf"),
        _inline(DAT, REFUSED, "one.bin"),
        _inline(DAT + b"2", REFUSED, "two.bin"),
    )

    assert response.content.parts[0].text == "ok"
    checks = [_described(request) for request in service.requests if CHECK_TEXT in _described(request)]
    assert checks == [["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT]]
    assert _described(service.requests[-1]) == [
        "Look at these.", "report.pdf", _note("one.bin", REFUSED), _note("two.bin", REFUSED),
    ]


async def test_a_failure_that_is_not_about_a_file_is_raised_after_one_request(monkeypatch) -> None:
    service = _serve(
        monkeypatch,
        _ModelService(failure=litellm.RateLimitError(message="slow down", llm_provider="openai", model="gpt-test")),
    )

    with pytest.raises(litellm.RateLimitError):
        await _ask(types.Part(text="Look."), _inline(DAT, REFUSED, "data.bin"))

    assert len(service.requests) == 1


class _Refusing:
    """A model class that refuses a request with a file of ``REFUSED`` or ``REFUSED_INLINE`` anywhere, and streams "o", "k" otherwise."""

    def __init__(self, **kwargs) -> None:
        self.model = "stand-in"
        self.requests: list[LlmRequest] = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        for content in llm_request.contents:
            for part in content.parts or []:
                media = list(part.function_response.parts or []) if part.function_response is not None else [part]
                if any(item.inline_data is not None and item.inline_data.mime_type in (REFUSED, REFUSED_INLINE) for item in media):
                    raise litellm.BadRequestError(message="Invalid file data", model="stand-in", llm_provider="openai")
        for piece in ("o", "k"):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=piece)]), partial=True)


async def test_only_the_try_that_goes_through_gives_responses() -> None:
    model = _file_inlining_lite_llm(_Refusing)()
    request = LlmRequest(
        model="stand-in",
        contents=[types.Content(role="user", parts=[types.Part(text="Look."), _inline(DAT, REFUSED_INLINE, "clip.bin")])],
    )

    responses = [response async for response in model.generate_content_async(request, stream=True)]

    assert [response.content.parts[0].text for response in responses] == ["o", "k"]
    assert len(model.requests) == 2


async def test_a_tools_media_the_model_refuses_leaves_the_function_response() -> None:
    """The function response goes on without the media; the note follows it as text."""
    media = types.FunctionResponsePart(
        inline_data=types.FunctionResponseBlob(data=DAT, mime_type=REFUSED, display_name="capture.bin")
    )
    answer = types.FunctionResponse(id="call-1", name="capture", response={"result": "done"}, parts=[media])
    model = _file_inlining_lite_llm(_Refusing)()
    request = LlmRequest(
        model="stand-in",
        contents=[
            types.Content(role="user", parts=[types.Part(text="Capture it.")]),
            types.Content(role="user", parts=[types.Part(function_response=answer)]),
        ],
    )

    [_ async for _ in model.generate_content_async(request)]

    sent, note = model.requests[-1].contents[1].parts
    assert sent.function_response.name == "capture" and not sent.function_response.parts
    assert note.text == _note("capture.bin", REFUSED)


async def test_the_document_left_out_is_not_put_into_the_request(service: _ModelService) -> None:
    """Held documents follow the request: the one left out is neither in the body nor missed."""
    await _ask(types.Part(text="Look."), _inline(PDF, "application/pdf", "report.pdf"), _inline(DAT, REFUSED, "data.bin"))

    last = service.requests[-1]
    assert last[1] == {
        "type": "file",
        "file": {"filename": "report.pdf", "file_data": f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}"},
    }
    assert last[2] == {"type": "text", "text": _note("data.bin", REFUSED)}


async def test_a_stored_file_the_model_refused_is_not_read_in_a_later_turn() -> None:
    """The session names the stored file behind the link; a refused version is left out unread."""
    from types import SimpleNamespace

    from google.adk.events import Event

    from aion.adk.authoring.files import STORED_FILES_KEY
    from aion.adk.authoring.invocation import reset_adk_ctx, set_adk_ctx
    from aion.core.a2a.file_contents import FileContents, keeping_file_contents
    from aion.core.agent.testing import InMemoryConversation, use_conversation

    url = "https://files.test/clip?grant=abc"

    class Recording(InMemoryConversation):
        def __init__(self) -> None:
            super().__init__(files={url: DAT})
            self.reads = 0

        async def read_file(self, part, *, max_bytes=None):
            self.reads += 1
            return await super().read_file(part, max_bytes=max_bytes)

    named = Event(author="agent", custom_metadata={STORED_FILES_KEY: {url: {"fileId": "f-1", "fileVersionId": "v-1"}}})
    conversation = Recording()
    model = _file_inlining_lite_llm(_Refusing)()

    def request() -> LlmRequest:
        part = types.Part(file_data=types.FileData(file_uri=url, mime_type=REFUSED_INLINE, display_name="clip.bin"))
        return LlmRequest(model="stand-in", contents=[types.Content(role="user", parts=[types.Part(text="Look."), part])])

    token = set_adk_ctx(SimpleNamespace(session=SimpleNamespace(events=[named])))
    try:
        with use_conversation(conversation):
            for _ in range(2):
                with keeping_file_contents(FileContents()):
                    [_ async for _ in model.generate_content_async(request())]
    finally:
        reset_adk_ctx(token)

    assert conversation.reads == 1
    look, note = model.requests[-1].contents[0].parts
    assert note.text == _note("clip.bin", REFUSED_INLINE)
