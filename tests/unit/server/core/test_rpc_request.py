"""Preparing an A2A JSON-RPC request for the server's middlewares.

``prepare_rpc_request`` reads the method, the id and ``params.metadata`` from
the body, validates the distribution payload in it, and keeps the result for
the rest of the request. A malformed payload is reported without its values.
"""

import json
import logging
from typing import Any

import pytest
from a2a.utils import DEFAULT_RPC_URL
from starlette.requests import Request

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1
from aion.server.core.middlewares._rpc_request import (
    InvalidExtensionPayloadError,
    PreparedRpcRequest,
    prepare_rpc_request,
)

from tests.unit.support.distribution import distribution_metadata

SECRET = "s3cr3t-value"


# !! Test Data Factories !!
def create_distribution_payload(identity_overrides=None):
    """Factory function to build a distribution extension payload as sent by the control plane.

    Field names and shape mirror a payload captured from staging, so this factory
    doubles as the contract check against the published extension spec at
    https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0.
    """
    identity = {
        "kind": "principal",
        "id": "identity-1",
        "identityNetwork": "Aion",
        "identityKind": "Personal",
        "representedUserId": "user-1",
        "organizationId": "org-1",
        "displayName": "Artem Sosnytskyi",
        "userName": "sosnytskyi_artem_dev",
        "agentType": "Personal",
    }
    identity.update(identity_overrides or {})

    return {
        "callerId": "aion:v1:AionUser:MTExMTExMTEtMTExMS00MTExLTgxMTEtMTExMTExMTExMTEx",
        "distribution": {
            "id": "dist-1",
            "endpointType": "A2A",
            "url": "https://example.com/distributions/dist-1/a2a/.well-known/agent-card.json",
            "componentAgentCardUrl": "https://example.com/environments/env-1/a2a/.well-known/agent-card.json",
            "identities": [identity],
        },
        "behavior": {
            "id": "beh-1",
            "behaviorKey": "testGraph",
            "versionId": "v1",
        },
        "environment": {
            "id": "env-1",
            "name": "Development",
            "projectId": "proj-1",
            "deploymentId": "dep-1",
            "configurationVariables": {},
        },
    }


