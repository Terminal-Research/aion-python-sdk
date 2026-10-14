"""What a toolkit's protocol values become in A2A, row by row of the translation table.

Each test drives the scripted toolkit through a ``ToolkitTaskHandler`` turn
and reads the A2A events the handler yields - what the executor's pipeline
then delivers and stores.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import (
    Message as A2AMessage,
    Part as A2APart,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.enums import ArtifactId
from aion.core.constants.a2a import (
    EVENT_EXTENSION_URI_V1,
    MESSAGING_EXTENSION_URI_V1,
    STREAM_DELTA_PAYLOAD_SCHEMA_V1,
)
from aion.core.toolkit import (
    Artifact,
    DataPart,
    FilePart,
    Message,
    TextPart,
    ToolkitArtifactEvent,
    ToolkitError,
    ToolkitMessageEvent,
    ToolkitRequest,
    ToolkitResult,
    ToolkitState,
    ToolkitStatusEvent,
)
from aion.server.a2a.utils import is_ephemeral_status_event
from aion.server.agent.execution.extensions.toolkit import (
    MAX_DURABLE_MESSAGES_PER_TURN,
    MAX_MESSAGE_TEXT_LENGTH,
    ToolkitAdmission,
    ToolkitTaskHandler,
)
from aion.server.agent.execution.extensions.toolkit.secret_values import MASK
from aion.server.agent.execution.extensions.toolkit.translation import (
    DEFAULT_EVENT_TYPE,
    TRUNCATION_MARK,
    error_schema,
)

from tests.support.scripted_toolkit.contract import URI
from tests.support.scripted_toolkit.toolkit import Script, ScriptedToolkit

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
TURN_ID = "m-user"
SECRET = "deployment-secret-value"


def task(**metadata: Any) -> Task:
    built = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    for key, value in metadata.items():
        built.metadata[key] = value
    return built


def request_context(current: Task) -> RequestContext:
    message = A2AMessage(message_id=TURN_ID, role=Role.ROLE_USER, parts=[A2APart(text="go")])
    return RequestContext(
        call_context=ServerCallContext(),
        request=SendMessageRequest(message=message),
        task_id=current.id,
        context_id=current.context_id,
        task=current,
    )


def handler(*outputs: Any, secrets: tuple[str, ...] = (SECRET,)) -> ToolkitTaskHandler:
    return ToolkitTaskHandler(ScriptedToolkit(Script(outputs=list(outputs))), secret_values=lambda: secrets)


async def run(toolkit_handler: ToolkitTaskHandler) -> list[Any]:
    admission = ToolkitAdmission(
        request=ToolkitRequest(message_id=TURN_ID, text="go"), daemon_variables={}, model_service=None
    )
    return [event async for event in toolkit_handler.stream(request_context(task()), admission)]


def completed(**fields: Any) -> ToolkitResult:
    return ToolkitResult(state=ToolkitState.COMPLETED, **fields)


def said(text: str, **fields: Any) -> ToolkitMessageEvent:
    return ToolkitMessageEvent(message=Message(parts=[TextPart(text=text)]), **fields)


def as_dict(proto) -> dict:
    return MessageToDict(proto, preserving_proto_field_name=True)


def toolkit_object(event: TaskStatusUpdateEvent) -> dict | None:
    metadata = as_dict(event).get("metadata", {})
    return metadata.get(URI)


def final(events: list[Any]) -> TaskStatusUpdateEvent:
    status = events[-1]
    assert isinstance(status, TaskStatusUpdateEvent)
    return status


# -- ToolkitMessageEvent --------------------------------------------------------


async def test_a_message_is_a_working_status_with_the_object() -> None:
    events = await run(handler(said("started", task_metadata={"stage": "preparing"}), completed()))

    update = events[0]
    assert isinstance(update, TaskStatusUpdateEvent)
    assert update.status.state == TaskState.TASK_STATE_WORKING
    assert update.status.message.role == Role.ROLE_AGENT
    assert [part.text for part in update.status.message.parts] == ["started"]
    assert toolkit_object(update) == {"stage": "preparing"}
    assert not is_ephemeral_status_event(update)


async def test_every_message_is_an_event_of_the_turn() -> None:
    typed = ToolkitMessageEvent(
        message=Message(
            type="stage.changed",
            parts=[DataPart(data={"stage": "executing"}, schema=f"{URI}#StageChanged")],
            metadata={"note": "kept"},
        )
    )

    events = await run(handler(said("first"), typed, completed()))

    first, second = events[0].status.message, events[1].status.message
    markup = as_dict(second)["metadata"][EVENT_EXTENSION_URI_V1]
    assert markup == {
        "type": "stage.changed",
        "source": URI,
        "id": second.message_id,
        "sequence": 2.0,
        "turn": TURN_ID,
    }
    assert as_dict(first)["metadata"][EVENT_EXTENSION_URI_V1]["type"] == DEFAULT_EVENT_TYPE
    assert as_dict(second)["metadata"]["note"] == "kept"
    assert list(second.extensions) == [EVENT_EXTENSION_URI_V1]
    part = as_dict(second.parts[0])
    assert part["data"] == {"stage": "executing"}
    assert part["media_type"] == "application/json"
    assert part["metadata"][EVENT_EXTENSION_URI_V1] == {"schema": f"{URI}#StageChanged"}


async def test_an_ephemeral_message_is_delivered_and_not_written() -> None:
    events = await run(handler(said("ls -la", ephemeral=True), completed()))

    update = events[0]
    assert is_ephemeral_status_event(update)
    assert [part.text for part in update.status.message.parts] == ["ls -la"]
    assert toolkit_object(update) is None


async def test_partial_text_streams_as_deltas_closed_by_the_next_message() -> None:
    events = await run(
        handler(
            said("Hel", partial=True, ephemeral=False),
            ToolkitMessageEvent(message=Message(parts=[TextPart(text="lo")], metadata={"lane": "a"}), partial=True),
            said("Hello"),
            said("Next", partial=True),
            completed(),
        )
    )

    first, second, whole, reopened = events[:4]
    for chunk in (first, second, reopened):
        assert isinstance(chunk, TaskArtifactUpdateEvent)
        assert chunk.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert not chunk.last_chunk
        assert as_dict(chunk)["metadata"] == {
            MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1}
        }
    assert (first.append, second.append, reopened.append) == (False, True, False)
    assert [part.text for part in second.artifact.parts] == ["lo"]
    assert as_dict(second.artifact)["metadata"] == {
        "lane": "a",
        "status": "active",
        "status_reason": "chunk_streaming",
    }
    assert isinstance(whole, TaskStatusUpdateEvent) and whole.status.message.parts[0].text == "Hello"


# -- ToolkitArtifactEvent -------------------------------------------------------


async def test_an_artifact_is_an_artifact_update_and_its_details_follow() -> None:
    artifact = Artifact(
        id="report", name="Report", description="What happened", parts=[TextPart(text="body")],
        metadata={"format": "md"},
    )

    events = await run(
        handler(
            ToolkitArtifactEvent(artifact=artifact, append=True, last_chunk=True, task_metadata={"report": "ready"}),
            completed(),
        )
    )

    update, details = events[:2]
    assert isinstance(update, TaskArtifactUpdateEvent)
    assert (update.artifact.artifact_id, update.artifact.name, update.artifact.description) == (
        "report", "Report", "What happened",
    )
    assert [part.text for part in update.artifact.parts] == ["body"]
    assert as_dict(update.artifact)["metadata"] == {"format": "md"}
    assert (update.append, update.last_chunk) == (True, True)
    assert isinstance(details, TaskStatusUpdateEvent)
    assert details.status.state == TaskState.TASK_STATE_WORKING
    assert not details.status.HasField("message")
    assert toolkit_object(details) == {"report": "ready"}


async def test_an_artifact_left_unfinished_is_closed_before_the_final_status() -> None:
    chunk = Artifact(id="log", parts=[TextPart(text="line 1")])

    events = await run(handler(ToolkitArtifactEvent(artifact=chunk), completed()))

    opened, closing, status = events
    assert opened.artifact.artifact_id == "log" and not opened.last_chunk
    assert isinstance(closing, TaskArtifactUpdateEvent)
    assert closing.artifact.artifact_id == "log"
    assert (closing.append, closing.last_chunk, list(closing.artifact.parts)) == (True, True, [])
    assert status.status.state == TaskState.TASK_STATE_COMPLETED


# -- ToolkitStatusEvent ---------------------------------------------------------


async def test_a_status_event_writes_details_without_a_message() -> None:
    events = await run(handler(ToolkitStatusEvent(task_metadata={"usage": {"tokens": 10}}), completed()))

    update = events[0]
    assert update.status.state == TaskState.TASK_STATE_WORKING
    assert not update.status.HasField("message")
    assert toolkit_object(update) == {"usage": {"tokens": 10.0}}


# -- the toolkit's object -------------------------------------------------------


async def test_a_patch_replaces_top_level_keys_and_none_deletes_one() -> None:
    events = await run(
        handler(
            ToolkitStatusEvent(task_metadata={"plan": {"done": 1, "total": 3}, "stage": "executing"}),
            ToolkitStatusEvent(task_metadata={"plan": {"done": 2}, "stage": None}),
            completed(),
        )
    )

    assert toolkit_object(events[1]) == {"plan": {"done": 2.0}}
    assert toolkit_object(final(events)) == {"plan": {"done": 2.0}}


async def test_a_toolkit_that_keeps_no_object_gets_no_key() -> None:
    events = await run(handler(said("hi"), completed()))

    assert all(toolkit_object(event) is None for event in events if isinstance(event, TaskStatusUpdateEvent))


# -- ToolkitResult --------------------------------------------------------------


async def test_the_result_closes_with_its_artifacts_then_the_final_status() -> None:
    events = await run(
        handler(
            completed(
                message=Message(parts=[TextPart(text="Done.")]),
                artifacts=[Artifact(id="summary", parts=[TextPart(text="All good")])],
                task_metadata={"outcome": "delivered"},
            )
        )
    )

    artifact, status = events
    assert isinstance(artifact, TaskArtifactUpdateEvent)
    assert (artifact.artifact.artifact_id, artifact.append, artifact.last_chunk) == ("summary", False, True)
    assert status.status.state == TaskState.TASK_STATE_COMPLETED
    assert [part.text for part in status.status.message.parts] == ["Done."]
    assert toolkit_object(status) == {"outcome": "delivered"}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ToolkitState.COMPLETED, TaskState.TASK_STATE_COMPLETED),
        (ToolkitState.FAILED, TaskState.TASK_STATE_FAILED),
        (ToolkitState.CANCELED, TaskState.TASK_STATE_CANCELED),
        (ToolkitState.REJECTED, TaskState.TASK_STATE_REJECTED),
        (ToolkitState.INPUT_REQUIRED, TaskState.TASK_STATE_INPUT_REQUIRED),
        (ToolkitState.AUTH_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED),
    ],
)
async def test_every_result_state_maps_to_its_task_state(state, expected) -> None:
    events = await run(handler(ToolkitResult(state=state)))

    assert final(events).status.state == expected
    assert not final(events).status.HasField("message")


async def test_an_error_closes_the_message_for_a_person_and_for_a_program() -> None:
    result = ToolkitResult(
        state=ToolkitState.FAILED,
        message=Message(parts=[TextPart(text="The run stopped.")]),
        error=ToolkitError(code="push_rejected", message="The push was rejected.", retryable=True),
    )

    events = await run(handler(result))

    status = final(events)
    assert status.status.state == TaskState.TASK_STATE_FAILED
    text, said_error, data = status.status.message.parts
    assert (text.text, said_error.text) == ("The run stopped.", "The push was rejected.")
    assert as_dict(data)["data"] == {"code": "push_rejected", "message": "The push was rejected.", "retryable": True}
    assert as_dict(data)["metadata"][EVENT_EXTENSION_URI_V1] == {"schema": error_schema(URI)}


async def test_an_error_without_a_message_gets_one() -> None:
    result = ToolkitResult(
        state=ToolkitState.REJECTED, error=ToolkitError(code="invalid_directive", message="No target.")
    )

    events = await run(handler(result))

    parts = final(events).status.message.parts
    assert parts[0].text == "No target."
    assert as_dict(parts[1])["data"]["code"] == "invalid_directive"


# -- parts ----------------------------------------------------------------------


async def test_files_travel_as_bytes_or_as_their_link(tmp_path) -> None:
    on_disk = tmp_path / "report.txt"
    on_disk.write_bytes(b"from disk")
    files = Message(
        parts=[
            FilePart(raw=b"in memory", filename="a.bin", media_type="application/octet-stream"),
            FilePart(path=str(on_disk), media_type="text/plain"),
            FilePart(url="https://files.example.com/b.pdf", filename="b.pdf"),
        ]
    )

    events = await run(handler(ToolkitMessageEvent(message=files), completed()))

    raw, path, url = events[0].status.message.parts
    assert (raw.raw, raw.filename, raw.media_type) == (b"in memory", "a.bin", "application/octet-stream")
    assert (path.raw, path.filename, path.media_type) == (b"from disk", "report.txt", "text/plain")
    assert (url.url, url.filename) == ("https://files.example.com/b.pdf", "b.pdf")


async def test_a_file_the_server_cannot_read_fails_the_turn(tmp_path, caplog) -> None:
    missing = Message(parts=[FilePart(path=str(tmp_path / "missing.txt"))])

    with caplog.at_level(logging.ERROR):
        events = await run(handler(ToolkitMessageEvent(message=missing), completed()))

    assert len(events) == 1
    assert final(events).status.state == TaskState.TASK_STATE_FAILED
    assert "missing.txt" in caplog.text


# -- invariants -----------------------------------------------------------------


async def test_secret_values_are_masked_in_everything_the_toolkit_yields() -> None:
    leaky = f"token={SECRET}"
    events = await run(
        handler(
            ToolkitMessageEvent(
                message=Message(parts=[TextPart(text=leaky), DataPart(data={"env": leaky})], metadata={"cmd": leaky}),
                task_metadata={"last": leaky},
            ),
            ToolkitArtifactEvent(artifact=Artifact(id="log", name=leaky, parts=[TextPart(text=leaky)]), last_chunk=True),
            ToolkitResult(state=ToolkitState.FAILED, error=ToolkitError(code="executor_failed", message=leaky)),
        )
    )

    assert SECRET not in str([as_dict(event) for event in events])
    assert MASK in as_dict(events[0])["status"]["message"]["parts"][0]["text"]
    assert toolkit_object(events[0]) == {"last": f"token={MASK}"}


async def test_a_long_text_part_is_cut(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        events = await run(handler(said("x" * (MAX_MESSAGE_TEXT_LENGTH + 10)), completed()))

    text = events[0].status.message.parts[0].text
    assert len(text) == MAX_MESSAGE_TEXT_LENGTH
    assert text.endswith(TRUNCATION_MARK)
    assert "was cut" in caplog.text


async def test_messages_past_the_turns_limit_are_delivered_live_only(caplog) -> None:
    durable = [said(f"m{index}") for index in range(MAX_DURABLE_MESSAGES_PER_TURN)]
    overflow = said("one too many", task_metadata={"count": "over"})

    with caplog.at_level(logging.WARNING):
        events = await run(handler(*durable, overflow, completed()))

    stored = events[:MAX_DURABLE_MESSAGES_PER_TURN]
    assert not any(is_ephemeral_status_event(event) for event in stored)
    live, details = events[MAX_DURABLE_MESSAGES_PER_TURN:MAX_DURABLE_MESSAGES_PER_TURN + 2]
    assert is_ephemeral_status_event(live) and live.status.message.parts[0].text == "one too many"
    assert not details.status.HasField("message") and toolkit_object(details) == {"count": "over"}
    assert "subscribers connected now only" in caplog.text
