"""Which message `AssistantAnswer` makes a completed task's answer, and what the answer carries.

The pipeline's tests follow the answer through a real turn; these pin down
the choice of message and the shape of the artifact on their own.
"""

from __future__ import annotations

import uuid

from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.enums import A2AMetadataKey, ArtifactId, ArtifactName, MessageType
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1
from aion.server.agent.execution.assistant_answer import AssistantAnswer, is_result_artifact

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"


def _message(*texts: str, role: Role = Role.ROLE_AGENT, metadata: dict | None = None) -> Message:
    message = Message(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        message_id=str(uuid.uuid4()),
        role=role,
        parts=[Part(text=text) for text in texts],
    )
    if metadata:
        message.metadata.update(metadata)
    return message


def _status(state: TaskState = TaskState.TASK_STATE_WORKING, message: Message | None = None) -> TaskStatusUpdateEvent:
    status = TaskStatus(state=state, message=message) if message is not None else TaskStatus(state=state)
    return TaskStatusUpdateEvent(task_id=TASK_ID, context_id=CONTEXT_ID, status=status)


def _completed(message: Message | None = None) -> TaskStatusUpdateEvent:
    return _status(TaskState.TASK_STATE_COMPLETED, message)


def _artifact(artifact_id: str) -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        artifact=Artifact(artifact_id=artifact_id, parts=[Part(text="content")]),
    )


def _answer_after(*events, completed: TaskStatusUpdateEvent | None = None, stored: Task | None = None):
    answer = AssistantAnswer()
    for event in events:
        answer.observe(event)
    return answer.for_completion(completed or _completed(), stored)


class TestResultArtifacts:
    def test_an_artifact_the_agent_named_is_a_result(self):
        assert is_result_artifact(Artifact(artifact_id="report"))

    def test_the_streamed_answer_is_a_result(self):
        assert is_result_artifact(Artifact(artifact_id=ArtifactId.STREAM_DELTA.value))

    def test_thoughts_reactions_and_the_copied_answer_are_not(self):
        for artifact_id in (ArtifactId.THINKING_DELTA, ArtifactId.REACTION, ArtifactId.ASSISTANT_ANSWER):
            assert not is_result_artifact(Artifact(artifact_id=artifact_id.value))


