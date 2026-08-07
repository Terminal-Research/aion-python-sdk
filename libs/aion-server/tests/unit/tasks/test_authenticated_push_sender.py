"""Tests for AuthenticatedPushNotificationSender.

The sender owns the inbound half of the push contract that the SDK base class
drops: a client that declares ``taskPushNotificationConfig.authentication``
expects its callback endpoint to be called with those credentials. The base
class emits only ``X-A2A-Notification-Token``, so an authenticated webhook
rejects every delivery. These tests pin the header derivation and the failure
handling around it.
"""

import datetime
import httpx
import logging
import pytest
from a2a.types import (
    Artifact,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.types.a2a_pb2 import AuthenticationInfo, TaskPushNotificationConfig
from unittest.mock import AsyncMock, Mock

from aion.server.tasks.authenticated_push_sender import AuthenticatedPushNotificationSender

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
WEBHOOK_URL = "https://api-staging.aion.to/a2a/callbacks/jobs/job-1/push"
CREDENTIALS = "secret-token-value"


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


def _config(
        *,
        token: str = "",
        scheme: str | None = None,
        credentials: str | None = None,
        url: str = WEBHOOK_URL,
) -> TaskPushNotificationConfig:
    """Builds a stored push configuration.

    Args:
        token: Value for the SDK's own notification token, empty to omit it.
        scheme: Authentication scheme to declare, None to omit authentication
            entirely, empty string to declare credentials without a scheme.
        credentials: Credential value to declare, None to omit authentication.
        url: Target webhook, distinct per config when a fan-out is under test.

    Returns:
        The configuration as it would come back from the config store.
    """
    config = TaskPushNotificationConfig(task_id=TASK_ID, url=url, token=token)
    if credentials is not None:
        config.authentication.CopyFrom(
            AuthenticationInfo(scheme=scheme or "", credentials=credentials)
        )
    return config


def _event() -> Task:
    """Returns a terminal Task, the payload the push channel settles a run with."""
    return Task(
        id=TASK_ID,
        context_id=CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )


def _event_status(state: int = TaskState.TASK_STATE_WORKING) -> TaskStatusUpdateEvent:
    """Returns an intermediate status update, the event a run emits most often."""
    return TaskStatusUpdateEvent(
        task_id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=state)
    )


def _event_artifact(
        artifact_id: str = "evolution-diff", last_chunk: bool = True
) -> TaskArtifactUpdateEvent:
    """Returns an artifact update, one of the many a streaming run emits."""
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(artifact_id=artifact_id),
        last_chunk=last_chunk,
    )


def _client(
        status_code: int = 200, body: str = "", elapsed_ms: float | None = None
) -> Mock:
    """Returns an httpx client stand-in whose POST answers with the given status.

    Args:
        status_code: Status the webhook answers with.
        body: Response body, as a receiver's refusal message.
        elapsed_ms: Round-trip duration to attribute to the response, None to
            leave it unset the way a hand-built response arrives.
    """
    response = httpx.Response(
        status_code, request=httpx.Request("POST", WEBHOOK_URL), text=body
    )
    if elapsed_ms is not None:
        response.elapsed = datetime.timedelta(milliseconds=elapsed_ms)
    client = Mock()
    client.post = AsyncMock(return_value=response)
    return client


def _store(config: TaskPushNotificationConfig) -> Mock:
    """Returns a config store stand-in that dispatches to a single webhook."""
    store = Mock()
    store.get_info_for_dispatch = AsyncMock(return_value=[config])
    return store


def _sender(config: TaskPushNotificationConfig, client: Mock) -> AuthenticatedPushNotificationSender:
    return AuthenticatedPushNotificationSender(
        httpx_client=client, config_store=_store(config)
    )


def _fan_out_sender(
        statuses: dict[str, int], client: Mock | None = None
) -> AuthenticatedPushNotificationSender:
    """Builds a sender whose task has one webhook per entry in ``statuses``.

    Args:
        statuses: Target URL mapped to the status code that URL answers with.
        client: Client stand-in to use, built from ``statuses`` when omitted.

    Returns:
        A sender wired to a store that dispatches to every URL in ``statuses``.
    """
    store = Mock()
    store.get_info_for_dispatch = AsyncMock(
        return_value=[_config(url=url) for url in statuses]
    )
    if client is None:
        async def post(url, **_kwargs):
            return httpx.Response(
                statuses[url], request=httpx.Request("POST", url), text=""
            )

        client = Mock()
        client.post = AsyncMock(side_effect=post)
    return AuthenticatedPushNotificationSender(httpx_client=client, config_store=store)


