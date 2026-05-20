from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

os.environ.setdefault("AION_CLIENT_ID", "test-client")
os.environ.setdefault("AION_CLIENT_SECRET", "test-secret")

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jwt")
pytest.importorskip("gql")

from aion.core.settings import api_settings as aion_api_settings
from aion.api.gql.client import AionGqlClient
from aion.api.gql.generated.graphql_client import (
    A2AJsonRpcRequestGQLInput,
    CapabilitySubjectGQLInput,
    ChatCompletionRequestInput,
    PrincipalSelectorGQLInput,
)
from aion.api.gql.generated.graphql_client.custom_fields import AgentBehaviorFields
from aion.api.gql.generated.graphql_client.custom_mutations import Mutation


class CapturingMutationClient:
    """Test double that records generated custom mutation requests."""

    def __init__(self) -> None:
        """Initialize the capture container."""
        self.calls: list[dict] = []

    async def mutation(self, field, operation_name: str):
        """Capture the mutation field and operation name."""
        field.to_ast(0)
        self.calls.append(
            {
                "operation_name": operation_name,
                "variables": field.get_formatted_variables(),
            }
        )
        return {"registerVersion": []}


def test_settings_loaded() -> None:
    """Configuration values should load from defaults."""
    assert aion_api_settings.api_keep_alive == 60


def test_chat_completion_request_uses_principal_for_agent_environment() -> None:
    """Chat completion requests should no longer carry agent environment selectors."""
    assert "agent_environment_id" not in ChatCompletionRequestInput.model_fields
    assert "agent_environment_id" in PrincipalSelectorGQLInput.model_fields

    principal = PrincipalSelectorGQLInput(agent_environment_id="agent-env-1")

    assert principal.model_dump(by_alias=True, exclude_none=True) == {
        "agentEnvironmentId": "agent-env-1"
    }


# Use ``anyio``'s pytest plugin to execute async tests using the ``asyncio`` backend
# only. This avoids unnecessary parametrization for other event loops.


@pytest.mark.anyio("asyncio")
async def test_chat_completion_stream_calls_gql(monkeypatch) -> None:
    """Test that chat_completion_stream properly calls the underlying GraphQL client."""
    expected_principal = PrincipalSelectorGQLInput(agent_environment_id="agent-env-1")

    async def mock_chat_completion_stream(
        *, request: ChatCompletionRequestInput, principal=None
    ):
        assert request == ChatCompletionRequestInput(
            model="test-model", messages=[], stream=True
        )
        assert principal == expected_principal
        yield {"done": True}

    from types import SimpleNamespace

    client = AionGqlClient()
    mock_client = SimpleNamespace()
    mock_client.chat_completion_stream = mock_chat_completion_stream
    client.client = mock_client
    client._is_initialized = True

    results = []
    async for chunk in client.chat_completion_stream(
        "test-model", [], True, principal=expected_principal
    ):
        results.append(chunk)

    assert results == [{"done": True}]


@pytest.mark.anyio("asyncio")
async def test_a2a_stream_calls_gql(monkeypatch) -> None:
    """Test that a2a_stream properly calls the underlying GraphQL client."""
    request = A2AJsonRpcRequestGQLInput(
        jsonrpc="2.0", method="testMethod", params=None, id=None
    )

    async def mock_a_2_a_stream(
        *,
        request: A2AJsonRpcRequestGQLInput,
        target: CapabilitySubjectGQLInput,
        principal=None,
    ):
        assert request == request_model
        assert target == CapabilitySubjectGQLInput(distribution_id="dist1")
        assert principal is None
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


@pytest.mark.anyio("asyncio")
async def test_version_logs_calls_gql(monkeypatch) -> None:
    """version_logs should delegate to the generated websocket subscription."""
    expected_start_time = "2026-05-14T15:00:00Z"

    async def mock_version_logs(*, start_time: str):
        assert start_time == expected_start_time
        yield {"versionLogs": {"message": "ready"}}

    from types import SimpleNamespace

    client = AionGqlClient()
    mock_client = SimpleNamespace()
    mock_client.version_logs = mock_version_logs
    client.client = mock_client
    client._is_initialized = True

    chunks = []
    async for chunk in client.version_logs(start_time=expected_start_time):
        chunks.append(chunk)

    assert chunks == [{"versionLogs": {"message": "ready"}}]


def test_register_version_custom_mutation_arguments_exclude_manifest() -> None:
    """Generated registerVersion custom mutation should expose only versionId."""
    field = Mutation.register_version(version_id="version-123").fields(
        AgentBehaviorFields.id
    )

    field.to_ast(0)
    argument_names = {
        variable["name"] for variable in field.get_formatted_variables().values()
    }

    assert argument_names == {"versionId"}


@pytest.mark.anyio("asyncio")
async def test_register_version_sends_only_version_id_variable() -> None:
    """register_version should send only the optional versionId variable."""
    client = AionGqlClient()
    gql_client = CapturingMutationClient()
    client.client = gql_client
    client._is_initialized = True

    result = await client.register_version(version_id="version-123")

    assert result == {"registerVersion": []}
    assert len(gql_client.calls) == 1
    call = gql_client.calls[0]
    assert call["operation_name"] == "RegisterVersion"
    assert {
        variable["name"]: variable["value"] for variable in call["variables"].values()
    } == {"versionId": "version-123"}


@pytest.mark.anyio("asyncio")
async def test_register_version_allows_version_authenticated_call() -> None:
    """register_version should support callers that omit version_id."""
    client = AionGqlClient()
    gql_client = CapturingMutationClient()
    client.client = gql_client
    client._is_initialized = True

    await client.register_version()

    assert gql_client.calls[0]["operation_name"] == "RegisterVersion"
    assert gql_client.calls[0]["variables"] == {}


# INITIALIZATION REQUIREMENT TESTS


@pytest.mark.anyio("asyncio")
async def test_chat_completion_stream_requires_initialize(dummy_jwt_manager) -> None:
    """Calling chat_completion_stream before initialize should raise RuntimeError."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url,
    )
    stream = client.chat_completion_stream(model="test-model", messages=[], stream=True)
    with pytest.raises(
        RuntimeError,
        match="AionGqlClient is not initialized before executing operations",
    ):
        await anext(stream)


@pytest.mark.anyio("asyncio")
async def test_a2a_stream_requires_initialize(dummy_jwt_manager) -> None:
    """Calling a2a_stream before initialize should raise RuntimeError."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url,
    )

    request = A2AJsonRpcRequestGQLInput(jsonrpc="2.0", method="test", id="test-id")

    stream = client.a2a_stream(request=request, distribution_id="test-distribution")

    with pytest.raises(
        RuntimeError,
        match="AionGqlClient is not initialized before executing operations",
    ):
        await anext(stream)


@pytest.mark.anyio("asyncio")
async def test_version_logs_requires_initialize(dummy_jwt_manager) -> None:
    """Calling version_logs before initialize should raise RuntimeError."""
    client = AionGqlClient(
        client_id="test-id",
        client_secret="test-secret",
        jwt_manager=dummy_jwt_manager,
        gql_url=aion_api_settings.gql_url,
        ws_url=aion_api_settings.ws_gql_url,
    )

    stream = client.version_logs(start_time="2026-05-14T15:00:00Z")

    with pytest.raises(
        RuntimeError,
        match="AionGqlClient is not initialized before executing operations",
    ):
        await anext(stream)
