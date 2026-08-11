"""Tests for the retry policy around version registration.

What is worth pinning here is which failures are worth retrying. That is the only
real decision in the service, it has no other guardrail, and it fails silently:
misjudge one code and the deployment stays invisible to the platform with nothing
red anywhere. The loop around it is a ``while True`` and needs no test of its own.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("httpx")

import httpx

import aion.cli.services.aion.deployment_register_version as register_version
from aion.api.gql.generated.graphql_client import (
    GraphQLClientGraphQLError,
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
    """Point the service at a client that raises, and count the attempts.

    Retrying is unbounded, so ``succeed_after`` is what ends the loop: every test
    states its own exit rather than borrowing one from a production limit. That is
    how the previous version of this file hung - it counted on the attempt budget
    to stop, and the day the budget went away it looped until it exhausted memory.
    """
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


def http_error(status_code: int) -> GraphQLClientHttpError:
    """Build the transport-level error the client raises for a bare HTTP status."""
    return GraphQLClientHttpError(
        status_code=status_code, response=httpx.Response(status_code=status_code))


def graphql_errors(*codes: str) -> GraphQLClientGraphQLMultiError:
    """Build a GraphQL response carrying one error per code."""
    return GraphQLClientGraphQLMultiError(
        [GraphQLClientGraphQLError(message=code, extensions={"code": code})
         for code in codes],
        None)


def uncoded_graphql_error() -> GraphQLClientGraphQLMultiError:
    """Build a GraphQL error that carries no extensions at all."""
    return GraphQLClientGraphQLMultiError(
        [GraphQLClientGraphQLError(message="version already registered")], None)


@pytest.mark.parametrize(
    "error, retried",
    [
        # The case the retry was written for: a connect timeout shorter than the
        # outage it hit.
        (httpx.ConnectTimeout(""), True),
        # The platform could not answer, as against would not.
        (http_error(500), True),
        (http_error(400), False),
        # Registration is the platform reading back the endpoint we serve, so it
        # fails until the tunnel or ingress in front of us starts routing.
        (graphql_errors("SERVICE_UNAVAILABLE"), True),
        # An unlabelled failure is a refusal by default: guessing that it is
        # temporary is how a permanent one turns into silence.
        (uncoded_graphql_error(), False),
        # Every error transient, not any one - the other half rejects us again no
        # matter how long we wait for this half to clear.
        (graphql_errors("SERVICE_UNAVAILABLE", "BAD_USER_INPUT"), False),
    ],
)
async def test_which_failures_are_worth_retrying(monkeypatch, error, retried):
    """Waiting must be reserved for failures that waiting can fix."""
    calls = install_failing_client(monkeypatch, error, succeed_after=3)

    assert await register_version.AionDeploymentRegisterVersionService().execute([]) \
        is retried
    assert calls["count"] == (3 if retried else 1)


async def test_rejected_credentials_are_not_retried(monkeypatch):
    """A latched 401 means no amount of waiting will produce a token.

    Retrying is unbounded, so getting this wrong is no longer a wasted minute - it
    is a loop asking the platform forever with credentials it has already refused.
    """
    monkeypatch.setattr(
        register_version.aion_jwt_manager, "_auth_failed", True, raising=False)
    calls = install_failing_client(monkeypatch, httpx.ConnectTimeout(""))

    assert not await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == 1


async def test_the_warnings_stop_once_local_mode_is_declared(monkeypatch, caplog):
    """Three warnings, then quiet: the state is announced once, not repeated.

    Nothing after the third attempt can say anything that line has not - the same
    endpoint fails the same way for as long as it is down - and an hourly reminder
    would only teach the reader to skip the level this service warns on.
    """
    caplog.set_level(logging.DEBUG)
    install_failing_client(
        monkeypatch, httpx.ConnectTimeout(""), succeed_after=6)

    assert await register_version.AionDeploymentRegisterVersionService().execute([])

    levels = [record.levelno for record in caplog.records]
    assert levels == [
        logging.WARNING,  # a deployment the platform cannot reach is not ordinary
        logging.WARNING,
        logging.WARNING,  # ...and this one says what that has added up to
        logging.DEBUG,    # from here the retrying is nobody's business
        logging.DEBUG,
        logging.INFO,     # the only line that revokes local mode
    ]
    assert "serve locally" in caplog.records[2].message
    assert "no longer serving locally" in caplog.records[-1].message


async def test_a_run_that_never_reached_local_mode_says_nothing_about_it(
        monkeypatch, caplog):
    """The success line claims to end a state, so it must not invent one."""
    caplog.set_level(logging.DEBUG)
    install_failing_client(
        monkeypatch, httpx.ConnectTimeout(""), succeed_after=2)

    await register_version.AionDeploymentRegisterVersionService().execute([])

    assert "locally" not in caplog.records[-1].message


async def test_retrying_is_not_capped(monkeypatch):
    """An outage that outlasts any fixed number of attempts must not cost the link.

    Registration is the platform reading back an endpoint that may take a while to
    start routing, and if waiting can fix that there is no attempt at which it
    stops being able to. A cap made a restart the price of a slow tunnel.
    """
    calls = install_failing_client(
        monkeypatch, httpx.ConnectTimeout(""), succeed_after=9)

    assert await register_version.AionDeploymentRegisterVersionService().execute([])
    assert calls["count"] == 9
