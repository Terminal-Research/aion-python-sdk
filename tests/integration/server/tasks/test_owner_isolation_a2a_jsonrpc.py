"""Owner isolation over the A2A wire, on both task stores.

A real JSON-RPC request path, end to end: a2a-sdk's
``DefaultServerCallContextBuilder`` takes the user from the trusted
``request.scope["user"]``, and ``AionJsonRpcDispatcher`` hands the
``ServerCallContext`` to ``AionRequestHandler``, whose store filters by its
``owner_resolver`` (a2a-sdk's ``resolve_user_scope``, the user name). Two
users share one ``contextId`` throughout.

The identity is this test's own: it installs Starlette's
``AuthenticationMiddleware`` with a backend that takes the user from a test
header. ``AppFactory`` installs no authentication, so a stock ``aion serve``
sees every caller as the anonymous user - one owner for everyone. What is
proven here is isolation once the application provides a verified user;
authenticating that user is the application's job.

Every operation a client can aim at a task it did not start answers as if
the task did not exist - ``TaskNotFound`` (-32001) or an empty listing -
and changes nothing: send (continuing a foreign ``taskId``, or a foreign
interrupted task found through the ``contextId``), get, list, cancel and
subscribe, on live and finished tasks, and Aion's ``GetContexts`` and
``GetContext``. The owner's own calls work as before, including the errors
that reveal a task exists (not cancelable, terminal).

The in-memory run and the PostgreSQL run are the same test; the lease owner
of task ownership (a server process) plays no part in any of it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils import DEFAULT_RPC_URL
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route
from unittest.mock import Mock

from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.core.app.handlers import AionJsonRpcDispatcher, AionRequestHandler
from aion.server.core.middlewares.aion_context import AionContextMiddleware
from aion.server.tasks.stores import TaskOwnerUndefinedError
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore

from .postgres_support import POSTGRES_TEST_URL, prepared_database, provider, truncate

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002


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


class _Agent:
    """Answers by the message text: ``done``, ``ask`` (input required) or ``hold``."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str, str]] = []
        self.release = asyncio.Event()
        self.cancels = 0

    async def execute(self, context, event_queue) -> None:
        text = context.get_user_input()
        self.runs.append((context.task_id, context.context_id, text))
        state = {
            "done": TaskState.TASK_STATE_COMPLETED,
            "ask": TaskState.TASK_STATE_INPUT_REQUIRED,
            "hold": TaskState.TASK_STATE_WORKING,
        }[text]
        await event_queue.enqueue_event(
            Task(id=context.task_id, context_id=context.context_id, status=TaskStatus(state=state))
        )
        if state == TaskState.TASK_STATE_WORKING:
            await self.release.wait()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

    async def cancel(self, context, event_queue) -> None:
        self.cancels += 1
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _database():
    if not POSTGRES_TEST_URL:
        yield None
        return
    async with prepared_database() as manager:
        yield manager


