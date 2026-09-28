"""An ordinary agent's answer, tool call and inputs, through `aion serve`.

The agent here is the framework's own: a model streaming its answer, a tool
the framework calls on the model's request, the framework's loop between
them. Nothing in it uses the Aion authoring API, so what the client sees is
the adapter's handling of what the framework itself produced - the path
every real agent takes and the command-contract agents never do.

What the client is owed on that path:

* the model's answer streams as deltas and then lands as one message;
* a model call that only asks for a tool, the call itself, the tool's
  result and the model's reasoning are not the agent speaking, and do not
  reach the client;
* two model calls in one turn are two streams, and what the first one said
  is kept rather than overwritten by the second;
* a file and a structured part reach the model in one agreed form: the file
  as a file with its media type and bytes, the data part as its JSON text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.scenarios.agents.native_script import (
    COUNTED_ANSWER,
    THOUGHT,
    WEATHER_PREAMBLE,
    tokens,
    weather_answer,
    weather_result,
)
from tests.scenarios.harness import (
    DataAttachment,
    FileAttachment,
    ScenarioClient,
    final_task,
    reply_texts,
)
from tests.scenarios.harness.shape import is_chunk

pytestmark = pytest.mark.native

PNG = FileAttachment(name="pixel.png", media_type="image/png", content=b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
DATA = {"order": 42, "items": ["tea", "cake"]}
DATA_METADATA = {"source": "crm-export"}


def _streams(events) -> list[str]:
    """The text of each stream-delta section, in order.

    A chunk with ``append`` unset opens a section; the ones after it extend it.
    """
    sections: list[str] = []
    for event in events:
        if not is_chunk(event):
            continue
        if not event.append or not sections:
            sections.append("")
        sections[-1] += event.text
    return sections


async def test_the_answer_streams_as_deltas_then_lands_as_one_message(client: ScenarioClient) -> None:
    """The ordinary answer: tokens as they come, then the whole of it, once."""
    answer = "the quick brown fox"

    events = await client.send(f"say {answer}")

    chunks = [event for event in events if is_chunk(event)]
    assert [event.text for event in chunks] == tokens(answer)
    assert [event.append for event in chunks] == [False] + [True] * (len(chunks) - 1)
    assert reply_texts(events) == [answer]
    closing = final_task(events)
    assert closing.state == "COMPLETED"
    stored = await client.get_task(closing.task_id)
    assert stored.status.message.parts[0].text == answer


async def test_the_tool_call_and_its_result_are_not_replies(client: ScenarioClient) -> None:
    """Only the model's words reach the client; the tool round-trip does not.

    The tool result is a message in the framework's conversation - a
    ``ToolMessage`` in LangGraph, a function response in ADK - and the call
    streams as chunks of its own. None of it is the agent speaking.
    """
    events = await client.send("weather Kyiv")

    assert reply_texts(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]
    assert weather_result("Kyiv") not in [event.text for event in events]
    assert all(event.text for event in events if is_chunk(event))
    assert final_task(events).state == "COMPLETED"


async def test_two_model_calls_are_two_streams(client: ScenarioClient) -> None:
    """The answer after the tool opens a stream of its own.

    Appended to the first, the client would show one message that says
    "Checking the weather.It is sunny in Kyiv." and then replace it.
    """
    events = await client.send("weather Kyiv")

    assert _streams(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]


async def test_a_model_call_that_only_calls_a_tool_says_nothing(client: ScenarioClient) -> None:
    """No empty reply, no empty chunk: a silent tool request is silent."""
    events = await client.send("tool-only Kyiv")

    assert reply_texts(events) == [weather_answer("Kyiv")]
    assert _streams(events) == [weather_answer("Kyiv")]


async def test_a_file_and_a_data_part_reach_the_model_in_the_agreed_form(
    client: ScenarioClient,
) -> None:
    """Contract: a file arrives as a file, a data part as its JSON text.

    The same description on both frameworks: the text, the file with its
    media type and every byte, and the data part as one text part holding
    its JSON - neither framework has a structured part for a user turn, and
    text is the form every model accepts. The JSON is compared as a value:
    the part travels as a protobuf ``Struct``, which keeps numbers as floats
    and does not keep key order. The part's metadata is not part of what the
    model reads; the agent finds it in the inbox (``core/test_files``).
    """
    events = await client.send(
        "inputs", files=[PNG], data=[DataAttachment(data=DATA, metadata=DATA_METADATA)]
    )

    (answer,) = reply_texts(events)
    text, file, data = json.loads(answer)
    assert text == {"type": "text", "text": "inputs"}
    assert file == {"type": "file", "mime_type": PNG.media_type, "size": len(PNG.content)}
    assert data["type"] == "text"
    assert json.loads(data["text"]) == DATA
    assert DATA_METADATA["source"] not in answer


async def test_the_models_reasoning_is_not_sent(client: ScenarioClient) -> None:
    """Thoughts stream first and are left out: a LangGraph reasoning block, an
    ADK part with ``thought`` set. Only the answer streams and lands."""
    events = await client.send("think short answer")

    assert reply_texts(events) == ["short answer"]
    assert _streams(events) == ["short answer"]
    assert not any(THOUGHT.split()[1] in event.text for event in events)


async def test_the_tool_runs_once_per_request(client: ScenarioClient, tmp_path: Path) -> None:
    """The model asks once, and the tool runs once.

    The call is counted where it happens, in the tool. A second model call
    built from a conversation that did not yet hold the tool's result would
    ask for it again - and on ADK it once did, while its session lagged the
    agent.
    """
    calls = tmp_path / "calls"

    events = await client.send(f"counted {calls}")

    assert final_task(events).state == "COMPLETED"
    assert reply_texts(events) == [WEATHER_PREAMBLE, COUNTED_ANSWER]
    assert calls.read_text().splitlines() == ["call"]
