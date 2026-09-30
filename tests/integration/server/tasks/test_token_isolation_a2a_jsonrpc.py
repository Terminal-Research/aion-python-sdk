"""The caller a stock ``aion serve`` names, over the A2A wire, on both task stores.

The middlewares are the ones ``AppFactory`` installs, and the tokens are signed
with stand-ins for the platform's key and the control plane's, so the owner of
a request is the ``sub`` of its bearer token and nothing else. Two callers
present the same ``contextId`` throughout; they hold call tokens or anonymous
session tokens, and the two kinds isolate the same way.

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
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from starlette.middleware import Middleware

from aion.server.auth import JwksKeySource, TokenVerifier
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
SESSION_A = "aion:anonymous:session-a"
SESSION_B = "aion:anonymous:session-b"


CLIENT_ID = "client-123"
SESSION_KID = "session-key-1"
PLATFORM_KEY = ec.generate_private_key(ec.SECP256R1())
SESSION_KEY = ec.generate_private_key(ec.SECP256R1())


class _PlatformKey:
    """The stand-in platform's public key, where the server would have fetched it."""

    async def load(self) -> None:
        pass

    def current_key(self):
        return PLATFORM_KEY.public_key()


def _control_plane() -> JwksKeySource:
    """A key source whose HTTP is a stand-in control plane serving the session key."""
    document = {"keys": [{**ECAlgorithm.to_jwk(SESSION_KEY.public_key(), as_dict=True), "kid": SESSION_KID}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=document))
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
    verifier = TokenVerifier(
        call_keys=_PlatformKey(),
        call_audience=CLIENT_ID,
        session_keys=_control_plane(),
    )
    await verifier.load()
    middleware = [
        Middleware(AionAuthMiddleware, verifier=verifier),
        Middleware(AionContextMiddleware),
    ]
    async with serving(request.param, _database, middleware) as built:
        yield built


def _call_token(subject: str) -> str:
    now = int(time.time())
    claims = {"aud": CLIENT_ID, "sub": subject, "iat": now, "exp": now + 300}
    return jwt.encode(claims, PLATFORM_KEY, algorithm="ES256")


def _session_token(subject: str) -> str:
    now = int(time.time())
    claims = {
        "iss": "aion",
        "aud": "aion-anonymous-session",
        "sub": subject,
        "subject_type": "AnonymousSession",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(claims, SESSION_KEY, algorithm="ES256", headers={"kid": SESSION_KID})


@pytest.fixture(
    params=[(ALICE, BOB), (SESSION_A, SESSION_B)],
    ids=["call-tokens", "anonymous-sessions"],
)
def callers(request) -> tuple[str, str]:
    """Two callers that present the same context: holders of call tokens, or of anonymous session tokens."""
    return request.param


def _forged_session_token() -> str:
    now = int(time.time())
    claims = {
        "iss": "aion",
        "aud": "aion-anonymous-session",
        "sub": SESSION_A,
        "subject_type": "AnonymousSession",
        "exp": now + 300,
    }
    forger = ec.generate_private_key(ec.SECP256R1())
    return jwt.encode(claims, forger, algorithm="ES256", headers={"kid": SESSION_KID})


def _as(server: JsonRpcServer, subject: str) -> dict[str, str]:
    """The headers of a caller: an anonymous session for ``anonymous:`` subjects, a call token otherwise."""
    make = _session_token if subject.startswith("aion:anonymous:") else _call_token
    return {"Authorization": f"Bearer {make(subject)}"}


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


async def test_two_callers_on_one_context_keep_their_tasks_apart(server, callers) -> None:
    alice, bob = callers
    context_id = str(uuid.uuid4())
    first = task_of(await _send(server, alice, "done", context_id))
    second = task_of(await _send(server, bob, "done", context_id))
    assert first["id"] != second["id"]

    # The header names the caller of every method, GetTask and ListTasks included.
    assert (await _call(server, alice, "GetTask", {"id": first["id"]}))["result"]["id"] == first["id"]
    assert error_of(await _call(server, bob, "GetTask", {"id": first["id"]})) == TASK_NOT_FOUND
    listed = (await _call(server, bob, "ListTasks", {"contextId": context_id}))["result"]["tasks"]
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

    second = task_of(await _send(server, bob, "done", context_id))
    first = task_of(await _send(server, alice, "done", context_id))

    assert second["id"] != asked["id"]
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
    await _send(server, bob, "done", shared)

    assert set((await _call(server, alice, "GetContexts", {}))["result"]) == {first_only, shared}
    assert (await _call(server, bob, "GetContexts", {}))["result"] == [shared]

    owners = (await _call(server, alice, "GetContext", {"context_id": first_only}))["result"]
    others = (await _call(server, bob, "GetContext", {"context_id": first_only}))["result"]
    assert state_of(owners) == "TASK_STATE_COMPLETED"
    assert state_of(others) == "TASK_STATE_UNSPECIFIED"
    assert not others.get("history")


async def test_a_finished_task_is_subscribed_to_only_by_its_owner(server, callers) -> None:
    alice, bob = callers
    done = task_of(await _send(server, alice, "done", str(uuid.uuid4())))

    assert error_of(await _call(server, bob, "SubscribeToTask", {"id": done["id"]})) == TASK_NOT_FOUND
    [owners] = await _call(server, alice, "SubscribeToTask", {"id": done["id"]})
    assert owners["result"]["task"]["id"] == done["id"]
    assert state_of(owners["result"]["task"]) == "TASK_STATE_COMPLETED"


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
    assert len(server.agent.runs) == runs
