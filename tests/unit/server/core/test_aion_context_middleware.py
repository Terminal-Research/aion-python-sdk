"""``AionContextMiddleware`` on its own, without ``AionAuthMiddleware`` in front.

It prepares the request through ``prepare_rpc_request``, refuses a malformed
payload, and names nobody: the caller of a request it lets through is the
anonymous one.
"""

import logging
from typing import Any

import pytest
from starlette.middleware import Middleware

from aion.core.constants import DISTRIBUTION_EXTENSION_URI_V1, TRACEABILITY_EXTENSION_URI_V1
from aion.server.core.middlewares import AionContextMiddleware

from tests.unit.support.distribution import distribution_metadata
from tests.unit.support.request_path import Probe, send_message

INVALID_REQUEST = -32600
SECRET = "s3cr3t-value"


def _without_project_holding_a_secret() -> dict[str, Any]:
    metadata = distribution_metadata("dist-1", configuration_variables={"API_KEY": SECRET})
    del metadata[DISTRIBUTION_EXTENSION_URI_V1]["environment"]["projectId"]
    return metadata


def _traceability_of_the_wrong_version() -> dict[str, Any]:
    """The rejected value itself is the secret: the answer must not quote it."""
    return {TRACEABILITY_EXTENSION_URI_V1: {"version": SECRET}}


async def test_it_reads_the_distribution_into_the_execution_scope() -> None:
    probe = Probe()
    async with probe.client(Middleware(AionContextMiddleware)) as client:
        answer = (await send_message(client, distribution_metadata("dist-1"))).json()

    assert answer["scope_distribution"] == "dist-1"
    assert (answer["owner"], answer["authenticated"], answer["scopes"]) == ("", False, None)


@pytest.mark.parametrize(
    ("metadata", "problem"),
    [
        (_without_project_holding_a_secret(), "environment.projectId: Field required [missing]"),
        (_traceability_of_the_wrong_version(), "version: Input should be '1.0.0' [literal_error]"),
    ],
    ids=["distribution", "traceability"],
)
async def test_it_refuses_a_malformed_payload_without_echoing_it(metadata, problem, caplog) -> None:
    probe = Probe()
    with caplog.at_level(logging.WARNING):
        async with probe.client(Middleware(AionContextMiddleware)) as client:
            response = await send_message(client, metadata, request_id={"not": "an id"})

    assert response.status_code == 200
    answer = response.json()
    assert (answer["error"]["code"], answer["id"]) == (INVALID_REQUEST, None)
    assert problem in answer["error"]["data"]
    assert SECRET not in answer["error"]["data"]
    assert SECRET not in caplog.text
    assert probe.calls == 0
