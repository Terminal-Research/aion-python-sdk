"""Tests for how AionPushNotificationSender queues and retries its deliveries.

Every webhook of a task has a queue of its own: ``send_notification`` puts
the event there and returns, and a worker delivers the queue's events one at
a time, in order. A delivery the webhook does not acknowledge is tried again
while another attempt can get past the failure, after the pauses of
``RETRY_DELAYS_SECONDS`` or the receiver's ``Retry-After``. While a webhook
fails, an outcome queued for it replaces the intermediate events waiting in
front of it.
"""

import asyncio
import contextvars
import datetime
import email.utils
import httpx
import logging
import pytest
from a2a.types import (
    Artifact,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)
from a2a.types.a2a_pb2 import TaskPushNotificationConfig
from unittest.mock import AsyncMock, Mock

from aion.server.tasks import push_sender as push_sender_module
from aion.server.tasks.push_sender import AionPushNotificationSender

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
URL = "https://receiver.example/push"
OTHER_URL = "https://other-receiver.example/push"

CALLER = contextvars.ContextVar("caller", default=None)


def _artifact(name: str) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID, context_id=CONTEXT_ID, artifact=Artifact(artifact_id=name)
    )


def _task(state: TaskState = TaskState.TASK_STATE_COMPLETED) -> Task:
    return Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state))


def _named(body: dict) -> str:
    """Names what a delivery carried: an artifact by its id, a task by its state."""
    if "artifactUpdate" in body:
        return body["artifactUpdate"]["artifact"]["artifactId"]
    if "task" in body:
        return body["task"]["status"]["state"].removeprefix("TASK_STATE_").lower()
    return "status"


class _Webhook:
    """Receivers whose answers are scripted per URL, recording every request.

    An answer is a status code, an ``httpx.Response`` or an exception to
    raise; once a URL's script is spent it answers ``default``. A closed gate
    holds the requests to its URL until it is opened.
    """

    def __init__(self, answers: dict[str, list] | None = None, default: int = 200) -> None:
        self.answers = {url: list(script) for url, script in (answers or {}).items()}
        self.default = default
        self.received: list[tuple[str, str]] = []
        self.callers: list[str | None] = []
        self.gates: dict[str, asyncio.Event] = {}

    def client(self) -> Mock:
        client = Mock()
        client.post = self.post
        client.aclose = AsyncMock()
        return client

    def to(self, url: str = URL) -> list[str]:
        """What ``url`` received, in order."""
        return [name for target, name in self.received if target == url]

    def hold(self, url: str = URL) -> asyncio.Event:
        gate = asyncio.Event()
        self.gates[url] = gate
        return gate

    async def post(self, url: str, *, json: dict, headers=None) -> httpx.Response:
        self.received.append((url, _named(json)))
        self.callers.append(CALLER.get())
        gate = self.gates.get(url)
        if gate is not None:
            await gate.wait()
        script = self.answers.get(url) or []
        answer = script.pop(0) if script else self.default
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(answer, request=httpx.Request("POST", url))


class _Pauses:
    """Stands in for the pause before a retry: records it, and waits while held."""

    def __init__(self) -> None:
        self.taken: list[float] = []
        self.gate: asyncio.Event | None = None

    def hold(self) -> asyncio.Event:
        self.gate = asyncio.Event()
        return self.gate

    async def __call__(self, seconds: float) -> None:
        self.taken.append(seconds)
        if self.gate is not None:
            await self.gate.wait()


def _sender(webhook: _Webhook, pauses: _Pauses, *urls: str) -> AionPushNotificationSender:
    store = Mock()
    store.get_info_for_dispatch = AsyncMock(
        return_value=[
            TaskPushNotificationConfig(id=f"config-{i}", task_id=TASK_ID, url=url)
            for i, url in enumerate(urls or (URL,))
        ]
    )
    return AionPushNotificationSender(
        httpx_client=webhook.client(), config_store=store, sleep=pauses
    )


