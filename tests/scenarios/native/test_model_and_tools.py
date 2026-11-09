"""An ordinary agent's answer, tool call and inputs, through `aion serve`.

The agent here is the framework's own: a model streaming its answer, a tool
the framework calls on the model's request, the framework's loop between
them. Nothing in it uses the Aion authoring API, so what the client sees is
the adapter's handling of what the framework itself produced - the path
every real agent takes and the command-contract agents never do.

What the client is owed on that path:

* the model's answer streams as chunks of an ``aion:stream-delta`` section,
  and then its whole text ends the section; it is the task's answer, kept as
  that artifact, and never a message;
* a model call that only asks for a tool, the call itself, the tool's
  result and the model's reasoning are not the agent speaking, and do not
  reach the client;
* two model calls in one turn are two sections, each ended with its own
  whole text rather than run into one;
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
    ASSISTANT_ANSWER_ARTIFACT_ID,
    STREAM_DELTA_ARTIFACT_ID,
    DataAttachment,
    FileAttachment,
    ScenarioClient,
    final_task,
    is_chunk,
    is_section_end,
    reply_texts,
    section_texts,
    stored_artifact,
    stored_texts,
    thought_texts,
)

pytestmark = pytest.mark.native

PNG = FileAttachment(name="pixel.png", media_type="image/png", content=b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
DATA = {"order": 42, "items": ["tea", "cake"]}
DATA_METADATA = {"source": "crm-export"}


async def test_the_answer_streams_as_chunks_then_its_whole_text(client: ScenarioClient) -> None:
    """The ordinary answer: tokens as they come, then the whole of it, once, as the task's artifact.

    The stand-in model produces its tokens at once, faster than the server
    passes chunks on, so the first token arrives by itself and the rest may
    be joined; whatever arrived is the start of the answer.
    """
    answer = "the quick brown fox"

    events = await client.send(f"say {answer}")

    chunks = [event for event in events if is_chunk(event)]
    assert chunks and chunks[0].text == tokens(answer)[0]
    assert [event.append for event in chunks] == [False] + [True] * (len(chunks) - 1)
    assert answer.startswith("".join(event.text for event in chunks))
    assert section_texts(events) == [answer]
    assert reply_texts(events) == []
    closing = final_task(events)
    assert closing.state == "COMPLETED"
    stored = await client.get_task(closing.task_id)
    streamed = stored_artifact(stored, STREAM_DELTA_ARTIFACT_ID)
    assert streamed is not None and [part.text for part in streamed.parts] == [answer]
    assert stored_texts(stored) == [f"say {answer}"]
    assert stored_artifact(stored, ASSISTANT_ANSWER_ARTIFACT_ID) is None


async def test_the_tool_call_and_its_result_do_not_reach_the_client(client: ScenarioClient) -> None:
    """Only the model's words reach the client; the tool round-trip does not.

    The tool result is a message in the framework's conversation - a
    ``ToolMessage`` in LangGraph, a function response in ADK - and the call
    streams as chunks of its own. None of it is the agent speaking.
    """
    events = await client.send("weather Kyiv")

    assert section_texts(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]
    assert reply_texts(events) == []
    assert weather_result("Kyiv") not in [event.text for event in events]
    assert all(event.text for event in events if is_chunk(event))
    assert final_task(events).state == "COMPLETED"


async def test_two_model_calls_are_two_sections(client: ScenarioClient) -> None:
    """The answer after the tool is a section of its own, ended with its own whole text.

    Run into the first, the client would show one answer that says
    "Checking the weather.It is sunny in Kyiv." A chunk of the second
    section that reaches the client opens it with ``append`` unset; the
    server may also join every chunk of it into the section's end.
    """
    events = await client.send("weather Kyiv")

    assert section_texts(events) == [WEATHER_PREAMBLE, weather_answer("Kyiv")]
    first_end = next(index for index, event in enumerate(events) if is_section_end(event))
    second = [event for event in events[first_end + 1:] if is_chunk(event)]
    assert not second or second[0].append is False


async def test_a_model_call_that_only_calls_a_tool_says_nothing(client: ScenarioClient) -> None:
    """No empty reply, no empty chunk: a silent tool request is silent."""
    events = await client.send("tool-only Kyiv")

    assert section_texts(events) == [weather_answer("Kyiv")]
    assert reply_texts(events) == []


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

    (answer,) = section_texts(events)
    text, file, data = json.loads(answer)
    assert text == {"type": "text", "text": "inputs"}
    assert file == {"type": "file", "mime_type": PNG.media_type, "size": len(PNG.content)}
    assert data["type"] == "text"
    assert json.loads(data["text"]) == DATA
    assert DATA_METADATA["source"] not in answer


async def test_the_models_reasoning_is_not_sent(client: ScenarioClient) -> None:
    """The model's reasoning streams first and is left out: a LangGraph reasoning
    block, an ADK part with ``thought`` set. Only the answer streams. Nor is the
    reasoning one of the agent's thoughts, which only the agent's own code sends."""
    events = await client.send("think short answer")

    assert section_texts(events) == ["short answer"]
    assert thought_texts(events) == []
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
    assert section_texts(events) == [WEATHER_PREAMBLE, COUNTED_ANSWER]
    assert calls.read_text().splitlines() == ["call"]
