"""The caller a stock ``aion serve`` names, over the A2A wire, on both task stores.

The middlewares are the ones ``AppFactory`` installs, and the tokens are signed
with a stand-in for Aion's published key, so the owner of a request is the
``sub`` of its bearer token and nothing else. Two callers present the same
``contextId`` throughout; they hold invocation tokens or anonymous session
tokens. Their tasks are isolated the same way. Their contexts are not: two
invocations from one gateway conversation share it, while an anonymous
session's context is its own and the other session is refused entry.

A verified caller is authenticated: its context history, finding its
interrupted task through the ``contextId``, subscribing to its tasks and their
push notification configs are open to it, and only to it. A
request without a valid token never reaches the agent.

The in-memory run and the PostgreSQL run are the same test.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

import httpx
import pytest
import pytest_asyncio
from starlette.middleware import Middleware

from aion.server.auth import JwksKeySource, Principal, TokenVerifier
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware

from tests.support.aion_tokens import CLIENT_ID, ISSUER, SigningKey, subject

from .jsonrpc_harness import (
    TASK_NOT_FOUND,
    UNSUPPORTED_OPERATION,
    JsonRpcServer,
    error_of,
    serving,
    state_of,
    task_of,
)
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SIGNER = SigningKey()
MISSING_AUTHENTICATION = -32051
"""Aion's JSON-RPC code for a request refused for its authentication."""
"""The stand-in Aion: it publishes this key and signs every caller's token with it."""

ALICE = subject("AionUser", "alice")
BOB = subject("AionUser", "bob")
SESSION_A = subject("AnonymousSession", "0c5a3e1f-7b2d-4a6c-9e8f-1a2b3c4d5e6f")
SESSION_B = subject("AnonymousSession", "5e6f7a8b-9c0d-4e1f-a2b3-c4d5e6f7a8b9")


