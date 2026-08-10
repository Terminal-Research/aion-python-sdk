"""Tests for authentication endpoints and auth-failure latching."""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

os.environ.setdefault("AION_CLIENT_ID", "test-client")
os.environ.setdefault("AION_CLIENT_SECRET", "test-secret")

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")

from aion.api.exceptions import AionAuthenticationError
from aion.api.http.client import AionHttpClient
from aion.api.http.jwt_manager import AionRefreshingJWTManager, describe_exception

AUTH_ENDPOINT = "/auth/tokens"


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    """Give the auth payload builder credentials to work with.

    ``api_settings`` is a singleton built when ``aion.core.settings`` is first
    imported, so setting environment variables at module scope only works when
    this module happens to be imported first. Patch the instance instead.
    """
    from aion.core.settings import api_settings

    monkeypatch.setattr(api_settings, "client_id", "test-client")
    monkeypatch.setattr(api_settings, "client_secret", "test-secret")


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

    def _fail(self):
        raise AionAuthenticationError(
            f"Authentication failed: {self.status_code}",
            status_code=self.status_code,
        )

    async def authenticate(self):
        self._fail()

    def authenticate_sync(self):
        self._fail()


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


def test_invalid_credentials_latch_auth_failed() -> None:
    """A 401 means the credentials are wrong, so refreshing should stop."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(401)

    assert manager.get_token_sync() is None
    assert manager.is_auth_failed is True


@pytest.mark.parametrize("status_code", [404, 500, 502, None])
def test_non_401_failures_do_not_latch(status_code) -> None:
    """Only rejected credentials are permanent; everything else stays retryable.

    ``_auth_failed`` is shared by every consumer of the global manager, so
    latching on a 404 or a 5xx would disable authentication process-wide -
    including callers whose own requests would have succeeded - until restart.
    """
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(status_code)

    assert manager.get_token_sync() is None
    assert manager.is_auth_failed is False


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


class ExplodingClient:
    """Auth client that fails with a non-HTTP exception, as a network error does."""

    def __init__(self, exception: BaseException) -> None:
        self.exception = exception

    async def authenticate(self):
        raise self.exception

    def authenticate_sync(self):
        raise self.exception


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


def test_rejected_credentials_are_recorded_as_last_auth_error() -> None:
    """A 401 should leave behind a reason a consumer can quote."""
    manager = AionRefreshingJWTManager()
    manager._client = FailingClient(401)

    assert manager.get_token_sync() is None
    assert "401" in manager.last_auth_error


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


class TestLifetimeReporting:
    """The token line is read next to log timestamps, which are in local time."""

    def _token(self, minutes: int):
        import datetime

        import jwt as pyjwt

        from aion.api.http.jwt_manager import Token

        exp = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(
            minutes=minutes)
        return Token.from_jwt(
            pyjwt.encode({"exp": int(exp.timestamp())}, "secret", algorithm="HS256"))

    def test_expiry_is_stated_in_the_local_clock(self) -> None:
        """A UTC expiry beside a local timestamp reads as a token already dead.

        This is not hypothetical: at UTC+3 a freshly issued hour-long token used
        to log an expiry two hours in the past.
        """
        import datetime

        from aion.api.http.jwt_manager import describe_lifetime

        token = self._token(60)
        local_hour = token.expires_at.astimezone().strftime("%H:%M")

        assert local_hour in describe_lifetime(token)
        assert "+00:00" not in describe_lifetime(token)

    def test_remaining_lifetime_leads_the_line(self) -> None:
        """The useful number is how long the token has left, not a wall-clock time."""
        from aion.api.http.jwt_manager import describe_lifetime

        assert describe_lifetime(self._token(60)).startswith("valid for 59m")

    def test_short_lifetimes_are_reported_in_seconds(self) -> None:
        """A token good for well under a minute must not round to '0m'."""
        from aion.api.http.jwt_manager import describe_lifetime

        assert "s (expires" in describe_lifetime(self._token(1))
