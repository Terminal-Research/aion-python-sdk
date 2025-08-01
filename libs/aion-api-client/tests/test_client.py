"""Tests for the Aion API client."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import logging

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

from aion.api.config import settings, aion_api_settings
from aion.api.gql import GqlClient, AionGqlApiClient


def test_settings_loaded() -> None:
    assert settings.debug is True
    assert aion_api_settings.keepalive == 60


def test_missing_env_logs_error(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR)
    monkeypatch.delenv("AION_CLIENT_ID", raising=False)
    monkeypatch.delenv("AION_SECRET", raising=False)
    GqlClient()
    assert any(
        "AION_CLIENT_ID and AION_SECRET" in message for message in caplog.messages
    )


@pytest.mark.asyncio
async def test_chat_completions_calls_gql(monkeypatch) -> None:
    async def mock_subscribe(query: str, variables: dict | None = None):
        assert "chatCompletionStream" in query
        assert variables == {
            "request": {"model": "test-model", "messages": [], "stream": True}
        }
        yield {"chatCompletionStream": {"done": True}}

    client = AionGqlApiClient(gql_client=GqlClient())
    monkeypatch.setattr(client._gql, "subscribe", mock_subscribe)

    results = []
    async for chunk in client.chat_completions("test-model", []):
        results.append(chunk)

    assert results == [{"done": True}]

