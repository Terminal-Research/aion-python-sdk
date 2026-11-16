"""Reading messages, artifacts and tasks: ``aion.core.a2a.content``."""

from __future__ import annotations

from a2a.types import Artifact, Message, Part, Role, Task, TaskState, TaskStatus

from aion.core.a2a import data_artifact, file_artifact, text_artifact, url_artifact
from aion.core.a2a.content import (
    answer_text,
    conversation_messages,
    data_of,
    file_parts,
    is_conversation_message,
    text_of,
)
from aion.core.a2a.enums import A2AMetadataKey, ArtifactId, MessageType
from aion.core.constants.a2a import EVENT_EXTENSION_URI_V1


def message(text: str, role: int = Role.ROLE_USER, message_id: str = "") -> Message:
    return Message(message_id=message_id or text, role=role, parts=[Part(text=text)])


def task(*history: Message, artifacts: tuple[Artifact, ...] = (), status: Message | None = None) -> Task:
    built = Task(
        id="task",
        context_id="ctx",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=list(history),
        artifacts=list(artifacts),
    )
    if status is not None:
        built.status.message.CopyFrom(status)
    return built


# -- parts -------------------------------------------------------------------------


def test_the_text_of_an_artifact_joins_the_chunks_it_was_written_in() -> None:
    artifact = Artifact(artifact_id="a", parts=[Part(text="Hel"), Part(text="lo")])

    assert text_of(artifact) == "Hello"


def test_the_text_of_a_message_keeps_its_paragraphs_apart() -> None:
    said = Message(message_id="m", role=Role.ROLE_USER, parts=[Part(text="One"), Part(text="Two")])

    assert text_of(said) == "One\nTwo"
    assert text_of(said, separator=" ") == "One Two"


def test_text_leaves_out_files_data_and_the_envelope_of_an_event() -> None:
    envelope = Part(text="event")
    envelope.metadata[EVENT_EXTENSION_URI_V1] = {"type": "x"}
    said = Message(
        message_id="m",
        role=Role.ROLE_USER,
        parts=[envelope, Part(text="Hi"), Part(raw=b"bytes", filename="a.bin"), *data_artifact({"n": 1}).parts],
    )

    assert text_of(said) == "Hi"


def test_data_parts_come_back_as_plain_python() -> None:
    artifact = Artifact(artifact_id="a", parts=[*data_artifact({"n": 3, "tags": ["x"]}).parts, Part(text="t")])

    assert data_of(artifact) == [{"n": 3.0, "tags": ["x"]}]
    assert data_of(text_artifact("t")) == []


def test_file_parts_are_the_inline_and_the_stored_ones() -> None:
    inline = file_artifact(b"data", mime_type="text/plain", name="a.txt").parts[0]
    stored = url_artifact("https://files.test/b.pdf", mime_type="application/pdf").parts[0]

    assert file_parts([Part(text="t"), inline, stored]) == [inline, stored]


# -- the messages of a task ---------------------------------------------------------


def test_a_running_tasks_current_message_is_part_of_its_conversation() -> None:
    asked, working = message("ask"), message("working", Role.ROLE_AGENT)

    assert conversation_messages(task(asked, status=working)) == [asked, working]


def test_the_message_a_task_stopped_on_is_not_counted_twice() -> None:
    asked, done = message("ask"), message("done", Role.ROLE_AGENT)

    assert conversation_messages(task(asked, done, status=done)) == [asked, done]


def test_an_event_the_agent_keeps_for_itself_is_not_a_conversation_message() -> None:
    event = message("values")
    event.metadata[A2AMetadataKey.MESSAGE_TYPE.value] = MessageType.LANGRAPH_VALUES.value
    said = message("hello")
    said.metadata[A2AMetadataKey.MESSAGE_TYPE.value] = MessageType.MESSAGE.value

    assert (is_conversation_message(event), is_conversation_message(said)) == (False, True)
    assert conversation_messages(task(event, said)) == [said]


# -- the answer of a task ------------------------------------------------------------


def test_a_streamed_answer_is_the_tasks_answer() -> None:
    streamed = Artifact(artifact_id=ArtifactId.STREAM_DELTA.value, parts=[Part(text="Stream"), Part(text="ed")])
    report = text_artifact("Report", artifact_id="report")

    assert answer_text(task(message("ask"), artifacts=(streamed, report))) == "Streamed"


def test_without_a_streamed_answer_the_last_artifact_with_text_answers() -> None:
    thoughts = Artifact(artifact_id=ArtifactId.THINKING_DELTA.value, parts=[Part(text="hm")])
    first, last = text_artifact("First", artifact_id="a"), text_artifact("Last", artifact_id="b")
    numbers = data_artifact({"n": 1}, artifact_id="c")

    assert answer_text(task(artifacts=(thoughts, first, last, numbers))) == "Last"


def test_a_task_that_only_talked_answers_with_the_copy_of_its_last_message() -> None:
    copied = Artifact(artifact_id=ArtifactId.ASSISTANT_ANSWER.value, parts=[Part(text="Done.")])
    history = (message("ask"), message("Working on it", Role.ROLE_AGENT), message("Done.", Role.ROLE_AGENT))

    assert answer_text(task(*history, artifacts=(copied,))) == "Done."


def test_an_unfinished_task_answers_with_what_the_agent_said_last() -> None:
    history = (message("ask"), message("Which file?", Role.ROLE_AGENT), message("that one"))

    assert answer_text(task(*history)) == "Which file?"


def test_a_task_that_said_nothing_has_no_answer() -> None:
    assert answer_text(task(message("ask"))) is None