async def _settle() -> None:
    """Let the queue workers run until they wait on something."""
    for _ in range(10):
        await asyncio.sleep(0)


def _response(status: int, retry_after: str) -> httpx.Response:
    return httpx.Response(
        status, headers={"Retry-After": retry_after}, request=httpx.Request("POST", URL)
    )


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == push_sender_module.__name__ and r.levelno >= logging.WARNING
    ]


class TestRetries:
    """A delivery is tried again while another attempt can get past its failure."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    async def test_a_refusal_a_later_attempt_can_get_past_is_retried(self, status):
        webhook, pauses = _Webhook({URL: [status]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert webhook.to() == ["a1", "a1"]
        assert pauses.taken == [1.0]

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
    async def test_a_refusal_no_attempt_can_get_past_is_not_retried(self, status, caplog):
        webhook, pauses = _Webhook({URL: [status]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert webhook.to() == ["a1"]
        assert pauses.taken == []
        assert len(_warnings(caplog)) == 1
        assert f"HTTP {status}" in _warnings(caplog)[0]

    @pytest.mark.anyio
    async def test_a_timeout_is_retried(self):
        webhook, pauses = _Webhook({URL: [httpx.ReadTimeout("no answer")]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert webhook.to() == ["a1", "a1"]

    @pytest.mark.anyio
    async def test_a_connection_that_failed_is_retried(self, caplog):
        webhook, pauses = _Webhook({URL: [httpx.ConnectError("refused")]}), _Pauses()
        sender = _sender(webhook, pauses)

        with caplog.at_level(logging.INFO, logger=push_sender_module.__name__):
            await sender.send_notification(TASK_ID, _artifact("a1"))
            await sender.drain()

        assert webhook.to() == ["a1", "a1"]
        assert any("could not reach URL" in r.getMessage() for r in caplog.records)

    @pytest.mark.anyio
    async def test_a_url_httpx_cannot_speak_to_is_not_retried(self):
        webhook, pauses = _Webhook({URL: [httpx.UnsupportedProtocol("ftp")]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert webhook.to() == ["a1"]
        assert pauses.taken == []

    @pytest.mark.anyio
    async def test_the_pauses_double_and_the_delivery_gives_up_after_six_attempts(self, caplog):
        webhook, pauses = _Webhook(default=503), _Pauses()
        sender = _sender(webhook, pauses)

        with caplog.at_level(logging.INFO, logger=push_sender_module.__name__):
            await sender.send_notification(TASK_ID, _artifact("a1"))
            await sender.drain()

        assert webhook.to() == ["a1"] * 6
        assert pauses.taken == [1.0, 2.0, 4.0, 8.0, 16.0]
        failed_attempts = [
            r for r in caplog.records if r.levelno == logging.INFO and "attempt" in r.getMessage()
        ]
        assert len(failed_attempts) == 5
        assert len(_warnings(caplog)) == 1
        assert "gave up after 6 attempts" in _warnings(caplog)[0]

    @pytest.mark.anyio
    async def test_a_delivery_acknowledged_on_a_retry_says_which_attempt_it_was(self, caplog):
        webhook, pauses = _Webhook({URL: [503, 503]}), _Pauses()
        sender = _sender(webhook, pauses)

        with caplog.at_level(logging.INFO, logger=push_sender_module.__name__):
            await sender.send_notification(TASK_ID, _artifact("a1"))
            await sender.drain()

        sent = [r.getMessage() for r in caplog.records if "sent to URL" in r.getMessage()]
        assert len(sent) == 1
        assert "attempt 3 of 6" in sent[0]
        assert _warnings(caplog) == []

    @pytest.mark.anyio
    async def test_a_retry_after_in_seconds_replaces_the_next_pause(self):
        webhook, pauses = _Webhook({URL: [_response(503, "3")]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert pauses.taken == [3.0]

    @pytest.mark.anyio
    async def test_a_retry_after_waits_at_most_the_longest_pause(self):
        webhook, pauses = _Webhook({URL: [_response(429, "120")]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert pauses.taken == [16.0]

    @pytest.mark.anyio
    async def test_a_retry_after_given_as_a_date_waits_until_then(self):
        when = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=5)
        retry_after = email.utils.format_datetime(when, usegmt=True)
        webhook, pauses = _Webhook({URL: [_response(503, retry_after)]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert len(pauses.taken) == 1
        assert 3.0 <= pauses.taken[0] <= 5.0

    @pytest.mark.anyio
    async def test_an_unreadable_retry_after_keeps_the_schedule(self):
        webhook, pauses = _Webhook({URL: [_response(503, "soon")]}), _Pauses()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.drain()

        assert pauses.taken == [1.0]


class TestQueue:
    """Each webhook receives a task's events in order, without holding anything else back."""

    @pytest.mark.anyio
    async def test_queuing_an_event_does_not_wait_for_the_webhook(self):
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        queued = await asyncio.wait_for(
            sender.send_notification(TASK_ID, _artifact("a1")), timeout=1
        )
        gate.set()
        await sender.drain()

        assert queued is True
        assert webhook.to() == ["a1"]

    @pytest.mark.anyio
    async def test_a_webhook_receives_a_tasks_events_in_order(self):
        webhook, pauses = _Webhook({URL: [503]}), _Pauses()
        sender = _sender(webhook, pauses)

        for name in ("a1", "a2", "a3"):
            await sender.send_notification(TASK_ID, _artifact(name))
        await sender.drain()

        assert webhook.to() == ["a1", "a1", "a2", "a3"]

    @pytest.mark.anyio
    async def test_a_failing_webhook_holds_back_no_other_webhook(self):
        webhook, pauses = _Webhook({URL: [503]}), _Pauses()
        gate = pauses.hold()
        sender = _sender(webhook, pauses, URL, OTHER_URL)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await sender.send_notification(TASK_ID, _artifact("a2"))

        assert await sender.drain(timeout=0.2) is False
        assert webhook.to(OTHER_URL) == ["a1", "a2"]
        assert webhook.to(URL) == ["a1"]

        gate.set()
        await sender.drain()
        assert webhook.to(URL) == ["a1", "a1", "a2"]

    @pytest.mark.anyio
    async def test_an_event_changed_after_it_was_queued_is_delivered_as_queued(self):
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)
        event = _artifact("a1")

        await sender.send_notification(TASK_ID, event)
        await _settle()
        event.artifact.artifact_id = "changed"
        await sender.send_notification(TASK_ID, _artifact("a2"))
        event.artifact.artifact_id = "changed again"
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a2"]

    @pytest.mark.anyio
    async def test_each_delivery_runs_in_the_context_its_event_was_queued_in(self):
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        CALLER.set("first request")
        await sender.send_notification(TASK_ID, _artifact("a1"))
        CALLER.set("second request")
        await sender.send_notification(TASK_ID, _artifact("a2"))
        CALLER.set(None)
        gate.set()
        await sender.drain()

        assert webhook.callers == ["first request", "second request"]

    @pytest.mark.anyio
    async def test_an_unreadable_config_store_queues_nothing(self):
        store = Mock()
        store.get_info_for_dispatch = AsyncMock(side_effect=RuntimeError("database down"))
        sender = AionPushNotificationSender(
            httpx_client=_Webhook().client(), config_store=store, sleep=_Pauses()
        )

        assert await sender.send_notification(TASK_ID, _artifact("a1")) is False


