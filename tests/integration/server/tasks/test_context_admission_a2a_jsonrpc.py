"""Who may use a context, over the A2A wire, on both stores: the holder admitted into it first.

The middlewares are the ones ``AppFactory`` installs and the tokens are signed
with a stand-in for Aion's key. An anonymous session holds a context
privately; invocations of one gateway conversation - one receiving agent
identity at one edge environment - hold it together. Anyone else who presents
the context ID is refused before anything happens, exactly as for a task that
does not exist, and the refusal leaves nothing behind. The reservation
outlives the tasks in the context, because the framework state keyed by it
does.

The in-memory run and the PostgreSQL run are the same test, except where a
test is about the database itself: several servers, a restart, another agent.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from starlette.middleware import Middleware

from aion.db.postgres import db_manager
from aion.server.auth import JwksKeySource, TokenVerifier
from aion.server.core.middlewares import AionAuthMiddleware, AionContextMiddleware
from aion.server.tasks.admission import ContextHolder, PostgresContextAdmission

from tests.support.aion_tokens import CLIENT_ID, ISSUER, SigningKey

from .jsonrpc_harness import TASK_NOT_FOUND, JsonRpcServer, error_of, serving, state_of, task_of
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SIGNER = SigningKey()

OTHER_EDGE = "2b4c6d8e-0f1a-4b3c-8d5e-7f9a1b2c3d4e"
OTHER_AGENT_IDENTITY = "8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0"


def _middleware() -> list[Middleware]:
    keys = JwksKeySource(
        "https://api.aion.example/runtime/a2a/verification-keys",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=SIGNER.jwks()))
        ),
    )
    verifier = TokenVerifier(keys, issuer=ISSUER, invocation_audience=CLIENT_ID)
    return [Middleware(AionAuthMiddleware, verifier=verifier), Middleware(AionContextMiddleware)]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def server(request, _database):
    async with serving(request.param, _database, _middleware()) as built:
        built.kind = request.param
        yield built


@pytest_asyncio.fixture(loop_scope="module")
async def postgres_server(_database):
    async with serving("postgres", _database, _middleware()) as built:
        yield built


def _session() -> dict[str, str]:
    """A new anonymous session's headers."""
    return _bearer(SIGNER.session_token(str(uuid.uuid4())))


def _invocation(principal_id: str, **claims: Any) -> dict[str, str]:
    """Headers of an invocation from the default gateway conversation unless ``claims`` move it."""
    return _bearer(SIGNER.invocation_token(principal_id, **claims))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _send(server: JsonRpcServer, headers: dict[str, str], text: str, context_id: str, task_id=None, **config):
    return await server.send(text, context_id, task_id, headers=headers, **config)


async def _tasks_of(server: JsonRpcServer, headers: dict[str, str]) -> list[dict]:
    return (await server.rpc("ListTasks", {}, headers=headers))["result"].get("tasks", [])


async def test_a_session_reuses_its_own_context(server) -> None:
    alice, context_id = _session(), str(uuid.uuid4())

    first = task_of(await _send(server, alice, "done", context_id))
    second = task_of(await _send(server, alice, "done", context_id))

    assert first["contextId"] == second["contextId"] == context_id


async def test_another_session_is_refused_before_anything_happens(server) -> None:
    alice, bob, context_id = _session(), _session(), str(uuid.uuid4())
    await _send(server, alice, "done", context_id)
    runs = len(server.agent.runs)

    unary = await _send(
        server,
        bob,
        "done",
        context_id,
        taskPushNotificationConfig={"url": "https://hooks.example/bob"},
    )
    streamed = await server.rpc(
        "SendStreamingMessage",
        {"message": {"messageId": "m-1", "contextId": context_id, "role": "ROLE_USER", "parts": [{"text": "done"}]}},
        headers=bob,
    )

    assert error_of(unary) == TASK_NOT_FOUND
    assert [error_of(event) for event in streamed] == [TASK_NOT_FOUND]
    # Nothing ran, nothing was stored for bob, and the context is still alice's.
    assert len(server.agent.runs) == runs
    assert await _tasks_of(server, bob) == []
    assert task_of(await _send(server, alice, "done", context_id))["contextId"] == context_id


async def test_the_refusal_is_the_one_for_a_task_that_does_not_exist(server) -> None:
    """Whoever holds the context, and whether anyone does, the answer is the same."""
    alice, bob, context_id = _session(), _session(), str(uuid.uuid4())
    await _send(server, alice, "done", context_id)

    refused = await _send(server, bob, "done", context_id)
    missing = await _send(server, bob, "done", str(uuid.uuid4()), task_id=str(uuid.uuid4()))

    assert error_of(refused) == error_of(missing) == TASK_NOT_FOUND


async def test_a_message_without_a_context_reserves_the_one_it_is_given(server) -> None:
    alice, bob = _session(), _session()
    started = task_of(await _send(server, alice, "done", ""))

    assert started["contextId"]
    assert error_of(await _send(server, bob, "done", started["contextId"])) == TASK_NOT_FOUND


async def test_a_continuation_names_its_tasks_context_or_none(server) -> None:
    alice = _session()
    asked = task_of(await _send(server, alice, "ask", str(uuid.uuid4())))

    elsewhere = await _send(server, alice, "done", str(uuid.uuid4()), task_id=asked["id"])
    resumed = task_of(await _send(server, alice, "done", "", task_id=asked["id"]))

    assert error_of(elsewhere) == TASK_NOT_FOUND
    assert (resumed["id"], resumed["contextId"], state_of(resumed)) == (
        asked["id"],
        asked["contextId"],
        "TASK_STATE_COMPLETED",
    )


