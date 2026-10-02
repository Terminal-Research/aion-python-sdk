"""The one rule for the server's mode: where it runs decides which tokens it accepts."""

import logging
from unittest.mock import Mock

import pytest

import aion.server.auth.mode as mode
from aion.server.auth import AuthConfigurationError, build_token_verifier, deployment_id, is_hosted

_URL = "https://api.aion.example/runtime/a2a/verification-keys"
_DEPLOYMENT = "3f2b8c1d-5a6e-4f70-9b8c-1d2e3f4a5b6c"


def _settings(monkeypatch, *, client_id="client-123", url=_URL, issuer="aion.io", strict=False) -> None:
    monkeypatch.setattr(
        mode, "api_settings", Mock(client_id=client_id, verification_keys_url=url, client_auth_issuer=issuer)
    )
    monkeypatch.setattr(mode, "app_settings", Mock(require_invocation_auth=strict))


def _deployment(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    else:
        monkeypatch.setenv("DEPLOYMENT_ID", value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (_DEPLOYMENT, _DEPLOYMENT), (_DEPLOYMENT.upper(), _DEPLOYMENT)],
    ids=["unset", "uuid", "uppercase-uuid"],
)
def test_a_server_is_hosted_when_the_deployment_id_is_a_uuid(monkeypatch, value, expected) -> None:
    _deployment(monkeypatch, value)

    assert deployment_id() == expected
    assert is_hosted() is (expected is not None)


@pytest.mark.parametrize("value", ["", "   ", "deployment-1", f"{_DEPLOYMENT}x"], ids=["empty", "blank", "name", "trailing"])
def test_a_set_deployment_id_that_is_not_a_uuid_stops_the_server(monkeypatch, value) -> None:
    """Never read as "not hosted": a broken hosted configuration must not open the server."""
    _deployment(monkeypatch, value)
    _settings(monkeypatch)

    with pytest.raises(AuthConfigurationError, match="DEPLOYMENT_ID"):
        build_token_verifier()


def test_a_hosted_server_accepts_invocation_tokens_only(monkeypatch) -> None:
    _deployment(monkeypatch, _DEPLOYMENT)
    _settings(monkeypatch)

    verifier = build_token_verifier()

    assert (verifier.invocation_audience, verifier.accept_sessions, verifier.trust_application_users) == (
        "client-123",
        False,
        False,
    )
    assert verifier.keys.url == _URL


@pytest.mark.parametrize("strict", [False, True])
def test_hosting_cannot_be_opted_out_of(monkeypatch, strict) -> None:
    _deployment(monkeypatch, _DEPLOYMENT)
    _settings(monkeypatch, strict=strict)

    verifier = build_token_verifier()

    assert (verifier.accept_sessions, verifier.trust_application_users) == (False, False)


def test_outside_the_platform_invocation_tokens_may_be_required(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch, strict=True)

    verifier = build_token_verifier()

    assert (verifier.invocation_audience, verifier.accept_sessions, verifier.trust_application_users) == (
        "client-123",
        False,
        False,
    )


def test_requiring_invocation_tokens_without_a_client_id_does_not_start(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch, client_id=None, strict=True)

    with pytest.raises(AuthConfigurationError, match="AION_REQUIRE_INVOCATION_AUTH.*AION_CLIENT_ID"):
        build_token_verifier()


@pytest.mark.parametrize("client_id", [None, ""], ids=["unset", "empty"])
def test_a_hosted_server_without_client_id_does_not_start(monkeypatch, client_id) -> None:
    _deployment(monkeypatch, _DEPLOYMENT)
    _settings(monkeypatch, client_id=client_id)

    with pytest.raises(AuthConfigurationError, match="AION_CLIENT_ID"):
        build_token_verifier()


def test_outside_the_platform_with_a_client_id_both_kinds_are_accepted(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch)

    verifier = build_token_verifier()

    assert (verifier.invocation_audience, verifier.accept_sessions, verifier.trust_application_users) == (
        "client-123",
        True,
        True,
    )


def test_outside_the_platform_without_a_client_id_only_sessions_are_accepted(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch, client_id=None)

    verifier = build_token_verifier()

    assert (verifier.invocation_audience, verifier.accept_sessions, verifier.trust_application_users) == (
        None,
        True,
        True,
    )


def test_one_key_source_and_the_configured_issuer_serve_both_kinds(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch, issuer="aion.staging")

    verifier = build_token_verifier()

    assert verifier.issuer == "aion.staging"
    assert verifier.keys.url == _URL


def test_keys_from_an_untrusted_url_stop_the_server(monkeypatch) -> None:
    _deployment(monkeypatch, None)
    _settings(monkeypatch, url="http://api.aion.example/runtime/a2a/verification-keys")

    with pytest.raises(AuthConfigurationError, match="HTTPS"):
        build_token_verifier()


@pytest.mark.parametrize(
    ("deployment", "client_id", "strict", "message"),
    [
        (_DEPLOYMENT, "client-123", False,
         "Authentication: hosted on the Aion platform; accepting invocation tokens only"),
        (None, "client-123", True,
         "Authentication: not hosted, invocation tokens required; accepting invocation tokens only"),
        (None, "client-123", False,
         "Authentication: not hosted; accepting invocation tokens, anonymous session tokens "
         "and the application's authenticated users"),
        (None, None, False,
         "Authentication: not hosted, no AION_CLIENT_ID; accepting anonymous session tokens "
         "and the application's authenticated users"),
    ],
    ids=["hosted", "strict", "client-id", "no-client-id"],
)
def test_the_startup_log_names_the_mode_in_words(monkeypatch, caplog, deployment, client_id, strict, message) -> None:
    _deployment(monkeypatch, deployment)
    _settings(monkeypatch, client_id=client_id, strict=strict)

    with caplog.at_level(logging.INFO, logger=mode.logger.name):
        build_token_verifier()

    records = [record for record in caplog.records if record.name == mode.logger.name]
    assert [(record.levelno, record.message) for record in records] == [(logging.INFO, message)]


async def test_a_local_server_verifies_sessions_without_any_credential_exchange(monkeypatch) -> None:
    """Only the public key set is fetched: no client secret, no token for the server itself."""
    from aion.api.http import aion_jwt_manager
    from tests.unit.support.tokens import AION_KEY, ControlPlane

    _deployment(monkeypatch, None)
    _settings(monkeypatch, client_id=None)
    control_plane = ControlPlane()
    monkeypatch.setattr(mode, "JwksKeySource", lambda url: control_plane.key_source())
    monkeypatch.setattr(aion_jwt_manager, "get_token", Mock(side_effect=AssertionError("no exchange")))

    verifier = build_token_verifier()
    await verifier.load()
    caller = await verifier.verify(AION_KEY.session_token())

    assert caller.session_id is not None
    assert control_plane.requests == 1
