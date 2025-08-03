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

from aion.api.gql.client import AionGqlClient


@pytest.mark.asyncio
async def test_chat_completion_stream_requires_initialize() -> None:
    """Calling chat_completion_stream before initialize should raise RuntimeError."""
    client = AionGqlClient()
    with pytest.raises(RuntimeError):
        await client.chat_completion_stream(model="test-model", messages=[], stream=True)


@pytest.mark.asyncio
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
