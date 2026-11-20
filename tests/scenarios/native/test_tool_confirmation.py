"""A tool's confirmation, answered ADK's way or the agent's, through the server.

ADK pauses a run on a tool's confirmation, and the user answers with an
ordinary message. ADK's own ``ToolConfirmation``, sent as a data part, is the
answer as it is. Anything else - a typed word, the object typed as JSON, a
pressed card button's ``actionId`` - reaches the agent unconfirmed in the
confirmation's payload, and the agent decides what it means; the server reads
no word as a yes.

The native agent has a tool of each kind (``adk_native``): two that ask for
themselves and read the answer when ADK runs them again (``confirm_action``,
and ``confirm_with_card``, which shows its question on a card with two
buttons), one ADK confirms for itself and the agent's ``before_tool_callback``
judges (``delete_cache``), and one ADK confirms for itself that only ADK's
object confirms (``drop_table``).
"""

from __future__ import annotations

import pytest

from tests.scenarios.agents.native_script import (
    APPROVE,
    DELETED,
    DROPPED,
    REJECT,
    REJECTED,
    confirmed_answer,
)
from tests.scenarios.core.test_cards import card_event, card_part
from tests.scenarios.harness import DataAttachment, ScenarioClient, card_action_event, final_task, section_texts

pytestmark = [pytest.mark.native, pytest.mark.capability("tool-confirmation")]

CONFIRMED = DataAttachment(data={"confirmed": True})
"""ADK's own confirmation object, sent as a data part."""

CONFIRMED_TYPED = '{"confirmed": true}'
"""The same object typed as JSON: text, which confirms nothing on its own."""


async def answer(
        client: ScenarioClient,
        command: str,
        reply: str = "",
        *,
        pressed: str | None = None,
        data: tuple[DataAttachment, ...] = (),
) -> list:
    """Start ``command``, check that it waits for a confirmation, answer it; return the answer's events.

    ``pressed`` answers with the click of the button with that action id,
    as a distribution sends it: a card action event.
    """
    opened = final_task(await client.send(command))
    assert opened.state == "INPUT_REQUIRED", f"{command!r} did not wait for a confirmation"

    if pressed:
        click = card_action_event(action_id=pressed)
        events = await client.send(
            reply, task_id=opened.task_id, context_id=opened.context_id,
            message_metadata=click.message_metadata, data=[click.data], extensions=[click.extension_uri],
        )
    else:
        events = await client.send(reply, task_id=opened.task_id, context_id=opened.context_id, data=data)

    assert final_task(events).state == "COMPLETED"
    return events


async def test_a_tool_shows_its_question_on_a_card_and_waits(client: ScenarioClient) -> None:
    events = await client.send("confirm-card deploy")

    closing = final_task(events)
    assert closing.state == "INPUT_REQUIRED"
    assert closing.text == "Proceed with deploy?"
    document = card_part(card_event(events).raw.status.message.parts).raw.decode("utf-8")
    assert "Proceed with deploy?" in document
    assert f'id="{APPROVE}"' in document and f'id="{REJECT}"' in document


async def test_adks_confirmation_object_typed_as_json_is_text_the_tool_reads(client: ScenarioClient) -> None:
    events = await answer(client, "confirm-card deploy", CONFIRMED_TYPED)

    assert section_texts(events) == [confirmed_answer("no")]


async def test_adks_confirmation_object_sent_as_data_answers_as_it_is(client: ScenarioClient) -> None:
    events = await answer(client, "confirm-card deploy", data=(DataAttachment(data={"confirmed": False}),))

    assert section_texts(events) == [confirmed_answer("no")]


@pytest.mark.parametrize(
    "action_id, said",
    [(APPROVE, "yes"), (REJECT, "no")],
    ids=["approve", "reject"],
)
async def test_a_pressed_button_reaches_the_tool_that_decides(client: ScenarioClient, action_id, said) -> None:
    events = await answer(client, "confirm-card deploy", pressed=action_id)

    assert section_texts(events) == [confirmed_answer(said)]


@pytest.mark.parametrize(
    "reply, said",
    [("sure", "yes"), ("Yes", "yes"), ("later", "no")],
    ids=["agents-word", "adks-word", "other"],
)
async def test_a_typed_answer_reaches_the_tool_that_decides(client: ScenarioClient, reply, said) -> None:
    events = await answer(client, "confirm-card deploy", reply)

    assert section_texts(events) == [confirmed_answer(said)]


@pytest.mark.parametrize(
    "reply, pressed, sends_object, said",
    [
        ("sure", None, False, DELETED),
        ("nope", None, False, REJECTED),
        ("", APPROVE, False, DELETED),
        ("", None, True, DELETED),
    ],
    ids=["typed-yes", "typed-no", "button", "object"],
)
async def test_the_agent_judges_the_answer_for_a_tool_adk_confirms(
        client: ScenarioClient, reply, pressed, sends_object, said,
) -> None:
    """``before_tool_callback`` runs before ADK checks the confirmation, and sets it."""
    events = await answer(client, "delete-cache", reply, pressed=pressed, data=(CONFIRMED,) if sends_object else ())

    assert section_texts(events) == [confirmed_answer(said)]


@pytest.mark.parametrize(
    "reply, pressed, sends_object, said",
    [
        ("", None, True, DROPPED),
        (CONFIRMED_TYPED, None, False, REJECTED),
        ("yes", None, False, REJECTED),
        ("", APPROVE, False, REJECTED),
    ],
    ids=["object", "object-typed", "typed", "button"],
)
async def test_a_tool_adk_confirms_takes_only_its_object(
        client: ScenarioClient, reply, pressed, sends_object, said,
) -> None:
    """With no judge of the agent's, an answer that is not ADK's object as data is a refusal, not a crash."""
    events = await answer(client, "drop-table", reply, pressed=pressed, data=(CONFIRMED,) if sends_object else ())

    assert section_texts(events) == [confirmed_answer(said)]
