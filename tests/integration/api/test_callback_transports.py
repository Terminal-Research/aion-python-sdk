"""Exercise real SDK transports against local, provider-free callback endpoints.

The server implements the backend's published error/wire shapes, not its
authorization logic; Scala ingress/controller tests cover that separate boundary.
No database or user server is used by this suite.
"""

import asyncio
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState, TaskStatus

from aion.api import AionFileClient, CapabilitySubject
from aion.api.gql.client import AionGqlClient
from aion.api.gql.generated.graphql_client import A2AJsonRpcRequestGQLInput
from aion.api.model_service_client import _aion_model_async_http_client
from aion.core.constants import AION_USAGE_ATTRIBUTION_HEADER, USAGE_ATTRIBUTION_EXTENSION_URI_V1
from aion.core.exceptions import AionDaemonIdentityRequired
from aion.core.principal import Principal
from aion.core.runtime.context import AionRuntimeContext, AionRuntimeContextRegistry, DirectAttribution
from aion.mcp import aion_mcp_endpoint
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.scope import init_execution_scope, clear_execution_scope
from aion.server.auth import Assurance, AuthenticatedCaller, CallerCredentials, CredentialKind


class VersionToken:
    async def get_token(self):
        return "version-token"


@pytest.fixture
def api():
    app = FastAPI()
    state = SimpleNamespace(requests=[], subscriptions=[], failure=False)
    details = {"code": "daemon_identity_required", "resourceType": "Deployment",
               "resourceId": "deployment-1", "retryable": False}

    @app.api_route("/{path:path}", methods=["POST", "PUT"])
    async def callback(request: Request, path: str):
        await request.body()
        state.requests.append((path, dict(request.headers)))
        if state.failure:
            error = {"code": -32600, "data": details} if path == "mcp" else details
            return JSONResponse({"error": error}, status_code=409)
        return {"id": "file-1", "versionId": "version-1", "ok": True}

    @app.websocket("/ws/graphql")
    async def graphql(ws: WebSocket):
        await ws.accept(subprotocol="graphql-transport-ws")
        assert (await ws.receive_json())["type"] == "connection_init"
        await ws.send_json({"type": "connection_ack"})
        subscribe = await ws.receive_json()
        assert subscribe["type"] == "subscribe"
        state.subscriptions.append(
            (ws.query_params.get("token"), subscribe["payload"]["variables"])
        )
        result = (
            {"__typename": "A2AJsonRpcErrorResponseGQL", "errorId": "test",
             "error": {"code": 1100, "message": "Assign a daemon", "data": details}}
            if state.failure else
            {"__typename": "A2AJsonRpcSuccessResponseGQL", "id": "test", "result": {"ok": True}}
        )
        await ws.send_json({
            "type": "next", "id": subscribe["id"],
            "payload": {"data": {"a2aRpc": result}},
        })
        await ws.send_json({"type": "complete", "id": subscribe["id"]})
        await ws.close()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    state.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Local callback stub did not start"
        yield state
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "Local callback stub did not stop"


@pytest.fixture(autouse=True)
def execution_scope():
    provider = RequestScopeRuntimeContextProvider()
    previous = AionRuntimeContextRegistry._provider
    AionRuntimeContextRegistry.set_provider(provider)
    provider.set_current_context(None)
    init_execution_scope()
    try:
        yield
    finally:
        provider.set_current_context(None)
        AionRuntimeContextRegistry._provider = previous
        clear_execution_scope()


async def gql_client(api):
    client = AionGqlClient(
        client_id="client", client_secret="secret", jwt_manager=VersionToken(),
        gql_url=api.url + "/graphql",
        ws_url=api.url.replace("http:", "ws:") + "/ws/graphql",
    )
    await client.initialize()
    return client


def incoming(number, resume):
    principal = Principal("AnonymousSession", f"00000000-0000-0000-0000-{number:012d}")
    caller = AuthenticatedCaller(
        principal, credential=CredentialKind.SESSION,
        assurance=Assurance.SESSION, issuer="aion.io", claims={},
    )
    call = ServerCallContext(state={"auth": CallerCredentials(caller)})
    if number == 1:
        call.state["headers"] = {AION_USAGE_ATTRIBUTION_HEADER: "opaque-forwarded"}
        call.requested_extensions = {USAGE_ATTRIBUTION_EXTENSION_URI_V1}
    message = Message(
        message_id=f"message-{number}", context_id=f"context-{number}",
        role=Role.ROLE_USER, parts=[Part(text="hello")],
    )
    task = Task(
        id=f"task-{number}", context_id=message.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
    ) if resume else None
    return RequestContext(
        call_context=call, request=SendMessageRequest(message=message), task=task,
    )


