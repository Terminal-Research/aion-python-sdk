"""The SDK's chat model against a model service that answers as Aion's does.

Aion's model service answers every request with HTTP 200 and a stream, a
request without one included, and reports a failure as an error event of
that stream (``tests.unit.support.model_service``). The real ``ChatOpenAI``
and OpenAI client send each request over HTTP to a stand-in for it: every
request is streamed, a call without a stream gets the answer its chunks
make, and a refused file is left out with a note.
"""

from __future__ import annotations

import base64
import json

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.content import create_file_block, create_text_block
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel

from aion.core.agent.left_out_files import CHECK_TEXT
from aion.langgraph.authoring.models import _file_inlining_chat_openai
from tests.unit.support.model_service import REFUSED, AionModelService, text, tool_call

QUESTION = "Look at these."


def _model(service: AionModelService, **kwargs):
    return _file_inlining_chat_openai()(
        model="gpt-test",
        api_key="key",
        base_url="https://models.test/v1",
        http_client=httpx.Client(transport=service.transport),
        http_async_client=httpx.AsyncClient(transport=service.transport),
        max_retries=0,
        **kwargs,
    )


def _file(content: bytes, mime_type: str, name: str) -> dict:
    return create_file_block(base64=base64.b64encode(content).decode(), mime_type=mime_type, filename=name)


def _question(*files: dict) -> list[HumanMessage]:
    return [HumanMessage(content=[create_text_block(text=QUESTION), *files])]


def _note(name: str) -> str:
    return f'[File "{name}" ({REFUSED}) was not accepted by the model and was left out]'


def _graph(node) -> object:
    builder = StateGraph(MessagesState)
    builder.add_node("answer", node)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile()


REPORT = _file(b"%PDF-1.7 report", "application/pdf", "report.pdf")
DATA = _file(b"\x00data", REFUSED, "data.bin")


class TestACallWithoutAStream:
    """The call is answered from a streamed request: the service answers a request without one with a stream."""

    async def test_ainvoke_outside_a_graph(self) -> None:
        service = AionModelService()

        answer = await _model(service).ainvoke("What is 2+2?")

        assert isinstance(answer, AIMessage)
        assert answer.content == "ok"
        assert [request["stream"] for request in service.requests] == [True]

    def test_invoke_outside_a_graph(self) -> None:
        service = AionModelService()

        assert _model(service).invoke("What is 2+2?").content == "ok"
        assert [request["stream"] for request in service.requests] == [True]

    async def test_a_graph_run_that_does_not_stream_its_messages(self) -> None:
        service = AionModelService()
        model = _model(service)

        async def answer(state):
            return {"messages": [await model.ainvoke(state["messages"])]}

        result = await _graph(answer).ainvoke({"messages": [HumanMessage("What is 2+2?")]})

        assert result["messages"][-1].content == "ok"
        assert [request["stream"] for request in service.requests] == [True]

    async def test_a_model_with_streaming_disabled(self) -> None:
        service = AionModelService()

        assert (await _model(service, disable_streaming=True).ainvoke("What is 2+2?")).content == "ok"
        assert [request["stream"] for request in service.requests] == [True]

    async def test_a_tool_call(self) -> None:
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        service = AionModelService(answers=[tool_call("add", {"a": 2, "b": 2})])

        answer = await _model(service).bind_tools([add]).ainvoke("What is 2+2?")

        assert [(call["name"], call["args"]) for call in answer.tool_calls] == [("add", {"a": 2, "b": 2})]

    async def test_structured_output(self) -> None:
        class Answer(BaseModel):
            value: int

        service = AionModelService(answers=[text(json.dumps({"value": 4}))])

        assert await _model(service).with_structured_output(Answer).ainvoke("What is 2+2?") == Answer(value=4)

    async def test_the_refused_file_is_left_out(self) -> None:
        service = AionModelService()

        answer = await _model(service).ainvoke(_question(REPORT, DATA))

        assert answer.content == "ok"
        assert service.described(QUESTION) == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]
        assert {request["stream"] for request in service.requests} == {True}


class TestARefusalInTheStream:
    """A provider's refusal arrives as the stream's error event, with no HTTP status."""

    async def test_a_streamed_call_leaves_the_refused_file_out_and_answers(self) -> None:
        service = AionModelService()

        chunks = [chunk async for chunk in _model(service).astream(_question(REPORT, DATA))]

        assert "".join(str(chunk.content) for chunk in chunks) == "ok"
        assert service.described(QUESTION) == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]

    def test_a_synchronous_streamed_call_too(self) -> None:
        service = AionModelService()

        chunks = list(_model(service).stream(_question(REPORT, DATA)))

        assert "".join(str(chunk.content) for chunk in chunks) == "ok"
        assert service.described(QUESTION) == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]

    async def test_each_file_is_checked_alone_with_a_stream_and_the_one_taken_stays(self) -> None:
        service = AionModelService()
        one, two = _file(b"\x00one", REFUSED, "one.bin"), _file(b"\x00two", REFUSED, "two.bin")

        chunks = [chunk async for chunk in _model(service).astream(_question(REPORT, one, two))]

        assert "".join(str(chunk.content) for chunk in chunks) == "ok"
        checks = [request for request in service.requests if CHECK_TEXT in json.dumps(request["content"])]
        assert [request["stream"] for request in checks] == [True, True, True]
        assert [described for described in service.described(QUESTION) if CHECK_TEXT in described] == [
            ["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT],
        ]
        assert service.described(QUESTION)[-1] == ["report.pdf", _note("one.bin"), _note("two.bin")]

    @pytest.mark.parametrize(
        "failure",
        [
            {"message": "OpenRouter API request failed: 429 Too Many Requests - {}", "type": "api_error", "param": None,
             "code": None},
            {"message": "Model 'gpt-test' is not available for text chat completions.", "type": "invalid_request_error",
             "param": "model", "code": "model_not_supported"},
        ],
        ids=["a provider's rate limit", "the service's own error"],
    )
    async def test_a_failure_that_is_not_a_refusal_is_raised_after_one_request(self, failure: dict) -> None:
        service = AionModelService(failure=failure)

        with pytest.raises(openai.APIError):
            [chunk async for chunk in _model(service).astream(_question(REPORT, DATA))]

        assert len(service.requests) == 1
