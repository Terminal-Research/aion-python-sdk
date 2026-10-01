"""Who a request is from, and what a server does with one that names nobody it trusts.

Every call to an agent carries a bearer token. Outside the Aion platform the
tokens a server accepts are anonymous session tokens, signed by the control
plane whose keys the server fetches from ``AION_API_HOST``; the scenarios run
a stand-in for it. The token's ``sub`` is the caller: two sessions on one
``contextId`` are two callers, and neither sees the other's tasks.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.scenarios.harness import ScenarioClient, ServeProcess, control_plane, final_task, session_subject

pytestmark = [pytest.mark.authentication]

TASK_NOT_FOUND = -32001
"""The JSON-RPC code A2A gives a task the caller cannot see."""

_SEND_MESSAGE = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "echo hello"}]}},
}


def _post(server: ServeProcess, headers: dict[str, str]) -> httpx.Response:
    return httpx.post(server.agent_url(), json=_SEND_MESSAGE, headers={"A2A-Version": "1.0", **headers}, timeout=10.0)


def test_a_call_without_a_token_is_refused(server: ServeProcess) -> None:
    """No ``Authorization`` header: ``401`` with a bearer challenge, and no run."""
    response = _post(server, {})

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize(
    "credentials",
    [
        pytest.param("not-a-jwt", id="not-a-jwt"),
        pytest.param(lambda: control_plane().forged_token(), id="signed-by-another-key"),
        pytest.param(lambda: control_plane().token(issued_at=0), id="expired"),
        pytest.param(lambda: control_plane().token(aud="somebody-else"), id="addressed-to-another-audience"),
        pytest.param(lambda: control_plane().token(assurance="account"), id="not-session-assurance"),
        pytest.param(lambda: control_plane().token(token_use="a2a_invocation"), id="not-a-session-purpose"),
    ],
)
def test_a_call_with_a_token_that_does_not_verify_is_refused(server: ServeProcess, credentials) -> None:
    """The token names no caller the server trusts: ``401``, and the reason never quotes it."""
    token = credentials() if callable(credentials) else credentials

    response = _post(server, {"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert token not in response.text


def test_a_call_with_an_anonymous_session_token_is_served(server: ServeProcess) -> None:
    """The control plane's token opens the endpoint the two refusals above close."""
    response = _post(server, {"Authorization": f"Bearer {control_plane().token()}"})

    assert response.status_code == 200
    assert "error" not in response.json()


async def test_two_sessions_on_one_context_do_not_see_each_others_tasks(server: ServeProcess) -> None:
    """The caller is the token's ``sub``: the same ``contextId`` opens a task apiece."""
    context_id = str(uuid.uuid4())
    async with (
        await ScenarioClient.connect(server.base_url, server.variant.agent_id, subject=session_subject(str(uuid.uuid4()))) as first,
        await ScenarioClient.connect(server.base_url, server.variant.agent_id, subject=session_subject(str(uuid.uuid4()))) as second,
    ):
        firsts = final_task(await first.send("echo one", context_id=context_id))
        seconds = final_task(await second.send("echo two", context_id=context_id))

        assert firsts.task_id != seconds.task_id
        assert [task.id for task in await first.tasks_in_context(context_id)] == [firsts.task_id]
        assert [task.id for task in await second.tasks_in_context(context_id)] == [seconds.task_id]
        refusal = await second.rpc("GetTask", {"id": firsts.task_id})
        assert refusal["error"]["code"] == TASK_NOT_FOUND