def _key_endpoint() -> JwksKeySource:
    """A key source whose HTTP is a stand-in Aion key endpoint serving the signer's key."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=SIGNER.jwks()))
    return JwksKeySource(
        "https://api.aion.example/runtime/a2a/verification-keys",
        client=httpx.AsyncClient(transport=transport),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def server(request, _database):
    # The middlewares AppFactory installs, outermost first.
    verifier = TokenVerifier(_key_endpoint(), issuer=ISSUER, invocation_audience=CLIENT_ID)
    await verifier.load()
    middleware = [
        Middleware(AionAuthMiddleware, verifier=verifier),
        Middleware(AionContextMiddleware),
    ]
    async with serving(request.param, _database, middleware) as built:
        yield built


def _token(caller: str, signer: SigningKey = SIGNER) -> str:
    """The token of a caller: an anonymous session for an ``AnonymousSession``, an invocation otherwise."""
    principal = Principal.from_subject(caller)
    if principal.type == "AnonymousSession":
        return signer.session_token(principal.id)
    return signer.invocation_token(principal.id, principal_type=principal.type)


@pytest.fixture(
    params=[(ALICE, BOB), (SESSION_A, SESSION_B)],
    ids=["invocation-tokens", "anonymous-sessions"],
)
def callers(request) -> tuple[str, str]:
    """Two callers that present the same context: holders of invocation tokens, or of anonymous session tokens."""
    return request.param


def _forged_session_token() -> str:
    """SESSION_A's token, signed by another key under the published key's ``kid``."""
    forger = SigningKey()
    forger.kid = SIGNER.kid
    return _token(SESSION_A, forger)


def _as(server: JsonRpcServer, subject: str) -> dict[str, str]:
    """The headers of a caller."""
    return {"Authorization": f"Bearer {_token(subject)}"}


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


def _share_contexts(caller: str) -> bool:
    """Whether callers of this kind share a context: invocations of one gateway conversation do."""
    return Principal.from_subject(caller).type != "AnonymousSession"


async def test_two_callers_on_one_context_keep_their_tasks_apart(server, callers) -> None:
    alice, bob = callers
    context_id = str(uuid.uuid4())
    first = task_of(await _send(server, alice, "done", context_id))
    runs = len(server.agent.runs)
    if _share_contexts(alice):
        second = task_of(await _send(server, bob, "done", context_id))
    else:
        assert error_of(await _send(server, bob, "done", context_id)) == TASK_NOT_FOUND
        assert len(server.agent.runs) == runs
        second = task_of(await _send(server, bob, "done", str(uuid.uuid4())))
    assert first["id"] != second["id"]

    # The header names the caller of every method, GetTask and ListTasks included.
    assert (await _call(server, alice, "GetTask", {"id": first["id"]}))["result"]["id"] == first["id"]
    assert error_of(await _call(server, bob, "GetTask", {"id": first["id"]})) == TASK_NOT_FOUND
    listed = (await _call(server, bob, "ListTasks", {"contextId": second["contextId"]}))["result"]["tasks"]
    assert [task["id"] for task in listed] == [second["id"]]


async def test_a_caller_cannot_continue_another_callers_task(server, callers) -> None:
    alice, bob = callers
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, alice, "ask", context_id))
    assert state_of(asked) == "TASK_STATE_INPUT_REQUIRED"
    runs = len(server.agent.runs)

    assert error_of(await _send(server, bob, "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs

    resumed = task_of(await _send(server, alice, "done", context_id, task_id=asked["id"]))
    assert (resumed["id"], state_of(resumed)) == (asked["id"], "TASK_STATE_COMPLETED")


async def test_an_interrupted_task_is_found_through_its_context_by_its_owner_only(server, callers) -> None:
    alice, bob = callers
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, alice, "ask", context_id))

    if _share_contexts(alice):
        second = task_of(await _send(server, bob, "done", context_id))
        assert second["id"] != asked["id"]
    else:
        assert error_of(await _send(server, bob, "done", context_id)) == TASK_NOT_FOUND
    first = task_of(await _send(server, alice, "done", context_id))

    assert (first["id"], state_of(first)) == (asked["id"], "TASK_STATE_COMPLETED")


async def test_a_live_task_is_cancelled_only_by_its_owner(server, callers) -> None:
    alice, bob = callers
    held = task_of(await _send(server, alice, "hold", str(uuid.uuid4()), returnImmediately=True))
    assert state_of(held) == "TASK_STATE_WORKING"

    assert error_of(await _call(server, bob, "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert server.agent.cancels == 0

    cancelled = (await _call(server, alice, "CancelTask", {"id": held["id"]}))["result"]
    assert state_of(cancelled) == "TASK_STATE_CANCELED"
    assert server.agent.cancels == 1


async def test_context_history_is_open_to_its_owner_only(server, callers) -> None:
    alice, bob = callers
    first_only, shared = str(uuid.uuid4()), str(uuid.uuid4())
    await _send(server, alice, "done", first_only)
    await _send(server, alice, "done", shared)
    bobs = await _send(server, bob, "done", shared)

    async def contexts(caller) -> set[str]:
        return {entry["contextId"] for entry in (await _call(server, caller, "GetContexts", {}))["result"]}

    assert await contexts(alice) == {first_only, shared}
    if _share_contexts(alice):
        # One gateway conversation: both participants are bound to it and
        # each reads the whole of it, the other's turns included.
        assert await contexts(bob) == {shared}
        alices_view = (await _call(server, alice, "GetContext", {"contextId": shared}))["result"]
        bobs_view = (await _call(server, bob, "GetContext", {"contextId": shared}))["result"]
        assert alices_view == bobs_view
    else:
        assert error_of(bobs) == TASK_NOT_FOUND
        assert await contexts(bob) == set()

    owners = (await _call(server, alice, "GetContext", {"contextId": first_only}))["result"]
    assert state_of(owners) == "TASK_STATE_COMPLETED"
    others = await _call(server, bob, "GetContext", {"contextId": first_only})
    assert others["error"]["code"] == 1000


async def test_a_finished_task_is_subscribed_to_only_by_its_owner(server, callers) -> None:
    alice, bob = callers
    done = task_of(await _send(server, alice, "done", str(uuid.uuid4())))

    assert error_of(await _call(server, bob, "SubscribeToTask", {"id": done["id"]})) == TASK_NOT_FOUND
    # The owner is told there is nothing left to stream, which says the task exists.
    assert error_of(await _call(server, alice, "SubscribeToTask", {"id": done["id"]})) == UNSUPPORTED_OPERATION


async def test_a_live_task_is_subscribed_to_only_by_its_owner(server, callers) -> None:
    alice, bob = callers
    held = task_of(await _send(server, alice, "hold", str(uuid.uuid4()), returnImmediately=True))

    assert error_of(await _call(server, bob, "SubscribeToTask", {"id": held["id"]})) == TASK_NOT_FOUND

    subscription = asyncio.create_task(_call(server, alice, "SubscribeToTask", {"id": held["id"]}))
    await asyncio.sleep(0.2)
    assert not subscription.done(), subscription.result()
    server.agent.release.set()
    events = await asyncio.wait_for(subscription, timeout=20)

    assert all("error" not in event for event in events), events
    tasks = [event["result"]["task"] for event in events]
    assert {task["id"] for task in tasks} == {held["id"]}
    assert state_of(tasks[-1]) == "TASK_STATE_COMPLETED"


async def test_push_configs_belong_to_the_tasks_owner(server, callers) -> None:
    alice, bob = callers
    task_id = task_of(await _send(server, alice, "done", str(uuid.uuid4())))["id"]
    config = {"taskId": task_id, "id": "hook-1", "url": "https://hooks.example/alice"}

    assert error_of(await _call(server, bob, "CreateTaskPushNotificationConfig", config)) == TASK_NOT_FOUND
    created = await _call(server, alice, "CreateTaskPushNotificationConfig", config)
    assert created["result"]["url"] == config["url"], created

    selector = {"taskId": task_id, "id": "hook-1"}
    assert error_of(await _call(server, bob, "GetTaskPushNotificationConfig", selector)) == TASK_NOT_FOUND
    assert error_of(await _call(server, bob, "ListTaskPushNotificationConfigs", {"taskId": task_id})) == TASK_NOT_FOUND
    assert error_of(await _call(server, bob, "DeleteTaskPushNotificationConfig", selector)) == TASK_NOT_FOUND

    # Nothing the other caller did reached the owner's config.
    owners = await _call(server, alice, "GetTaskPushNotificationConfig", selector)
    assert owners["result"]["url"] == config["url"], owners
    listed = (await _call(server, alice, "ListTaskPushNotificationConfigs", {"taskId": task_id}))["result"]
    assert [item["id"] for item in listed["configs"]] == ["hook-1"]

    assert "error" not in await _call(server, alice, "DeleteTaskPushNotificationConfig", selector)
    listed = (await _call(server, alice, "ListTaskPushNotificationConfigs", {"taskId": task_id}))["result"]
    assert not listed.get("configs")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": f"Bearer {_forged_session_token()}"},
    ],
    ids=["no-token", "invalid-token", "session-token-not-signed-by-the-control-plane"],
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
    assert response.json()["error"]["code"] == MISSING_AUTHENTICATION
    assert len(server.agent.runs) == runs


async def test_a_renewed_session_token_is_the_same_caller(server) -> None:
    """Renewal changes the bearer, not the session: the new token reads and continues the old token's task."""
    session_id = Principal.from_subject(SESSION_A).id
    first = SIGNER.session_token(session_id, issued_at=int(time.time()) - 600)
    renewed = SIGNER.session_token(session_id)
    assert first != renewed
    context_id = str(uuid.uuid4())

    asked = task_of(
        await server.send("ask", context_id, None, headers={"Authorization": f"Bearer {first}"})
    )
    renewed_headers = {"Authorization": f"Bearer {renewed}"}
    read = await server.rpc("GetTask", {"id": asked["id"]}, headers=renewed_headers)
    assert read["result"]["id"] == asked["id"]
    resumed = task_of(await server.send("done", context_id, asked["id"], headers=renewed_headers))
    assert (resumed["id"], state_of(resumed)) == (asked["id"], "TASK_STATE_COMPLETED")


