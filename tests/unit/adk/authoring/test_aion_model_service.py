"""The SDK's ADK model against a model service that answers as Aion's does.

Aion's model service answers every request with HTTP 200 and a stream, a
request without one included, and reports a failure as an error event of
that stream (``tests.unit.support.model_service``). The real ``LiteLlm``,
LiteLLM and OpenAI client send each request over HTTP to a stand-in for it:
every request is streamed, a request without a stream gets the response its
chunks make, and a refused file is left out with a note.
"""

from __future__ import annotations

import json

import httpx
import litellm
import openai
import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from aion.adk.authoring import models
from aion.adk.authoring.models import aion_lite_llm
from aion.core.agent.left_out_files import CHECK_TEXT
from tests.unit.support.model_service import REFUSED, AionModelService, text, tool_call

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


class _Config:
    def litellm_kwargs(self):
        return {"api_base": "https://models.test/v1", "api_key": "placeholder"}


def _serve(monkeypatch, service: AionModelService) -> AionModelService:
    send = litellm.acompletion

    async def acompletion(**kwargs):
        client = openai.AsyncOpenAI(
            api_key="key", base_url="https://models.test/v1", max_retries=0,
            http_client=httpx.AsyncClient(transport=service.transport),
        )
        if kwargs.get("tools"):
            # LiteLLM loads its MCP handler for a request with tools, and that
            # needs an mcp release newer than the one langchain-mcp-adapters
            # allows in an environment with both frameworks; it has no part here.
            kwargs["_skip_mcp_handler"] = True
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


def _request(*parts: types.Part) -> LlmRequest:
    return LlmRequest(model="openai/gpt-test", contents=[types.Content(role="user", parts=[types.Part(text=QUESTION), *parts])])


async def _ask_streamed(*parts: types.Part) -> str:
    """The streamed answer to a question with ``parts``, as ADK's runner asks for it."""
    responses = [
        response async for response in aion_lite_llm("openai/gpt-test").generate_content_async(_request(*parts), stream=True)
    ]
    return "".join(part.text or "" for response in responses if response.partial for part in response.content.parts)


def _note(name: str) -> str:
    return f'[File "{name}" ({REFUSED}) was not accepted by the model and was left out]'


REPORT = _inline(b"%PDF-1.7 report", "application/pdf", "report.pdf")
DATA = _inline(b"\x00data", REFUSED, "data.bin")


class TestARequestWithoutAStream:
    """The request is streamed and answered with the response its chunks make."""

    async def test_the_model_answers_once(self, monkeypatch) -> None:
        service = _serve(monkeypatch, AionModelService())

        responses = [
            response async for response in aion_lite_llm("openai/gpt-test").generate_content_async(_request(), stream=False)
        ]

        assert [response.content.parts[0].text for response in responses] == ["ok"]
        assert [request["stream"] for request in service.requests] == [True]

    async def test_the_refused_file_is_left_out(self, monkeypatch) -> None:
        service = _serve(monkeypatch, AionModelService())

        [response] = [
            response
            async for response in aion_lite_llm("openai/gpt-test").generate_content_async(_request(REPORT, DATA), stream=False)
        ]

        assert response.content.parts[0].text == "ok"
        assert service.described(QUESTION) == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]

    async def test_an_agent_behind_an_agent_tool(self, monkeypatch) -> None:
        """ADK runs the agent of an ``AgentTool`` without a stream, whatever the caller's run asks for."""
        from google.adk.agents import LlmAgent
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools.agent_tool import AgentTool

        service = _serve(monkeypatch, AionModelService(answers=[
            tool_call("helper", {"request": "What is 2+2?"}),
            text("4"),
            text("The helper says 4."),
        ]))
        helper = LlmAgent(name="helper", model=aion_lite_llm("openai/gpt-test"), instruction="Answer the question.")
        root = LlmAgent(
            name="root", model=aion_lite_llm("openai/gpt-test"), instruction="Ask the helper.", tools=[AgentTool(agent=helper)],
        )
        runner = Runner(app_name="app", agent=root, session_service=InMemorySessionService())
        session = await runner.session_service.create_session(app_name="app", user_id="user")

        answers = [
            part.text
            async for event in runner.run_async(
                user_id="user", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="Ask the helper what 2+2 is.")]),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            )
            if event.author == "root" and not event.partial and event.content
            for part in event.content.parts
            if part.text
        ]

        assert answers == ["The helper says 4."]
        assert [request["stream"] for request in service.requests] == [True, True, True]


class TestARefusalInTheStream:
    """A provider's refusal arrives as the stream's error event, with no HTTP status."""

    @pytest.mark.parametrize("refusal", REFUSALS.values(), ids=REFUSALS.keys())
    async def test_the_refused_file_is_left_out_and_the_request_answers(self, monkeypatch, refusal: str) -> None:
        service = _serve(monkeypatch, AionModelService(refusal=refusal))

        assert await _ask_streamed(REPORT, DATA) == "ok"
        assert service.described(QUESTION) == [["report.pdf", "data.bin"], ["report.pdf", _note("data.bin")]]

    async def test_each_file_is_checked_alone_with_a_stream_and_the_one_taken_stays(self, monkeypatch) -> None:
        service = _serve(monkeypatch, AionModelService())
        one, two = _inline(b"\x00one", REFUSED, "one.bin"), _inline(b"\x00two", REFUSED, "two.bin")

        assert await _ask_streamed(REPORT, one, two) == "ok"
        checks = [request for request in service.requests if CHECK_TEXT in json.dumps(request["content"])]
        assert [request["stream"] for request in checks] == [True, True, True]
        assert [described for described in service.described(QUESTION) if CHECK_TEXT in described] == [
            ["two.bin", CHECK_TEXT], ["one.bin", CHECK_TEXT], ["report.pdf", CHECK_TEXT],
        ]
        assert service.described(QUESTION)[-1] == ["report.pdf", _note("one.bin"), _note("two.bin")]

    async def test_a_provider_failure_that_is_not_a_refusal_is_raised_after_one_request(self, monkeypatch) -> None:
        failure = {"message": "OpenRouter API request failed: 429 Too Many Requests - {}", "type": "api_error", "param": None,
                   "code": None}
        service = _serve(monkeypatch, AionModelService(failure=failure))

        with pytest.raises(openai.APIError) as raised:
            await _ask_streamed(REPORT, DATA)

        assert raised.value.status_code == 429
        assert len(service.requests) == 1
