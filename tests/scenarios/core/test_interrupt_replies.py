"""The user's reply to a question reaches the agent the way its framework hands answers over.

``ask-value`` asks a question with options and reports, as JSON, the value
the framework handed the agent: what LangGraph's ``interrupt()`` returned, or
the function response ADK resumed its ``adk_request_input`` call with. The
reply is read by one rule on both frameworks:

* its data part, when it has one, else the ``actionId`` of the card button it
  presses, else its text - JSON written in the text stays text;
* the parts a distribution adds on its own (the event payloads of a message
  from Slack, say) are not the reply;
* a message with more than one data part of its own is refused before the task
  runs, and the task keeps waiting.

LangGraph hands the value over as it is. ADK takes a function response, an
object: a data part that holds an object is the response itself, and any
other value is ``{"result": ...}``.

The question carries what the agent asks about as a data part beside its
text: the ``interrupt()`` value in LangGraph, the call in ADK.
"""

from __future__ import annotations

from typing import Any

import pytest
from a2a.types import TaskState
from a2a.utils.errors import InvalidParamsError

from tests.scenarios.commands import ask_value_question, ask_value_text
from tests.scenarios.frameworks import Framework
from tests.scenarios.harness import (
    EVENT_EXTENSION_URI,
    MESSAGING_EXTENSION_URI,
    DataAttachment,
    ScenarioClient,
    card_action_event,
    data_of_parts,
    final_task,
    message_event,
    reply_texts,
)

pytestmark = [pytest.mark.interrupts, pytest.mark.command("ask-value")]


def _handed_over(framework: Framework, value: Any) -> Any:
    """What the framework hands the agent for a reply whose value is ``value``."""
    if framework.name == "adk" and not isinstance(value, dict):
        return {"result": value}
    return value


async def _asked(client: ScenarioClient):
    closing = final_task(await client.send("ask-value"))
    assert closing.state == "INPUT_REQUIRED"
    return closing


async def _reply(client: ScenarioClient, text: str, **kwargs: Any) -> list[str]:
    asked = await _asked(client)
    events = await client.send(text, task_id=asked.task_id, context_id=asked.context_id, **kwargs)
    assert final_task(events).state == "COMPLETED"
    return reply_texts(events)


async def test_the_question_carries_what_it_asks_about_as_data(client: ScenarioClient, framework: Framework) -> None:
    asked = await _asked(client)

    stored = await client.get_task(asked.task_id)
    question = stored.status.message
    assert question.parts[0].text == ask_value_question()["prompt"]
    (data,) = data_of_parts(question.parts)
    if framework.name == "langgraph":
        assert data == ask_value_question()
    else:
        assert data["name"] == "adk_request_input"
        assert data["args"]["message"] == ask_value_question()["prompt"]
        assert data["args"]["options"] == ask_value_question()["options"]


async def test_a_text_reply_is_its_text(client: ScenarioClient, framework: Framework) -> None:
    assert ask_value_text(_handed_over(framework, "Go")) in await _reply(client, "Go")


async def test_json_written_in_the_text_stays_text(client: ScenarioClient, framework: Framework) -> None:
    text = '{"language": "Go"}'

    assert ask_value_text(_handed_over(framework, text)) in await _reply(client, text)


async def test_a_data_part_is_the_reply_as_it_is(client: ScenarioClient, framework: Framework) -> None:
    answer = {"language": "Go", "reasons": ["speed"]}

    replies = await _reply(client, "", data=[DataAttachment(data=answer)])

    assert ask_value_text(_handed_over(framework, answer)) in replies


async def test_an_empty_object_is_a_reply_too(client: ScenarioClient, framework: Framework) -> None:
    """``{}`` resumes the agent: it is not taken for a map of answers to no interrupt."""
    assert ask_value_text(_handed_over(framework, {})) in await _reply(client, "", data=[DataAttachment(data={})])


async def test_the_parts_a_distribution_adds_are_not_the_reply(client: ScenarioClient, framework: Framework) -> None:
    """A reply from a messaging distribution: the user's text, and the payloads it adds on its own."""
    event = message_event()
    source = DataAttachment(
        data={"provider": "slack", "event": {"type": "message", "text": "Go"}},
        metadata={EVENT_EXTENSION_URI: {"schema": f"{MESSAGING_EXTENSION_URI}#SourceSystemEventPayload"}},
    )

    replies = await _reply(
        client, "Go",
        message_metadata=event.message_metadata, data=[event.data, source], extensions=[event.extension_uri],
    )

    assert ask_value_text(_handed_over(framework, "Go")) in replies


async def test_a_pressed_card_button_is_its_action_id(client: ScenarioClient, framework: Framework) -> None:
    click = card_action_event(action_id="Go")

    replies = await _reply(
        client, "", message_metadata=click.message_metadata, data=[click.data], extensions=[click.extension_uri],
    )

    assert ask_value_text(_handed_over(framework, "Go")) in replies


async def test_a_reply_with_several_data_parts_is_refused_and_the_task_keeps_waiting(client: ScenarioClient) -> None:
    asked = await _asked(client)

    with pytest.raises(InvalidParamsError) as refused:
        await client.send(
            "", task_id=asked.task_id, context_id=asked.context_id,
            data=[DataAttachment(data={"language": "Go"}), DataAttachment(data={"language": "Python"})],
        )

    assert "at most one data part" in str(refused.value)
    assert (await client.get_task(asked.task_id)).status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    events = await client.send("Go", task_id=asked.task_id, context_id=asked.context_id)
    assert final_task(events).state == "COMPLETED"
