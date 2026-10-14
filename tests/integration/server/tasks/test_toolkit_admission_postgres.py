"""A request a toolkit refuses leaves nothing behind in PostgreSQL.

The toolkit host decides the route of a new task and runs the toolkit's
checks - the caller check, ``preflight`` - before a2a-sdk creates the task.
A refusal is a JSON-RPC error whose ``ErrorInfo`` metadata carries ``code``
and ``retryable``, and no ``tasks``, ``task_versions`` or ``task_events`` row
exists afterwards; the same server, given a request it accepts, writes all
three. The request path is the real one, as ``AppFactory`` wires it, with the
scripted toolkit found through its entry point.
"""

from __future__ import annotations

import importlib.metadata
import json
import uuid
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import Mock

import httpx
import pytest
import pytest_asyncio

from .postgres_support import POSTGRES_TEST_URL, prepared_database, stores, truncate

from a2a.server.cluster import DatabaseTaskEventStream
from a2a.utils import DEFAULT_RPC_URL
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.core.toolkit import ENTRY_POINT_GROUP, ToolkitDescriptor, codes
from aion.db.postgres.manager import db_manager
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.extensions.toolkit import discovery, load_toolkit_handlers
from aion.server.agent.execution.extensions.toolkit import handler as handler_module
from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.auth import JwksKeySource, TokenVerifier
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware
from aion.server.tasks.admission import PostgresContextAdmission
from aion.server.tasks.contexts import PostgresContextCatalog

from tests.support.aion_tokens import CLIENT_ID, ISSUER, SigningKey
from tests.support.scripted_toolkit.contract import SCRIPT_EVENT_TYPE, SCRIPT_SCHEMA, URI
from tests.support.scripted_toolkit.toolkit import ScriptedToolkit

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SIGNER = SigningKey()
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

OPEN = ScriptedToolkit()
INVOCATION_ONLY = ScriptedToolkit(
    descriptor=ToolkitDescriptor(uri=URI, description="platform calls only", invocation_only=True)
)
NOT_CONFIGURED = ScriptedToolkit(configure_error=RuntimeError("SCRIPTED_TOKEN is not set"))


