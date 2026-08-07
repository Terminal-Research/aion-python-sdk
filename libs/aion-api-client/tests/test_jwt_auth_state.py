"""Tests for authentication endpoints and the process-wide auth-failure latch."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")

from aion.api.exceptions import AionAuthenticationError
from aion.api.http.client import AionHttpClient
from aion.api.http.jwt_manager import AionRefreshingJWTManager, describe_exception

AUTH_ENDPOINT = "/auth/tokens"


@pytest.fixture(autouse=True)
def credentials(configured_auth):
    """Give every test in this module a workspace that looks authenticated.

    ``AionRefreshingJWTManager`` refuses to reach the network when the platform
    credentials are absent, so tests that stub the HTTP client still need
    ``api_settings`` to say authentication is worth attempting.
    """
    return configured_auth


class RecordingHttpClient(AionHttpClient):
    """AionHttpClient that records requests instead of sending them."""

    def __init__(self, status_code: int = 200, token: str | None = None) -> None:
        super().__init__()
        self.status_code = status_code
        self.token = token
        self.endpoints: list[str] = []

    def _respond(self, endpoint: str):
        import httpx

        self.endpoints.append(endpoint)
        return httpx.Response(
            status_code=self.status_code,
            json={"accessToken": self.token} if self.token else {},
        )

    async def request(self, method: str, endpoint: str, *args, **kwargs):
        return self._respond(endpoint)

    def request_sync(self, method: str, endpoint: str, *args, **kwargs):
        return self._respond(endpoint)


class FailingClient:
    """Auth client that always fails with a fixed status code."""

    def __init__(self, status_code: int | None) -> None:
        self.status_code = status_code
        self.calls = 0

    def _fail(self):
        self.calls += 1
        raise AionAuthenticationError(
            f"Authentication failed: {self.status_code}",
            status_code=self.status_code,
        )

    async def authenticate(self):
        self._fail()

    def authenticate_sync(self):
        self._fail()


class ExplodingClient:
    """Auth client that fails with a non-HTTP exception, as a network error does."""

    def __init__(self, exception: BaseException) -> None:
        self.exception = exception

    async def authenticate(self):
        raise self.exception

    def authenticate_sync(self):
        raise self.exception


@pytest.fixture
def rejecting_manager():
    """A manager whose every authentication attempt is answered with a 401."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(401)
    return manager


def _auth_failure_reports(caplog):
    """The lines that tell a reader authentication failed, at any severity."""
    return [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
        and "authentication failed" in record.getMessage()
    ]


@pytest.mark.anyio("asyncio")
async def test_authenticate_uses_tokens_endpoint(valid_jwt_token) -> None:
    """The async auth call should target the canonical endpoint."""
    client = RecordingHttpClient(token=valid_jwt_token)

    await client.authenticate()

    assert client.endpoints == [AUTH_ENDPOINT]


def test_authenticate_sync_uses_same_endpoint_as_async(valid_jwt_token) -> None:
    """Sync and async auth must not drift onto different endpoints.

    They did once: the move to ``/auth/tokens`` updated only the async call and
    left the sync one on ``/auth/token``, which 404s. That killed every
    synchronous consumer (model service, MCP, remote logstash) while the async
    path kept working, so nothing pointed at the endpoint as the cause.
    """
    client = RecordingHttpClient(token=valid_jwt_token)

    client.authenticate_sync()

    assert client.endpoints == [AUTH_ENDPOINT]


def test_authentication_error_carries_status_code() -> None:
    """Non-200 responses should report the status they actually got."""
    client = RecordingHttpClient(status_code=404)

    with pytest.raises(AionAuthenticationError) as excinfo:
        client.authenticate_sync()

    assert excinfo.value.status_code == 404
    assert "404" in str(excinfo.value)


def test_invalid_credentials_latch_auth_failed(rejecting_manager) -> None:
    """A 401 means the credentials are wrong, so refreshing should stop."""
    assert rejecting_manager.get_token_sync() is None
    assert rejecting_manager.is_auth_failed is True


@pytest.mark.parametrize("status_code", [404, 500, 502, None])
def test_non_401_failures_do_not_latch(status_code) -> None:
    """Only rejected credentials are permanent; everything else stays retryable.

    The latch is shared by every consumer of the global manager, so tripping it
    on a 404 or a 5xx would disable authentication process-wide - including
    callers whose own requests would have succeeded - until restart.
    """
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(status_code)

    assert manager.get_token_sync() is None
    assert manager.is_auth_failed is False


def test_401_is_reported_once_across_async_and_sync_paths(rejecting_manager, caplog):
    """The async and sync refresh paths share one latch, so one 401, one report."""
    with caplog.at_level(logging.DEBUG, logger="aion.api.http.jwt_manager"):
        assert asyncio.run(rejecting_manager.get_token()) is None
        assert asyncio.run(rejecting_manager.get_token()) is None
        assert rejecting_manager.get_token_sync() is None

    assert len(_auth_failure_reports(caplog)) == 1
    assert rejecting_manager.is_auth_failed
    # Only the first attempt reaches the network; the latch stops the rest.
    assert rejecting_manager._client.calls == 1


