"""Push notification delivery to a real HTTP callback.

The A2A protocol lets a client register a callback URL on a task so the
server POSTs every state change there.  These scenarios start a local HTTP
callback server in a background thread, register it as the push target,
drive a task to completion, and verify that the callback received the
terminal notification.

A callback that answers 200 to anything says nothing about how the delivery
was authenticated, so the authenticated scenarios run against a callback that
requires the exact headers the push configuration declared and refuses
everything else - the same two channels the A2A configuration carries:
``authentication`` becomes ``Authorization``, ``token`` becomes
``X-A2A-Notification-Token``.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from tests.scenarios.harness import (
    AUTHORIZATION_HEADER,
    NOTIFICATION_TOKEN_HEADER,
    CallbackServer,
    Notification,
    PushAuth,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    final_task,
)

pytestmark = pytest.mark.lifecycle

PUSH_VARIANT = ServeVariant(
    name="push",
    env={"PUSH_NOTIFICATION_TIMEOUT_SECONDS": "5"},
)

PUSH_AUTH = PushAuth(
    scheme="Bearer",
    credentials="scenario-push-credentials",
    token="scenario-notification-token",
)
"""What the client declares, and what the callback then insists on."""

EXPECTED_HEADERS = {
    AUTHORIZATION_HEADER: f"{PUSH_AUTH.scheme} {PUSH_AUTH.credentials}",
    NOTIFICATION_TOKEN_HEADER: PUSH_AUTH.token,
}


def _task_from_notification(notification: Notification) -> dict[str, Any]:
    """Extract the Task payload from a push notification body.

    The push body is a ``StreamResponse`` serialized via ``MessageToDict``.
    A terminal notification carries a ``task`` key with the full task;
    intermediate ones carry ``statusUpdate``.
    """
    return notification.body.get("task", {})


def _task_id_from_notification(notification: Notification) -> str | None:
    """The task id from either a task or a statusUpdate notification."""
    task = notification.body.get("task")
    if task:
        return task.get("id")
    status_update = notification.body.get("statusUpdate")
    if status_update:
        return status_update.get("taskId")
    return None


def _find_terminal(notifications: list[Notification]) -> Notification | None:
    """The first notification that carries a terminal Task."""
    for n in notifications:
        task = n.body.get("task", {})
        state = task.get("status", {}).get("state", "")
        if state in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"):
            return n
    return None


@pytest.fixture(scope="session")
def push_server(server_for) -> ServeProcess:
    """A deployment with a short push notification timeout."""
    return server_for(PUSH_VARIANT)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def push_client(push_server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client on the push deployment."""
    scenario_client = await ScenarioClient.connect(
        push_server.base_url, push_server.variant.agent_id
    )
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


@pytest.fixture
async def callback() -> AsyncIterator[CallbackServer]:
    """A callback server that lives for one test and accepts any delivery."""
    server = await CallbackServer.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def authenticated_callback() -> AsyncIterator[CallbackServer]:
    """A callback that refuses anything not carrying the declared credentials."""
    server = await CallbackServer.start(required_headers=EXPECTED_HEADERS)
    try:
        yield server
    finally:
        await server.stop()


@pytest.mark.variant("push")
@pytest.mark.command("echo")
async def test_push_notification_reaches_callback(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """The server POSTs a terminal notification to the registered URL."""
    events = await push_client.send(
        "echo hello",
        push_notification_url=callback.url,
    )

    task_ev = final_task(events)
    assert task_ev.state == "COMPLETED"

    notifications = await callback.wait(timeout=15)
    assert len(notifications) >= 1, "no push notification received"

    terminal = _find_terminal(notifications)
    assert terminal is not None, (
        f"no terminal notification among {len(notifications)} received"
    )

    pushed_task = _task_from_notification(terminal)
    assert pushed_task.get("status", {}).get("state") == "TASK_STATE_COMPLETED"


@pytest.mark.variant("push")
@pytest.mark.command("echo")
async def test_push_notification_carries_the_task_id(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """The pushed body identifies the task the notification is about."""
    events = await push_client.send(
        "echo world",
        push_notification_url=callback.url,
    )

    task_ev = final_task(events)
    notifications = await callback.wait(timeout=15)
    assert notifications, "no push notification received"

    for notification in notifications:
        pushed_id = _task_id_from_notification(notification)
        assert pushed_id == task_ev.task_id, (
            f"pushed task id {pushed_id!r} != stream task id {task_ev.task_id!r}"
        )


@pytest.mark.variant("push")
@pytest.mark.command("echo")
async def test_a_declared_credential_is_presented_to_the_callback(
    push_client: ScenarioClient,
    authenticated_callback: CallbackServer,
) -> None:
    """The callback accepts the delivery, because it arrived authenticated."""
    events = await push_client.send(
        "echo authenticated",
        push_notification_url=authenticated_callback.url,
        push_auth=PUSH_AUTH,
    )
    task_ev = final_task(events)

    notifications = await authenticated_callback.wait(timeout=15)
    assert notifications, "no push notification received"
    accepted = [n for n in notifications if n.accepted]
    assert accepted, "every delivery was refused: the credentials did not arrive"

    for notification in accepted:
        assert notification.header(AUTHORIZATION_HEADER) == EXPECTED_HEADERS[AUTHORIZATION_HEADER]
        assert notification.header(NOTIFICATION_TOKEN_HEADER) == PUSH_AUTH.token

    terminal = _find_terminal(accepted)
    assert terminal is not None, f"no terminal notification among {len(accepted)} accepted"
    assert _task_id_from_notification(terminal) == task_ev.task_id
    assert _task_from_notification(terminal).get("status", {}).get("state") == (
        "TASK_STATE_COMPLETED"
    )


@pytest.mark.variant("push")
@pytest.mark.command("echo")
async def test_an_undeclared_credential_is_refused_by_the_callback(
    push_client: ScenarioClient,
    authenticated_callback: CallbackServer,
) -> None:
    """The callback really checks: the same delivery without credentials is rejected.

    What this pins down is the callback, not the server - a receiver that
    answered 200 regardless would make the scenario above prove nothing.
    """
    await push_client.send(
        "echo unauthenticated",
        push_notification_url=authenticated_callback.url,
    )

    notifications = await authenticated_callback.wait(timeout=15)
    assert notifications, "no push notification received"
    assert not any(n.accepted for n in notifications)
    assert all(n.header(AUTHORIZATION_HEADER) is None for n in notifications)
