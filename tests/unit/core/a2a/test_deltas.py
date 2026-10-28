"""The events `aion.core.a2a.deltas` builds for the streamed answer and the agent's thoughts."""

from __future__ import annotations

from a2a.types import Artifact, Part, TaskArtifactUpdateEvent
from google.protobuf.json_format import MessageToDict

from aion.core.a2a.deltas import join_stream_delta, stream_delta_chunk, stream_delta_end, thinking_delta
from aion.core.a2a.enums import ArtifactId, ArtifactName
from aion.core.a2a.extensions.messaging import MessageActionPayload
from aion.core.constants.a2a import MESSAGING_EXTENSION_URI_V1, STREAM_DELTA_PAYLOAD_SCHEMA_V1

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"
SCHEMA_MARKER = {MESSAGING_EXTENSION_URI_V1: {"schema": STREAM_DELTA_PAYLOAD_SCHEMA_V1}}
ROUTING = MessageActionPayload(trajectory="reply", context_id="C1", reply_to_message_id="m-1")


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

    def test_a_chunk_the_framework_marks_last_ends_the_section(self):
        event = stream_delta_chunk(
            task_id=TASK_ID, context_id=CONTEXT_ID, parts=[Part(text="lo")], append=True, last_chunk=True
        )

        assert event.last_chunk is True

    def test_a_chunk_carries_only_the_agents_metadata(self):
        assert not _chunk("Hel").artifact.HasField("metadata")
        assert MessageToDict(_chunk("Hel", metadata={"model": "m-1"}).artifact.metadata) == {"model": "m-1"}

    def test_the_event_carries_the_payload_schema_marker(self):
        assert MessageToDict(_chunk("Hel").metadata) == SCHEMA_MARKER


class TestStreamDeltaEnd:
    def test_the_end_mark_is_one_empty_text_part_appended_as_the_last_chunk(self):
        event = stream_delta_end(task_id=TASK_ID, context_id=CONTEXT_ID)

        assert (event.task_id, event.context_id) == (TASK_ID, CONTEXT_ID)
        assert event.artifact.artifact_id == ArtifactId.STREAM_DELTA.value
        assert [part.WhichOneof("content") for part in event.artifact.parts] == ["text"]
        assert event.artifact.parts[0].text == ""
        assert (event.append, event.last_chunk) == (True, True)
        assert not event.artifact.HasField("metadata")
        assert MessageToDict(event.metadata) == SCHEMA_MARKER

    def test_the_empty_part_survives_the_wire(self):
        """An empty text part is still a text part in JSON, so the update is never partless."""
        wire = MessageToDict(stream_delta_end(task_id=TASK_ID, context_id=CONTEXT_ID))

        assert wire["artifact"]["parts"] == [{"text": ""}]


class TestRoutedSection:
    def test_a_routed_chunk_carries_the_target_after_its_content(self):
        event = stream_delta_chunk(
            task_id=TASK_ID, context_id=CONTEXT_ID, parts=[Part(text="Hel")], append=False, routing=ROUTING
        )

        assert event.artifact.parts[0].text == "Hel"
        assert MessageActionPayload.is_part(event.artifact.parts[1])
        assert list(event.artifact.extensions) == [MESSAGING_EXTENSION_URI_V1]
        assert MessageToDict(event.metadata) == SCHEMA_MARKER

    def test_without_a_target_a_section_declares_no_extension(self):
        assert list(_chunk("Hel", append=False).artifact.extensions) == []


class TestJoinStreamDelta:
    def test_the_text_of_every_chunk_becomes_one_part(self):
        artifact = Artifact(parts=[Part(text="Hel"), Part(text="lo, "), Part(text="world"), Part(text="")])

        join_stream_delta(artifact)

        assert [part.text for part in artifact.parts] == ["Hello, world"]

    def test_the_routing_target_follows_the_joined_text(self):
        artifact = Artifact(parts=[Part(text="Hel"), ROUTING.to_part(), Part(text="lo"), Part(text="")])

        join_stream_delta(artifact)

        assert artifact.parts[0].text == "Hello"
        assert [MessageActionPayload.is_part(part) for part in artifact.parts] == [False, True]

    def test_an_artifact_already_in_one_part_is_left_as_it_is(self):
        artifact = Artifact(parts=[Part(text="Hello"), ROUTING.to_part()])
        before = Artifact()
        before.CopyFrom(artifact)

        join_stream_delta(artifact)

        assert artifact == before


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
