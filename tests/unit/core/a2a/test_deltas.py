"""The events `aion.core.a2a.deltas` builds for the streamed answer and the agent's thoughts."""

from __future__ import annotations

from a2a.types import Part, TaskArtifactUpdateEvent, TaskState, TaskStatus, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.deltas import is_stream_delta_chunk, stream_delta_chunk, stream_delta_final, thinking_delta
from aion.core.a2a.enums import ArtifactId, ArtifactName
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
SCHEMA_MARKER = {MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1}}


def _chunk(*texts: str, append: bool = True, metadata=None) -> TaskArtifactUpdateEvent:
    return stream_delta_chunk(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        parts=[Part(text=text) for text in texts],
        append=append,
        metadata=metadata,
    )


class TestStreamDeltaChunk:
    def test_a_chunk_is_a_stream_delta_update_that_does_not_close_the_artifact(self):
        event = _chunk("Hel", "lo", append=False)

        assert (event.task_id, event.context_id) == (TASK_ID, CONTEXT_ID)
        assert event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert event.artifact.name == ArtifactName.STREAM_DELTA.value
        assert [part.text for part in event.artifact.parts] == ["Hel", "lo"]
        assert event.append is False
        assert event.last_chunk is False

    def test_append_is_what_the_caller_says(self):
        assert _chunk("lo").append is True

    def test_a_chunk_says_it_is_streaming(self):
        assert MessageToDict(_chunk("Hel").artifact.metadata) == {
            "status": "active",
            "status_reason": "chunk_streaming",
        }

    def test_the_agents_metadata_joins_the_artifacts(self):
        metadata = MessageToDict(_chunk("Hel", metadata={"model": "m-1"}).artifact.metadata)

        assert metadata["model"] == "m-1"
        assert metadata["status"] == "active"

    def test_the_event_carries_the_payload_schema_marker(self):
        assert MessageToDict(_chunk("Hel").metadata) == SCHEMA_MARKER


class TestStreamDeltaFinal:
    def test_a_final_is_the_whole_section_in_one_text_part_that_replaces_the_chunks(self):
        event = stream_delta_final(task_id=TASK_ID, context_id=CONTEXT_ID, text="Hello, world")

        assert event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert event.artifact.name == ArtifactName.STREAM_DELTA.value
        assert [part.text for part in event.artifact.parts] == ["Hello, world"]
        assert event.append is False
        assert event.last_chunk is True

    def test_a_final_says_the_section_is_finalized(self):
        event = stream_delta_final(
            task_id=TASK_ID, context_id=CONTEXT_ID, text="Hello", metadata={"model": "m-1"}
        )

        assert MessageToDict(event.artifact.metadata) == {"status": "finalized", "model": "m-1"}
        assert MessageToDict(event.metadata) == SCHEMA_MARKER


class TestThinkingDelta:
    def test_a_thought_is_one_text_part_added_to_the_thoughts(self):
        event = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="checking the logs")

        assert (event.task_id, event.context_id) == (TASK_ID, CONTEXT_ID)
        assert event.artifact.artifact_id == ArtifactId.THINKING_DELTA.value
        assert event.artifact.name == ArtifactName.THINKING_DELTA.value
        assert [part.text for part in event.artifact.parts] == ["checking the logs"]
        assert event.append is True
        assert event.last_chunk is False

    def test_a_thought_carries_only_the_agents_metadata(self):
        bare = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="hm")
        with_metadata = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="hm", metadata={"step": 2})

        assert not bare.artifact.HasField("metadata")
        assert not bare.HasField("metadata")
        assert MessageToDict(with_metadata.artifact.metadata) == {"step": 2.0}


class TestIsStreamDeltaChunk:
    def test_a_chunk_is_one(self):
        assert is_stream_delta_chunk(_chunk("Hel", append=False))
        assert is_stream_delta_chunk(_chunk("lo"))

    def test_a_whole_section_is_not(self):
        assert not is_stream_delta_chunk(stream_delta_final(task_id=TASK_ID, context_id=CONTEXT_ID, text="Hello"))

    def test_another_artifact_or_event_is_not(self):
        thought = thinking_delta(task_id=TASK_ID, context_id=CONTEXT_ID, text="hm")
        status = TaskStatusUpdateEvent(
            task_id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_WORKING)
        )

        assert not is_stream_delta_chunk(thought)
        assert not is_stream_delta_chunk(status)
        assert not is_stream_delta_chunk(None)
