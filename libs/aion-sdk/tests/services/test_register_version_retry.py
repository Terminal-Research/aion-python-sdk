"""Tests for the retry policy around version registration."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

import httpx

import aion.cli.services.aion.deployment_register_version as register_version
from aion.api.gql.generated.graphql_client import (
    GraphQLClientGraphQLMultiError,
    GraphQLClientHttpError,
)


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    """Collapse the backoff so the tests exercise the policy, not the clock."""
    monkeypatch.setattr(register_version, "REGISTRATION_BASE_DELAY_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def unlatched_auth(monkeypatch):
    """Start each test with credentials that have not been rejected.

    ``aion_jwt_manager`` is a process-wide singleton, so a latch set by one test
    would silently disable retrying in every test that followed.
    """
    monkeypatch.setattr(
        register_version.aion_jwt_manager, "_auth_failed", False, raising=False)


def install_failing_client(monkeypatch, exception, succeed_after=None):
    """Point the service at a client that raises, and count the attempts."""
    calls = {"count": 0}

    class FakeClient:
        async def register_version(self, version_id):
            calls["count"] += 1
            if succeed_after is not None and calls["count"] >= succeed_after:
                return None
            raise exception

    class FakeContextClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return FakeClient()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        register_version, "AionGqlContextClient", FakeContextClient)
    return calls


def graphql_refusal() -> GraphQLClientGraphQLMultiError:
    class Error:
        message = "version already registered"
        path = None
        extensions = None

    return GraphQLClientGraphQLMultiError([Error()], None)


async def test_transient_failure_is_retried_until_it_succeeds(monkeypatch):
    """A connect timeout shorter than the outage must not cost the registration.

    This is the case that motivated the retry: registration fires once at startup,
    so a momentary blip used to leave the agent running but unknown to the platform
    until someone restarted it.
    """
    calls = install_failing_client(
        monkeypatch, httpx.ConnectTimeout(""), succeed_after=3)

    assert await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == 3


async def test_transient_failure_gives_up_after_the_budget(monkeypatch):
    """Retrying is bounded - a real outage should end in the warning, not a loop."""
    calls = install_failing_client(monkeypatch, httpx.ConnectTimeout(""))

    assert not await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == register_version.REGISTRATION_ATTEMPTS


async def test_graphql_refusal_is_not_retried(monkeypatch):
    """The platform answered and said no; asking again only delays the warning."""
    calls = install_failing_client(monkeypatch, graphql_refusal())

    assert not await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == 1


async def test_rejected_credentials_are_not_retried(monkeypatch):
    """A latched 401 means no amount of waiting will produce a token."""
    monkeypatch.setattr(
        register_version.aion_jwt_manager, "_auth_failed", True, raising=False)
    calls = install_failing_client(monkeypatch, httpx.ConnectTimeout(""))

    assert not await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == 1


@pytest.mark.parametrize(
    "status_code, expected_attempts",
    [
        (500, 5),
        (503, 5),
        (429, 5),
        (400, 1),
        (404, 1),
    ],
)
async def test_http_status_decides_whether_to_retry(
    monkeypatch, status_code, expected_attempts
):
    """5xx and 429 are transient; a 4xx is the platform rejecting the request."""
    error = GraphQLClientHttpError(
        status_code=status_code, response=httpx.Response(status_code=status_code))
    calls = install_failing_client(monkeypatch, error)

    assert not await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == expected_attempts
