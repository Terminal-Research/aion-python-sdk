"""End-to-end extension payload delivery to the agent.

The server's extension pipeline parses extension URIs from the request,
activates the ones it knows (distribution, messaging, etc.), and hands
the result to the agent through ``AionRuntimeContext.extensions``.

These scenarios pin down what the agent sees: which extensions are active,
which are unknown, and whether the distribution context is present — from
the outside, through the ``ext`` command. The other half of the pipeline is
what it refuses: an extension this deployment has not enabled, and one
declared without the extension it requires. Both are refused before any task
exists, which is the part that is asserted rather than assumed.

The deployment is the default one, which enables nothing beyond what the SDK
registers as active; the daemon deployment has
[test_daemon.py](test_daemon.py).
"""

from __future__ import annotations

import pytest
from a2a.utils.errors import InvalidParamsError

from tests.scenarios.commands import ExtResponse, echo_text
from tests.scenarios.extensions import SCENARIO_REQUIRES_EXTENSION_URI
from tests.scenarios.harness import (
    DAEMON_EXTENSION_URI,
    DISTRIBUTION_EXTENSION_URI,
    Ev,
    ScenarioClient,
    daemon_metadata,
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


# --------------------------------------------------------------------------
# What the pipeline refuses
# --------------------------------------------------------------------------

@pytest.mark.command("ext")
async def test_an_extension_this_deployment_has_not_enabled_is_refused(
    client: ScenarioClient,
) -> None:
    """A known extension is not an available one: the deployment decides."""
    context_id = "daemon-not-enabled"

    with pytest.raises(InvalidParamsError) as error:
        await client.send(
            "ext",
            context_id=context_id,
            metadata=daemon_metadata(),
            extensions=[DAEMON_EXTENSION_URI],
        )

    message = str(error.value)
    assert DAEMON_EXTENSION_URI in message
    assert "enabled_extensions" in message
    assert await client.tasks_in_context(context_id) == []


@pytest.mark.command("ext")
async def test_an_extension_declared_without_what_it_requires_is_refused(
    client: ScenarioClient,
) -> None:
    """Co-activation is checked before the agent runs, and the refusal names the gap."""
    context_id = "requires-not-declared"

    with pytest.raises(InvalidParamsError) as error:
        await client.send(
            "ext",
            context_id=context_id,
            extensions=[SCENARIO_REQUIRES_EXTENSION_URI],
        )

    message = str(error.value)
    assert SCENARIO_REQUIRES_EXTENSION_URI in message
    assert DAEMON_EXTENSION_URI in message
    assert await client.tasks_in_context(context_id) == []


@pytest.mark.command("echo")
async def test_a_refused_declaration_leaves_the_agent_serving(client: ScenarioClient) -> None:
    """A rejected request is one request: the next ordinary one is answered.

    In the same context, so the listing above is held to something: the
    refusal left it empty, and the request that follows leaves exactly the
    one task it opened.
    """
    context_id = "refused-then-served"

    with pytest.raises(InvalidParamsError):
        await client.send(
            "ext", context_id=context_id, extensions=[SCENARIO_REQUIRES_EXTENSION_URI]
        )

    events = await client.send("echo still here", context_id=context_id)

    assert echo_text("still here") in reply_texts(events)
    assert final_task(events).state == "COMPLETED"
    assert [task.id for task in await client.tasks_in_context(context_id)] == [
        final_task(events).task_id
    ]
