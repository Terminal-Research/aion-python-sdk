"""Who a request is from, and what a server does with one that names nobody it trusts.

Every call to an agent carries a bearer token. Outside the Aion platform the
tokens a server accepts by default are anonymous session tokens, signed by the control
plane whose keys the server fetches from ``AION_API_HOST``; the scenarios run
a stand-in for it. The token's ``sub`` is the caller, and a session's context
is its own: another session that presents the same ``contextId`` is refused as
if the task did not exist, before the agent runs.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import httpx
import pytest

from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import (
    CLIENT_ID,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    control_plane,
    final_task,
    session_subject,
)

pytestmark = [pytest.mark.authentication]

TASK_NOT_FOUND = -32001
MISSING_AUTHENTICATION = -32010
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
    assert response.json()["error"]["code"] == MISSING_AUTHENTICATION


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
    assert response.json()["error"]["code"] == MISSING_AUTHENTICATION
    assert token not in response.text


def test_a_call_with_an_anonymous_session_token_is_served(server: ServeProcess) -> None:
    """The control plane's token opens the endpoint the two refusals above close."""
    response = _post(server, {"Authorization": f"Bearer {control_plane().token()}"})

    assert response.status_code == 200
    assert "error" not in response.json()


async def test_a_session_cannot_enter_another_sessions_context(server: ServeProcess) -> None:
    """The caller is the token's ``sub``: the first session holds the context, the second is refused."""
    context_id = str(uuid.uuid4())
    async with (
        await ScenarioClient.connect(server.base_url, server.variant.agent_id, subject=session_subject(str(uuid.uuid4()))) as first,
        await ScenarioClient.connect(server.base_url, server.variant.agent_id, subject=session_subject(str(uuid.uuid4()))) as second,
    ):
        firsts = final_task(await first.send("echo one", context_id=context_id))

        refusal = await second.rpc(
            "SendMessage",
            {"message": {"messageId": str(uuid.uuid4()), "contextId": context_id, "role": "ROLE_USER",
                         "parts": [{"text": "echo two"}]}},
        )
        assert refusal["error"]["code"] == TASK_NOT_FOUND
        assert await second.tasks_in_context(context_id) == []
        refusal = await second.rpc("GetTask", {"id": firsts.task_id})
        assert refusal["error"]["code"] == TASK_NOT_FOUND

        again = final_task(await first.send("echo three", context_id=context_id))
        assert {task.id for task in await first.tasks_in_context(context_id)} == {firsts.task_id, again.task_id}


_DEPLOYMENT_ID = "3f2b8c1d-5a6e-4f70-9b8c-1d2e3f4a5b6c"

_MODES = {
    "hosted": {"DEPLOYMENT_ID": _DEPLOYMENT_ID, "AION_CLIENT_ID": CLIENT_ID},
    "strict": {"AION_REQUIRE_INVOCATION_AUTH": "true", "AION_CLIENT_ID": CLIENT_ID},
}
"""The environment of a server serving invocations only, by mode."""

_UNSAFE = {
    "malformed-deployment-id": ({"DEPLOYMENT_ID": "not-a-uuid"}, "DEPLOYMENT_ID is set but is not a deployment UUID"),
    "hosted-without-client-id": ({"DEPLOYMENT_ID": _DEPLOYMENT_ID}, "AION_CLIENT_ID is not"),
    "strict-without-client-id": ({"AION_REQUIRE_INVOCATION_AUTH": "true"}, "AION_CLIENT_ID is not"),
}
"""Settings that cannot protect a server, and what its startup error says."""


@contextlib.contextmanager
def _serving(framework: Framework, variant: ServeVariant) -> Iterator[ServeProcess]:
    """A server of its own for one scenario: a mode the shared servers do not run in."""
    process = ServeProcess(framework, variant).start()
    try:
        yield process
    finally:
        process.stop()


@pytest.mark.parametrize("mode", sorted(_MODES))
def test_a_hosted_or_strict_server_serves_invocations_only(framework: Framework, mode: str) -> None:
    """Aion's invocation for this client ID is served; a session and another client's invocation are refused."""
    with _serving(framework, ServeVariant(env=_MODES[mode])) as server:
        served = _post(server, {"Authorization": f"Bearer {control_plane().invocation_token()}"})
        session = _post(server, {"Authorization": f"Bearer {control_plane().token()}"})
        elsewhere = _post(
            server, {"Authorization": f"Bearer {control_plane().invocation_token(audience='another-client')}"}
        )

    assert served.status_code == 200
    assert "error" not in served.json()
    assert (session.status_code, elsewhere.status_code) == (401, 401)


@pytest.mark.parametrize("settings", sorted(_UNSAFE))
def test_settings_that_cannot_protect_the_server_stop_it_starting(framework: Framework, settings: str) -> None:
    """Not a server that starts open: ``aion serve`` exits with an error, and says why."""
    env, reason = _UNSAFE[settings]
    process = ServeProcess(framework, ServeVariant(env=env, expect_healthy=False))
    try:
        with pytest.raises(RuntimeError, match="exited with code 1") as failed:
            process.start()
    finally:
        process.stop()

    assert reason in str(failed.value)
