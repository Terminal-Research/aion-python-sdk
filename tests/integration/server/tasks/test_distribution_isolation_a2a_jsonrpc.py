"""The caller a stock ``aion serve`` names, over the A2A wire, on both task stores.

The middlewares are the ones ``AppFactory`` installs, so the user a request
carries is the one the SDK names: the distribution in the request's
``params.metadata``, never authenticated, or the anonymous caller. Two
distributions of one agent present the same ``contextId`` throughout.

What they can reach follows from where a caller can be named. A2A v1's
``SendMessage`` and ``CancelTask`` carry metadata, and so does v0.3's
``tasks/get``; v1's ``GetTask`` and ``ListTasks`` do not, so they run as the
anonymous caller and never reach a distribution's task. A distribution is not
authenticated, so context history and finding an interrupted task through
its ``contextId`` stay closed to it.

The in-memory run and the PostgreSQL run are the same test.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest
import pytest_asyncio
from starlette.middleware import Middleware

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.server.core.middlewares import AionContextMiddleware, CallerIdentityMiddleware

from .jsonrpc_harness import (
    A2A_V03,
    A2A_V1,
    INVALID_PARAMS,
    INVALID_REQUEST,
    TASK_NOT_FOUND,
    JsonRpcServer,
    error_of,
    serving,
    state_of,
    task_of,
)
from .postgres_support import POSTGRES_TEST_URL, prepared_database

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

# The middlewares AppFactory installs, outermost first.
_MIDDLEWARE = [
    Middleware(CallerIdentityMiddleware),
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


def _distribution(distribution_id: str) -> dict[str, Any]:
    """``params.metadata`` as a platform request carries it, naming this distribution."""
    return {
        DISTRIBUTION_EXTENSION_URI_V1: {
            "distribution": {
                "id": distribution_id,
                "endpointType": "A2A",
                "url": "https://example.invalid/a2a",
                "identities": [],
            },
            "behavior": {"id": "behavior-1", "behaviorKey": "main", "versionId": "version-1"},
            "environment": {
                "id": "environment-1",
                "name": "test",
                "projectId": "project-1",
                "deploymentId": "deployment-1",
                "configurationVariables": {},
            },
        }
    }


async def _send(
    server: JsonRpcServer,
    distribution: Optional[str],
    text: str,
    context_id: str,
    task_id: Optional[str] = None,
    **config: Any,
) -> Any:
    """``SendMessage`` from this distribution, or from nobody for ``None``."""
    metadata = _distribution(distribution) if distribution is not None else None
    return await server.send(text, context_id, task_id, metadata=metadata, **config)


async def _call(
    server: JsonRpcServer,
    distribution: Optional[str],
    method: str,
    params: dict[str, Any],
    *,
    version: str = A2A_V1,
) -> Any:
    """One call from this distribution, or from nobody for ``None``."""
    if distribution is not None:
        params = {**params, "metadata": _distribution(distribution)}
    return await server.rpc(method, params, version=version)


def _v03_not_found(response: dict) -> bool:
    """Whether a v0.3 call was answered as if the task did not exist.

    The installed a2a-sdk (1.1.5) v0.3 adapter does not map ``TaskNotFoundError``
    to its code: the answer is -32603 with the message, so the message is
    what tells. The adapter is a2a-sdk's to fix, not this test's.
    """
    assert "result" not in response, response
    return response["error"]["message"] == "Task not found"


async def test_two_distributions_on_one_context_keep_their_tasks_apart(server) -> None:
    context_id = str(uuid.uuid4())
    as_ = task_of(await _send(server, "dist-a", "done", context_id))
    bs = task_of(await _send(server, "dist-b", "done", context_id))
    assert as_["id"] != bs["id"]
    assert as_["contextId"] == bs["contextId"] == context_id

    # v0.3's tasks/get carries metadata, so a distribution reads its own task there.
    own = await _call(server, "dist-a", "tasks/get", {"id": as_["id"]}, version=A2A_V03)
    assert own["result"]["id"] == as_["id"]
    assert _v03_not_found(await _call(server, "dist-b", "tasks/get", {"id": as_["id"]}, version=A2A_V03))

    # v1's GetTask and ListTasks carry none: they run as the anonymous caller.
    assert error_of(await _call(server, None, "GetTask", {"id": as_["id"]})) == TASK_NOT_FOUND
    assert (await _call(server, None, "ListTasks", {}))["result"].get("tasks", []) == []


async def test_v1_get_task_cannot_carry_a_distribution(server) -> None:
    """No metadata field in v1's GetTask: adding one is invalid params, not a way in.

    Pins why those methods are anonymous. Should a2a-sdk give them the field,
    this fails, and the methods can start naming their caller.
    """
    done = task_of(await _send(server, "dist-a", "done", str(uuid.uuid4())))

    assert error_of(await _call(server, "dist-a", "GetTask", {"id": done["id"]})) == INVALID_PARAMS


async def test_a_distribution_cannot_continue_another_distributions_task(server) -> None:
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, "dist-a", "ask", context_id))
    assert state_of(asked) == "TASK_STATE_INPUT_REQUIRED"
    runs = len(server.agent.runs)

    assert error_of(await _send(server, "dist-b", "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    assert error_of(await _send(server, None, "done", context_id, task_id=asked["id"])) == TASK_NOT_FOUND
    assert len(server.agent.runs) == runs

    resumed = task_of(await _send(server, "dist-a", "done", context_id, task_id=asked["id"]))
    assert resumed["id"] == asked["id"]
    assert state_of(resumed) == "TASK_STATE_COMPLETED"


async def test_an_interrupted_task_is_continued_by_its_task_id_only(server) -> None:
    """A distribution is not authenticated, so its ``contextId`` finds no interrupted task."""
    context_id = str(uuid.uuid4())
    asked = task_of(await _send(server, "dist-a", "ask", context_id))

    fresh = task_of(await _send(server, "dist-a", "done", context_id))

    assert fresh["id"] != asked["id"]
    still = (await _call(server, "dist-a", "tasks/get", {"id": asked["id"]}, version=A2A_V03))["result"]
    assert still["status"]["state"] == "input-required"


async def test_a_live_task_is_cancelled_only_by_its_distribution(server) -> None:
    held = task_of(await _send(server, "dist-a", "hold", str(uuid.uuid4()), returnImmediately=True))
    assert state_of(held) == "TASK_STATE_WORKING"

    assert error_of(await _call(server, "dist-b", "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert error_of(await _call(server, None, "CancelTask", {"id": held["id"]})) == TASK_NOT_FOUND
    assert server.agent.cancels == 0

    cancelled = (await _call(server, "dist-a", "CancelTask", {"id": held["id"]}))["result"]
    assert state_of(cancelled) == "TASK_STATE_CANCELED"
    assert server.agent.cancels == 1


async def test_context_history_stays_closed_to_a_distribution(server) -> None:
    """``GetContexts`` and ``GetContext`` serve only an authenticated caller."""
    context_id = str(uuid.uuid4())
    await _send(server, "dist-a", "done", context_id)

    assert (await _call(server, "dist-a", "GetContexts", {}))["result"] == []
    conversation = (await _call(server, "dist-a", "GetContext", {"context_id": context_id}))["result"]
    assert conversation["contextId"] == context_id
    assert state_of(conversation) == "TASK_STATE_UNSPECIFIED"
    assert not conversation.get("history")


async def test_a_request_without_a_distribution_stays_anonymous(server) -> None:
    anonymous = task_of(await _send(server, None, "done", str(uuid.uuid4())))

    assert (await _call(server, None, "GetTask", {"id": anonymous["id"]}))["result"]["id"] == anonymous["id"]
    assert _v03_not_found(await _call(server, "dist-a", "tasks/get", {"id": anonymous["id"]}, version=A2A_V03))


async def test_a_malformed_distribution_is_refused_before_the_agent_runs(server) -> None:
    missing_field = _distribution("dist-a")
    del missing_field[DISTRIBUTION_EXTENSION_URI_V1]["environment"]["projectId"]
    runs = len(server.agent.runs)

    for metadata in (missing_field, _distribution("")):
        response = await server.send("done", str(uuid.uuid4()), metadata=metadata)
        assert error_of(response) == INVALID_REQUEST

    assert len(server.agent.runs) == runs
    assert (await _call(server, None, "ListTasks", {}))["result"].get("tasks", []) == []
