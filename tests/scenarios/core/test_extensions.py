"""End-to-end extension payload delivery to the agent.

The server's extension pipeline parses extension URIs from the request,
activates the ones it knows (distribution, messaging, etc.), and hands
the result to the agent through ``AionRuntimeContext.extensions``.

These scenarios pin down what the agent sees: which extensions are active,
which are unknown, and whether the distribution context is present — from
the outside, through the ``ext`` command.
"""

from __future__ import annotations

import pytest

from tests.scenarios.commands import ExtResponse
from tests.scenarios.harness import (
    DISTRIBUTION_EXTENSION_URI,
    Ev,
    ScenarioClient,
    distribution_metadata,
    final_task,
    reply_texts,
)

pytestmark = pytest.mark.extensions

UNKNOWN_EXTENSION_URI = "https://example.invalid/extensions/unknown/1.0.0"


def ext_response(events: list[Ev]) -> ExtResponse:
    """The structured answer the ``ext`` command sent back."""
    return ExtResponse.model_validate_json(reply_texts(events)[-1])


@pytest.mark.command("ext")
async def test_distribution_extension_is_active(client: ScenarioClient) -> None:
    """Requesting the distribution extension makes it active inside the agent."""
    events = await client.send(
        "ext",
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI],
    )

    response = ext_response(events)
    assert DISTRIBUTION_EXTENSION_URI in response.active
    assert response.has_distribution is True
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("ext")
async def test_no_extensions_requested(client: ScenarioClient) -> None:
    """Without any extensions on the request the agent sees none active."""
    events = await client.send("ext")

    response = ext_response(events)
    assert response.active == []
    assert response.has_distribution is False


@pytest.mark.command("ext")
async def test_unknown_extension_is_reported(client: ScenarioClient) -> None:
    """An extension the server does not recognize lands in ``unknown``."""
    events = await client.send(
        "ext",
        extensions=[UNKNOWN_EXTENSION_URI],
    )

    response = ext_response(events)
    assert UNKNOWN_EXTENSION_URI in response.unknown
    assert UNKNOWN_EXTENSION_URI not in response.active


@pytest.mark.command("ext")
async def test_distribution_present_alongside_unknown(client: ScenarioClient) -> None:
    """Both a known and an unknown extension can arrive on the same request."""
    events = await client.send(
        "ext",
        metadata=distribution_metadata(),
        extensions=[DISTRIBUTION_EXTENSION_URI, UNKNOWN_EXTENSION_URI],
    )

    response = ext_response(events)
    assert DISTRIBUTION_EXTENSION_URI in response.active
    assert UNKNOWN_EXTENSION_URI in response.unknown
    assert response.has_distribution is True
