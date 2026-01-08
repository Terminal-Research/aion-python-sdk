from unittest.mock import Mock

import pytest
from a2a.types import Artifact, Task, Part, TextPart
from langchain_core.messages import AIMessageChunk, AIMessage

from aion.shared.types import ArtifactName, ArtifactStreamingStatus, ArtifactStreamingStatusReason
from aion.server.utils import StreamingArtifactBuilder, StreamingArtifactBuilderPartMode


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
    def builder_concatenated(self, mock_task):
        return StreamingArtifactBuilder(
            task=mock_task,
            part_mode=StreamingArtifactBuilderPartMode.CONCATENATED
        )

    @pytest.fixture
    def builder_separated(self, mock_task):
        return StreamingArtifactBuilder(
            task=mock_task,
            part_mode=StreamingArtifactBuilderPartMode.SEPARATED
        )

    def test_concatenated_mode_accumulates_content(self, builder_concatenated):
        """CONCATENATED mode should accumulate content in single part."""
        # First chunk
        event1 = builder_concatenated.build_streaming_chunk_event("Hello")
        assert event1.artifact.parts[0].root.text == "Hello"

        # Simulate adding artifact to task
        builder_concatenated.task.artifacts.append(event1.artifact)

        # Second chunk - should accumulate content
        event2 = builder_concatenated.build_streaming_chunk_event(" world")
        assert event2.artifact.parts[0].root.text == "Hello world"
        assert event2.append is False  # Replaces, doesn't append

    def test_separated_mode_appends_parts(self, builder_separated):
        """SEPARATED mode should append each chunk as separate part."""
        # First chunk
        event1 = builder_separated.build_streaming_chunk_event("Hello")
        assert event1.append is False

        # Simulate existing artifact with proper metadata
        existing_artifact = Mock(spec=Artifact)
        existing_artifact.artifact_id = ArtifactName.STREAM_DELTA.value
        existing_artifact.metadata = {"status": ArtifactStreamingStatus.ACTIVE.value}
        builder_separated.task.artifacts.append(existing_artifact)

        # Second chunk - should append
        event2 = builder_separated.build_streaming_chunk_event(" world")
        assert event2.append is True

    def test_inactive_artifact_replacement(self, builder_concatenated):
        """Inactive artifact should be replaced with new one."""
        inactive_artifact = Mock(spec=Artifact)
        inactive_artifact.artifact_id = ArtifactName.STREAM_DELTA.value
        inactive_artifact.metadata = {"status": ArtifactStreamingStatus.FINALIZED.value}
        builder_concatenated.task.artifacts = [inactive_artifact]

        # Should return None, indicating new artifact creation
        result = builder_concatenated.get_existing_streaming_artifact(active_only=True)
        assert result is None

    def test_content_extraction_edge_cases(self, builder_concatenated):
        """Handle edge cases in content extraction."""
        # None artifact
        assert builder_concatenated._extract_content_from_artifact(None) == ""

        # Part without text attribute - need to ensure hasattr returns False
        part_without_text = Mock()
        part_without_text.root = Mock(spec=[])  # Mock without 'text' attribute
        artifact = Mock(parts=[part_without_text])
        assert builder_concatenated._extract_content_from_artifact(artifact) == ""

    def test_finalized_event_handling(self, builder_concatenated):
        """Test finalization of existing artifacts with extra metadata."""
        # No existing artifact - should return None
        result = builder_concatenated.build_meta_complete_event()
        assert result is None

        # With existing artifact - should finalize it with extra metadata
        existing_artifact = Mock(spec=Artifact)
        existing_artifact.artifact_id = ArtifactName.STREAM_DELTA.value
        existing_artifact.metadata = {"status": ArtifactStreamingStatus.ACTIVE.value}
        existing_artifact.parts = [Part(root=TextPart(text="test content"))]
        builder_concatenated.task.artifacts = [existing_artifact]

        result = builder_concatenated.build_meta_complete_event(extra_metadata={"final": "true"})
        assert result is not None
        # Check that both standard and extra metadata are present
        assert result.artifact.metadata["status"] == ArtifactStreamingStatus.FINALIZED.value
        assert result.artifact.metadata["status_reason"] == ArtifactStreamingStatusReason.COMPLETE_MESSAGE.value
        assert result.artifact.metadata["final"] == "true"
        assert result.append is False
        assert result.last_chunk is True

    def test_meta_complete_event(self, builder_concatenated):
        """Test meta completion events."""
        # No existing artifact - should return None
        result = builder_concatenated.build_meta_complete_event()
        assert result is None

        # With existing artifact - should create completion event
        existing_artifact = Mock(spec=Artifact)
        existing_artifact.artifact_id = ArtifactName.STREAM_DELTA.value
        existing_artifact.metadata = {"status": ArtifactStreamingStatus.ACTIVE.value}
        existing_artifact.parts = [Part(root=TextPart(text="test content"))]
        builder_concatenated.task.artifacts = [existing_artifact]

        result = builder_concatenated.build_meta_complete_event(
            ArtifactStreamingStatusReason.COMPLETE_TASK
        )
        assert result is not None
        assert result.artifact.metadata["status"] == ArtifactStreamingStatus.FINALIZED.value
        assert result.artifact.metadata["status_reason"] == ArtifactStreamingStatusReason.COMPLETE_TASK.value
