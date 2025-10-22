from unittest.mock import Mock
import pytest
from a2a.types import Task, TaskState, Artifact, Message, TaskStatus
from aion.shared.types import ArtifactName, ConversationTaskStatus
from aion.server.utils import ConversationBuilder


class TestConversationBuilder:
    """Tests for ConversationBuilder class."""

    @pytest.fixture
    def mock_task_status(self):
        """Create a mock TaskStatus."""
        status = Mock(spec=TaskStatus)
        status.state = TaskState.completed
        status.message = None
        return status

    @pytest.fixture
    def mock_message(self):
        """Create a mock Message."""
        message = Mock(spec=Message)
        message.metadata = None
        return message

    @pytest.fixture
    def mock_artifact(self):
        """Create a mock Artifact."""
        artifact = Mock(spec=Artifact)
        artifact.name = "test_artifact"
        artifact.artifact_id = "test_id"
        return artifact

    def test_build_from_tasks_empty_list(self):
        """Test building conversation from empty task list."""
        result = ConversationBuilder.build_from_tasks("ctx_123", [])

        assert result.context_id == "ctx_123"
        assert result.history == []
        assert result.artifacts == []
        assert result.status.state == TaskState.unknown

    def test_build_from_tasks_with_tasks(self, mock_task_status, mock_message, mock_artifact):
        """Test building conversation from tasks with content."""
        task = Mock(spec=Task)
        task.history = [mock_message]
        task.artifacts = [mock_artifact]
        task.status = mock_task_status

        result = ConversationBuilder.build_from_tasks("ctx_123", [task])

        assert result.context_id == "ctx_123"
        assert len(result.history) == 1
        assert len(result.artifacts) == 1
        assert result.status.state == TaskState.completed

    def test_extract_messages_from_tasks_no_history(self):
        """Test extracting messages from tasks with no history."""
        task = Mock(spec=Task)
        task.history = None

        result = ConversationBuilder.extract_messages_from_tasks([task])

        assert result == []

    def test_extract_messages_from_tasks_with_metadata_filter(self, mock_task_status):
        """Test message extraction with metadata filtering."""
        # Message with no metadata - should be included
        msg1 = Mock(spec=Message)
        msg1.metadata = None

        # Message with message type - should be included
        msg2 = Mock(spec=Message)
        msg2.metadata = {"aion:message_type": "message"}

        # Message with other type - should be excluded
        msg3 = Mock(spec=Message)
        msg3.metadata = {"aion:message_type": "system"}

        # Task with result message - should be included
        result_message = Mock(spec=Message)
        mock_task_status.message = result_message

        task = Mock(spec=Task)
        task.history = [msg1, msg2, msg3]
        task.status = mock_task_status

        result = ConversationBuilder.extract_messages_from_tasks([task])

        # Should include msg1, msg2, and result_message (3 total)
        assert len(result) == 3
        assert msg1 in result
        assert msg2 in result
        assert result_message in result
        assert msg3 not in result

    def test_extract_messages_from_tasks_reverse_order(self, mock_task_status):
        """Test message extraction with reverse order."""
        msg1 = Mock(spec=Message)
        msg1.metadata = None
        msg2 = Mock(spec=Message)
        msg2.metadata = None

        mock_task_status.message = None

        task = Mock(spec=Task)
        task.history = [msg1, msg2]
        task.status = mock_task_status

        result = ConversationBuilder.extract_messages_from_tasks([task], reverse=True)

        # Should be in reverse order
        assert result[0] == msg2
        assert result[1] == msg1

    def test_extract_artifacts_from_tasks_no_artifacts(self):
        """Test extracting artifacts from tasks with no artifacts."""
        task = Mock(spec=Task)
        task.artifacts = None

        result = ConversationBuilder.extract_artifacts_from_tasks([task])

        assert result == []

    def test_extract_artifacts_from_tasks_filters_message_result(self, mock_artifact):
        """Test artifact extraction filters MESSAGE_RESULT artifacts."""
        # Regular artifact - should be included
        regular_artifact = Mock(spec=Artifact)
        regular_artifact.name = "regular"
        regular_artifact.artifact_id = "regular_id"

        # MESSAGE_RESULT artifact - should be filtered out
        message_result_artifact = Mock(spec=Artifact)
        message_result_artifact.name = ArtifactName.MESSAGE_RESULT.value
        message_result_artifact.artifact_id = "msg_result_id"

        task = Mock(spec=Task)
        task.artifacts = [regular_artifact, message_result_artifact]

        result = ConversationBuilder.extract_artifacts_from_tasks([task])

        assert len(result) == 1
        assert regular_artifact in result
        assert message_result_artifact not in result

    def test_extract_artifacts_from_tasks_deduplication(self):
        """Test artifact deduplication by artifact_id."""
        # Two artifacts with same ID - should deduplicate
        artifact1 = Mock(spec=Artifact)
        artifact1.name = "test1"
        artifact1.artifact_id = "same_id"

        artifact2 = Mock(spec=Artifact)
        artifact2.name = "test2"
        artifact2.artifact_id = "same_id"

        # Artifact without ID - should always be included
        artifact_no_id = Mock(spec=Artifact)
        artifact_no_id.name = "no_id"
        artifact_no_id.artifact_id = None

        task = Mock(spec=Task)
        task.artifacts = [artifact1, artifact2, artifact_no_id]

        result = ConversationBuilder.extract_artifacts_from_tasks([task])

        # Should include first artifact with ID and the one without ID
        assert len(result) == 2
        assert artifact1 in result
        assert artifact_no_id in result
        assert artifact2 not in result

    def test_extract_artifacts_from_tasks_fallback_to_id(self):
        """Test artifact extraction falls back to 'id' attribute if 'artifact_id' not present."""
        artifact = Mock(spec=Artifact)
        artifact.name = "test"
        artifact.artifact_id = None  # No artifact_id
        artifact.id = "fallback_id"  # But has id

        task = Mock(spec=Task)
        task.artifacts = [artifact]

        result = ConversationBuilder.extract_artifacts_from_tasks([task])

        assert len(result) == 1
        assert artifact in result

    def test_extract_artifacts_from_tasks_reverse_order(self):
        """Test artifact extraction with reverse order."""
        artifact1 = Mock(spec=Artifact)
        artifact1.name = "first"
        artifact1.artifact_id = "id1"

        artifact2 = Mock(spec=Artifact)
        artifact2.name = "second"
        artifact2.artifact_id = "id2"

        task = Mock(spec=Task)
        task.artifacts = [artifact1, artifact2]

        result = ConversationBuilder.extract_artifacts_from_tasks([task], reverse=True)

        # Should be in reverse order
        result_list = list(result)
        assert result_list[0] == artifact2
        assert result_list[1] == artifact1
