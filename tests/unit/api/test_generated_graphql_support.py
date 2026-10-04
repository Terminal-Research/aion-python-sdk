from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from graphql import parse


GENERATED_CLIENT_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "aion"
    / "api"
    / "gql"
    / "generated"
    / "graphql_client"
)


@pytest.mark.parametrize("version_id", [None, "00000000-0000-0000-0000-000000000001"])
async def test_registration_custom_operation_reaches_http(version_id):
    """AST serialization must work without the generator installed at runtime."""
    from aion.api.gql.generated.graphql_client.client import GqlClient
    from aion.api.gql.generated.graphql_client.custom_fields import AgentBehaviorFields
    from aion.api.gql.generated.graphql_client.custom_mutations import Mutation

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        operation = parse(payload["query"]).definitions[0]
        field = operation.selection_set.selections[0]
        assert operation.name.value == "RegisterVersion"
        assert field.name.value == "registerVersion"
        if version_id is None:
            assert not field.arguments
            assert not payload["variables"]
        else:
            variable_name = field.arguments[0].value.name.value
            assert payload["variables"][variable_name] == version_id
        requests.append(payload)
        return httpx.Response(200, json={"data": {"registerVersion": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = GqlClient(url="https://control-plane.test/graphql", http_client=http)
        result = await client.mutation(
            Mutation.register_version(version_id=version_id).fields(AgentBehaviorFields.id),
            operation_name="RegisterVersion",
        )

    assert result == {"registerVersion": []}
    assert len(requests) == 1


def load_generated_module(module_name: str):
    module_path = GENERATED_CLIENT_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formatted_variables_include_nested_subfield_and_fragment_arguments():
    base_operation = load_generated_module("base_operation")
    graph_ql_field = base_operation.GraphQLField

    root = graph_ql_field("root")
    child = graph_ql_field("child")
    grandchild = graph_ql_field(
        "grandchild",
        {"grandchildId": {"type": "ID!", "value": "grandchild-1"}},
    )
    child._subfields.append(grandchild)
    root._subfields.append(child)

    fragment_parent = graph_ql_field("fragmentParent")
    fragment_child = graph_ql_field("fragmentChild")
    fragment_grandchild = graph_ql_field(
        "fragmentGrandchild",
        {"fragmentId": {"type": "ID!", "value": "fragment-1"}},
    )
    fragment_child._subfields.append(fragment_grandchild)
    fragment_parent._subfields.append(fragment_child)
    root._inline_fragments["FragmentType"] = (fragment_parent,)

    root.to_ast(0)

    variables_by_name = {
        variable["name"]: variable["value"]
        for variable in root.get_formatted_variables().values()
    }
    assert variables_by_name == {
        "grandchildId": "grandchild-1",
        "fragmentId": "fragment-1",
    }


def test_graphql_error_preserves_original_payload():
    exceptions = load_generated_module("exceptions")
    graph_ql_error = exceptions.GraphQLClientGraphQLError
    raw_error = {
        "message": "No access",
        "locations": [{"line": 1, "column": 2}],
        "path": ["viewer"],
        "extensions": {"code": "FORBIDDEN"},
    }

    parsed = graph_ql_error.from_dict(raw_error)
    constructed = graph_ql_error("No access", original=raw_error)

    assert parsed.original is raw_error
    assert constructed.original is raw_error


def test_chat_completion_stream_contract_uses_optional_principal_selector():
    from aion.api.gql.generated.graphql_client import (
        CHAT_COMPLETION_STREAM_GQL,
        ChatCompletionRequestInput,
    )

    assert "agent_environment_id" not in ChatCompletionRequestInput.model_fields
    assert "$principal: String" in CHAT_COMPLETION_STREAM_GQL
    assert "chatCompletionStream(request: $request, principal: $principal)" in (
        CHAT_COMPLETION_STREAM_GQL
    )
    assert "finishReason" in CHAT_COMPLETION_STREAM_GQL
    assert "finish_reason" not in CHAT_COMPLETION_STREAM_GQL


def test_version_logs_subscription_is_generated():
    from aion.api.gql.generated.graphql_client import (
        VERSION_LOGS_GQL,
        VersionLogs,
    )
    from aion.api.gql.generated.graphql_client.client import GqlClient

    assert VersionLogs is not None
    assert hasattr(GqlClient, "version_logs")
    assert "subscription VersionLogs($startTime: OffsetDateTime!)" in (
        VERSION_LOGS_GQL
    )
    assert "versionLogs(startTime: $startTime)" in VERSION_LOGS_GQL
    assert "level" in VERSION_LOGS_GQL
    assert "level_value" in VERSION_LOGS_GQL
    assert "timestamp" in VERSION_LOGS_GQL
    assert "message" in VERSION_LOGS_GQL
    assert "properties" in VERSION_LOGS_GQL
    assert "key" in VERSION_LOGS_GQL
    assert "value" in VERSION_LOGS_GQL
    assert "versionId" not in VERSION_LOGS_GQL
    assert "organizationId" not in VERSION_LOGS_GQL
