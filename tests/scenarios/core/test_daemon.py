"""Daemon-scoped requests: who the platform says is asking, and about what.

The daemon extension carries an identity pair, a behavior and an environment
in request metadata. The deployment has to have turned the extension on, the
payload has to validate, and what reaches the agent has to be the record the
platform sent - not the distribution's principal, which is a different
payload with a different meaning.

The deployment here is the default one with the daemon URI in
``enabled_extensions``; the scenarios for a deployment that has not enabled it
are in [test_extensions.py](test_extensions.py).
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from a2a.utils.errors import InvalidParamsError

from tests.scenarios.commands import WhoamiResponse
from tests.scenarios.extensions import SCENARIO_REQUIRES_EXTENSION_URI
from tests.scenarios.harness import (
    DAEMON_BEHAVIOR_ID,
    DAEMON_ENVIRONMENT_ID,
    DAEMON_EXTENSION_URI,
    DAEMON_IDENTITY_ID,
    DISTRIBUTION_EXTENSION_URI,
    REQUESTER_IDENTITY_ID,
    Ev,
    ScenarioClient,
    ServeProcess,
    ServeVariant,
    daemon_metadata,
    distribution_metadata,
    final_task,
    reply_texts,
)

pytestmark = pytest.mark.daemon

DAEMON = ServeVariant(name="daemon", enabled_extensions=(DAEMON_EXTENSION_URI,))


@pytest.fixture(scope="session")
def daemon_server(server_for) -> ServeProcess:
    """A deployment that has enabled the daemon extension."""
    return server_for(DAEMON)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def daemon_client(daemon_server: ServeProcess) -> AsyncIterator[ScenarioClient]:
    """A client on the daemon deployment."""
    scenario_client = await ScenarioClient.connect(
        daemon_server.base_url, daemon_server.variant.agent_id
    )
    try:
        yield scenario_client
    finally:
        await scenario_client.close()


def whoami_response(events: list[Ev]) -> WhoamiResponse:
    """The structured answer the ``whoami`` command sent back."""
    return WhoamiResponse.model_validate_json(reply_texts(events)[-1])


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
async def test_the_daemon_payload_reaches_the_agent_whole(daemon_client: ScenarioClient) -> None:
    """Every field of the daemon payload arrives as the platform sent it."""
    events = await daemon_client.send(
        "whoami",
        metadata=daemon_metadata(),
        extensions=[DAEMON_EXTENSION_URI],
    )

    response = whoami_response(events)
    assert response.daemon_identity_id == DAEMON_IDENTITY_ID
    assert response.requester_identity_id == REQUESTER_IDENTITY_ID
    assert response.environment == DAEMON_ENVIRONMENT_ID
    assert response.behavior == DAEMON_BEHAVIOR_ID
    assert final_task(events).state == "COMPLETED"


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
async def test_a_daemon_request_without_a_requester_is_still_addressed(
    daemon_client: ScenarioClient,
) -> None:
    """The requester is optional; the daemon it is addressed to is not."""
    events = await daemon_client.send(
        "whoami",
        metadata=daemon_metadata(requester_identity_id=None),
        extensions=[DAEMON_EXTENSION_URI],
    )

    response = whoami_response(events)
    assert response.daemon_identity_id == DAEMON_IDENTITY_ID
    assert response.requester_identity_id is None


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
async def test_the_daemon_identity_is_not_the_distribution_principal(
    daemon_client: ScenarioClient,
) -> None:
    """Two payloads on one request stay two payloads: the daemon answers for itself."""
    metadata = {**daemon_metadata(), **distribution_metadata()}

    events = await daemon_client.send(
        "whoami",
        metadata=metadata,
        extensions=[DAEMON_EXTENSION_URI, DISTRIBUTION_EXTENSION_URI],
    )

    response = whoami_response(events)
    assert response.daemon_identity_id == DAEMON_IDENTITY_ID
    assert response.environment == DAEMON_ENVIRONMENT_ID
    # The distribution on the same request names another environment; reading
    # one payload's field off the other is the mistake this pins down.
    assert response.environment != "environment-scenarios"


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
async def test_a_declared_daemon_extension_without_a_payload_is_refused(
    daemon_client: ScenarioClient,
) -> None:
    """Declaring the extension is a promise to carry it; an empty request breaks it."""
    context_id = "daemon-declaration-without-payload"

    with pytest.raises(InvalidParamsError) as error:
        await daemon_client.send(
            "whoami", context_id=context_id, extensions=[DAEMON_EXTENSION_URI]
        )

    message = str(error.value)
    assert DAEMON_EXTENSION_URI in message
    assert "missing" in message
    assert await daemon_client.tasks_in_context(context_id) == []


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
async def test_a_malformed_daemon_payload_is_refused_by_field(
    daemon_client: ScenarioClient,
) -> None:
    """A payload that does not validate names the field, and opens no task."""
    context_id = "malformed-daemon-payload"
    broken = {DAEMON_EXTENSION_URI: {"daemonIdentity": {"kind": "daemon"}}}

    with pytest.raises(InvalidParamsError) as error:
        await daemon_client.send(
            "whoami",
            context_id=context_id,
            metadata=broken,
            extensions=[DAEMON_EXTENSION_URI],
        )

    message = str(error.value)
    assert DAEMON_EXTENSION_URI in message
    assert "daemonIdentity" in message or "daemon_identity" in message
    assert await daemon_client.tasks_in_context(context_id) == []


@pytest.mark.variant("daemon")
@pytest.mark.command("whoami")
@pytest.mark.extensions
async def test_an_extension_gets_what_it_requires_when_both_are_declared(
    daemon_client: ScenarioClient,
) -> None:
    """Co-activation satisfied: the request is served, not refused."""
    events = await daemon_client.send(
        "whoami",
        metadata=daemon_metadata(),
        extensions=[SCENARIO_REQUIRES_EXTENSION_URI, DAEMON_EXTENSION_URI],
    )

    assert whoami_response(events).daemon_identity_id == DAEMON_IDENTITY_ID
    assert final_task(events).state == "COMPLETED"
