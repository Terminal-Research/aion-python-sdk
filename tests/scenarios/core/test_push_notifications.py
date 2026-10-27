"""Push notification delivery to a real HTTP callback.

The A2A protocol lets a client register a callback URL on a task so the
server POSTs every change the task stores there.  These scenarios start a
local HTTP callback server in a background thread, register it as the push
target, drive a task to completion, and verify that the callback received
the terminal notification - and, before it, every artifact update the
stream carried, the SDK's own artifacts included: the streamed answer, the
thoughts, a reaction and the answer of a task that only talked.

A callback that answers 200 to anything says nothing about how the delivery
was authenticated, so the authenticated scenarios run against a callback that
requires the exact headers the push configuration declared and refuses
everything else - the same two channels the A2A configuration carries:
``authentication`` becomes ``Authorization``, ``token`` becomes
``X-A2A-Notification-Token``.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

import asyncio
import json

import pytest
import pytest_asyncio
from google.protobuf.timestamp_pb2 import Timestamp

from tests.scenarios.commands import ask_question, echo_text, react_text, stream_text, think_text, thoughts_texts
from tests.scenarios.harness import (
    ASSISTANT_ANSWER_ARTIFACT_ID,
    AUTHORIZATION_HEADER,
    NOTIFICATION_TOKEN_HEADER,
    REACTION_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    THINKING_DELTA_ARTIFACT_ID,
    CallbackServer,
    Ev,
    Notification,
    PushAuth,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    final_task,
    message_event,
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
    """The task id from a task, statusUpdate or artifactUpdate notification."""
    task = notification.body.get("task")
    if task:
        return task.get("id")
    update = notification.body.get("statusUpdate") or notification.body.get("artifactUpdate")
    if update:
        return update.get("taskId")
    return None


def _pushed_artifact_updates(notifications: list[Notification]) -> list[tuple]:
    """Each artifactUpdate notification as (artifact id, part texts, append, lastChunk), in order."""
    updates = []
    for notification in notifications:
        update = notification.body.get("artifactUpdate")
        if update:
            artifact = update["artifact"]
            texts = tuple(part["text"] for part in artifact.get("parts", []) if "text" in part)
            updates.append(
                (artifact["artifactId"], texts, update.get("append", False), update.get("lastChunk", False))
            )
    return updates


def _streamed_artifact_updates(events: list[Ev]) -> list[tuple]:
    """Each artifact update of a stream, in the form ``_pushed_artifact_updates`` gives."""
    return [
        (event.artifact_id, event.texts, event.append, event.last_chunk)
        for event in events
        if event.kind == "artifact"
    ]


def _pushed_artifact_texts(notification: Notification, artifact_id: str) -> list[str]:
    """The part texts of one artifact of the Task a notification carries."""
    for artifact in _task_from_notification(notification).get("artifacts", []):
        if artifact["artifactId"] == artifact_id:
            return [part["text"] for part in artifact.get("parts", []) if "text" in part]
    raise AssertionError(f"the pushed task holds no {artifact_id}: {notification.body}")


def _status_time(notification: Notification) -> int:
    """The status time a Task or status-update notification carries, in nanoseconds."""
    body = notification.body
    status = (body.get("task") or body.get("statusUpdate", {}))["status"]
    time = Timestamp()
    time.FromJsonString(status["timestamp"])
    return time.ToNanoseconds()


def _find_terminal(notifications: list[Notification]) -> Notification | None:
    """The first notification that carries a terminal Task."""
    for n in notifications:
        task = n.body.get("task", {})
        state = task.get("status", {}).get("state", "")
        if state in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"):
            return n
    return None


def _pushed_states(notifications: list[Notification]) -> list[str]:
    """The state of each Task or status-update notification, in the order they arrived."""
    states = []
    for notification in notifications:
        carrier = notification.body.get("task") or notification.body.get("statusUpdate")
        if carrier:
            states.append(carrier["status"]["state"])
    return states


async def _wait_until(
    callback: CallbackServer, arrived: Callable[[list[Notification]], bool], timeout: float = 15
) -> list[Notification]:
    """Every notification received until ``arrived`` holds for them, or the timeout passed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        notifications = await callback.wait(timeout=max(0.0, deadline - loop.time()))
        if arrived(notifications) or loop.time() >= deadline:
            return notifications
        await asyncio.sleep(0.1)


