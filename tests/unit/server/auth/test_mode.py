"""The one rule for the server's mode: platform credentials mean tokens are required."""

from unittest.mock import Mock

import pytest

import aion.server.auth.mode as mode
from aion.server.auth import PlatformKeySource, authentication_required, build_token_verifier


def test_with_credentials_tokens_are_required_and_verified_with_the_platforms_key(monkeypatch) -> None:
    monkeypatch.setattr(mode, "api_settings", Mock(has_credentials=True))

    assert authentication_required() is True
    assert isinstance(build_token_verifier().key_source, PlatformKeySource)


def test_without_credentials_the_server_runs_in_local_mode(monkeypatch) -> None:
    monkeypatch.setattr(mode, "api_settings", Mock(has_credentials=False))

    assert authentication_required() is False
    assert build_token_verifier() is None
