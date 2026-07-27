"""Tests for the process-wide Aion authentication latch."""

import asyncio
import logging

import pytest

from aion.api.exceptions import AionAuthenticationError
from aion.api.http.jwt_manager import AionRefreshingJWTManager


@pytest.fixture
def rejecting_manager(configured_auth):
    """A manager whose every authentication attempt is answered with a 401."""

    class RejectingHttpClient:
        def __init__(self):
            self.calls = 0

        async def authenticate(self):
            self.calls += 1
            raise AionAuthenticationError("Invalid client credentials")

        def authenticate_sync(self):
            self.calls += 1
            raise AionAuthenticationError("Invalid client credentials")

    manager = AionRefreshingJWTManager()
    manager._client = RejectingHttpClient()
    return manager


def _auth_warnings(caplog):
    return [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and "authentication failed" in record.getMessage()
    ]


def test_401_is_reported_once_across_async_and_sync_paths(rejecting_manager, caplog):
    """The async and sync refresh paths share one latch, so one 401, one warning."""
    with caplog.at_level(logging.DEBUG, logger="aion.api.http.jwt_manager"):
        assert asyncio.run(rejecting_manager.get_token()) is None
        assert asyncio.run(rejecting_manager.get_token()) is None
        assert rejecting_manager.get_token_sync() is None

    assert len(_auth_warnings(caplog)) == 1
    assert rejecting_manager.is_auth_failed
    # Only the first attempt reaches the network; the latch stops the rest.
    assert rejecting_manager._client.calls == 1


def test_reset_allows_the_next_failure_to_be_reported_again(rejecting_manager, caplog):
    with caplog.at_level(logging.DEBUG, logger="aion.api.http.jwt_manager"):
        asyncio.run(rejecting_manager.get_token())
        rejecting_manager.reset_auth_state()
        asyncio.run(rejecting_manager.get_token())

    assert len(_auth_warnings(caplog)) == 2


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

    assert _auth_warnings(caplog) == []
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
