"""Which push notification URLs a server accepts: public ones on a hosted server, any elsewhere.

A server the Aion platform hosts (``DEPLOYMENT_ID`` set) must not post to the
deployment's own network on a client's say-so; a server run locally delivers
to the webhooks it is given, ``localhost`` among them.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from tests.integration.server.tasks.postgres_support import truncate
from tests.support.agent_app import agent_app

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

INVALID_PARAMS = -32602
DEPLOYMENT_ID = "3f2b8c1e-7d4a-4e5f-9a6b-1c2d3e4f5a6b"

LOOPBACK = "http://127.0.0.1:9/hook"
PRIVATE = "http://10.0.0.1/hook"
METADATA = "http://169.254.169.254/latest/meta-data"
PUBLIC = "http://93.184.215.14/hook"
"""A public address, written as one so that the check resolves nothing over the network."""


@pytest_asyncio.fixture(params=["memory", "postgres"], loop_scope="module")
async def hosted(request, _database, monkeypatch):
    """The application as the platform hosts it, on either store."""
    if request.param == "postgres":
        if _database is None:
            pytest.skip("POSTGRES_TEST_URL is not set")
        await truncate()
    monkeypatch.setenv("DEPLOYMENT_ID", DEPLOYMENT_ID)
    async with agent_app(_database if request.param == "postgres" else None) as app:
        yield app


async def _create_config(app, url: str) -> dict:
    sent = await app.send("hello")
    task_id = sent["result"]["task"]["id"]
    return await app.rpc("CreateTaskPushNotificationConfig", {"taskId": task_id, "url": url})


@pytest.mark.parametrize("url", [LOOPBACK, PRIVATE, METADATA])
async def test_a_hosted_server_refuses_a_config_for_a_non_public_address(hosted, url) -> None:
    response = await _create_config(hosted, url)

    assert response["error"]["code"] == INVALID_PARAMS


async def test_a_hosted_server_refuses_an_inline_config_for_a_non_public_address(hosted) -> None:
    message = {
        "messageId": uuid.uuid4().hex,
        "contextId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": "hello"}],
    }
    response = await hosted.rpc(
        "SendMessage",
        {"message": message, "configuration": {"taskPushNotificationConfig": {"url": PRIVATE}}},
    )

    assert response["error"]["code"] == INVALID_PARAMS


async def test_a_hosted_server_accepts_a_config_for_a_public_address(hosted) -> None:
    response = await _create_config(hosted, PUBLIC)

    assert response["result"]["url"] == PUBLIC


async def test_a_server_that_is_not_hosted_accepts_a_loopback_address(served) -> None:
    response = await _create_config(served, LOOPBACK)

    assert response["result"]["url"] == LOOPBACK
