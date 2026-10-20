"""An executable extension's tasks in PostgreSQL: a refusal, a message into a running task, an abandoned turn.

The request path is the real one, as ``AppFactory`` wires it, with the
scripted executor registered and created the way the server creates its
executable extensions' executors. An executor that refuses its request closes
the task ``REJECTED`` with the refusal's code; a request it accepts is stored
with the executor's object.

Two servers over the one database stand for two instances of the agent: a
message into an extension's running task is refused with ``task_busy`` on
either, before a2a-sdk sees it, and a message into an extension's task on a
server without the extension is refused with the task left as it was. A task
abandoned by the server running it - nothing written for longer than the
timeout - is settled by a message into it, which is then refused like one
into a finished task; and the server still running the turn stops it at its
next write.
"""

from __future__ import annotations

import asyncio
import json
import uuid
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

from aion.core.a2a import codes
from aion.core.config import AgentConfig
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1
from aion.core.runtime.context.registry import AionRuntimeContextRegistry
from aion.db.postgres.manager import db_manager
from aion.server.agent.execution import AionAgentRequestExecutor
from aion.server.agent.execution.context.providers import RequestScopeRuntimeContextProvider
from aion.server.agent.execution.extensions import create_extension_executors, register_extensions
from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.auth import JwksKeySource, TokenVerifier
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware
from aion.server.tasks.abandoned import AbandonedTasks, PostgresStaleTasks
from aion.server.tasks.admission import PostgresContextAdmission
from aion.server.tasks.context_artifacts import PostgresContextArtifacts
from aion.server.tasks.context_runs import PostgresContextRuns
from aion.server.tasks.contexts import PostgresContextCatalog
from aion.server.tasks.conversation import StoredConversations

from tests.support.aion_tokens import CLIENT_ID, ISSUER, SigningKey
from tests.support.executors import FakeFrameworkExecutor, agent_of
from tests.support.scripted_executor.contract import URI, script_contract
from tests.support.scripted_executor.executor import SCRIPTED

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SIGNER = SigningKey()
INVALID_PARAMS = -32602

AGENT_ID = "extension-agent"


class ExtensionServer:
    """A JSON-RPC server over PostgreSQL whose request executor serves one executable extension."""

    def __init__(self, executor: AionAgentRequestExecutor, abandoned_tasks: Optional[AbandonedTasks] = None) -> None:
        self.executor = executor
        store, handler_store = stores(AGENT_ID)
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
            abandoned_tasks=abandoned_tasks,
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
            self,
            script: Optional[dict[str, Any]],
            *,
            method: str = "SendMessage",
            token: Optional[str] = None,
            context_id: Optional[str] = None,
            task_id: Optional[str] = None,
            uri: str = URI,
    ) -> Any:
        """Send a message, as a stream answers with its events.

        With a ``script`` the message activates the extension at ``uri`` and
        carries the script; without one it is plain text, which a server
        routes by the task it names. ``task_id`` continues that task;
        ``context_id`` addresses a context, a new one by default.
        """
        message: dict[str, Any] = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": "run"}]}
        if task_id is not None:
            message["taskId"] = task_id
        else:
            message["contextId"] = context_id or str(uuid.uuid4())
        if script is not None:
            event_type, schema = script_contract(uri)
            message["extensions"] = [uri]
            message["metadata"] = {
                EVENT_EXTENSION_URI_V1: {"type": event_type, "source": "aion://tests", "id": "evt-1"}
            }
            message["parts"].append({"data": script, "metadata": {EVENT_EXTENSION_URI_V1: {"schema": schema}}})
        return await self.call(method, {"message": message}, token=token)

    async def call(self, method: str, params: dict[str, Any], *, token: Optional[str] = None) -> Any:
        """One JSON-RPC call; a stream answers with its events."""
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
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
    """The extension registry, filled by each server's registration."""
    return isolated_registry


