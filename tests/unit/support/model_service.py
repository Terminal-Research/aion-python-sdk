"""A stand-in for Aion's model service over HTTP, answering as the service does.

Aion's model service answers every chat completion with HTTP 200 and a
stream - a request without one included - and reports a failure as an error
event of that stream, with no HTTP status. A provider's refusal is an
``api_error`` whose message starts with the provider's status. The SDK's
models are tested against this stand-in through the real OpenAI client (and
LiteLLM, for ADK), each request sent over HTTP to ``transport``.
"""

from __future__ import annotations

import json
from typing import Optional

import httpx

REFUSED = "application/x-refused"
"""The media type of a file the stand-in's provider refuses."""

PROVIDER_REFUSAL = (
    'OpenRouter API request failed: 400 Bad Request - {"error":{"message":"Provider returned error","code":400}}'
)
"""A provider's refusal as the service words it."""


def _chunk(delta: dict, finish_reason: Optional[str] = None) -> dict:
    return {
        "id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": finish_reason}],
    }


def text(content: str) -> list[dict]:
    """The chunks of an answer that is ``content``, in two pieces."""
    middle = len(content) // 2
    return [_chunk({"content": content[:middle]}), _chunk({"content": content[middle:]}), _chunk({"content": ""}, "stop")]


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> list[dict]:
    """The chunks of an answer that calls the tool ``name``, its arguments in two pieces."""
    encoded = json.dumps(arguments)
    middle = len(encoded) // 2
    return [
        _chunk({"content": None, "tool_calls": [
            {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": encoded[:middle]}},
        ]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": encoded[middle:]}}]}),
        _chunk({}, "tool_calls"),
    ]


class AionModelService:
    """Answers as Aion's model service: HTTP 200 and a stream, a failure as its error event.

    A request that carries a file of ``REFUSED`` gets ``refusal``; any other
    gets the next of ``answers``, then "ok". ``requests`` holds, for each
    request, whether it asked for a stream and what its user messages carried.
    """

    def __init__(
        self,
        *,
        answers: Optional[list[list[dict]]] = None,
        refusal: str = PROVIDER_REFUSAL,
        failure: Optional[dict] = None,
    ) -> None:
        self.answers = list(answers or [])
        self.refusal = refusal
        self.failure = failure
        self.requests: list[dict] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

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
            events = self.answers.pop(0) if self.answers else text("ok")
        stream = "".join(f"data:{json.dumps(event)}\n\n" for event in events) + "data:[DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=stream)

    def described(self, question: str) -> list[list[str]]:
        """Each request's files by name and its texts but ``question``."""
        return [
            [
                part["file"]["filename"] if part["type"] == "file" else part["text"]
                for part in request["content"]
                if part["type"] in ("file", "text") and not (part["type"] == "text" and part["text"] == question)
            ]
            for request in self.requests
        ]
