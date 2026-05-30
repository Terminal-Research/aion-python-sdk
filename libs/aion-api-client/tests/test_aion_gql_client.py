"""Tests for AionGqlClient initialization and parameter validation."""

from __future__ import annotations

import os
import sys

from aion.core.settings import api_settings as aion_api_settings

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

import aion.api.gql.client as gql_client_module
from aion.api.control_plane import CapabilitySubject, PrincipalSelector
from aion.api.gql.client import AionGqlClient
from aion.api.gql.generated.graphql_client import (
    A2AJsonRpcRequestGQLInput,
    CapabilitySubjectGQLInput,
)
from aion.api.http import aion_jwt_manager


# Using the ``anyio`` pytest plugin ensures our async tests run without requiring
# the separate ``pytest-asyncio`` dependency. Limit the backend to ``asyncio``
# to avoid unnecessary trio parametrization.


@pytest.mark.anyio("asyncio")
async def test_initialize_twice_logs_warning(monkeypatch, dummy_jwt_manager) -> None:
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
    warning_messages: list[str] = []
    monkeypatch.setattr(gql_client_module.logger, "warning", warning_messages.append)

    await client.initialize()
    assert mock_build_client.calls == 1

    await client.initialize()

    assert mock_build_client.calls == 1
    assert any("already initialized" in message for message in warning_messages)


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


@pytest.mark.anyio("asyncio")
async def test_a2a_stream_accepts_typed_target_and_principal(dummy_jwt_manager) -> None:
    """A2A streaming should accept shared control-plane addressing models."""

    class FakeGeneratedClient:
        def __init__(self) -> None:
            self.calls = []

        async def a_2_a_stream(self, *, request, target, principal, **kwargs):
            self.calls.append({
                "request": request,
                "target": target,
                "principal": principal,
                "kwargs": kwargs,
            })
            yield "chunk"

    generated_client = FakeGeneratedClient()
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url,
    )
    client.client = generated_client
    client._is_initialized = True

    chunks = [
        chunk
        async for chunk in client.a2a_stream(
            A2AJsonRpcRequestGQLInput(jsonrpc="2.0", method="message/send"),
            target=CapabilitySubject.environment("env-id"),
            principal=PrincipalSelector.agent_environment("env-id"),
        )
    ]

    assert chunks == ["chunk"]
    call = generated_client.calls[0]
    assert call["target"].agent_environment_id == "env-id"
    assert call["principal"] == "aion://agent/environment/env-id"


@pytest.mark.anyio("asyncio")
async def test_a2a_stream_accepts_generated_graphql_transport_inputs(
    dummy_jwt_manager,
) -> None:
    """A2A streaming should keep GraphQL inputs at the GraphQL boundary."""

    class FakeGeneratedClient:
        def __init__(self) -> None:
            self.calls = []

        async def a_2_a_stream(self, *, request, target, principal, **kwargs):
            self.calls.append({
                "request": request,
                "target": target,
                "principal": principal,
                "kwargs": kwargs,
            })
            yield "chunk"

    generated_client = FakeGeneratedClient()
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url,
    )
    client.client = generated_client
    client._is_initialized = True

    target = CapabilitySubjectGQLInput(agent_environment_id="env-id")
    principal = "aion://agent/environment/env-id"
    chunks = [
        chunk
        async for chunk in client.a2a_stream(
            A2AJsonRpcRequestGQLInput(jsonrpc="2.0", method="message/send"),
            target=target,
            principal=principal,
        )
    ]

    assert chunks == ["chunk"]
    call = generated_client.calls[0]
    assert call["target"] is target
    assert call["principal"] == principal