def test_reset_allows_the_next_failure_to_be_reported_again(rejecting_manager, caplog):
    with caplog.at_level(logging.DEBUG, logger="aion.api.http.jwt_manager"):
        asyncio.run(rejecting_manager.get_token())
        rejecting_manager.reset_auth_state()
        asyncio.run(rejecting_manager.get_token())

    assert len(_auth_failure_reports(caplog)) == 2


def test_missing_credentials_skip_the_network_entirely(
    rejecting_manager, configured_auth, caplog, monkeypatch
):
    """Without credentials there is nothing to try, so say so once and stop.

    This used to raise out of the payload builder on every single call, which
    reported an error per consumer instead of a fact about the workspace.
    """
    monkeypatch.setattr(configured_auth, "client_secret", None)

    with caplog.at_level(logging.DEBUG, logger="aion.api.http.jwt_manager"):
        assert asyncio.run(rejecting_manager.get_token()) is None
        assert rejecting_manager.get_token_sync() is None

    assert _auth_failure_reports(caplog) == []
    assert rejecting_manager._client.calls == 0
    assert not rejecting_manager.is_auth_failed
    unconfigured = [
        record for record in caplog.records
        if "authentication is not configured" in record.getMessage()
    ]
    assert len(unconfigured) == 1


def test_lock_survives_being_used_from_a_second_event_loop(rejecting_manager):
    """Forked agents run a fresh loop; a lock bound to the parent's would raise."""

    async def contend(manager):
        await asyncio.gather(*(manager.get_token() for _ in range(5)))

    asyncio.run(contend(rejecting_manager))
    asyncio.run(contend(rejecting_manager))


@pytest.mark.anyio("asyncio")
async def test_async_path_survives_sync_failure(valid_jwt_token) -> None:
    """A failed sync refresh must not block a working async one."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(404)
    manager.get_token_sync()

    manager._client = RecordingHttpClient(token=valid_jwt_token)

    assert await manager.get_token() == valid_jwt_token


def test_transient_failure_recovers_on_next_request(valid_jwt_token) -> None:
    """Once the endpoint recovers, the next request should get a token."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(503)

    assert manager.get_token_sync() is None

    manager._client = RecordingHttpClient(token=valid_jwt_token)

    assert manager.get_token_sync() == valid_jwt_token


def test_successful_refresh_clears_latch(valid_jwt_token) -> None:
    """A successful authentication should reset a previously latched failure."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(401)
    manager.get_token_sync()
    assert manager.is_auth_failed is True

    manager.reset_auth_state()
    manager._client = RecordingHttpClient(token=valid_jwt_token)

    assert manager.get_token_sync() == valid_jwt_token
    assert manager.is_auth_failed is False


def test_describe_exception_keeps_type_when_message_is_empty() -> None:
    """httpx network errors stringify to nothing, so the type has to carry the meaning.

    ``str(httpx.ConnectTimeout(""))`` is the empty string. Logged with "%s" it
    produced a line that trailed off after the colon and named neither the failure
    nor its cause - the reason an authentication outage was undiagnosable from the
    logs alone.
    """
    import httpx

    assert describe_exception(httpx.ConnectTimeout("")) == "ConnectTimeout"
    assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"
    assert describe_exception(ValueError("boom")) == "ValueError: boom"


def test_rejected_credentials_are_recorded_as_last_auth_error(rejecting_manager) -> None:
    """A 401 should leave behind a reason a consumer can quote."""
    assert rejecting_manager.get_token_sync() is None
    assert "401" in rejecting_manager.last_auth_error


def test_network_failure_is_recorded_as_last_auth_error() -> None:
    """The unexpected-exception path must record too, not just the HTTP ones.

    This is the path a connect timeout takes, and it is the one that used to
    discard its exception entirely.
    """
    import httpx

    manager = AionRefreshingJWTManager()
    manager._client = ExplodingClient(httpx.ConnectTimeout(""))

    assert manager.get_token_sync() is None
    assert manager.last_auth_error == "ConnectTimeout"


def test_successful_refresh_clears_last_auth_error(valid_jwt_token) -> None:
    """A recovered endpoint must not keep reporting the failure that preceded it."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(503)
    manager.get_token_sync()
    assert manager.last_auth_error is not None

    manager._client = RecordingHttpClient(token=valid_jwt_token)

    assert manager.get_token_sync() == valid_jwt_token
    assert manager.last_auth_error is None


@pytest.mark.anyio("asyncio")
async def test_async_path_records_failures_too() -> None:
    """get_token and get_token_sync share the contract, so both must record."""
    import httpx

    manager = AionRefreshingJWTManager()
    manager._client = ExplodingClient(httpx.ConnectError(""))

    assert await manager.get_token() is None
    assert manager.last_auth_error == "ConnectError"
