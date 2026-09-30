"""The one rule for the server's mode: where it runs decides which tokens it accepts."""

import logging
from unittest.mock import Mock

import pytest

import aion.server.auth.mode as mode
from aion.server.auth import JwksKeySource, PlatformKeySource, build_token_verifier, is_hosted

_URL = "https://api.aion.example/runtime/a2a/verification-keys"


def _settings(monkeypatch, *, has_credentials: bool, client_id="client-123") -> None:
    monkeypatch.setattr(
        mode, "api_settings", Mock(has_credentials=has_credentials, client_id=client_id, verification_keys_url=_URL)
    )


@pytest.mark.parametrize(
    ("value", "hosted"),
    [(None, False), ("", False), ("   ", False), ("deployment-1", True)],
    ids=["unset", "empty", "blank", "set"],
)
def test_a_server_is_hosted_when_the_deployment_id_is_set(monkeypatch, value, hosted) -> None:
    if value is None:
        monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    else:
        monkeypatch.setenv("DEPLOYMENT_ID", value)

    assert is_hosted() is hosted


def test_a_hosted_server_accepts_only_the_platforms_call_tokens(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_ID", "deployment-1")
    _settings(monkeypatch, has_credentials=True)

    verifier = build_token_verifier()

    assert isinstance(verifier.call_keys, PlatformKeySource)
    assert verifier.call_audience == "client-123"
    assert verifier.session_keys is None


def test_outside_the_platform_with_credentials_both_kinds_are_accepted(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    _settings(monkeypatch, has_credentials=True)

    verifier = build_token_verifier()

    assert isinstance(verifier.call_keys, PlatformKeySource)
    assert verifier.call_audience == "client-123"
    assert isinstance(verifier.session_keys, JwksKeySource)


def test_outside_the_platform_without_credentials_only_anonymous_sessions_are_accepted(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    _settings(monkeypatch, has_credentials=False, client_id=None)

    verifier = build_token_verifier()

    assert (verifier.call_keys, verifier.call_audience) == (None, None)
    assert isinstance(verifier.session_keys, JwksKeySource)


def test_a_client_id_alone_is_not_an_identity_on_the_platform(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    _settings(monkeypatch, has_credentials=False, client_id="client-123")

    verifier = build_token_verifier()

    assert (verifier.call_keys, verifier.call_audience) == (None, None)


@pytest.mark.parametrize(("deployment_id", "words"), [("deployment-1", "hosted on the Aion platform"), (None, "not hosted")])
def test_the_startup_log_names_the_mode_in_words(monkeypatch, caplog, deployment_id, words) -> None:
    if deployment_id is None:
        monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    else:
        monkeypatch.setenv("DEPLOYMENT_ID", deployment_id)
    _settings(monkeypatch, has_credentials=True)

    with caplog.at_level(logging.INFO, logger=mode.logger.name):
        build_token_verifier()

    assert words in caplog.text
    assert "DEPLOYMENT_ID" not in caplog.text


@pytest.mark.parametrize("client_id", [None, ""], ids=["unset", "empty"])
def test_a_hosted_server_without_client_id_warns_about_refused_call_tokens(monkeypatch, caplog, client_id) -> None:
    monkeypatch.setenv("DEPLOYMENT_ID", "deployment-1")
    _settings(monkeypatch, has_credentials=False, client_id=client_id)

    with caplog.at_level(logging.INFO, logger=mode.logger.name):
        build_token_verifier()

    records = [record for record in caplog.records if record.name == mode.logger.name]
    assert [(record.levelno, record.message) for record in records] == [
        (
            logging.WARNING,
            "Authentication: hosted on the Aion platform, but AION_CLIENT_ID is not set; "
            "every call token will be refused with 401",
        )
    ]


def test_a_hosted_server_with_client_id_logs_that_call_tokens_are_accepted(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DEPLOYMENT_ID", "deployment-1")
    _settings(monkeypatch, has_credentials=True)

    with caplog.at_level(logging.INFO, logger=mode.logger.name):
        build_token_verifier()

    records = [record for record in caplog.records if record.name == mode.logger.name]
    assert [(record.levelno, record.message) for record in records] == [
        (logging.INFO, "Authentication: hosted on the Aion platform; accepting the platform's call tokens")
    ]