class TestTheAnswer:
    def test_the_answer_is_every_part_of_the_last_agent_message(self):
        last = _message("here it is", "and more")
        last.parts.append(Part(url="https://files.example/report.pdf", filename="report.pdf"))

        answer = _answer_after(_status(message=_message("looking")), _status(message=last))

        assert answer.task_id == TASK_ID
        assert answer.context_id == CONTEXT_ID
        assert answer.artifact.artifact_id == ArtifactId.ASSISTANT_ANSWER.value
        assert answer.artifact.name == ArtifactName.ASSISTANT_ANSWER.value
        assert list(answer.artifact.parts) == list(last.parts)
        assert answer.append is False
        assert answer.last_chunk is True

    def test_a_message_the_agent_stored_without_a_status_counts(self):
        answer = _answer_after(_message("noted"))

        assert [part.text for part in answer.artifact.parts] == ["noted"]

    def test_the_message_on_the_completed_status_is_the_last_one(self):
        answer = _answer_after(_status(message=_message("looking")), completed=_completed(_message("done")))

        assert [part.text for part in answer.artifact.parts] == ["done"]

    def test_the_status_message_of_a_whole_task_counts(self):
        task = Task(
            id=TASK_ID,
            context_id=CONTEXT_ID,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=_message("from the task")),
        )

        answer = _answer_after(task)

        assert [part.text for part in answer.artifact.parts] == ["from the task"]

    def test_a_user_message_is_not_the_answer(self):
        answer = _answer_after(_message("agent said"), _message("user said", role=Role.ROLE_USER))

        assert [part.text for part in answer.artifact.parts] == ["agent said"]

    def test_a_message_without_parts_is_not_the_answer(self):
        answer = _answer_after(_message("agent said"), _message())

        assert [part.text for part in answer.artifact.parts] == ["agent said"]

    def test_an_internal_event_message_is_not_the_answer(self):
        event = _message("graph values", metadata={A2AMetadataKey.MESSAGE_TYPE.value: MessageType.EVENT.value})

        answer = _answer_after(_message("agent said"), event)

        assert [part.text for part in answer.artifact.parts] == ["agent said"]

    def test_the_answer_keeps_the_messages_extensions_and_the_agents_metadata(self):
        last = _message("done", metadata={"source": "planner", "aion:network": {"route": "x"}})
        last.extensions.append("https://example.invalid/ext")

        answer = _answer_after(last)

        assert list(answer.artifact.extensions) == ["https://example.invalid/ext"]
        assert MessageToDict(answer.artifact.metadata) == {
            "source": "planner",
            A2AMetadataKey.SOURCE_MESSAGE_ID.value: last.message_id,
        }

    def test_the_answer_names_the_message_it_was_copied_from(self):
        last = _message("done")

        answer = _answer_after(_message("looking"), last)

        assert answer.artifact.metadata[A2AMetadataKey.SOURCE_MESSAGE_ID.value] == last.message_id

    def test_the_answer_leaves_out_the_messages_routing_target(self):
        """The message was delivered where its target says; a copy carrying the target would be delivered again."""
        routing = MessageActionPayload(trajectory="reply", context_id="C1", reply_to_message_id="m-1")
        last = _message("done")
        last.parts.append(routing.to_part())
        last.extensions.append(MESSAGING_EXTENSION_URI_V1)

        answer = _answer_after(last)

        assert [part.text for part in answer.artifact.parts] == ["done"]
        assert list(answer.artifact.extensions) == []

    def test_the_answer_keeps_the_extension_another_messaging_payload_needs(self):
        routing = MessageActionPayload(trajectory="conversation", context_id="C1")
        file_part = Part(url="https://files.example/report.pdf", media_type="application/pdf")
        file_part.metadata.update({MESSAGING_EXTENSION_URI_V1: {"schema": "#FileActionPayload"}})
        last = _message("here", metadata=None)
        last.parts.extend([file_part, routing.to_part()])
        last.extensions.append(MESSAGING_EXTENSION_URI_V1)

        answer = _answer_after(last)

        assert len(answer.artifact.parts) == 2
        assert list(answer.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]

    def test_the_answer_does_not_share_parts_with_the_message(self):
        last = _message("done")

        answer = _answer_after(last)
        last.parts[0].text = "changed"

        assert answer.artifact.parts[0].text == "done"


class TestNoAnswer:
    def test_a_result_of_the_turn_means_no_answer(self):
        assert _answer_after(_message("here it is"), _artifact("report")) is None

    def test_a_whole_task_with_a_result_means_no_answer(self):
        task = Task(id=TASK_ID, context_id=CONTEXT_ID, artifacts=[Artifact(artifact_id="report")])

        assert _answer_after(_message("here it is"), task) is None

    def test_a_result_the_stored_task_holds_means_no_answer(self):
        stored = Task(id=TASK_ID, context_id=CONTEXT_ID, artifacts=[Artifact(artifact_id="report")])

        assert _answer_after(_message("here it is"), stored=stored) is None

    def test_thoughts_reactions_and_an_earlier_answer_are_not_results(self):
        stored = Task(
            id=TASK_ID,
            context_id=CONTEXT_ID,
            artifacts=[Artifact(artifact_id=ArtifactId.ASSISTANT_ANSWER.value, parts=[Part(text="before")])],
        )

        answer = _answer_after(
            _artifact(ArtifactId.THINKING_DELTA.value),
            _artifact(ArtifactId.REACTION.value),
            _message("now"),
            stored=stored,
        )

        assert [part.text for part in answer.artifact.parts] == ["now"]

    def test_no_message_means_no_answer(self, caplog):
        with caplog.at_level("WARNING", logger="aion.server.agent.execution.assistant_answer"):
            assert _answer_after(_status()) is None

        assert f"Task {TASK_ID} completed without a result or a message" in caplog.text

    def test_a_turn_is_answered_once(self):
        answer = AssistantAnswer()
        answer.observe(_message("done"))

        assert answer.for_completion(_completed(), None) is not None
        assert answer.for_completion(_completed(), None) is None