def _our_records(caplog) -> list[logging.LogRecord]:
    """Returns the records the sender itself emitted, ignoring the SDK's."""
    return [r for r in caplog.records if r.name.endswith("authenticated_push_sender")]


def _sent_headers(client: Mock) -> dict[str, str] | None:
    """Extracts the headers of the single POST the sender issued."""
    client.post.assert_awaited_once()
    return client.post.await_args.kwargs["headers"]


class TestAuthorizationHeader:
    """Declared credentials become a standard Authorization header."""

    @pytest.mark.anyio
    async def test_declared_scheme_and_credentials_are_sent(self):
        """The scheme/credentials pair is sent verbatim as ``<scheme> <credentials>``."""
        client = _client()
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client)["Authorization"] == f"Bearer {CREDENTIALS}"

    @pytest.mark.anyio
    async def test_non_bearer_scheme_is_preserved(self):
        """A scheme other than Bearer is not rewritten."""
        client = _client()
        sender = _sender(_config(scheme="Basic", credentials="dXNlcjpwYXNz"), client)

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client)["Authorization"] == "Basic dXNlcjpwYXNz"

    @pytest.mark.anyio
    async def test_missing_scheme_defaults_to_bearer(self):
        """Credentials without a named scheme are sent as a Bearer token.

        Clients populate ``credentials`` and leave ``scheme`` empty often enough
        that dropping the header would be the more surprising behaviour.
        """
        client = _client()
        sender = _sender(_config(scheme="", credentials=CREDENTIALS), client)

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client)["Authorization"] == f"Bearer {CREDENTIALS}"

    @pytest.mark.anyio
    async def test_scheme_without_credentials_sends_nothing(self):
        """A scheme with no credential value carries no authentication to send."""
        client = _client()
        sender = _sender(_config(scheme="Bearer", credentials=""), client)

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client) is None


