"""Tests for how a serving deployment learns its VERSION_ID.

The value has three possible sources and they are not interchangeable: an explicit
environment variable pins a deployment, the access token already names the version
its credentials were issued for, and the control-plane query is what covers every
other principal. What is worth pinning is the order between them and the fallback,
because getting it wrong is silent - the deployment serves, just under the wrong
identity or without one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

import aion.cli.services.serve.environment_preparer as environment_preparer
from aion.cli.services.serve.environment_preparer import (
    ServeEnvironmentPreparerService,
)


@pytest.fixture(autouse=True)
def clean_version_env(monkeypatch):
    """Keep an ambient VERSION_ID out of tests that are about the other sources."""
    monkeypatch.delenv("VERSION_ID", raising=False)


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    """Give the service credentials, since without them it never looks anything up."""
    from aion.core.settings import api_settings

    monkeypatch.setattr(api_settings, "client_id", "test-client", raising=False)
    monkeypatch.setattr(api_settings, "client_secret", "test-secret", raising=False)


@pytest.fixture
def token_version(monkeypatch):
    """Control what version the access token claims to be scoped to."""

    def _set(version_id):
        async def get_version_id():
            return version_id

        monkeypatch.setattr(
            environment_preparer.aion_jwt_manager,
            "get_version_id",
            get_version_id,
            raising=False,
        )

    return _set


@pytest.fixture
def control_plane(monkeypatch):
    """Record whether the control plane was queried, and with what answer."""
    calls = {"count": 0}

    def _set(version_id):
        async def fetch(self):
            calls["count"] += 1
            return version_id

        monkeypatch.setattr(
            ServeEnvironmentPreparerService,
            "_fetch_version_from_control_plane",
            fetch,
        )
        return calls

    return _set


async def test_token_version_is_used_without_querying_the_control_plane(
    token_version, control_plane
) -> None:
    """The whole point of reading the token is to skip a round trip, so verify it."""
    token_version("version-from-token")
    calls = control_plane("version-from-control-plane")

    context = await ServeEnvironmentPreparerService().execute()

    assert context.version_id == "version-from-token"
    assert calls["count"] == 0


async def test_non_version_token_falls_back_to_the_control_plane(
    token_version, control_plane
) -> None:
    """A user-authenticated caller has no version in its token but still has one.

    ``get_version_id`` withholds the subject unless it is a version, and the query
    resolves the version by client id, so this is the path that keeps those callers
    working rather than the path that fails them.
    """
    token_version(None)
    calls = control_plane("version-from-control-plane")

    context = await ServeEnvironmentPreparerService().execute()

    assert context.version_id == "version-from-control-plane"
    assert calls["count"] == 1


async def test_environment_variable_still_wins(
    token_version, control_plane, monkeypatch
) -> None:
    """An explicitly pinned VERSION_ID must not be overridden by the token."""
    monkeypatch.setenv("VERSION_ID", "version-from-env")
    token_version("version-from-token")
    calls = control_plane("version-from-control-plane")

    context = await ServeEnvironmentPreparerService().execute()

    assert context.version_id == "version-from-env"
    assert calls["count"] == 0


async def test_token_failure_does_not_prevent_the_fallback(
    monkeypatch, control_plane
) -> None:
    """A broken token read must degrade to the query, not abort the whole startup."""

    async def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        environment_preparer.aion_jwt_manager,
        "get_version_id",
        explode,
        raising=False,
    )
    calls = control_plane("version-from-control-plane")

    context = await ServeEnvironmentPreparerService().execute()

    assert context.version_id == "version-from-control-plane"
    assert calls["count"] == 1


async def test_no_source_yields_no_version(token_version, control_plane) -> None:
    """With nothing to go on the service reports None rather than inventing a value."""
    token_version(None)
    control_plane(None)

    context = await ServeEnvironmentPreparerService().execute()

    assert context.version_id is None
