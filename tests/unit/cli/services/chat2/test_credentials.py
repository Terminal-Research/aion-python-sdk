"""Tests for the Python chat credential helper."""

from __future__ import annotations

import io
import json
import pytest
from types import SimpleNamespace

from aion.cli.services.chat import credentials


def test_credential_helper_gets_refresh_token(monkeypatch) -> None:
    """Ensure get reads from the Python-specific credential namespace."""
    calls: list[tuple[str, str]] = []

    def get_password(service: str, account: str) -> str:
        calls.append((service, account))
        return "stored-refresh-token"

    monkeypatch.setattr(
        credentials,
        "_load_keyring",
        lambda: SimpleNamespace(get_password=get_password),
    )

    request = io.StringIO(json.dumps({"action": "get", "environmentId": "development"}))
    response = credentials._handle_request(credentials._read_request(request))

    assert response == {"refreshToken": "stored-refresh-token"}
    assert calls == [("aion-chat-python", "development:user")]


def test_credential_helper_sets_refresh_token(monkeypatch) -> None:
    """Ensure set writes to the Python-specific credential namespace."""
    calls: list[tuple[str, str, str]] = []

    def set_password(service: str, account: str, password: str) -> None:
        calls.append((service, account, password))

    monkeypatch.setattr(
        credentials,
        "_load_keyring",
        lambda: SimpleNamespace(set_password=set_password),
    )

    request = io.StringIO(
        json.dumps(
            {
                "action": "set",
                "environmentId": "staging",
                "refreshToken": "new-refresh-token",
            }
        )
    )
    response = credentials._handle_request(credentials._read_request(request))

    assert response == {}
    assert calls == [("aion-chat-python", "staging:user", "new-refresh-token")]


def test_credential_helper_uses_python_specific_service_name(monkeypatch) -> None:
    """Ensure Python-launched chat does not share npm keyring item ownership."""
    calls: list[tuple[str, str]] = []

    def get_password(service: str, account: str) -> None:
        calls.append((service, account))
        return None

    monkeypatch.setattr(
        credentials,
        "_load_keyring",
        lambda: SimpleNamespace(get_password=get_password),
    )

    request = io.StringIO(json.dumps({"action": "get", "environmentId": "development"}))
    response = credentials._handle_request(credentials._read_request(request))

    assert response == {}
    assert calls == [("aion-chat-python", "development:user")]


def test_credential_helper_rejects_invalid_request() -> None:
    """Ensure malformed helper requests fail before keyring access."""
    request = io.StringIO(json.dumps({"action": "set", "environmentId": "production"}))

    try:
        credentials._read_request(request)
    except credentials.CredentialHelperError as exc:
        assert "requires refreshToken" in str(exc)
    else:  # pragma: no cover - defensive assertion for plain pytest output.
        raise AssertionError("Expected CredentialHelperError")


def test_guest_storage_cannot_overwrite_account_credentials(monkeypatch) -> None:
    """Guest renewal uses its source-scoped account, not the login entry."""
    key = "aion-chat:anonymous-session:v1:https%3A%2F%2Fapi.test:development:local:http%3A%2F%2Flocalhost%3A8000"
    values = {("aion-chat-python", "development:user"): "account-refresh"}
    monkeypatch.setattr(credentials, "_load_keyring", lambda: SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, password: values.__setitem__((service, account), password),
    ))
    for token in ("guest", "renewed-guest"):
        request = credentials._read_request(io.StringIO(json.dumps({
            "action": "set-session", "sessionKey": key, "session": token,
        })))
        credentials._handle_request(request)
    assert credentials._handle_request({"action": "get-session", "sessionKey": key}) == {"session": "renewed-guest"}
    assert values[("aion-chat-python", "development:user")] == "account-refresh"


@pytest.mark.parametrize("key", ["development:user", "aion-chat:anonymous-session:v1:bad"])
def test_guest_helper_rejects_account_namespace(key) -> None:
    """Malformed helper input cannot target the account refresh-token slot."""
    with pytest.raises(credentials.CredentialHelperError):
        credentials._read_request(io.StringIO(json.dumps({
            "action": "set-session", "sessionKey": key, "session": "guest",
        })))


def test_helper_never_prints_secrets_in_backend_errors(monkeypatch, capsys) -> None:
    """Keychain implementations may include their inputs in exception text."""
    monkeypatch.setattr(credentials.sys, "stdin", io.StringIO('{"action":"get","environmentId":"development"}'))
    def fail(_request):
        raise RuntimeError("secret-value")
    monkeypatch.setattr(credentials, "_handle_request", fail)
    assert credentials.main() == 1
    output = capsys.readouterr()
    assert "secret-value" not in output.err
    assert output.out == ""