async def test_two_first_callers_race_and_exactly_one_gets_in(server) -> None:
    context_id = str(uuid.uuid4())
    callers = [_session() for _ in range(4)]

    answers = await asyncio.gather(*(_send(server, caller, "done", context_id) for caller in callers))

    admitted = [caller for caller, answer in zip(callers, answers) if "error" not in answer]
    assert len(admitted) == 1
    assert all(error_of(answer) == TASK_NOT_FOUND for answer in answers if "error" in answer)
    assert "error" not in await _send(server, admitted[0], "done", context_id)


async def test_the_context_stays_reserved_after_its_tasks_are_gone(server) -> None:
    """Checkpoints and sessions outlive task rows; so does the reservation that guards them."""
    alice, bob, context_id = _session(), _session(), str(uuid.uuid4())
    await _send(server, alice, "done", context_id)

    if server.kind == "memory":
        server.store.tasks.clear()
    else:
        async with db_manager.get_session() as session:
            await session.execute(text("TRUNCATE task_claims, tasks CASCADE"))
            await session.commit()

    assert error_of(await _send(server, bob, "done", context_id)) == TASK_NOT_FOUND
    assert "error" not in await _send(server, alice, "done", context_id)


async def test_one_gateway_conversation_shares_its_context(server) -> None:
    context_id = str(uuid.uuid4())
    alice = _invocation("alice")
    bob = _invocation("bob")
    guest = _invocation(str(uuid.uuid4()), principal_type="AnonymousSession", assurance="session")

    tasks = [task_of(await _send(server, caller, "done", context_id)) for caller in (alice, bob, guest)]

    assert {task["contextId"] for task in tasks} == {context_id}
    assert len({task["id"] for task in tasks}) == 3


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (lambda: _invocation("alice"), lambda: _invocation("alice", edge_agent_environment_id=OTHER_EDGE)),
        (lambda: _invocation("alice"), lambda: _invocation("alice", owner_agent_identity_id=OTHER_AGENT_IDENTITY)),
        (lambda: _invocation("alice"), _session),
        (_session, lambda: _invocation("alice")),
    ],
    ids=["other-edge", "other-agent-identity", "session-into-gateway", "gateway-into-session"],
)
async def test_a_context_is_private_or_one_gateway_conversations_never_both(server, first, second) -> None:
    context_id = str(uuid.uuid4())
    await _send(server, first(), "done", context_id)
    runs = len(server.agent.runs)

    assert error_of(await _send(server, second(), "done", context_id)) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs


async def test_the_reservation_survives_a_restart(postgres_server) -> None:
    alice, bob, context_id = _session(), _session(), str(uuid.uuid4())
    await _send(postgres_server, alice, "done", context_id)

    restarted = JsonRpcServer("postgres", _middleware())
    try:
        assert error_of(await _send(restarted, bob, "done", context_id)) == TASK_NOT_FOUND
        assert "error" not in await _send(restarted, alice, "done", context_id)
    finally:
        await restarted.aclose()


async def test_two_servers_on_one_database_admit_one_holder(postgres_server) -> None:
    context_id = str(uuid.uuid4())
    other = JsonRpcServer("postgres", _middleware())
    try:
        alice, bob = _session(), _session()
        answers = await asyncio.gather(
            _send(postgres_server, alice, "done", context_id),
            _send(other, bob, "done", context_id),
        )
    finally:
        await other.aclose()

    assert sorted("error" in answer for answer in answers) == [False, True]


async def test_each_agent_reserves_its_own_contexts(postgres_server) -> None:
    context_id = str(uuid.uuid4())
    alice, bob = ContextHolder.private("alice"), ContextHolder.private("bob")

    assert await PostgresContextAdmission("agent-one", db_manager).admit(context_id, alice)
    assert await PostgresContextAdmission("agent-two", db_manager).admit(context_id, bob)
    assert not await PostgresContextAdmission("agent-one", db_manager).admit(context_id, bob)


async def test_a_refusal_writes_no_reservation(postgres_server) -> None:
    context_id = str(uuid.uuid4())
    admission = PostgresContextAdmission("agent-one", db_manager)
    await admission.admit(context_id, ContextHolder.private("alice"))

    assert not await admission.admit(context_id, ContextHolder.shared(OTHER_AGENT_IDENTITY, OTHER_EDGE))

    async with db_manager.get_session() as session:
        rows = (
            await session.execute(
                text("SELECT kind, owner_scope FROM context_reservations WHERE context_id = :id"),
                {"id": context_id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [("private", "alice")]


async def test_in_a_shared_conversation_only_the_initiator_cancels_a_task(server) -> None:
    """Deleting a conversation, Aion cancels each task as its initiator; a participant cannot."""
    context_id = str(uuid.uuid4())
    held = task_of(
        await _send(server, _invocation("alice"), "hold", context_id, returnImmediately=True)
    )

    refused = await server.rpc("CancelTask", {"id": held["id"]}, headers=_invocation("bob"))
    assert error_of(refused) == TASK_NOT_FOUND
    assert server.agent.cancels == 0

    # Aion, acting for the task's persisted initiator, presents alice's invocation.
    cancelled = await server.rpc("CancelTask", {"id": held["id"]}, headers=_invocation("alice"))
    assert state_of(cancelled["result"]) == "TASK_STATE_CANCELED"
