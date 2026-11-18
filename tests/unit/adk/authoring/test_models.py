import asyncio
import json
from types import ModuleType
import sys

import pytest
import httpx

from aion.adk.authoring.models import aion_lite_llm
import aion.adk.authoring.models as models
from aion.core.exceptions import AionDaemonIdentityRequired
from aion.core.runtime.context import AionRuntimeContext


class FakeConfig:
    def __init__(self):
        self.api_key = lambda: "jwt-token"

    def litellm_kwargs(self):
        return {
            "api_base": "https://api.example.test/v1",
            "api_key": self.api_key,
        }


def test_aion_lite_llm_configures_google_adk_litellm(monkeypatch):
    captured = {}

    class FakeLiteLlm:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeLiteLLMClient:
        pass

    google = ModuleType("google")
    google_adk = ModuleType("google.adk")
    google_adk_models = ModuleType("google.adk.models")
    lite_llm = ModuleType("google.adk.models.lite_llm")
    lite_llm.LiteLlm = FakeLiteLlm
    lite_llm.LiteLLMClient = FakeLiteLLMClient

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", google_adk)
    monkeypatch.setitem(sys.modules, "google.adk.models", google_adk_models)
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", lite_llm)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        models,
        "aion_model_request_headers",
        lambda existing=None: {
            **(existing or {}),
            "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
        },
    )

    result = aion_lite_llm(
        "model-id-from-control-plane",
        temperature=0.2,
    )

    assert isinstance(result, FakeLiteLlm)
    assert captured == {
        "model": "model-id-from-control-plane",
        "api_base": "https://api.example.test/v1",
        "llm_client": captured["llm_client"],
        "temperature": 0.2,
    }
    assert isinstance(captured["llm_client"], FakeLiteLLMClient)

    litellm = _fake_litellm(monkeypatch)
    completion_calls = litellm.completion_calls
    acompletion_calls = litellm.acompletion_calls

    sync_result = captured["llm_client"].completion(
        model="openai/model-id",
        messages=[],
        tools=None,
        api_key="stale-token",
    )
    async_result = asyncio.run(
        captured["llm_client"].acompletion(
            model="openai/model-id",
            messages=[],
            tools=None,
            api_key="stale-token",
        )
    )

    # The model service answers every request with a stream: one without a
    # stream is streamed and answered with what its chunks make.
    assert sync_result == ("assembled", ["completion chunk"])
    assert async_result == ("assembled", ["acompletion chunk"])
    assert completion_calls[0]["stream"] is True
    assert acompletion_calls[0]["stream"] is True
    # LiteLLM takes the first segment off as the provider and sends the rest:
    # the catalog id reaches the model service whole.
    assert completion_calls[0]["model"] == "openai/openai/model-id"
    assert acompletion_calls[0]["model"] == "openai/openai/model-id"
    assert completion_calls[0]["api_key"] == "fresh-jwt"
    assert acompletion_calls[0]["api_key"] == "fresh-jwt"
    assert completion_calls[0]["extra_headers"] == {
        "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
    }
    assert acompletion_calls[0]["extra_headers"] == {
        "Aion-Principal-Selector": "aion://agent/environment/fresh-env",
    }


@pytest.mark.parametrize(
    ("catalog_id", "for_litellm"),
    [
        ("openai/gpt-test", "openai/openai/gpt-test"),
        ("anthropic/claude-test", "openai/anthropic/claude-test"),
        ("gpt-test", "openai/gpt-test"),
    ],
)
def test_a_catalog_id_is_routed_through_litellms_openai_provider(catalog_id, for_litellm):
    """Whatever the id looks like, LiteLLM speaks the OpenAI protocol to the
    model service and sends the id as the catalog spells it."""
    assert models._routed(catalog_id) == for_litellm


def _fake_adk_modules(monkeypatch):
    """Install stand-ins for google-adk and litellm, returning the litellm calls."""

    class FakeLiteLlm:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeLiteLLMClient:
        pass

    google = ModuleType("google")
    google_adk = ModuleType("google.adk")
    google_adk_models = ModuleType("google.adk.models")
    lite_llm = ModuleType("google.adk.models.lite_llm")
    lite_llm.LiteLlm = FakeLiteLlm
    lite_llm.LiteLLMClient = FakeLiteLLMClient

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.adk", google_adk)
    monkeypatch.setitem(sys.modules, "google.adk.models", google_adk_models)
    monkeypatch.setitem(sys.modules, "google.adk.models.lite_llm", lite_llm)

    litellm = _fake_litellm(monkeypatch)
    return litellm.calls