@pytest.mark.parametrize("resume", [False, True])
async def test_all_clients_keep_concurrent_execution_attribution(api, resume):
    gql = await gql_client(api)
    barrier = asyncio.Event()
    started = []

    async def stream(context):
        started.append(context)
        if len(started) == 2:
            barrier.set()
        await barrier.wait()
        async with _aion_model_async_http_client(lambda: "version-token") as model:
            response = await model.post(api.url + "/v1/chat/completions", json={})
            assert response.status_code == 200
        endpoint = await aion_mcp_endpoint(jwt_manager=VersionToken(), base_url=api.url)
        factory = endpoint.as_langchain_config()["httpx_client_factory"]
        async with factory(headers=dict(endpoint.headers)) as mcp:
            assert (await mcp.post(api.url + "/mcp", json={})).status_code == 200
        async with AionFileClient(jwt_manager=VersionToken(), base_url=api.url) as files:
            await files.create(
                b"content", organization_id="org-1",
                purpose="MessagingMedia", file_name="x.txt",
            )
            await files.replace(
                "file-1", b"updated", expected_version_id="version-1",
                expected_revision=1, file_name="x.txt",
            )
        responses = [chunk async for chunk in gql.a2a_stream(
            A2AJsonRpcRequestGQLInput(jsonrpc="2.0", method="message/send"),
            target=CapabilitySubject.environment("env-1"))]
        assert len(responses) == 1
        if False:
            yield

    worker = AionAgentRequestExecutor(SimpleNamespace(stream=stream, resume=stream))
    try:
        await asyncio.wait_for(asyncio.gather(*[
            worker.execute(incoming(number, resume), AsyncMock()) for number in (1, 2)
        ]), timeout=20)
    finally:
        await gql.client.http_client.aclose()
    assert AionRuntimeContextRegistry.get_current_context() is None
    guest = Principal("AnonymousSession", "00000000-0000-0000-0000-000000000002").subject
    assert len(api.requests) == 8
    assert sum(headers.get("aion-caller-id") == guest for _, headers in api.requests) == 4
    for _, headers in api.requests:
        assert headers["authorization"] == "Bearer version-token"
        assert "aion-principal-selector" not in headers
        assert (headers.get("aion-caller-id"), headers.get("aion-usage-attribution")) in (
            (guest, None), (None, "opaque-forwarded"))
    assert len(api.subscriptions) == 2
    for token, variables in api.subscriptions:
        assert token == "version-token"
        assert variables["principal"] is None
        pairs = variables["serviceParameters"]["additional"]
        assert pairs in ([{"key": "Aion-Caller-Id", "value": guest}],
                        [{"key": "Aion-Usage-Attribution", "value": "opaque-forwarded"}])


@pytest.mark.parametrize("client_kind", ["model", "mcp", "file", "a2a"])
async def test_missing_daemon_is_typed_without_retries(api, client_kind):
    api.failure = True
    AionRuntimeContextRegistry.set_current_context(AionRuntimeContext(
        callback_attribution=DirectAttribution(
            Principal("ExternalAnonymous", "external-anonymous")
        )
    ))
    with pytest.raises(AionDaemonIdentityRequired) as caught:
        if client_kind == "model":
            async with _aion_model_async_http_client(lambda: "version-token") as client:
                await client.post(api.url + "/v1/chat/completions", json={})
        elif client_kind == "mcp":
            endpoint = await aion_mcp_endpoint(jwt_manager=VersionToken(), base_url=api.url)
            factory = endpoint.as_langchain_config()["httpx_client_factory"]
            async with factory(headers=dict(endpoint.headers)) as client:
                await client.post(api.url + "/mcp", json={})
        elif client_kind == "file":
            async with AionFileClient(jwt_manager=VersionToken(), base_url=api.url) as client:
                await client.create(
                    b"content", organization_id="org-1",
                    purpose="MessagingMedia", file_name="x.txt",
                )
        else:
            client = await gql_client(api)
            try:
                async for _ in client.a2a_stream(
                    A2AJsonRpcRequestGQLInput(jsonrpc="2.0", method="message/send"),
                    target=CapabilitySubject.environment("env-1"),
                ):
                    pytest.fail("A missing daemon must not yield a successful result")
            finally:
                await client.client.http_client.aclose()
    assert caught.value.code == "daemon_identity_required"
    assert caught.value.retryable is False
    assert len(api.requests) + len(api.subscriptions) == 1
