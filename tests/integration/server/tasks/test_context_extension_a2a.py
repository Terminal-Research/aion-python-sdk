"""The Context extension over the wire: JSON-RPC and HTTP+JSON, end to end through the request handler.

Messages are sent the way a client sends them, so contexts are reserved and
callers bound by the same admission every message passes; the three methods
are then called through both transports against the same server:

* the two transports give the same answers, and lifecycle errors keep their
  codes in JSON-RPC and their statuses and problem types in HTTP+JSON;
* deleting a context with a live task cancels it through the ordinary
  cancellation path, waits for it, deletes the framework state under the
  holder's scope, and leaves nothing a later read or send could reach;
* while a context is being deleted, a message into it is refused.

Each test runs on the in-memory store and on PostgreSQL.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import pytest
import pytest_asyncio
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from aion.core.constants.a2a import CONTEXT_EXTENSION_URI_V1
from aion.server.core.middlewares import AionContextMiddleware

from .jsonrpc_harness import TASK_NOT_FOUND, JsonRpcServer, error_of, serving, task_of
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]


class _HeaderAuth(AuthenticationBackend):
    """The user named in ``X-Test-User``; only the test can reach this app."""

    async def authenticate(self, conn):
        name = conn.headers.get("x-test-user")
        if not name:
            return None
        return AuthCredentials(["authenticated"]), SimpleUser(name)


_MIDDLEWARE = [
    Middleware(AuthenticationMiddleware, backend=_HeaderAuth()),
    Middleware(AionContextMiddleware),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def server(request, _database):
    async with serving(request.param, _database, _MIDDLEWARE) as built:
        yield built


def _as(user: Optional[str]) -> dict[str, str]:
    return {"X-Test-User": user} if user else {}


async def _rpc(server: JsonRpcServer, user: str, method: str, params: Optional[dict[str, Any]] = None) -> Any:
    return await server.rpc(method, params or {}, headers=_as(user))


async def _http(server: JsonRpcServer, user: str, path: str, body: Optional[dict[str, Any]] = None):
    return await server.client.post(f"/{path}", json=body or {}, headers=_as(user))


async def _send(server: JsonRpcServer, user: str, text: str, context_id: str, **config: Any) -> Any:
    return await server.send(text, context_id, headers=_as(user), **config)


async def test_both_transports_list_and_read_the_same_contexts(server) -> None:
    context_id = str(uuid.uuid4())
    task_of(await _send(server, "alice", "done", context_id))

    listed_rpc = (await _rpc(server, "alice", "GetContexts"))["result"]
    listed_http = await _http(server, "alice", "contexts:get")
    assert listed_http.status_code == 200
    assert listed_http.json() == listed_rpc
    assert [entry["contextId"] for entry in listed_rpc] == [context_id]
    assert listed_rpc[0]["title"] is None and listed_rpc[0]["summary"] is None
    assert listed_rpc[0]["lastActivityAt"].endswith("Z")

    read_rpc = (await _rpc(server, "alice", "GetContext", {"contextId": context_id}))["result"]
    read_http = await _http(server, "alice", "context:get", {"contextId": context_id})
    assert read_http.status_code == 200
    assert read_http.json() == read_rpc
    assert [message["parts"][0]["text"] for message in read_rpc["history"]] == ["done"]
    assert read_rpc["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_errors_keep_their_identity_on_both_transports(server) -> None:
    context_id = str(uuid.uuid4())
    task_of(await _send(server, "alice", "done", context_id))

    rpc = await _rpc(server, "bob", "GetContext", {"contextId": context_id})
    assert rpc["error"] == {
        "code": 1000,
        "message": "Context not found",
        "data": {"contextId": context_id, "retryable": False},
    }
    http = await _http(server, "bob", "context:get", {"contextId": context_id})
    assert http.status_code == 404
    assert http.headers["content-type"].startswith("application/problem+json")
    problem = http.json()
    assert problem["type"] == f"{CONTEXT_EXTENSION_URI_V1}#context-not-found"
    assert (problem["contextId"], problem["retryable"]) == (context_id, False)

    assert (await _rpc(server, "alice", "GetContexts", {"historyLength": 101}))["error"]["code"] == -32602
    assert (await _http(server, "alice", "contexts:get", {"historyLength": 101})).status_code == 400
    assert (await _http(server, "alice", "context:delete", {"contextId": "  "})).status_code == 400


async def test_deleting_a_context_cancels_its_live_task_and_leaves_nothing(server) -> None:
    context_id = str(uuid.uuid4())
    task_of(await _send(server, "alice", "done", context_id))
    held = task_of(await _send(server, "alice", "hold", context_id, returnImmediately=True))
    await asyncio.sleep(0.1)

    deleted = await _http(server, "alice", "context:delete", {"contextId": context_id})

    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"contextId": context_id}
    assert server.agent.cancels == 1
    assert server.agent.deleted_state == [(context_id, "alice", None)]
    assert error_of(await _rpc(server, "alice", "GetTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert (await _rpc(server, "alice", "GetContexts"))["result"] == []
    assert (await _rpc(server, "alice", "GetContext", {"contextId": context_id}))["error"]["code"] == 1000

    # Repeating it, or deleting what was never there, is the same success.
    again = await _rpc(server, "alice", "DeleteContext", {"contextId": context_id})
    assert again["result"] == {"contextId": context_id}

    # The ID now starts a new conversation, for whoever uses it first.
    task_of(await _send(server, "bob", "done", context_id))
    history = (await _rpc(server, "bob", "GetContext", {"contextId": context_id}))["result"]["history"]
    assert len(history) == 1


async def test_another_callers_delete_leaves_the_context_alone(server) -> None:
    context_id = str(uuid.uuid4())
    task_of(await _send(server, "alice", "done", context_id))

    assert (await _rpc(server, "bob", "DeleteContext", {"contextId": context_id}))["result"] == {
        "contextId": context_id
    }

    assert [entry["contextId"] for entry in (await _rpc(server, "alice", "GetContexts"))["result"]] == [context_id]
    assert server.agent.deleted_state == []


async def test_a_context_being_deleted_admits_no_new_message(server) -> None:
    context_id = str(uuid.uuid4())
    task_of(await _send(server, "alice", "done", context_id))
    ticket = await server.handler._contexts._catalog.begin_deletion(context_id, "alice")
    assert ticket.operation_id is not None

    assert error_of(await _send(server, "alice", "done", context_id)) == TASK_NOT_FOUND

    finished = await _rpc(server, "alice", "DeleteContext", {"contextId": context_id})
    assert finished["result"] == {"contextId": context_id}
    task_of(await _send(server, "alice", "done", context_id))
