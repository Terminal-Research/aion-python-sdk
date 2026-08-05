"""Tests for AuthenticatedPushNotificationSender.

The sender owns the inbound half of the push contract that the SDK base class
drops: a client that declares ``taskPushNotificationConfig.authentication``
expects its callback endpoint to be called with those credentials. The base
class emits only ``X-A2A-Notification-Token``, so an authenticated webhook
rejects every delivery. These tests pin the header derivation and the failure
handling around it.
"""

import httpx
import logging
import pytest
from a2a.types import Task, TaskState, TaskStatus
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
) -> TaskPushNotificationConfig:
    """Builds a stored push configuration.

    Args:
        token: Value for the SDK's own notification token, empty to omit it.
        scheme: Authentication scheme to declare, None to omit authentication
            entirely, empty string to declare credentials without a scheme.
        credentials: Credential value to declare, None to omit authentication.

    Returns:
        The configuration as it would come back from the config store.
    """
    config = TaskPushNotificationConfig(task_id=TASK_ID, url=WEBHOOK_URL, token=token)
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


def _client(status_code: int = 200, body: str = "") -> Mock:
    """Returns an httpx client stand-in whose POST answers with the given status."""
    response = httpx.Response(
        status_code, request=httpx.Request("POST", WEBHOOK_URL), text=body
    )
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
        """The per-webhook dispatch reports False so the fan-out can log a summary."""
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
