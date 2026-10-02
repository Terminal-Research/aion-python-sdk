"""Owner isolation over the A2A wire, on both task stores.

A real JSON-RPC request path, end to end: a2a-sdk's
``DefaultServerCallContextBuilder`` takes the user from the trusted
``request.scope["user"]``, and ``AionJsonRpcDispatcher`` hands the
``ServerCallContext`` to ``AionRequestHandler``, whose store filters by its
``owner_resolver`` (a2a-sdk's ``resolve_user_scope``, the user name). A
context is private to the user who started it: another user who presents its
``contextId`` is refused like a foreign task.

The identity is this test's own: it installs Starlette's
``AuthenticationMiddleware`` with a backend that takes an authenticated user
from a test header, so every operation - v1's ``GetTask``, ``ListTasks`` and
``SubscribeToTask`` included, which carry no metadata - can be made as either
user. What is proven here is the isolation itself, whatever puts the user
into ``request.scope["user"]``. The caller a stock ``aion serve`` names, the
distribution, is covered by ``test_distribution_isolation_a2a_jsonrpc``.

Every operation a client can aim at a task it did not start answers as if
the task did not exist - ``TaskNotFound`` (-32001) or an empty listing -
and changes nothing: send (continuing a foreign ``taskId``, or a foreign
interrupted task found through the ``contextId``), get, list, cancel and
subscribe, on live and finished tasks, and Aion's ``GetContexts`` and
``GetContext`` - and sending into another user's context. The owner's own calls work as before, including the errors
that reveal a task exists (not cancelable, terminal).

The in-memory run and the PostgreSQL run are the same test; the lease owner
of task ownership (a server process) plays no part in any of it.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import pytest
import pytest_asyncio
from a2a.server.context import ServerCallContext
from a2a.types import TaskState
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

from aion.server.core.middlewares import AionContextMiddleware
from aion.server.tasks.stores import TaskOwnerUndefinedError

from .jsonrpc_harness import (
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    JsonRpcServer,
    error_of,
    serving,
    state_of,
    task_of,
)
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]


class _HeaderAuth(AuthenticationBackend):
    """This test's stand-in for real authentication: the user named in ``X-Test-User``.

    Trusting a header is only acceptable because nothing but the test can
    reach this app. Without the header the request is unauthenticated.
    """

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


def _as(user: Optional[str]) -> Optional[dict[str, str]]:
    return {"X-Test-User": user} if user else None


async def _call(server: JsonRpcServer, user: Optional[str], method: str, params: dict[str, Any]) -> Any:
    return await server.rpc(method, params, headers=_as(user))


async def _send(
    server: JsonRpcServer,
    user: Optional[str],
    text: str,
    context_id: str,
    task_id: Optional[str] = None,
    **config: Any,
) -> Any:
    return await server.send(text, context_id, task_id, headers=_as(user), **config)


async def _get(server: JsonRpcServer, user: Optional[str], task_id: str) -> dict:
    return await _call(server, user, "GetTask", {"id": task_id})


async def test_send_get_and_list_keep_each_users_tasks_apart(server) -> None:
    alices_context, mallorys_context = str(uuid.uuid4()), str(uuid.uuid4())
    alices = task_of(await _send(server, "alice", "done", alices_context))
    runs = len(server.agent.runs)
    assert error_of(await _send(server, "mallory", "done", alices_context)) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs
    mallorys = task_of(await _send(server, "mallory", "done", mallorys_context))
    assert (alices["contextId"], mallorys["contextId"]) == (alices_context, mallorys_context)

    assert (await _get(server, "alice", alices["id"]))["result"]["id"] == alices["id"]
    assert error_of(await _get(server, "mallory", alices["id"])) == TASK_NOT_FOUND
    assert error_of(await _get(server, None, alices["id"])) == TASK_NOT_FOUND

    for user, own in (("alice", alices), ("mallory", mallorys)):
        for params in ({}, {"contextId": own["contextId"]}):
            listing = (await _call(server, user, "ListTasks", params))["result"]
            assert [task["id"] for task in listing["tasks"]] == [own["id"]], (user, params)
            assert listing["totalSize"] == 1
    foreign = (await _call(server, "mallory", "ListTasks", {"contextId": alices_context}))["result"]
    assert foreign.get("tasks", []) == []
    assert (await _call(server, None, "ListTasks", {}))["result"].get("tasks", []) == []


async def test_send_cannot_continue_another_users_task(server) -> None:
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, "alice", "ask", context_id))
    assert state_of(asked) == "TASK_STATE_INPUT_REQUIRED"
    runs = len(server.agent.runs)

    assert error_of(await _send(server, "mallory", "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    # The context alone does not let them in either.
    assert error_of(await _send(server, "mallory", "done", context_id)) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs

    still = (await _get(server, "alice", asked["id"]))["result"]
    assert state_of(still) == "TASK_STATE_INPUT_REQUIRED"
    assert len(still.get("history", [])) == len(asked.get("history", []))

    resumed = task_of(await _send(server, "alice", "done", context_id))
    assert resumed["id"] == asked["id"]
    assert state_of(resumed) == "TASK_STATE_COMPLETED"


async def test_a_finished_task_answers_only_its_owner(server) -> None:
    done = task_of(await _send(server, "alice", "done", str(uuid.uuid4())))

    assert error_of(await _call(server, "mallory", "CancelTask", {"id": done["id"]})) == TASK_NOT_FOUND
    assert error_of(await _call(server, "alice", "CancelTask", {"id": done["id"]})) == TASK_NOT_CANCELABLE

    theirs = await _call(server, "mallory", "SubscribeToTask", {"id": done["id"]})
    assert error_of(theirs) == TASK_NOT_FOUND
    # The owner is answered with the task as it ended.
    [owners] = await _call(server, "alice", "SubscribeToTask", {"id": done["id"]})
    assert owners["result"]["task"]["id"] == done["id"]
    assert state_of(owners["result"]["task"]) == "TASK_STATE_COMPLETED"


async def test_a_live_task_can_be_subscribed_to_and_cancelled_only_by_its_owner(server) -> None:
    context_id = str(uuid.uuid4())
    held = task_of(await _send(server, "alice", "hold", context_id, returnImmediately=True))
    assert state_of(held) == "TASK_STATE_WORKING"

    theirs = await _call(server, "mallory", "SubscribeToTask", {"id": held["id"]})
    assert error_of(theirs) == TASK_NOT_FOUND
    assert error_of(await _call(server, "mallory", "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert server.agent.cancels == 0
    assert state_of((await _get(server, "alice", held["id"]))["result"]) == "TASK_STATE_WORKING"

    cancelled = (await _call(server, "alice", "CancelTask", {"id": held["id"]}))["result"]
    assert state_of(cancelled) == "TASK_STATE_CANCELED"
    assert server.agent.cancels == 1


async def test_the_owners_subscription_follows_the_live_task_to_its_end(server) -> None:
    held = task_of(await _send(server, "alice", "hold", str(uuid.uuid4()), returnImmediately=True))
    subscription = asyncio.create_task(_call(server, "alice", "SubscribeToTask", {"id": held["id"]}))
    await asyncio.sleep(0.2)
    assert not subscription.done(), subscription.result()

    server.agent.release.set()
    events = await asyncio.wait_for(subscription, timeout=20)

    # Attached while the task was live, then carried to its end: the stream
    # opens and closes with the task (see the streaming terminal contract).
    assert all("error" not in event for event in events), events
    tasks = [event["result"]["task"] for event in events]
    assert {task["id"] for task in tasks} == {held["id"]}
    assert state_of(tasks[0]) == "TASK_STATE_WORKING"
    assert state_of(tasks[-1]) == "TASK_STATE_COMPLETED"


async def test_aions_context_methods_hold_only_the_callers_contexts(server) -> None:
    """``GetContexts`` and ``GetContext``, Aion's method extensions, read through the same store."""
    alices_only, alices_other, mallorys = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await _send(server, "alice", "done", alices_only)
    await _send(server, "alice", "done", alices_other)
    await _send(server, "mallory", "done", mallorys)

    contexts = {
        user: (await _call(server, user, "GetContexts", {}))["result"] for user in ("alice", "mallory")
    }
    assert set(contexts["alice"]) == {alices_only, alices_other}
    assert contexts["mallory"] == [mallorys]

    owners = (await _call(server, "alice", "GetContext", {"context_id": alices_only}))["result"]
    theirs = (await _call(server, "mallory", "GetContext", {"context_id": alices_only}))["result"]
    assert owners["contextId"] == theirs["contextId"] == alices_only
    assert state_of(owners) == "TASK_STATE_COMPLETED"
    # No task of theirs on this context: the conversation is empty, as for
    # a context nobody has used.
    assert state_of(theirs) == "TASK_STATE_UNSPECIFIED"
    assert not theirs.get("history") and not theirs.get("artifacts")


async def test_a_task_created_without_a_context_never_reaches_an_anonymous_client(server) -> None:
    """``None`` names no owner, so the store refuses to create the task at all.

    An explicit ``ServerCallContext()`` names the anonymous owner instead,
    and then every unauthenticated client sees the task - which is what that
    owner means.
    """
    with pytest.raises(TaskOwnerUndefinedError):
        await server.write(TaskState.TASK_STATE_COMPLETED, None)
    assert (await _call(server, None, "ListTasks", {}))["result"].get("tasks", []) == []

    anonymous = await server.write(TaskState.TASK_STATE_COMPLETED, ServerCallContext())
    assert (await _get(server, None, anonymous))["result"]["id"] == anonymous
    assert error_of(await _get(server, "alice", anonymous)) == TASK_NOT_FOUND
