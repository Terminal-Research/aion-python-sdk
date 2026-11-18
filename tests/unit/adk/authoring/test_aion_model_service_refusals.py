"""The SDK's ADK model leaves out a refused file when the model service answers as Aion's does.

Aion's model service answers every chat completion with HTTP 200 and a
stream - a request without one included - and reports a failure as an error
event of that stream, with no HTTP status. A provider's refusal is an
``api_error`` whose message starts with the provider's status. Here the real
``LiteLlm``, LiteLLM and OpenAI client send each request over HTTP to a
stand-in for that service, which refuses a request carrying a file of
``REFUSED``. ADK's runner asks the model for a stream.
"""

from __future__ import annotations

import json
from typing import Optional

import httpx
import litellm
import openai
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from aion.adk.authoring import models
from aion.adk.authoring.models import aion_lite_llm
from aion.core.agent.left_out_files import CHECK_TEXT

REFUSED = "application/x-refused"
QUESTION = "Look at these."
OPENAI_ANSWER = '{"error":{"message":"Invalid file data","type":"invalid_request_error","code":"invalid_value"}}'
REFUSALS = {
    "an OpenAI model": (
        "OpenRouter API request failed: 400 Bad Request - "
        + json.dumps({"error": {"message": "Provider returned error", "code": 400, "metadata": {"raw": OPENAI_ANSWER}}})
    ),
    "another provider": (
        'OpenRouter API request failed: 400 Bad Request - {"error":{"message":"Provider returned error","code":400}}'
    ),
}
"""A provider's refusal as the service words it; LiteLLM reads a refusal from the first by its words alone."""


def _chunk(content: str, finish_reason: Optional[str] = None) -> dict:
    return {
        "id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
    }


class _AionModelService:
    """Answers as Aion's model service: HTTP 200 and a stream, a failure as its error event."""

    def __init__(self, refusal: str = REFUSALS["another provider"], failure: Optional[dict] = None) -> None:
        self.refusal = refusal
        self.failure = failure
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = [
            part
            for message in body["messages"]
            if message["role"] == "user"
            for part in (
                [{"type": "text", "text": message["content"]}] if isinstance(message["content"], str) else message["content"]
            )
        ]
        self.requests.append({"stream": body.get("stream", False), "content": content})
        if self.failure is not None:
            events = [{"error": self.failure}]
        elif any(f"data:{REFUSED};" in json.dumps(part) for part in content):
            events = [{"error": {"message": self.refusal, "type": "api_error", "param": None, "code": None}}]
        else:
            events = [_chunk("ok"), _chunk("", "stop")]
        text = "".join(f"data:{json.dumps(event)}\n\n" for event in events) + "data:[DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

    def described(self) -> list[list[str]]:
        """Each request's files by name and its texts but the question."""
        return [
            [
                part["file"]["filename"] if part["type"] == "file" else part["text"]
                for part in request["content"]
                if not (part["type"] == "text" and part["text"] == QUESTION)
            ]
            for request in self.requests
        ]


class _Config:
    def litellm_kwargs(self):
        return {"api_base": "https://models.test/v1", "api_key": "placeholder"}


def _serve(monkeypatch, service: _AionModelService) -> _AionModelService:
    send = litellm.acompletion

    async def acompletion(**kwargs):
        client = openai.AsyncOpenAI(
            api_key="key", base_url="https://models.test/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(service)),
        )
        return await send(**kwargs, client=client)

    async def acreate_file(**kwargs):
        raise AssertionError("a document was uploaded to the provider's Files API")

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    monkeypatch.setattr(litellm, "acreate_file", acreate_file)
    monkeypatch.setattr(models, "aion_openai_config", lambda: _Config())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "jwt")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda headers=None: {})
    return service


def _inline(content: bytes, mime_type: str, name: str) -> types.Part:
    return types.Part(inline_data=types.Blob(data=content, mime_type=mime_type, display_name=name))


async def _ask(*parts: types.Part) -> str:
    """The streamed answer to a question with ``parts``, as ADK's runner asks for it."""
    request = LlmRequest(model="openai/gpt-test", contents=[types.Content(role="user", parts=[types.Part(text=QUESTION), *parts])])
    responses = [response async for response in aion_lite_llm("openai/gpt-test").generate_content_async(request, stream=True)]
    return "".join(part.text or "" for response in responses if response.partial for part in response.content.parts)


def _note(name: str) -> str:
    return f'[File "{name}" ({REFUSED}) was not accepted by the model and was left out]'


REPORT = _inline(b"%PDF-1.7 report", "application/pdf", "report.pdf")
DATA = _inline(b"\x00data", REFUSED, "data.bin")


@pytest.mark.parametrize("refusal", REFUSALS.values(), ids=REFUSALS.keys())
async def test_the_refused_file_is_left_out_and_the_request_answers(monkeypatch, refusal: str) -> None:
    service = _serve(monkeypatch, _AionModelService(refusal=refusal))

    assert await _ask(REPORT, DATA) == "ok"
    assert service.described() == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]


async def test_each_file_is_checked_alone_with_a_stream_and_the_one_taken_stays(monkeypatch) -> None:
    service = _serve(monkeypatch, _AionModelService())
    one, two = _inline(b"\x00one", REFUSED, "one.bin"), _inline(b"\x00two", REFUSED, "two.bin")

    assert await _ask(REPORT, one, two) == "ok"
    checks = [request for request in service.requests if CHECK_TEXT in json.dumps(request["content"])]
    assert [request["stream"] for request in checks] == [True, True, True]
    assert [described for described in service.described() if CHECK_TEXT in described] == [
        ["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT],
    ]
    assert service.described()[-1] == ["report.pdf", _note("one.bin"), _note("two.bin")]


async def test_a_provider_failure_that_is_not_a_refusal_is_raised_after_one_request(monkeypatch) -> None:
    failure = {"message": "OpenRouter API request failed: 429 Too Many Requests - {}", "type": "api_error", "param": None, "code": None}
    service = _serve(monkeypatch, _AionModelService(failure=failure))

    with pytest.raises(openai.APIError) as raised:
        await _ask(REPORT, DATA)

    assert raised.value.status_code == 429

    assert len(service.requests) == 1