def _request(body: Any) -> Request:
    """A POST to the JSON-RPC endpoint carrying ``body``, encoded as JSON unless it is bytes."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": DEFAULT_RPC_URL, "headers": [], "query_string": b""}
    return Request(scope, receive)


def _call(metadata: Any = None, *, request_id: Any = "req-1") -> dict[str, Any]:
    params: dict[str, Any] = {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}}
    if metadata is not None:
        params["metadata"] = metadata
    return {"jsonrpc": "2.0", "id": request_id, "method": "SendMessage", "params": params}


async def _prepare(payload: Any, *, request_id: Any = "req-1") -> PreparedRpcRequest:
    return await prepare_rpc_request(_request(_call({DISTRIBUTION_EXTENSION_URI_V1: payload}, request_id=request_id)))


def _malformed(**configuration_variables: Any) -> dict[str, Any]:
    """A payload without the required ``environment.projectId``."""
    payload = distribution_metadata()[DISTRIBUTION_EXTENSION_URI_V1]
    del payload["environment"]["projectId"]
    payload["environment"]["configurationVariables"] = configuration_variables
    return payload


class TestDistributionPayloadContract:
    async def test_parses_payload_with_identity_network(self, caplog):
        """A fully populated identity parses and logs no warning."""
        with caplog.at_level(logging.WARNING):
            prepared = await _prepare(create_distribution_payload())

        assert prepared.distribution.distribution.identities[0].identity_network == "Aion"
        assert caplog.records == []

    @pytest.mark.parametrize(
        "field",
        ["identityNetwork", "identityKind", "organizationId"],
    )
    async def test_rejects_identity_missing_required_field(self, field):
        """The spec marks these identity fields required, so omitting one is an error."""
        payload = create_distribution_payload()
        del payload["distribution"]["identities"][0][field]

        with pytest.raises(InvalidExtensionPayloadError):
            await _prepare(payload)

    @pytest.mark.parametrize(
        "field",
        ["projectId", "deploymentId", "configurationVariables"],
    )
    async def test_rejects_environment_missing_required_field(self, field):
        """The spec marks these environment fields required, so omitting one is an error."""
        payload = create_distribution_payload()
        del payload["environment"][field]

        with pytest.raises(InvalidExtensionPayloadError):
            await _prepare(payload)

    async def test_rejects_an_empty_distribution_id(self):
        """A distribution routing identifier must not be empty."""
        payload = create_distribution_payload()
        payload["distribution"]["id"] = ""

        with pytest.raises(InvalidExtensionPayloadError):
            await _prepare(payload)

    async def test_binds_every_field_the_control_plane_sends(self):
        """Every field in a real control-plane payload lands on the model.

        Guards against the failure mode where a renamed wire field is silently
        dropped by extra="ignore" and the attribute just reads as None.
        """
        extension = (await _prepare(create_distribution_payload())).distribution

        assert extension.caller_id == create_distribution_payload()["callerId"]
        identity = extension.distribution.identities[0]
        assert identity.identity_network == "Aion"
        assert identity.identity_kind == "Personal"
        assert identity.agent_type == "Personal"
        assert identity.represented_user_id == "user-1"
        assert extension.distribution.component_agent_card_url.endswith(
            "/environments/env-1/a2a/.well-known/agent-card.json"
        )
        assert extension.environment.project_id == "proj-1"

    async def test_metadata_without_the_extension_carries_no_distribution(self):
        prepared = await prepare_rpc_request(_request(_call({"other": {"k": "v"}})))

        assert prepared.distribution is None
        assert prepared.metadata == {"other": {"k": "v"}}

    async def test_a_null_payload_carries_no_distribution(self):
        """JSON ``null`` under the extension URI is no payload, not a malformed one."""
        prepared = await _prepare(None)

        assert prepared.distribution is None


class TestTheRequestAsItCame:
    @pytest.mark.parametrize("request_id", ["req-1", 7, 0])
    async def test_a_string_or_integer_id_is_kept(self, request_id):
        prepared = await prepare_rpc_request(_request(_call(request_id=request_id)))

        assert prepared.request_id == request_id
        assert prepared.method == "SendMessage"

    @pytest.mark.parametrize("request_id", [{"a": 1}, [1, 2], 1.5, None], ids=["object", "array", "float", "null"])
    async def test_any_other_id_is_answered_as_null(self, request_id):
        """The rule a2a-sdk's dispatcher applies to the id it answers to."""
        prepared = await prepare_rpc_request(_request(_call(request_id=request_id)))

        assert prepared.request_id is None

    async def test_the_body_keeps_its_id(self):
        request = _request(_call(request_id={"a": 1}))

        await prepare_rpc_request(request)

        assert (await request.json())["id"] == {"a": 1}

    @pytest.mark.parametrize(("request_id", "answered"), [({"a": 1}, None), ("req-1", "req-1"), (7, 7)])
    async def test_a_refusal_answers_to_the_same_id(self, request_id, answered):
        with pytest.raises(InvalidExtensionPayloadError) as refusal:
            await _prepare(_malformed(), request_id=request_id)

        assert refusal.value.request_id == answered

    @pytest.mark.parametrize("body", [b"not json", ["a", "batch"], "text"], ids=["not-json", "array", "string"])
    async def test_a_body_that_is_no_json_rpc_object_prepares_empty(self, body):
        """Answering it is the dispatcher's job; the middlewares let it through."""
        prepared = await prepare_rpc_request(_request(body))

        assert prepared == PreparedRpcRequest()

    async def test_a_second_ask_reuses_the_first_preparation(self):
        request = _request(_call(distribution_metadata()))

        first = await prepare_rpc_request(request)
        second = await prepare_rpc_request(request)

        assert second is first
        assert first.distribution.distribution.id == "dist-1"


class TestNothingSecretLeaks:
    async def test_a_refusal_describes_fields_never_values(self):
        """Neither a sibling value nor the rejected value itself reaches the description."""
        payload = _malformed(API_KEY=SECRET)
        payload["distribution"]["url"] = {"token": SECRET}

        with pytest.raises(InvalidExtensionPayloadError) as refusal:
            await _prepare(payload)

        detail = refusal.value.detail
        assert "environment.projectId: Field required [missing]" in detail
        assert "distribution.url" in detail
        assert SECRET not in detail
        assert SECRET not in str(refusal.value)
        assert refusal.value.__cause__ is None and refusal.value.__suppress_context__

    async def test_the_prepared_request_keeps_the_payload_out_of_its_repr(self):
        prepared = await prepare_rpc_request(
            _request(_call(distribution_metadata(configuration_variables={"API_KEY": SECRET})))
        )

        assert SECRET not in repr(prepared)
        assert prepared.distribution.environment.configuration_variables == {"API_KEY": SECRET}