class TestOutcomeWhileTheWebhookFails:
    """While a webhook fails, a queued outcome replaces the events waiting in front of it."""

    @pytest.mark.anyio
    async def test_an_outcome_queued_while_the_webhook_fails_replaces_the_waiting_events(self):
        webhook, pauses = _Webhook({URL: [503]}), _Pauses()
        gate = pauses.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        for name in ("a2", "a3"):
            await sender.send_notification(TASK_ID, _artifact(name))
        await sender.send_notification(TASK_ID, _task())
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a1", "completed"]

    @pytest.mark.anyio
    async def test_a_failure_replaces_the_events_waiting_in_front_of_an_outcome(self):
        webhook, pauses = _Webhook({URL: [503]}), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        await sender.send_notification(TASK_ID, _artifact("a2"))
        await sender.send_notification(TASK_ID, _task())
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a1", "completed"]

    @pytest.mark.anyio
    async def test_while_the_webhook_answers_every_event_is_delivered(self):
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        for name in ("a2", "a3"):
            await sender.send_notification(TASK_ID, _artifact(name))
        await sender.send_notification(TASK_ID, _task())
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a2", "a3", "completed"]

    @pytest.mark.anyio
    async def test_the_event_being_delivered_keeps_all_its_attempts(self):
        webhook, pauses = _Webhook({URL: [503] * 6}), _Pauses()
        gate = pauses.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        await sender.send_notification(TASK_ID, _task())
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1"] * 6 + ["completed"]

    @pytest.mark.anyio
    async def test_an_earlier_outcome_is_not_replaced(self):
        webhook, pauses = _Webhook({URL: [503]}), _Pauses()
        gate = pauses.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        await sender.send_notification(TASK_ID, _task(TaskState.TASK_STATE_INPUT_REQUIRED))
        await sender.send_notification(TASK_ID, _artifact("a2"))
        await sender.send_notification(TASK_ID, _task())
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a1", "input_required", "completed"]


