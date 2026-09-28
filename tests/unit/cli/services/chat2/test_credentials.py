"""Tests for the Python chat credential helper."""

from __future__ import annotations

import io
import json
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