class _Server:
    """One server as ``AppFactory`` wires it, minus what isolation does not touch."""

    def __init__(self, kind: str) -> None:
        if kind == "memory":
            self.store = InMemoryTaskStore()
            lease = self.store.ownership_provider
        else:
            lease = provider("pod-a")
            self.store = PostgresTaskStore(agent_id=lease.agent_id, ownership_provider=lease)
        self.lease = lease
        self.agent = _Agent()
        card = Mock()
        card.capabilities.streaming = True
        self.handler = AionRequestHandler(
            agent_executor=self.agent,
            task_store=self.store,
            agent_card=card,
            ownership_provider=lease,
            request_context_builder=AionRequestContextBuilder(
                task_store=self.store, auto_discover_interrupted_task=True
            ),
        )
        dispatcher = AionJsonRpcDispatcher(request_handler=self.handler, enable_v0_3_compat=True)
        app = Starlette(
            routes=[Route(DEFAULT_RPC_URL, endpoint=dispatcher.handle_requests, methods=["POST"])],
            middleware=[
                Middleware(AuthenticationMiddleware, backend=_HeaderAuth()),
                Middleware(AionContextMiddleware),
            ],
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def call(self, user: str | None, method: str, params: dict[str, Any]) -> Any:
        """One JSON-RPC call: the response object, or every event of a stream."""
        headers = {"A2A-Version": "1.0"}
        if user:
            headers["X-Test-User"] = user
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        response = await asyncio.wait_for(
            self.client.post(DEFAULT_RPC_URL, json=body, headers=headers), timeout=20
        )
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return [
                json.loads(line[len("data:"):])
                for chunk in response.text.split("\n\n")
                for line in chunk.splitlines()
                if line.startswith("data:")
            ]
        return response.json()

    async def send(self, user: str | None, text: str, context_id: str, task_id: str | None = None, **config):
        message = {
            "messageId": uuid.uuid4().hex,
            "contextId": context_id,
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        params: dict[str, Any] = {"message": message}
        if config:
            params["configuration"] = config
        return await self.call(user, "SendMessage", params)

    async def write(self, state: TaskState, context: ServerCallContext | None) -> str:
        """Create a task from Python, beside the server, as an integrator's code would."""
        task_id = str(uuid.uuid4())
        claim = await self.lease.acquire(task_id)
        try:
            await self.store.save(
                Task(id=task_id, context_id=str(uuid.uuid4()), status=TaskStatus(state=state)), context
            )
        finally:
            await self.lease.release(claim)
        return task_id

    async def aclose(self) -> None:
        self.agent.release.set()
        await self.client.aclose()
        await self.handler._active_task_registry.aclose()


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def server(request, _database):
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    built = _Server(request.param)
    try:
        yield built
    finally:
        await built.aclose()


def _task(response: dict) -> dict:
    assert "error" not in response, response
    return response["result"]["task"]


def _error(response: dict | list) -> int:
    """The error code of a call; a stream refused before it starts is a plain response."""
    if isinstance(response, list):
        assert len(response) == 1, response
        [response] = response
    assert "result" not in response, response
    return response["error"]["code"]


def _state(task: dict) -> str:
    return task["status"]["state"]


async def _get(server: _Server, user: str | None, task_id: str) -> dict:
    return await server.call(user, "GetTask", {"id": task_id})


async def test_send_get_and_list_on_one_context_keep_each_users_tasks_apart(server) -> None:
    context_id = str(uuid.uuid4())
    alices = _task(await server.send("alice", "done", context_id))
    mallorys = _task(await server.send("mallory", "done", context_id))
    assert alices["id"] != mallorys["id"]
    assert alices["contextId"] == mallorys["contextId"] == context_id

    assert (await _get(server, "alice", alices["id"]))["result"]["id"] == alices["id"]
    assert _error(await _get(server, "mallory", alices["id"])) == TASK_NOT_FOUND
    assert _error(await _get(server, None, alices["id"])) == TASK_NOT_FOUND

    for user, own in (("alice", alices), ("mallory", mallorys)):
        for params in ({}, {"contextId": context_id}):
            listing = (await server.call(user, "ListTasks", params))["result"]
            assert [task["id"] for task in listing["tasks"]] == [own["id"]], (user, params)
            assert listing["totalSize"] == 1
    assert (await server.call(None, "ListTasks", {}))["result"].get("tasks", []) == []


async def test_send_cannot_continue_another_users_task(server) -> None:
    context_id = str(uuid.uuid4())
    asked = _task(await server.send("alice", "ask", context_id))
    assert _state(asked) == "TASK_STATE_INPUT_REQUIRED"
    runs = len(server.agent.runs)

    assert _error(await server.send("mallory", "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs

    # The context alone must not find it either: the interrupted task the
    # context builder discovers is the caller's, and mallory has none.
    theirs = _task(await server.send("mallory", "done", context_id))
    assert theirs["id"] != asked["id"]
    assert server.agent.runs[-1][0] == theirs["id"]

    still = (await _get(server, "alice", asked["id"]))["result"]
    assert _state(still) == "TASK_STATE_INPUT_REQUIRED"
    assert len(still.get("history", [])) == len(asked.get("history", []))

    resumed = _task(await server.send("alice", "done", context_id))
    assert resumed["id"] == asked["id"]
    assert _state(resumed) == "TASK_STATE_COMPLETED"


async def test_a_finished_task_answers_only_its_owner(server) -> None:
    done = _task(await server.send("alice", "done", str(uuid.uuid4())))

    assert _error(await server.call("mallory", "CancelTask", {"id": done["id"]})) == TASK_NOT_FOUND
    assert _error(await server.call("alice", "CancelTask", {"id": done["id"]})) == TASK_NOT_CANCELABLE

    theirs = await server.call("mallory", "SubscribeToTask", {"id": done["id"]})
    assert _error(theirs) == TASK_NOT_FOUND
    # The owner is answered with the task as it ended.
    [owners] = await server.call("alice", "SubscribeToTask", {"id": done["id"]})
    assert owners["result"]["task"]["id"] == done["id"]
    assert _state(owners["result"]["task"]) == "TASK_STATE_COMPLETED"


async def test_a_live_task_can_be_subscribed_to_and_cancelled_only_by_its_owner(server) -> None:
    context_id = str(uuid.uuid4())
    held = _task(await server.send("alice", "hold", context_id, returnImmediately=True))
    assert _state(held) == "TASK_STATE_WORKING"

    theirs = await server.call("mallory", "SubscribeToTask", {"id": held["id"]})
    assert _error(theirs) == TASK_NOT_FOUND
    assert _error(await server.call("mallory", "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert server.agent.cancels == 0
    assert _state((await _get(server, "alice", held["id"]))["result"]) == "TASK_STATE_WORKING"

    cancelled = (await server.call("alice", "CancelTask", {"id": held["id"]}))["result"]
    assert _state(cancelled) == "TASK_STATE_CANCELED"
    assert server.agent.cancels == 1


async def test_the_owners_subscription_follows_the_live_task_to_its_end(server) -> None:
    held = _task(await server.send("alice", "hold", str(uuid.uuid4()), returnImmediately=True))
    subscription = asyncio.create_task(server.call("alice", "SubscribeToTask", {"id": held["id"]}))
    await asyncio.sleep(0.2)
    assert not subscription.done(), subscription.result()

    server.agent.release.set()
    events = await asyncio.wait_for(subscription, timeout=20)

    # Attached while the task was live, then carried to its end: the stream
    # opens and closes with the task (see the streaming terminal contract).
    assert all("error" not in event for event in events), events
    tasks = [event["result"]["task"] for event in events]
    assert {task["id"] for task in tasks} == {held["id"]}
    assert _state(tasks[0]) == "TASK_STATE_WORKING"
    assert _state(tasks[-1]) == "TASK_STATE_COMPLETED"


async def test_aions_context_methods_hold_only_the_callers_contexts(server) -> None:
    """``GetContexts`` and ``GetContext``, Aion's method extensions, read through the same store."""
    alices_only, shared = str(uuid.uuid4()), str(uuid.uuid4())
    await server.send("alice", "done", alices_only)
    await server.send("alice", "done", shared)
    await server.send("mallory", "done", shared)

    contexts = {
        user: (await server.call(user, "GetContexts", {}))["result"] for user in ("alice", "mallory")
    }
    assert set(contexts["alice"]) == {alices_only, shared}
    assert contexts["mallory"] == [shared]

    owners = (await server.call("alice", "GetContext", {"context_id": alices_only}))["result"]
    theirs = (await server.call("mallory", "GetContext", {"context_id": alices_only}))["result"]
    assert owners["contextId"] == theirs["contextId"] == alices_only
    assert _state(owners) == "TASK_STATE_COMPLETED"
    # No task of theirs on this context: the conversation is empty, as for
    # a context nobody has used.
    assert _state(theirs) == "TASK_STATE_UNSPECIFIED"
    assert not theirs.get("history") and not theirs.get("artifacts")


async def test_a_task_created_without_a_context_never_reaches_an_anonymous_client(server) -> None:
    """``None`` names no owner, so the store refuses to create the task at all.

    An explicit ``ServerCallContext()`` names the anonymous owner instead,
    and then every unauthenticated client sees the task - which is what that
    owner means.
    """
    with pytest.raises(TaskOwnerUndefinedError):
        await server.write(TaskState.TASK_STATE_COMPLETED, None)
    assert (await server.call(None, "ListTasks", {}))["result"].get("tasks", []) == []

    anonymous = await server.write(TaskState.TASK_STATE_COMPLETED, ServerCallContext())
    assert (await _get(server, None, anonymous))["result"]["id"] == anonymous
    assert _error(await _get(server, "alice", anonymous)) == TASK_NOT_FOUND