class ToolkitServer:
    """A JSON-RPC server over PostgreSQL whose executor hosts one toolkit."""

    def __init__(self, handlers: list) -> None:
        store, handler_store = stores("toolkit-agent")
        executor = AionAgentRequestExecutor(aion_agent=Mock(), extension_handlers=handlers)
        card = Mock()
        card.capabilities.streaming = True
        card.capabilities.push_notifications = True
        self.handler = AionRequestHandler(
            agent_executor=executor,
            task_store=handler_store,
            event_stream=DatabaseTaskEventStream(
                db_manager.get_engine(), create_table=False, poll_interval_s=0.05
            ),
            agent_card=card,
            admission=PostgresContextAdmission(store.agent_id, db_manager),
            context_catalog=PostgresContextCatalog(store.agent_id, db_manager),
            request_context_builder=AionRequestContextBuilder(
                task_store=store, auto_discover_interrupted_task=True
            ),
        )
        keys = JwksKeySource(
            "https://api.aion.example/runtime/a2a/verification-keys",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=SIGNER.jwks()))
            ),
        )
        verifier = TokenVerifier(keys, issuer=ISSUER, invocation_audience=CLIENT_ID)
        app = Starlette(
            routes=[
                Route(
                    DEFAULT_RPC_URL,
                    endpoint=AionJsonRpcDispatcher(request_handler=self.handler).handle_requests,
                    methods=["POST"],
                )
            ],
            middleware=[
                Middleware(AionAuthMiddleware, verifier=verifier),
                Middleware(AionContextMiddleware),
            ],
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def send(
            self, script: dict[str, Any], *, method: str = "SendMessage", token: Optional[str] = None
    ) -> Any:
        """Send a message to the toolkit, carrying ``script``; a stream answers with its events."""
        message = {
            "messageId": uuid.uuid4().hex,
            "contextId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "extensions": [URI],
            "metadata": {
                EVENT_EXTENSION_URI_V1: {"type": SCRIPT_EVENT_TYPE, "source": "aion://tests", "id": "evt-1"}
            },
            "parts": [
                {"text": "run the script"},
                {"data": script, "metadata": {EVENT_EXTENSION_URI_V1: {"schema": SCRIPT_SCHEMA}}},
            ],
        }
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": {"message": message}}
        response = await self.client.post(
            DEFAULT_RPC_URL,
            json=body,
            headers={
                "A2A-Version": "1.0",
                "Authorization": f"Bearer {token or SIGNER.session_token()}",
            },
        )
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return [
                json.loads(line[len("data:"):])
                for chunk in response.text.split("\n\n")
                for line in chunk.splitlines()
                if line.startswith("data:")
            ]
        return response.json()

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.handler._active_task_registry.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest.fixture(autouse=True)
def runtime_provider():
    """The request-scoped runtime context provider the server installs at startup."""
    AionRuntimeContextRegistry.set_provider(RequestScopeRuntimeContextProvider())
    yield
    AionRuntimeContextRegistry.clear_provider()


@pytest.fixture
def registry(isolated_registry):
    """The extension registry, with what discovery registers undone after the test."""
    registered = set(discovery._REGISTERED_URIS)
    yield isolated_registry
    discovery._REGISTERED_URIS.clear()
    discovery._REGISTERED_URIS.update(registered)


async def _server(_database, attribute: str) -> ToolkitServer:
    """A server whose toolkit is found through an entry point naming ``attribute`` of this module."""
    if _database is None:
        pytest.skip("POSTGRES_TEST_URL is not set")
    await truncate()
    entry_point = importlib.metadata.EntryPoint(
        name="scripted", value=f"{__name__}:{attribute}", group=ENTRY_POINT_GROUP
    )
    handlers = await load_toolkit_handlers(
        SimpleNamespace(enabled_extensions=[URI]), entry_points=[entry_point]
    )
    return ToolkitServer(handlers)


@pytest_asyncio.fixture(loop_scope="module")
async def server(_database, registry):
    built = await _server(_database, "OPEN")
    yield built
    await built.aclose()


async def rows() -> dict[str, int]:
    """How many rows each task table holds."""
    counts = {}
    async with db_manager.get_session() as session:
        for table in ("tasks", "task_versions", "task_events"):
            counts[table] = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
    return counts


NOTHING = {"tasks": 0, "task_versions": 0, "task_events": 0}


def refusal(response: Any) -> tuple[int, dict[str, str]]:
    """The error code and the ``ErrorInfo`` metadata of a refusal, unary or streamed."""
    if isinstance(response, list):
        [response] = response
    assert "result" not in response, response
    error = response["error"]
    return error["code"], error["data"][0]["metadata"]


@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_a_preflight_refusal_creates_no_task(server, method) -> None:
    response = await server.send(
        {"refuse": {"code": "invalid_directive", "message": "No target.", "retryable": False}}, method=method
    )

    assert refusal(response) == (INVALID_PARAMS, {"code": "invalid_directive", "retryable": "false"})
    assert await rows() == NOTHING


async def test_a_preflight_crash_creates_no_task(server) -> None:
    response = await server.send({"crash_preflight": True})

    assert refusal(response) == (INTERNAL_ERROR, {"code": codes.INTERNAL_ERROR, "retryable": "false"})
    assert await rows() == NOTHING


async def test_an_accepted_request_writes_its_task(server) -> None:
    """The control: the same server stores what it accepts, with the toolkit's object."""
    response = await server.send(
        {
            "outputs": [
                {"kind": "status", "task_metadata": {"stage": "running"}},
                {"kind": "result", "state": "completed", "task_metadata": {"stage": "done"}},
            ]
        }
    )

    task = response["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["metadata"][URI] == {"stage": "done"}
    counts = await rows()
    assert counts["tasks"] == 1 and counts["task_versions"] == 1 and counts["task_events"] > 0


async def test_a_caller_an_invocation_only_toolkit_does_not_serve_creates_no_task(
    _database, registry, monkeypatch
) -> None:
    monkeypatch.setattr(handler_module, "api_settings", SimpleNamespace(client_id=CLIENT_ID))
    built = await _server(_database, "INVOCATION_ONLY")
    try:
        refused = await built.send({})
        assert refusal(refused) == (INVALID_PARAMS, {"code": codes.CALLER_NOT_ALLOWED, "retryable": "false"})
        assert await rows() == NOTHING

        served = await built.send({}, token=SIGNER.invocation_token())
        assert served["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    finally:
        await built.aclose()


async def test_an_unavailable_toolkit_creates_no_task(_database, registry) -> None:
    built = await _server(_database, "NOT_CONFIGURED")
    try:
        response = await built.send({})

        code, metadata = refusal(response)
        assert (code, metadata) == (INVALID_PARAMS, {"code": codes.EXTENSION_UNAVAILABLE, "retryable": "true"})
        assert "SCRIPTED_TOKEN is not set" in response["error"]["message"]
        assert await rows() == NOTHING
    finally:
        await built.aclose()
