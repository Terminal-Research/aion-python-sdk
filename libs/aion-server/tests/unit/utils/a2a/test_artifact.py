from unittest.mock import Mock

import pytest
from a2a.types import Task, Part, TextPart

from aion.shared.agent.adapters import MessageEvent
from aion.shared.types import ArtifactName
from aion.server.utils import StreamingArtifactBuilder


class TestStreamingArtifactBuilder:
    """Essential tests for StreamingArtifactBuilder."""

    @pytest.fixture
    def mock_task(self):
        task = Mock(spec=Task)
        task.id = "test-task-id"
        task.context_id = "test-context-id"
        task.artifacts = []
        return task

    @pytest.fixture
    def builder(self, mock_task):
        return StreamingArtifactBuilder(task=mock_task)

    def _create_message_event(self, text: str, is_chunk: bool = True, is_last_chunk: bool = False) -> MessageEvent:
        """Helper to create MessageEvent for testing."""
        return MessageEvent(
            content=[Part(root=TextPart(text=text))],
            is_chunk=is_chunk,
            is_last_chunk=is_last_chunk
        )

    def test_first_chunk_does_not_append(self, builder):
        """First chunk should not append since streaming hasn't started."""
        msg1 = self._create_message_event("Hello")
        event1 = builder.build_streaming_chunk_event(msg1)
        assert event1.append is False
        assert event1.artifact.parts[0].root.text == "Hello"

    def test_subsequent_chunks_append(self, builder):
        """Subsequent chunks should append after streaming has started."""
        # First chunk
        msg1 = self._create_message_event("Hello")
        event1 = builder.build_streaming_chunk_event(msg1)
        assert event1.append is False

        # Second chunk - should append
        msg2 = self._create_message_event(" world")
        event2 = builder.build_streaming_chunk_event(msg2)
        assert event2.append is True
        assert event2.artifact.parts[0].root.text == " world"

    def test_streaming_state_persists(self, builder):
        """Streaming state should persist across multiple chunks."""
        # First chunk
        msg1 = self._create_message_event("Hello")
        event1 = builder.build_streaming_chunk_event(msg1)
        assert event1.append is False

        # All subsequent chunks should append
        for i, text in enumerate([" world", "!", " How", " are", " you?"]):
            msg = self._create_message_event(text)
            event = builder.build_streaming_chunk_event(msg)
            assert event.append is True, f"Chunk {i+1} should append"

    def test_last_chunk_flag_from_message_event(self, builder):
        """Test that last_chunk flag is properly taken from MessageEvent."""
        # Regular chunk (not last)
        msg_chunk = self._create_message_event("Hello", is_chunk=True, is_last_chunk=False)
        event_chunk = builder.build_streaming_chunk_event(msg_chunk)
        assert event_chunk.last_chunk is False

        # Last chunk
        msg_last = self._create_message_event(" world", is_chunk=True, is_last_chunk=True)
        event_last = builder.build_streaming_chunk_event(msg_last)
        assert event_last.last_chunk is True

    def test_metadata_passed_through(self, builder):
        """Test that metadata is properly passed through to the artifact."""
        msg = self._create_message_event("Test")
        metadata = {"status": "active", "test_key": "test_value"}
        event = builder.build_streaming_chunk_event(msg, metadata=metadata)
        assert event.artifact.metadata == metadata
