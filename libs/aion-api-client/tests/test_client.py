from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

os.environ.setdefault("AION_CLIENT_ID", "test-client")
os.environ.setdefault("AION_CLIENT_SECRET", "test-secret")

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

from aion.api.config import aion_api_settings
from aion.api.gql.client import AionGqlClient
from aion.api.gql.generated.graphql_client import JSONRPCRequestInput


def test_settings_loaded() -> None:
    """Configuration values should load from defaults."""
    assert aion_api_settings.keepalive == 60


@pytest.mark.asyncio
async def test_chat_completion_stream_calls_gql(monkeypatch) -> None:
    async def mock_chat_completion_stream(*, model: str, messages: list, stream: bool):
        assert model == "test-model"
        assert messages == []
        assert stream is True
        yield {"done": True}

    from types import SimpleNamespace

    client = AionGqlClient()
    mock_client = SimpleNamespace()
    mock_client.chat_completion_stream = mock_chat_completion_stream
    client.client = mock_client
    client._is_initialized = True

    results = []
    async for chunk in client.chat_completion_stream("test-model", [], True):
        results.append(chunk)

    assert results == [{"done": True}]


@pytest.mark.asyncio
async def test_a2a_stream_calls_gql(monkeypatch) -> None:
    request = JSONRPCRequestInput(jsonrpc="2.0", method="testMethod", params=None, id=None)

    async def mock_a_2_a_stream(*, request: JSONRPCRequestInput, distribution_id: str):
        assert request == request_model
        assert distribution_id == "dist1"
        yield {"result": 1}

    from types import SimpleNamespace

    request_model = request
    client = AionGqlClient()
    mock_client = SimpleNamespace()
    mock_client.a_2_a_stream = mock_a_2_a_stream
    client.client = mock_client
    client._is_initialized = True

    chunks = []
    async for chunk in client.a2a_stream(request_model, "dist1"):
        chunks.append(chunk)

    assert chunks == [{"result": 1}]
