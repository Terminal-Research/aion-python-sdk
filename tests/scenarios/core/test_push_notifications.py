"""Push notification delivery to a real HTTP callback.

The A2A protocol lets a client register a callback URL on a task so the
server POSTs every state change there.  These scenarios start a local HTTP
callback server in a background thread, register it as the push target,
drive a task to completion, and verify that the callback received the
terminal notification.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from tests.scenarios.harness import (
    CallbackServer,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    final_task,
)
from tests.scenarios.harness.callback import Notification

pytestmark = pytest.mark.lifecycle

PUSH_VARIANT = ServeVariant(
    name="push",
    env={"PUSH_NOTIFICATION_TIMEOUT_SECONDS": "5"},
)


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
    """A callback server that lives for one test."""
    server = await CallbackServer.start()
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