class TestQueueLimit:
    """A webhook holds at most QUEUE_LIMIT waiting events of a task, never dropping an outcome."""

    @pytest.mark.anyio
    async def test_a_full_queue_drops_its_oldest_event_and_says_so_once(self, monkeypatch, caplog):
        monkeypatch.setattr(push_sender_module, "QUEUE_LIMIT", 3)
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        for name in ("a2", "a3", "a4", "a5", "a6"):
            await sender.send_notification(TASK_ID, _artifact(name))
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "a4", "a5", "a6"]
        assert len(_warnings(caplog)) == 1
        assert "holds 3 events" in _warnings(caplog)[0]

    @pytest.mark.anyio
    async def test_a_full_queue_keeps_its_outcome(self, monkeypatch):
        monkeypatch.setattr(push_sender_module, "QUEUE_LIMIT", 2)
        webhook, pauses = _Webhook(), _Pauses()
        gate = webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        await sender.send_notification(TASK_ID, _task(TaskState.TASK_STATE_INPUT_REQUIRED))
        await sender.send_notification(TASK_ID, _artifact("a2"))
        await sender.send_notification(TASK_ID, _artifact("a3"))
        gate.set()
        await sender.drain()

        assert webhook.to() == ["a1", "input_required", "a3"]


class TestClosing:
    """The queues live in the process: closing the sender gives up on what they hold."""

    @pytest.mark.anyio
    async def test_drain_says_when_its_time_ran_out(self):
        webhook, pauses = _Webhook(), _Pauses()
        webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))

        assert await sender.drain(timeout=0.05) is False

    @pytest.mark.anyio
    async def test_closing_gives_up_on_what_is_queued_and_says_how_much(self, caplog):
        webhook, pauses = _Webhook(), _Pauses()
        webhook.hold()
        sender = _sender(webhook, pauses)

        await sender.send_notification(TASK_ID, _artifact("a1"))
        await _settle()
        await sender.send_notification(TASK_ID, _artifact("a2"))
        await sender.aclose()

        assert _warnings(caplog) == [
            "2 push-notification(s) of 1 task(s) were not delivered before the sender closed"
        ]
        sender._client.aclose.assert_awaited_once()

    @pytest.mark.anyio
    async def test_a_closed_sender_queues_nothing(self, caplog):
        webhook, pauses = _Webhook(), _Pauses()
        sender = _sender(webhook, pauses)
        await sender.aclose()

        assert await sender.send_notification(TASK_ID, _artifact("a1")) is False
        assert webhook.received == []
        assert any("sender is closed" in line for line in _warnings(caplog))