def _fake_litellm(monkeypatch) -> ModuleType:
    """A stand-in for litellm whose completions stream one chunk; it records each call."""
    litellm = ModuleType("litellm")
    litellm.calls, litellm.completion_calls, litellm.acompletion_calls = [], [], []

    def completion(**kwargs):
        litellm.calls.append(kwargs)
        litellm.completion_calls.append(kwargs)
        return iter(["completion chunk"])

    async def acompletion(**kwargs):
        litellm.calls.append(kwargs)
        litellm.acompletion_calls.append(kwargs)

        async def chunks():
            yield "acompletion chunk"

        return chunks()

    litellm.completion = completion
    litellm.acompletion = acompletion
    litellm.stream_chunk_builder = lambda chunks: ("assembled", chunks)
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    return litellm


def test_lite_llm_client_allows_deployment_initiated_work(monkeypatch):
    """A Version-only callback leaves daemon resolution to the control plane."""
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(
        "aion.api.callback_attribution.get_aion_runtime_context", lambda: None
    )

    client = aion_lite_llm("model-id").kwargs["llm_client"]

    client.completion(model="openai/model-id", messages=[], tools=None)
    asyncio.run(client.acompletion(model="openai/model-id", messages=[], tools=None))

    assert len(calls) == 2
    for call in calls:
        assert call["api_key"] == "fresh-jwt"
        assert call.get("extra_headers", {}) == {}


@pytest.mark.parametrize("stream", [False, True])
def test_every_request_skips_litellms_mcp_gateway(monkeypatch, stream):
    """LiteLLM imports its MCP gateway for a request with tools, and since 1.103 the
    import needs an mcp release langchain-mcp-adapters does not allow: with both
    frameworks installed, the request would fail before it is sent. ADK sends its
    tools as functions, which the gateway never serves."""
    calls = _fake_adk_modules(monkeypatch)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "fresh-jwt")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda existing=None: {})
    client = aion_lite_llm("model-id").kwargs["llm_client"]
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]

    client.completion(model="openai/model-id", messages=[], tools=tools, stream=stream)
    asyncio.run(client.acompletion(model="openai/model-id", messages=[], tools=tools, stream=stream))

    assert [call["_skip_mcp_handler"] for call in calls] == [True, True]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("code", ["daemon_identity_required", "provider_unavailable"])
def test_litellm_stream_translates_only_configuration_errors(
    monkeypatch, asynchronous, partial, code,
):
    """Real LiteLLM iteration keeps typed errors, partial output and cleanup."""
    import openai

    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "version-token")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda _: {})
    consumed = []
    responses = []
    chunk = {"id": "chunk-1", "created": 0, "model": "test-model",
             "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]}
    error = {"error": {"message": "An actionable error", "type": "configuration_error", "code": code}}

    def events():
        for event in ([chunk, error] if partial else [error]):
            consumed.append(event)
            yield ("data: " + json.dumps(event) + "\n\n").encode()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield from events()

    class AsyncBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for event in events():
                yield event

    def handle(request):
        response = httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=AsyncBody() if asynchronous else Body())
        responses.append(response)
        return response

    client = models.aion_lite_llm("openai/test-model").llm_client
    kwargs = {"model": "openai/test-model", "messages": [{"role": "user", "content": "hello"}],
              "api_base": "https://api.example.test/v1", "stream": True}

    async def consume_async():
        async with openai.AsyncOpenAI(api_key="version-token", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))) as upstream:
            stream = await client.acompletion(**kwargs, client=upstream)
            assert not consumed
            if partial:
                first = await anext(stream)
                assert first.choices[0].delta.content == "hello"
                assert len(consumed) == 1
            await anext(stream)

    with pytest.raises((AionDaemonIdentityRequired, openai.APIError)) as caught:
        if asynchronous:
            asyncio.run(consume_async())
        else:
            with openai.OpenAI(api_key="version-token", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handle))) as upstream:
                stream = client.completion(**kwargs, client=upstream)
                assert not consumed
                if partial:
                    assert next(stream).choices[0].delta.content == "hello"
                    assert len(consumed) == 1
                next(stream)
    if code == "daemon_identity_required":
        assert isinstance(caught.value, AionDaemonIdentityRequired)
        assert caught.value.retryable is False
    else:
        assert not isinstance(caught.value, AionDaemonIdentityRequired)
        assert isinstance(caught.value, openai.APIError)
    assert len(responses) == 1
    assert responses[0].is_closed
    assert consumed[-1] == error


