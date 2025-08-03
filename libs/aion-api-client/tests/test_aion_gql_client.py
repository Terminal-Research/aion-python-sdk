"""Tests for the Aion GraphQL client."""

from __future__ import annotations

import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

os.environ.setdefault("AION_CLIENT_ID", "test-id")
os.environ.setdefault("AION_CLIENT_SECRET", "test-secret")

import jwt
from datetime import datetime, timedelta, timezone

from aion.api.gql.client import AionGqlClient
from aion.api.http.jwt_manager import AionJWTManager, Token
from aion.api.http import aion_jwt_manager


# Using the ``anyio`` pytest plugin ensures our async tests run without requiring
# the separate ``pytest-asyncio`` dependency.  Limit the backend to ``asyncio``
# to avoid unnecessary trio parametrization.
@pytest.mark.anyio("asyncio")
async def test_chat_completion_stream_requires_initialize() -> None:
    """Calling chat_completion_stream before initialize should raise RuntimeError."""
    client = AionGqlClient()
    stream = client.chat_completion_stream(
        model="test-model", messages=[], stream=True
    )
    with pytest.raises(RuntimeError):
        await anext(stream)


@pytest.mark.anyio("asyncio")
async def test_initialize_twice_logs_warning(monkeypatch, caplog) -> None:
    """Repeated initialize calls log a warning and do not rebuild the client."""
    client = AionGqlClient()

    async def mock_build_client() -> None:
        mock_build_client.calls += 1

    mock_build_client.calls = 0
    monkeypatch.setattr(client, "_build_client", mock_build_client)

    await client.initialize()
    assert mock_build_client.calls == 1

    caplog.set_level(logging.WARNING)
    await client.initialize()

    assert mock_build_client.calls == 1
    assert any("already initialized" in message for message in caplog.messages)


@pytest.mark.anyio("asyncio")
async def test_custom_jwt_manager_overrides_global(monkeypatch) -> None:
    """Providing a custom JWT manager should bypass the global instance."""

    exp = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    token_value = jwt.encode({"exp": int(exp.timestamp())}, "secret", algorithm="HS256")

    class DummyManager(AionJWTManager):
        def __init__(self, token: str) -> None:
            super().__init__()
            self._token = Token.from_jwt(token)

        async def _refresh_token(self) -> None:  # pragma: no cover - not used
            return None

    dummy = DummyManager(token_value)

    async def fail() -> None:
        raise AssertionError("global manager should not be used")

    monkeypatch.setattr(aion_jwt_manager, "get_token", fail)

    client = AionGqlClient(jwt_manager=dummy)
    await client.initialize()

    assert token_value in client.client.url