def _unattributed() -> dict[str, str]:
    """An invocation whose initiator Aion kept only as the common ``ExternalAnonymous`` attribution."""
    token = SIGNER.invocation_token("anonymous", principal_type="ExternalAnonymous", assurance="unattributed")
    return {"Authorization": f"Bearer {token}"}


async def test_an_unattributed_invocation_runs_but_reaches_no_task_after(server) -> None:
    """Its subject is shared by everyone anonymous, so it tells no initiator apart: it starts tasks, nothing more."""
    context_id = str(uuid.uuid4())
    asked = task_of(await server.send("ask", context_id, headers=_unattributed()))
    assert state_of(asked) == "TASK_STATE_INPUT_REQUIRED"
    selector = {"taskId": asked["id"], "id": "hook-1"}

    refused = [
        await server.rpc("GetTask", {"id": asked["id"]}, headers=_unattributed()),
        await server.rpc("CancelTask", {"id": asked["id"]}, headers=_unattributed()),
        await server.rpc("SubscribeToTask", {"id": asked["id"]}, headers=_unattributed()),
        await server.rpc("CreateTaskPushNotificationConfig", {**selector, "url": "https://hooks.example/x"},
                         headers=_unattributed()),
        await server.rpc("GetTaskPushNotificationConfig", selector, headers=_unattributed()),
        await server.rpc("ListTaskPushNotificationConfigs", {"taskId": asked["id"]}, headers=_unattributed()),
        await server.rpc("DeleteTaskPushNotificationConfig", selector, headers=_unattributed()),
        await server.send("done", context_id, asked["id"], headers=_unattributed()),
    ]
    assert [error_of(answer) for answer in refused] == [TASK_NOT_FOUND] * len(refused)
    assert server.agent.cancels == 0

    assert (await server.rpc("ListTasks", {}, headers=_unattributed()))["result"].get("tasks", []) == []
    assert (await server.rpc("GetContexts", {}, headers=_unattributed()))["result"] == []
    unattributed_read = await server.rpc("GetContext", {"contextId": context_id}, headers=_unattributed())
    assert unattributed_read["error"]["code"] == 1000
    # Nor can it delete what it cannot see: the answer is success, and nothing changes.
    deleted = await server.rpc("DeleteContext", {"contextId": context_id}, headers=_unattributed())
    assert deleted["result"] == {"contextId": context_id}

    # A message into the context starts a task of its own; the interrupted one is nobody's to resume.
    fresh = task_of(await server.send("done", context_id, headers=_unattributed()))
    assert fresh["id"] != asked["id"]

    # The request that creates a task follows it to the end, streamed too.
    streamed = await server.rpc(
        "SendStreamingMessage",
        {"message": {"messageId": "m-s", "contextId": context_id, "role": "ROLE_USER", "parts": [{"text": "done"}]}},
        headers=_unattributed(),
    )
    assert all("error" not in event for event in streamed), streamed
    assert state_of(streamed[-1]["result"]["task"]) == "TASK_STATE_COMPLETED"