class _ModelService:
    """Stands in for LiteLLM's ``acompletion``: records the model of each request, answers "4"."""

    def __init__(self):
        self.models = []

    async def __call__(self, **kwargs):
        import litellm

        self.models.append(kwargs["model"])
        if kwargs.get("stream"):
            return _streamed("4")
        return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "4"}}])


async def _streamed(text: str):
    import litellm

    yield litellm.ModelResponseStream(
        choices=[{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
    )


@pytest.fixture
def model_service(monkeypatch):
    """The real ADK and LiteLLM, with the call to the model service faked."""
    import litellm

    service = _ModelService()
    monkeypatch.setattr(litellm, "acompletion", service)
    monkeypatch.setattr(models, "aion_openai_config", lambda: FakeConfig())
    monkeypatch.setattr(models, "aion_model_api_key", lambda: "jwt-token")
    monkeypatch.setattr(models, "aion_model_request_headers", lambda existing=None: {})
    return service


def _runtime(model):
    """A stand-in runtime context of a turn whose environment selects ``model``."""
    from types import SimpleNamespace

    return SimpleNamespace(model=model)


async def _run_turns(agent, runtimes, monkeypatch):
    """Run one turn per runtime context through ADK's Runner, in one session."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    runner = Runner(app_name="app", agent=agent, session_service=InMemorySessionService())
    session = await runner.session_service.create_session(app_name="app", user_id="user")
    for runtime in runtimes:
        monkeypatch.setattr(models, "get_aion_runtime_context", lambda runtime=runtime: runtime)
        message = types.Content(role="user", parts=[types.Part(text="What is 2+2?")])
        async for _ in runner.run_async(user_id="user", session_id=session.id, new_message=message):
            pass


def test_a_model_function_names_the_model_of_each_request(model_service, monkeypatch):
    """Each turn goes to the model its runtime context selects; the stand-in name is never sent."""
    from google.adk.agents import LlmAgent

    seen = []

    def environment_model(runtime):
        seen.append(runtime)
        return runtime.model

    model = aion_lite_llm(environment_model)
    agent = LlmAgent(name="assistant", model=model)
    first, second = _runtime("openai/first"), _runtime("anthropic/second")

    asyncio.run(_run_turns(agent, [first, second], monkeypatch))

    assert model.model == models.CHOSEN_PER_REQUEST
    assert seen == [first, second]
    assert model_service.models == ["openai/openai/first", "openai/anthropic/second"]


def test_an_async_model_function_is_awaited(model_service, monkeypatch):
    """An ``async def`` choice may wait for I/O; the model it returns names the request."""
    from google.adk.agents import LlmAgent

    async def environment_model(runtime):
        await asyncio.sleep(0)
        return runtime.model

    agent = LlmAgent(name="assistant", model=aion_lite_llm(environment_model))

    asyncio.run(_run_turns(agent, [_runtime("openai/first"), _runtime("openai/second")], monkeypatch))

    assert model_service.models == ["openai/openai/first", "openai/openai/second"]


def test_a_model_the_agent_callback_names_is_kept(model_service, monkeypatch):
    """A ``before_model_callback`` that names the model decides; the function is not asked."""
    from google.adk.agents import LlmAgent

    def from_callback(callback_context, llm_request):
        llm_request.model = "openai/from-callback"

    def environment_model(runtime):
        raise AssertionError("not asked when the callback named the model")

    agent = LlmAgent(name="assistant", model=aion_lite_llm(environment_model), before_model_callback=from_callback)

    asyncio.run(_run_turns(agent, [_runtime("openai/first")], monkeypatch))

    assert model_service.models == ["openai/openai/from-callback"]


async def _no_model(runtime):
    return None


@pytest.mark.parametrize("choose_model", [lambda runtime: None, _no_model], ids=["plain", "async"])
def test_no_model_chosen_fails_the_task_without_a_request(model_service, monkeypatch, choose_model):
    """A function that chooses no model fails the task, and nothing reaches the model service."""
    from aion.core.exceptions import TaskFailedError
    from google.adk.models import LlmRequest
    from google.genai import types

    model = aion_lite_llm(choose_model)
    monkeypatch.setattr(models, "get_aion_runtime_context", lambda: None)
    request = LlmRequest(
        model=models.CHOSEN_PER_REQUEST,
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )

    async def generate():
        async for _ in model.generate_content_async(request):
            pass

    with pytest.raises(TaskFailedError, match="No model was chosen"):
        asyncio.run(generate())
    assert model_service.models == []