class TestBaseBehaviourIsPreserved:
    """The notification token keeps working exactly as it did before."""

    @pytest.mark.anyio
    async def test_config_without_authentication_sends_no_authorization(self):
        """An unauthenticated webhook is called the way the SDK base class called it."""
        client = _client()
        sender = _sender(_config(token="notify-token"), client)

        await sender.send_notification(TASK_ID, _event())

        headers = _sent_headers(client)
        assert headers == {"X-A2A-Notification-Token": "notify-token"}

    @pytest.mark.anyio
    async def test_token_and_authentication_are_sent_together(self):
        """The two channels are independent: a config setting both gets both headers."""
        client = _client()
        sender = _sender(
            _config(token="notify-token", scheme="Bearer", credentials=CREDENTIALS),
            client,
        )

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client) == {
            "X-A2A-Notification-Token": "notify-token",
            "Authorization": f"Bearer {CREDENTIALS}",
        }

    @pytest.mark.anyio
    async def test_bare_config_sends_no_headers(self):
        """With neither token nor credentials the request carries no headers at all."""
        client = _client()
        sender = _sender(_config(), client)

        await sender.send_notification(TASK_ID, _event())

        assert _sent_headers(client) is None

    @pytest.mark.anyio
    async def test_event_is_posted_to_the_configured_url_as_a_stream_response(self):
        """Authentication changes the headers only; URL and body stay as the base class sent them."""
        client = _client()
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        await sender.send_notification(TASK_ID, _event())

        url = client.post.await_args.args[0]
        payload = client.post.await_args.kwargs["json"]
        assert url == WEBHOOK_URL
        assert payload["task"]["id"] == TASK_ID
        assert payload["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


class TestFailureHandling:
    """A rejected delivery is reported, not raised, and never leaks the credential."""

    @pytest.mark.anyio
    async def test_rejected_delivery_does_not_raise(self):
        """A 401 from the webhook is swallowed, matching the base class contract.

        Push dispatch runs in the background consumer with no caller to receive
        an exception, so one failing webhook must not tear down the fan-out.
        """
        client = _client(status_code=401)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        await sender.send_notification(TASK_ID, _event())

        client.post.assert_awaited_once()

    @pytest.mark.anyio
    async def test_dispatch_reports_failure(self):
        """The per-webhook dispatch reports False so the fan-out can count it."""
        client = _client(status_code=500)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        delivered = await sender._dispatch_notification(
            _event(), _config(scheme="Bearer", credentials=CREDENTIALS), TASK_ID
        )

        assert delivered is False

    @pytest.mark.anyio
    async def test_successful_dispatch_reports_success(self):
        """A 200 response is reported as delivered."""
        config = _config(scheme="Bearer", credentials=CREDENTIALS)
        client = _client()
        sender = _sender(config, client)

        delivered = await sender._dispatch_notification(_event(), config, TASK_ID)

        assert delivered is True

    @pytest.mark.anyio
    async def test_credentials_are_never_logged(self, caplog):
        """Failure logs identify the task and the URL, never the credential value."""
        client = _client(status_code=401)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert CREDENTIALS not in caplog.text
        assert WEBHOOK_URL in caplog.text

    @pytest.mark.anyio
    async def test_rejection_is_reported_by_status_not_stack_trace(self, caplog):
        """A refused delivery reads as "the webhook said 401", not as a crash.

        The status is the entire diagnosis for an authentication failure, and a
        traceback through our own call frames only buries it.
        """
        client = _client(status_code=401)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        record = next(r for r in caplog.records if r.name.endswith("authenticated_push_sender"))
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        assert "401" in record.getMessage()

    @pytest.mark.anyio
    async def test_rejection_reports_what_the_receiver_said(self, caplog):
        """The receiver's own message distinguishes a gateway error from an app refusal."""
        client = _client(status_code=503, body="upstream connect error")
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert "upstream connect error" in caplog.text

    @pytest.mark.anyio
    async def test_long_rejection_body_is_truncated(self, caplog):
        """A receiver answering with an HTML page must not flood the log."""
        client = _client(status_code=503, body="x" * 5000)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        record = next(r for r in caplog.records if r.name.endswith("authenticated_push_sender"))
        assert len(record.getMessage()) < 600
        assert "…" in record.getMessage()

    @pytest.mark.anyio
    async def test_empty_rejection_body_is_labelled(self, caplog):
        """A bare status code still reads as a complete sentence."""
        client = _client(status_code=502)
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert "<empty body>" in caplog.text

    @pytest.mark.anyio
    async def test_timeout_is_reported_as_a_condition(self, caplog):
        """A slow receiver is an operational condition with a documented knob."""
        client = Mock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        record = next(r for r in caplog.records if r.name.endswith("authenticated_push_sender"))
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        assert "PUSH_NOTIFICATION_TIMEOUT_SECONDS" in record.getMessage()

    @pytest.mark.anyio
    async def test_unexpected_failure_keeps_its_traceback(self, caplog):
        """Anything not recognised as an operational condition still logs in full."""
        client = Mock()
        client.post = AsyncMock(side_effect=RuntimeError("boom"))
        sender = _sender(_config(scheme="Bearer", credentials=CREDENTIALS), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        record = next(r for r in caplog.records if r.name.endswith("authenticated_push_sender"))
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None


class TestLogsIdentifyTheEvent:
    """Every delivery log names the event, so one line differs from the next.

    A run pushes many times to the same callback URL — a status update per
    transition, an update per artifact, the terminal Task at the end. Naming
    only the task would make those lines identical, leaving the log unable to
    answer whether the outcome landed or which artifact the receiver refused.
    """

    @pytest.mark.anyio
    async def test_delivered_terminal_task_is_named_with_its_state(self, caplog):
        """The line that matters most — the outcome reached the client — says so."""
        client = _client()
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        message = _our_records(caplog)[0].getMessage()
        assert "Task state=TASK_STATE_COMPLETED" in message
        assert "HTTP 200" in message

    @pytest.mark.anyio
    async def test_status_update_is_distinguishable_from_the_terminal_task(self, caplog):
        """An intermediate update must not read like the settled Task."""
        client = _client()
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event_status())

        message = _our_records(caplog)[0].getMessage()
        assert "TaskStatusUpdateEvent state=TASK_STATE_WORKING" in message

    @pytest.mark.anyio
    async def test_artifact_update_names_the_artifact_and_the_chunk(self, caplog):
        """Which artifact, and whether it was the closing chunk."""
        client = _client()
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event_artifact())

        message = _our_records(caplog)[0].getMessage()
        assert "artifact=evolution-diff" in message
        assert "last_chunk=True" in message

    @pytest.mark.anyio
    async def test_rejection_names_what_was_rejected(self, caplog):
        """Ranking a failure needs the event: a refused outcome is not a refused ping."""
        client = _client(status_code=503, body="upstream connect error")
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        message = _our_records(caplog)[0].getMessage()
        assert "Task state=TASK_STATE_COMPLETED" in message
        assert "503" in message

    @pytest.mark.anyio
    async def test_timeout_names_what_was_being_delivered(self, caplog):
        """Same question on the timeout path, where the receiver never answered."""
        client = Mock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event_status())

        assert "TaskStatusUpdateEvent state=TASK_STATE_WORKING" in caplog.text

    @pytest.mark.anyio
    async def test_task_id_is_not_repeated_in_the_message(self, caplog):
        """The id reaches the record as a structured field; the text must not echo it."""
        client = _client()
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert TASK_ID not in _our_records(caplog)[0].getMessage()

    @pytest.mark.anyio
    async def test_round_trip_duration_is_reported_when_known(self, caplog):
        """Latency is the only warning before the timeout budget starts biting."""
        client = _client(elapsed_ms=143)
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert "143 ms" in _our_records(caplog)[0].getMessage()

    @pytest.mark.anyio
    async def test_missing_duration_does_not_break_the_line(self, caplog):
        """httpx times one transport round-trip; a response without one still logs."""
        client = _client()
        sender = _sender(_config(), client)

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        message = _our_records(caplog)[0].getMessage()
        assert "ms" not in message
        assert message.endswith("(HTTP 200)")

    @pytest.mark.anyio
    async def test_unknown_state_falls_back_to_its_value(self, caplog):
        """A peer on a newer schema must not turn a log line into a traceback.

        proto3 keeps an unrecognised enum number rather than rejecting it, so a
        state this build has no name for can reach the log.
        """
        client = _client()
        sender = _sender(_config(), client)
        event = _event()
        event.status.state = 9999

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, event)

        assert "state=9999" in _our_records(caplog)[0].getMessage()


