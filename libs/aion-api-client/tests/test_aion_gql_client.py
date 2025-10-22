"""Tests for AionGqlClient initialization and parameter validation."""

from __future__ import annotations

import logging
import os
import sys

from aion.api.config import aion_api_settings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

from aion.api.gql.client import AionGqlClient
from aion.api.http import aion_jwt_manager


# Using the ``anyio`` pytest plugin ensures our async tests run without requiring
# the separate ``pytest-asyncio`` dependency. Limit the backend to ``asyncio``
# to avoid unnecessary trio parametrization.


@pytest.mark.anyio("asyncio")
async def test_initialize_twice_logs_warning(monkeypatch, caplog, dummy_jwt_manager) -> None:
    """Repeated initialize calls log a warning and do not rebuild the client."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

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
async def test_custom_jwt_manager_overrides_global(dummy_jwt_manager, monkeypatch, valid_jwt_token) -> None:
    """Providing a custom JWT manager should bypass the global instance."""

    async def fail() -> None:
        raise AssertionError("global manager should not be used")

    monkeypatch.setattr(aion_jwt_manager, "get_token", fail)

    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )
    await client.initialize()

    assert valid_jwt_token in client.client.url


@pytest.mark.anyio("asyncio")
async def test_initialize_missing_client_id(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when client_id is missing."""
    client = AionGqlClient(
        client_id=None,  # Missing client_id
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="client_id is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_empty_client_id(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when client_id is empty string."""
    client = AionGqlClient(
        client_id="",  # Empty client_id
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="client_id is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_missing_client_secret(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when client_secret is missing."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret=None,  # Missing client_secret
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="client_secret is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_empty_client_secret(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when client_secret is empty string."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="",  # Empty client_secret
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="client_secret is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_missing_jwt_manager() -> None:
    """Initialize should raise ValueError when jwt_manager is missing."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=None,  # Missing jwt_manager
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="jwt_manager is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_missing_gql_url(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when gql_url is missing."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=None,   # Missing gql_url
        ws_url=aion_api_settings.ws_gql_url
    )

    with pytest.raises(ValueError, match="gql_url is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_initialize_missing_ws_url(dummy_jwt_manager) -> None:
    """Initialize should raise ValueError when ws_url is missing."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=None # Missing ws_url
    )

    with pytest.raises(ValueError, match="ws_url is required"):
        await client.initialize()


@pytest.mark.anyio("asyncio")
async def test_successful_initialization_with_all_params(dummy_jwt_manager, monkeypatch) -> None:
    """Initialize should succeed when all required parameters are provided."""

    # Mock the _build_client method to avoid actual network calls
    async def mock_build_client():
        pass

    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url
    )

    monkeypatch.setattr(client, "_build_client", mock_build_client)

    # Should not raise any exception
    result = await client.initialize()
    assert result is client
    assert client._is_initialized is True