async def _server(
        _database, extension=SCRIPTED, *, fresh: bool = True, stale_timeout: float = 0
) -> ExtensionServer:
    """A server that registers the executable extension ``extension`` and serves it.

    ``extension`` None is a server without it. ``fresh`` empties the
    database first; a second server of the same agent keeps what is there.
    ``stale_timeout`` is the server's ``AION_STALE_TASK_TIMEOUT``; 0 settles
    no abandoned task. Nothing sweeps in the background: a test sweeps itself.
    """
    if _database is None:
        pytest.skip("POSTGRES_TEST_URL is not set")
    if fresh:
        await truncate()
    task_store, versioned_store = stores(AGENT_ID)
    abandoned_tasks = AbandonedTasks(
        PostgresStaleTasks(AGENT_ID, db_manager, versioned_store), timeout=stale_timeout
    )
    agent = agent_of(FakeFrameworkExecutor())
    if extension is None:
        return ExtensionServer(AionAgentRequestExecutor(agent), abandoned_tasks)
    enabled = register_extensions(
        AgentConfig(path="agent:graph", enabled_extensions=[extension.uri]), executables=[extension]
    )
    conversations = StoredConversations(
        task_store=task_store,
        context_runs=PostgresContextRuns(AGENT_ID, db_manager),
        context_artifacts=PostgresContextArtifacts(AGENT_ID, db_manager),
        owner_resolver=agent.owner_resolver,
    )
    executor = AionAgentRequestExecutor(
        agent, extensions=create_extension_executors(enabled), conversations=conversations
    )
    return ExtensionServer(executor, abandoned_tasks)


