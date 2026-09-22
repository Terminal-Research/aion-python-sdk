"""An Aion event, from the wire to the agent.

A distribution does not send prose: it sends a CloudEvents envelope on the
message and a schema-tagged data part carrying the payload. The server reads
the pair, validates the payload against the schema the part names, and hands
the agent a typed ``Event``.

These scenarios send each event kind the core vocabularies define and ask the
agent what it received. The kind, the producer's event id and every field of
the payload are the contract, and they hold on every framework. Which router
handler the event reached is one adapter's authoring surface, so the scenario
about that carries ``@pytest.mark.event_router`` and skips where
``frameworks.NO_EVENT_ROUTER`` says an adapter has none.
"""

from __future__ import annotations

import pytest
from a2a.utils.errors import InvalidParamsError

from tests.scenarios.commands import EventResponse
from tests.scenarios.extensions import (
    SCENARIO_EVENT_EXTENSION_URI,
    SCENARIO_EVENT_PAYLOAD_SCHEMA,
    SCENARIO_EVENT_TYPE,
)
from tests.scenarios.harness import (
    MESSAGING_EXTENSION_URI,
    DataAttachment,
    Ev,
    EventEnvelope,
    ScenarioClient,
    card_action_event,
    command_event,
    event_envelope,
    final_task,
    message_event,
    reaction_event,
    reply_texts,
)

pytestmark = pytest.mark.extensions


def unclaimed_event() -> EventEnvelope:
    """An event of a kind no handler is registered for, so the fallback takes it."""
    return event_envelope(
        kind=SCENARIO_EVENT_TYPE,
        extension_uri=SCENARIO_EVENT_EXTENSION_URI,
        schema=SCENARIO_EVENT_PAYLOAD_SCHEMA,
        payload={"label": "no handler of its own"},
    )


EVENTS = {
    "message": (message_event, "on_message"),
    "reaction": (reaction_event, "on_reaction"),
    "command": (command_event, "on_command"),
    "card-action": (card_action_event, "on_card_action"),
    "unclaimed": (unclaimed_event, "on_event"),
}
"""Every event kind a scenario sends, and the handler it selects.

The first four are the core vocabularies, each with a handler of its own. The
last is the suite's own kind, which exists so the router's fallback is reached
by an event rather than by an argument - see ``tests/scenarios/extensions.py``.
"""


def event_response(events: list[Ev]) -> EventResponse:
    """The structured answer the ``event`` command sent back."""
    return EventResponse.model_validate_json(reply_texts(events)[-1])


async def send_event(client: ScenarioClient, envelope: EventEnvelope) -> list[Ev]:
    """Drive the ``event`` command with one event envelope on the request."""
    return await client.send(
        "event",
        message_metadata=envelope.message_metadata,
        data=[envelope.data],
        extensions=[envelope.extension_uri],
    )


@pytest.mark.command("event")
@pytest.mark.parametrize("kind", sorted(EVENTS))
async def test_an_event_reaches_the_agent_as_it_was_sent(
    client: ScenarioClient, kind: str
) -> None:
    """The kind, the event id and every payload field survive the trip."""
    build, _ = EVENTS[kind]
    envelope = build()

    events = await send_event(client, envelope)

    response = event_response(events)
    assert response.kind == envelope.kind
    assert response.event_id == envelope.event_id
    assert response.payload == dict(envelope.payload)
    assert final_task(events).state == "COMPLETED"


@pytest.mark.command("event")
@pytest.mark.event_router
@pytest.mark.parametrize("kind", sorted(EVENTS))
async def test_the_event_kind_selects_the_router_handler(
    client: ScenarioClient, kind: str
) -> None:
    """Each kind reaches the handler registered for it, and no other."""
    build, handler = EVENTS[kind]

    events = await send_event(client, build())

    assert event_response(events).handler == handler


@pytest.mark.command("event")
async def test_a_request_with_no_event_carries_none(client: ScenarioClient) -> None:
    """A plain A2A request is not an event, and the agent is told so."""
    events = await client.send("event")

    response = event_response(events)
    assert response.kind is None
    assert response.event_id is None
    assert response.payload == {}


@pytest.mark.command("event")
async def test_a_payload_that_does_not_match_its_schema_is_refused(
    client: ScenarioClient,
) -> None:
    """A part tagged with a known schema is held to it, and the refusal says so."""
    context_id = "malformed-event-payload"
    envelope = message_event()
    broken = DataAttachment(
        data={"userId": "user-scenarios"},  # no contextId, messageId or trajectory
        metadata=envelope.data.metadata,
    )

    with pytest.raises(InvalidParamsError) as error:
        await client.send(
            "event",
            context_id=context_id,
            message_metadata=envelope.message_metadata,
            data=[broken],
            extensions=[MESSAGING_EXTENSION_URI],
        )

    assert "failed validation" in str(error.value)
    assert await client.tasks_in_context(context_id) == []
