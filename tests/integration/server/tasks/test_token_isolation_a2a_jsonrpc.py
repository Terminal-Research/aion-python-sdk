"""The caller a stock ``aion serve`` names, over the A2A wire, on both task stores.

The middlewares are the ones ``AppFactory`` installs when the deployment has
credentials, and the tokens are signed with a stand-in for the platform's key,
so the owner of a request is the ``sub`` of its bearer token and nothing else.
Two callers present the same ``contextId`` throughout.

A verified caller is authenticated: its context history and finding its
interrupted task through the ``contextId`` are open to it, and only to it. A
request without a valid token never reaches the agent.

The in-memory run and the PostgreSQL run are the same test.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.middleware import Middleware

from aion.server.auth import TokenVerifier
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware

from .jsonrpc_harness import (
    TASK_NOT_FOUND,
    JsonRpcServer,
    error_of,
    serving,
    state_of,
    task_of,
)
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

ALICE = "aion:user:alice"
BOB = "aion:user:bob"


PLATFORM_KEY = ec.generate_private_key(ec.SECP256R1())


class _PlatformKey:
    """The stand-in platform's public key, where the server would have fetched it."""

    async def load(self) -> None:
        pass

    def current_key(self):
        return PLATFORM_KEY.public_key()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def server(request, _database):
    # The middlewares AppFactory installs with credentials, outermost first.
    middleware = [
        Middleware(AionAuthMiddleware, verifier=TokenVerifier(_PlatformKey())),
        Middleware(AionContextMiddleware),
    ]
    async with serving(request.param, _database, middleware) as built:
        yield built


def _as(server: JsonRpcServer, subject: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode({"sub": subject, "iat": now, "exp": now + 300}, PLATFORM_KEY, algorithm="ES256")
    return {"Authorization": f"Bearer {token}"}


async def _send(
    server: JsonRpcServer,
    subject: str,
    text: str,
    context_id: str,
    task_id: Optional[str] = None,
    **config: Any,
) -> Any:
    return await server.send(text, context_id, task_id, headers=_as(server, subject), **config)


async def _call(server: JsonRpcServer, subject: str, method: str, params: dict[str, Any]) -> Any:
    return await server.rpc(method, params, headers=_as(server, subject))


async def test_two_callers_on_one_context_keep_their_tasks_apart(server) -> None:
    context_id = str(uuid.uuid4())
    alices = task_of(await _send(server, ALICE, "done", context_id))
    bobs = task_of(await _send(server, BOB, "done", context_id))
    assert alices["id"] != bobs["id"]

    # The header names the caller of every method, GetTask and ListTasks included.
    assert (await _call(server, ALICE, "GetTask", {"id": alices["id"]}))["result"]["id"] == alices["id"]
    assert error_of(await _call(server, BOB, "GetTask", {"id": alices["id"]})) == TASK_NOT_FOUND
    listed = (await _call(server, BOB, "ListTasks", {"contextId": context_id}))["result"]["tasks"]
    assert [task["id"] for task in listed] == [bobs["id"]]


async def test_a_caller_cannot_continue_another_callers_task(server) -> None:
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, ALICE, "ask", context_id))
    assert state_of(asked) == "TASK_STATE_INPUT_REQUIRED"
    runs = len(server.agent.runs)

    assert error_of(await _send(server, BOB, "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs

    resumed = task_of(await _send(server, ALICE, "done", context_id, task_id=asked["id"]))
    assert (resumed["id"], state_of(resumed)) == (asked["id"], "TASK_STATE_COMPLETED")


async def test_an_interrupted_task_is_found_through_its_context_by_its_owner_only(server) -> None:
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, ALICE, "ask", context_id))

    bobs = task_of(await _send(server, BOB, "done", context_id))
    alices = task_of(await _send(server, ALICE, "done", context_id))

    assert bobs["id"] != asked["id"]
    assert (alices["id"], state_of(alices)) == (asked["id"], "TASK_STATE_COMPLETED")


async def test_a_live_task_is_cancelled_only_by_its_owner(server) -> None:
    held = task_of(await _send(server, ALICE, "hold", str(uuid.uuid4()), returnImmediately=True))
    assert state_of(held) == "TASK_STATE_WORKING"

    assert error_of(await _call(server, BOB, "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert server.agent.cancels == 0

    cancelled = (await _call(server, ALICE, "CancelTask", {"id": held["id"]}))["result"]
    assert state_of(cancelled) == "TASK_STATE_CANCELED"
    assert server.agent.cancels == 1


async def test_context_history_is_open_to_its_owner_only(server) -> None:
    alices_only, shared = str(uuid.uuid4()), str(uuid.uuid4())
    await _send(server, ALICE, "done", alices_only)
    await _send(server, ALICE, "done", shared)
    await _send(server, BOB, "done", shared)

    assert set((await _call(server, ALICE, "GetContexts", {}))["result"]) == {alices_only, shared}
    assert (await _call(server, BOB, "GetContexts", {}))["result"] == [shared]

    owners = (await _call(server, ALICE, "GetContext", {"context_id": alices_only}))["result"]
    theirs = (await _call(server, BOB, "GetContext", {"context_id": alices_only}))["result"]
    assert state_of(owners) == "TASK_STATE_COMPLETED"
    assert state_of(theirs) == "TASK_STATE_UNSPECIFIED"
    assert not theirs.get("history")


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}],
    ids=["no-token", "invalid-token"],
)
async def test_a_request_without_a_valid_token_never_reaches_the_agent(server, headers) -> None:
    runs = len(server.agent.runs)

    response = await server.client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "SendMessage",
            "params": {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "done"}]}},
        },
        headers={"A2A-Version": "1.0", **headers},
    )

    assert response.status_code == 401
    assert len(server.agent.runs) == runs