class TestFanOutSummary:
    """The fan-out reports only what the per-webhook lines do not already say.

    The SDK base class answers any failure with a bare "some notifications
    failed" line carrying nothing but the task id, which on the common
    single-webhook task is a second, less informative copy of the rejection
    logged one line above. These tests pin the summary to the case where it
    adds something: a real fan-out, reported with counts.
    """

    @pytest.mark.anyio
    async def test_single_failing_webhook_is_reported_once(self, caplog):
        """One webhook, one failure, one line — the one naming the URL and the status."""
        sender = _fan_out_sender({WEBHOOK_URL: 503})

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        records = _our_records(caplog)
        assert len(records) == 1
        assert "503" in records[0].getMessage()
        assert "fan-out" not in caplog.text

    @pytest.mark.anyio
    async def test_partial_fan_out_failure_is_summarised_with_counts(self, caplog):
        """With several receivers the summary states how much of the fan-out got through."""
        sender = _fan_out_sender(
            {WEBHOOK_URL: 503, f"{WEBHOOK_URL}-b": 200, f"{WEBHOOK_URL}-c": 401}
        )

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        summary = next(r for r in _our_records(caplog) if "fan-out" in r.getMessage())
        assert summary.levelno == logging.WARNING
        assert "2 of 3 deliveries failed" in summary.getMessage()

    @pytest.mark.anyio
    async def test_fully_delivered_fan_out_is_not_summarised(self, caplog):
        """Nothing failed, so there is no failure to summarise."""
        sender = _fan_out_sender({WEBHOOK_URL: 200, f"{WEBHOOK_URL}-b": 200})

        with caplog.at_level(logging.DEBUG):
            await sender.send_notification(TASK_ID, _event())

        assert "fan-out" not in caplog.text

    @pytest.mark.anyio
    async def test_task_without_webhooks_dispatches_nothing(self):
        """A task nobody subscribed to short-circuits before any request goes out."""
        client = Mock()
        client.post = AsyncMock()
        sender = _fan_out_sender({}, client)

        await sender.send_notification(TASK_ID, _event())

        client.post.assert_not_awaited()

    @pytest.mark.anyio
    async def test_every_webhook_is_attempted_despite_an_early_failure(self):
        """A refusal from one receiver must not cost the others their delivery."""
        sender = _fan_out_sender(
            {WEBHOOK_URL: 401, f"{WEBHOOK_URL}-b": 200, f"{WEBHOOK_URL}-c": 200}
        )

        await sender.send_notification(TASK_ID, _event())

        assert sender._client.post.await_count == 3