async def _wait_for_terminal(callback: CallbackServer, timeout: float = 15) -> list[Notification]:
    """Every notification received until the terminal one arrived, or the timeout passed."""
    return await _wait_until(
        callback, lambda notifications: _find_terminal(notifications) is not None, timeout
    )


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
    assert pushed_task["status"]["timestamp"] == task_ev.raw.status.timestamp.ToJsonString()
    working_statuses = [
        notification.body["statusUpdate"]["status"]
        for notification in notifications
        if notification.body.get("statusUpdate", {}).get("status", {}).get("state")
        == "TASK_STATE_WORKING"
    ]
    assert working_statuses
    assert all(status.get("timestamp") for status in working_statuses)


async def _push_turn(client: ScenarioClient, callback: CallbackServer, text: str, **send) -> tuple:
    """Send one turn with the callback registered, and wait for its terminal notification."""
    events = await client.send(text, push_notification_url=callback.url, **send)
    assert final_task(events).state == "COMPLETED"
    notifications = await _wait_for_terminal(callback)
    terminal = _find_terminal(notifications)
    assert terminal is not None, f"no terminal notification among {len(notifications)} received"
    return events, notifications[: notifications.index(terminal)], terminal


@pytest.mark.variant("push")
@pytest.mark.command("stream")
async def test_a_streamed_answer_is_pushed_as_it_was_streamed(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """Every stream-delta update the stream carried reaches the callback, in order; the task pushed last holds the whole text."""
    events, before, terminal = await _push_turn(push_client, callback, "stream 3")

    streamed = _streamed_artifact_updates(events)
    assert streamed, "the stream carried no artifact update"
    assert {update[0] for update in streamed} == {STREAM_DELTA_ARTIFACT_ID}
    assert _pushed_artifact_updates(before) == streamed
    assert _pushed_artifact_texts(terminal, STREAM_DELTA_ARTIFACT_ID) == [stream_text(3)]


@pytest.mark.variant("push")
@pytest.mark.command("think")
async def test_thoughts_are_pushed(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """Each thought reaches the callback as the update it was on the stream; the task pushed last holds them all."""
    events, before, terminal = await _push_turn(push_client, callback, "think 3")

    assert _pushed_artifact_updates(before) == _streamed_artifact_updates(events)
    assert _pushed_artifact_texts(terminal, THINKING_DELTA_ARTIFACT_ID) == list(thoughts_texts(3))
    assert _pushed_artifact_texts(terminal, ASSISTANT_ANSWER_ARTIFACT_ID) == [think_text()]


@pytest.mark.variant("push")
@pytest.mark.command("react")
async def test_a_reaction_is_pushed(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """The reaction reaches the callback as it reached the stream, and the task pushed last keeps it."""
    envelope = message_event()
    events, before, terminal = await _push_turn(
        push_client,
        callback,
        "react",
        message_metadata=envelope.message_metadata,
        data=[envelope.data],
        extensions=[envelope.extension_uri],
    )

    pushed = _pushed_artifact_updates(before)
    assert pushed == _streamed_artifact_updates(events)
    assert REACTION_ARTIFACT_ID in [update[0] for update in pushed]
    reactions = [
        artifact
        for artifact in _task_from_notification(terminal).get("artifacts", [])
        if artifact["artifactId"] == REACTION_ARTIFACT_ID
    ]
    assert len(reactions) == 1
    assert _pushed_artifact_texts(terminal, ASSISTANT_ANSWER_ARTIFACT_ID) == [react_text()]


@pytest.mark.variant("push")
@pytest.mark.command("echo")
async def test_the_answer_of_a_task_that_only_talked_is_pushed(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """The ``aion:assistant-answer`` update reaches the callback before the completed task, which holds it."""
    events, before, terminal = await _push_turn(push_client, callback, "echo pushed answer")

    assert _pushed_artifact_updates(before) == [
        (ASSISTANT_ANSWER_ARTIFACT_ID, (echo_text("pushed answer"),), False, True)
    ]
    assert _pushed_artifact_updates(before) == _streamed_artifact_updates(events)
    assert _pushed_artifact_texts(terminal, ASSISTANT_ANSWER_ARTIFACT_ID) == [echo_text("pushed answer")]


@pytest.mark.variant("push")
@pytest.mark.command("fail")
async def test_a_crashed_agent_s_failure_is_pushed(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """The FAILED a crash leaves reaches the callback as the full Task."""
    events = await push_client.send(
        "fail exception",
        push_notification_url=callback.url,
    )
    assert final_task(events).state == "FAILED"

    notifications = await _wait_for_terminal(callback)
    terminal = _find_terminal(notifications)
    assert terminal is not None, f"no terminal notification among {len(notifications)} received"
    assert _task_from_notification(terminal)["status"]["state"] == "TASK_STATE_FAILED"


@pytest.mark.variant("push")
@pytest.mark.command("ask")
async def test_an_interrupt_is_pushed_to_a_caller_that_returned_immediately(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """A task that stops for input is pushed once, as the full Task, after the turn's earlier events.

    This is how the hosted platform sends: a unary send that returns as soon
    as the task exists, after which the callback is the only way the caller
    learns that the agent asked something.
    """
    events = await push_client.send(
        "ask",
        push_notification_url=callback.url,
        stream=False,
        return_immediately=True,
    )
    task_id = final_task(events).task_id

    notifications = await _wait_until(
        callback, lambda received: "TASK_STATE_INPUT_REQUIRED" in _pushed_states(received)
    )
    # A second delivery of the interrupt would follow the first within moments.
    notifications = await callback.wait(timeout=1, count=len(notifications) + 1)

    states = _pushed_states(notifications)
    assert states.count("TASK_STATE_INPUT_REQUIRED") == 1, f"pushed states: {states}"
    assert states[0] == "TASK_STATE_SUBMITTED"
    assert states[-1] == "TASK_STATE_INPUT_REQUIRED"
    interrupt = notifications[-1]
    pushed_task = _task_from_notification(interrupt)
    assert pushed_task["id"] == task_id
    assert ask_question() in json.dumps(pushed_task["status"]["message"])
    assert all(_task_id_from_notification(n) == task_id for n in notifications)


@pytest.mark.command("steps")
async def test_pushed_status_times_follow_the_turn_of_a_unary_send(
    push_client: ScenarioClient,
    callback: CallbackServer,
) -> None:
    """A unary send's notifications carry status times in the order of the turn."""
    events = await push_client.send(
        "steps 2",
        push_notification_url=callback.url,
        stream=False,
    )
    assert final_task(events).state == "COMPLETED"

    notifications = await callback.wait(timeout=15)
    while _find_terminal(notifications) is None:
        more = await callback.wait(timeout=15, count=len(notifications) + 1)
        if len(more) == len(notifications):
            break
        notifications = more
    terminal = _find_terminal(notifications)
    assert terminal is not None, f"no terminal notification among {len(notifications)} received"

    nanoseconds = [
        _status_time(notification)
        for notification in notifications[: notifications.index(terminal) + 1]
        if "task" in notification.body or "statusUpdate" in notification.body
    ]
    assert len(nanoseconds) >= 3
    assert nanoseconds == sorted(nanoseconds)


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


@pytest.mark.command("echo")
async def test_a_push_config_is_created_read_listed_and_deleted(client: ScenarioClient) -> None:
    """The four push config methods, on a task of this caller; a config created without an id takes the task's."""
    task_id = final_task(await client.send("echo configs")).task_id
    url = "http://127.0.0.1:9/scenario-hook"

    created = await client.rpc("CreateTaskPushNotificationConfig", {"taskId": task_id, "url": url})
    read = await client.rpc("GetTaskPushNotificationConfig", {"taskId": task_id, "id": task_id})
    listed = await client.rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})
    deleted = await client.rpc("DeleteTaskPushNotificationConfig", {"taskId": task_id, "id": task_id})
    after = await client.rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})

    assert created["result"] == {"id": task_id, "taskId": task_id, "url": url}
    assert read["result"] == created["result"]
    assert listed["result"]["configs"] == [created["result"]]
    assert "error" not in deleted
    assert after["result"].get("configs", []) == []