@pytest_asyncio.fixture(loop_scope="module")
async def server(_database, registry):
    built = await _server(_database)
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
async def test_a_refusal_closes_its_task_rejected_with_the_code(server, method) -> None:
    response = await server.send(
        {"refuse": {"code": "forbidden_target", "message": "No target.", "retryable": False}}, method=method
    )

    task = (response[-1] if isinstance(response, list) else response)["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_REJECTED"
    error = task["status"]["message"]["parts"][-1]["data"]
    assert error == {"code": "forbidden_target", "message": "No target.", "retryable": False}
    assert (await rows())["tasks"] == 1


async def test_an_accepted_request_writes_its_task(server) -> None:
    """The control: the same server stores what it accepts, with the executor's object."""
    response = await server.send(
        {
            "outputs": [
                {"kind": "status", "metadata": {URI: {"stage": "running"}}},
                {"kind": "status", "state": "completed", "metadata": {URI: {"stage": "done"}}},
            ]
        }
    )

    task = response["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["metadata"][URI] == {"stage": "done"}
    counts = await rows()
    assert counts["tasks"] == 1 and counts["task_versions"] == 1 and counts["task_events"] > 0


WORKING_UNTIL_CANCELLED = {"outputs": [{"kind": "status", "text": "Working"}], "wait_for_cancel": True}
"""A turn that says it is working, then waits for ``cancel()``."""

COMPLETES = {"outputs": [{"kind": "status", "state": "completed"}]}
"""A turn that completes at once."""


async def stored_task(task_id: str) -> tuple[str, int]:
    """The state and version a task has in the database."""
    async with db_manager.get_session() as session:
        state = (
            await session.execute(text("SELECT state FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)})
        ).scalar()
        version = (
            await session.execute(text("SELECT version FROM task_versions WHERE task_id = :id"), {"id": task_id})
        ).scalar()
    return state, version


async def working_task_in(context_id: str) -> str:
    """The id of the context's task once it is working; fails after five seconds."""
    for _ in range(100):
        async with db_manager.get_session() as session:
            found = (
                await session.execute(
                    text("SELECT id FROM tasks WHERE context_id = :context AND state = 'TASK_STATE_WORKING'"),
                    {"context": context_id},
                )
            ).scalar()
        if found is not None:
            return str(found)
        await asyncio.sleep(0.05)
    raise AssertionError(f"no working task appeared in context {context_id}")


async def test_a_message_into_a_running_extension_task_is_refused_on_every_server(_database, registry) -> None:
    """a2a-sdk would run it as a second turn of the task; refused before it sees it, and the task runs on."""
    first = await _server(_database)
    second = await _server(_database, fresh=False)
    context_id = str(uuid.uuid4())
    token = SIGNER.session_token()
    try:
        running = asyncio.create_task(first.send(WORKING_UNTIL_CANCELLED, context_id=context_id, token=token))
        blocking = await working_task_in(context_id)

        busy = {"code": codes.TASK_BUSY, "retryable": "true", "taskId": blocking}
        for server in (first, second):
            assert refusal(await server.send(None, task_id=blocking, token=token)) == (INVALID_PARAMS, busy)
        assert (await stored_task(blocking))[0] == "TASK_STATE_WORKING"

        cancelled = await first.call("CancelTask", {"id": blocking}, token=token)
        assert cancelled["result"]["status"]["state"] == "TASK_STATE_CANCELED"
        await running
    finally:
        await first.aclose()
        await second.aclose()


async def test_a_message_into_an_extensions_task_on_a_server_without_the_extension_is_refused(
    _database, registry
) -> None:
    """The task is not handed to the agent's framework: refused, and left as it was."""
    with_extension = await _server(_database)
    without = await _server(_database, None, fresh=False)
    token = SIGNER.session_token()
    try:
        waiting = await with_extension.send({"outputs": [{"kind": "status", "state": "input-required"}]}, token=token)
        task_id = waiting["result"]["task"]["id"]
        before = await stored_task(task_id)

        response = await without.send(None, task_id=task_id, token=token)

        assert refusal(response) == (INVALID_PARAMS, {"code": codes.EXTENSION_UNAVAILABLE, "retryable": "true"})
        assert await stored_task(task_id) == before == ("TASK_STATE_INPUT_REQUIRED", before[1])
    finally:
        await with_extension.aclose()
        await without.aclose()


STALE_TIMEOUT = 60.0
"""The servers' ``AION_STALE_TASK_TIMEOUT`` in the scenarios of an abandoned task; tasks are aged past it."""


async def age(task_id: str, seconds: float = STALE_TIMEOUT + 5) -> None:
    """Move the task's last write ``seconds`` back, as if nothing had written it since."""
    async with db_manager.get_session() as session:
        await session.execute(
            text("UPDATE tasks SET updated_at = now() - make_interval(secs => :seconds) WHERE id = :id"),
            {"seconds": seconds, "id": uuid.UUID(task_id)},
        )
        await session.commit()


async def settled_reason(task_id: str) -> Optional[str]:
    async with db_manager.get_session() as session:
        return (
            await session.execute(
                text("SELECT metadata->>'aion:settledReason' FROM tasks WHERE id = :id"), {"id": uuid.UUID(task_id)}
            )
        ).scalar()


async def test_a_message_into_an_abandoned_task_settles_it_and_is_refused(_database, registry) -> None:
    first = await _server(_database, stale_timeout=STALE_TIMEOUT)
    second = await _server(_database, fresh=False, stale_timeout=STALE_TIMEOUT)
    context_id = str(uuid.uuid4())
    token = SIGNER.session_token()
    running = asyncio.create_task(
        first.send(WORKING_UNTIL_CANCELLED, context_id=context_id, token=token)
    )
    try:
        blocking = await working_task_in(context_id)
        await age(blocking)

        response = await second.send(None, task_id=blocking, token=token)

        assert "terminal state" in response["error"]["message"]
        assert (await stored_task(blocking))[0] == "TASK_STATE_FAILED"
        assert await settled_reason(blocking) == "task_abandoned"
    finally:
        running.cancel()
        await first.aclose()
        await second.aclose()


async def test_the_server_still_running_an_abandoned_turn_stops_it_at_its_next_write(_database, registry) -> None:
    """Its write meets the settlement's version: the turn is torn down, the executor asked to stop, nothing overwritten."""
    first = await _server(_database, stale_timeout=STALE_TIMEOUT)
    second = await _server(_database, fresh=False, stale_timeout=STALE_TIMEOUT)
    context_id = str(uuid.uuid4())
    token = SIGNER.session_token()
    running = asyncio.create_task(
        first.send(
            {**WORKING_UNTIL_CANCELLED, "tick_seconds": 0.3}, context_id=context_id, token=token
        )
    )
    try:
        blocking = await working_task_in(context_id)
        settled = None
        for _ in range(20):
            await age(blocking)
            settled = await second.handler._abandoned_tasks.settle(blocking)
            if settled is not None:
                break
        assert settled is not None, "a tick landed between every aging and its settlement"

        await asyncio.wait_for(running, 10)
        await asyncio.sleep(0.6)

        assert (blocking, None) in first.executor.pool.extensions[URI].cancels
        assert (await stored_task(blocking))[0] == "TASK_STATE_FAILED"
        assert await settled_reason(blocking) == "task_abandoned"
    finally:
        running.cancel()
        await first.aclose()
        await second.aclose()
