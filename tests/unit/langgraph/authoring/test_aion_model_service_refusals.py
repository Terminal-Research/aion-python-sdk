"""The SDK's chat model leaves out a refused file when the model service answers as Aion's does.

Aion's model service answers every chat completion with HTTP 200 and a
stream - a request without one included - and reports a failure as an error
event of that stream, with no HTTP status. A provider's refusal is an
``api_error`` whose message starts with the provider's status. Here the real
``ChatOpenAI`` and OpenAI client send each request over HTTP to a stand-in
for that service, which refuses a request carrying a file of ``REFUSED``.
"""

from __future__ import annotations

import base64
import json
from typing import Optional

import httpx
import openai
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.messages.content import create_file_block, create_text_block

from aion.core.agent.left_out_files import CHECK_TEXT
from aion.langgraph.authoring.models import _file_inlining_chat_openai

REFUSED = "application/x-refused"
PROVIDER_REFUSAL = (
    'OpenRouter API request failed: 400 Bad Request - {"error":{"message":"Provider returned error","code":400}}'
)
QUESTION = "Look at these."


def _event(data: dict) -> str:
    return f"data:{json.dumps(data)}\n\n"


def _chunk(content: str, finish_reason: Optional[str] = None) -> dict:
    return {
        "id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
    }


class _AionModelService:
    """Answers as Aion's model service: HTTP 200 and a stream, a failure as its error event.

    ``requests`` holds, for each request, whether it asked for a stream and
    what its user messages carried.
    """

    def __init__(self, failure: Optional[dict] = None) -> None:
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
            events = [{"error": {"message": PROVIDER_REFUSAL, "type": "api_error", "param": None, "code": None}}]
        else:
            events = [_chunk("o"), _chunk("k"), _chunk("", "stop")]
        text = "".join(_event(event) for event in events) + "data:[DONE]\n\n"
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


def _model(service: _AionModelService):
    transport = httpx.MockTransport(service)
    return _file_inlining_chat_openai()(
        model="gpt-test",
        api_key="key",
        base_url="https://models.test/v1",
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
        max_retries=0,
    )


def _file(content: bytes, mime_type: str, name: str) -> dict:
    return create_file_block(base64=base64.b64encode(content).decode(), mime_type=mime_type, filename=name)


def _question(*files: dict) -> list[HumanMessage]:
    return [HumanMessage(content=[create_text_block(text=QUESTION), *files])]


def _note(name: str) -> str:
    return f'[File "{name}" ({REFUSED}) was not accepted by the model and was left out]'


REPORT = _file(b"%PDF-1.7 report", "application/pdf", "report.pdf")
DATA = _file(b"\x00data", REFUSED, "data.bin")


async def test_a_streamed_call_leaves_the_refused_file_out_and_answers() -> None:
    service = _AionModelService()

    chunks = [chunk async for chunk in _model(service).astream(_question(REPORT, DATA))]

    assert "".join(str(chunk.content) for chunk in chunks) == "ok"
    assert service.described() == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]


def test_a_synchronous_streamed_call_too() -> None:
    service = _AionModelService()

    chunks = list(_model(service).stream(_question(REPORT, DATA)))

    assert "".join(str(chunk.content) for chunk in chunks) == "ok"
    assert service.described() == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]


async def test_each_file_is_checked_alone_with_a_stream_and_the_one_taken_stays() -> None:
    service = _AionModelService()
    one, two = _file(b"\x00one", REFUSED, "one.bin"), _file(b"\x00two", REFUSED, "two.bin")

    chunks = [chunk async for chunk in _model(service).astream(_question(REPORT, one, two))]

    assert "".join(str(chunk.content) for chunk in chunks) == "ok"
    checks = [request for request in service.requests if CHECK_TEXT in json.dumps(request["content"])]
    assert [request["stream"] for request in checks] == [True, True, True]
    assert [described for described in service.described() if CHECK_TEXT in described] == [
        ["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT],
    ]
    assert service.described()[-1] == ["report.pdf", _note("one.bin"), _note("two.bin")]


@pytest.mark.parametrize(
    "failure",
    [
        {"message": "OpenRouter API request failed: 429 Too Many Requests - {}", "type": "api_error", "param": None, "code": None},
        {"message": "Model 'gpt-test' is not available for text chat completions.", "type": "invalid_request_error",
         "param": "model", "code": "model_not_supported"},
    ],
    ids=["a provider's rate limit", "the service's own error"],
)
async def test_a_failure_that_is_not_a_refusal_is_raised_after_one_request(failure: dict) -> None:
    service = _AionModelService(failure=failure)

    with pytest.raises(openai.APIError):
        [chunk async for chunk in _model(service).astream(_question(REPORT, DATA))]

    assert len(service.requests) == 1
