from unittest.mock import Mock
import pytest
from a2a.types import Task, TaskState, TaskStatus
from aion.server.a2a.constants import INTERRUPT_TASK_STATES
from aion.server.a2a.utils import is_task_interrupted


class TestCheckIfTaskIsInterrupted:
    """Tests for is_task_interrupted function."""

    @pytest.fixture
    def mock_task_status(self):
        """Create a mock TaskStatus."""
        return Mock(spec=TaskStatus)

    @pytest.fixture
    def mock_task(self, mock_task_status):
        """Create a mock Task with status."""
        task = Mock(spec=Task)
        task.status = mock_task_status
        return task

    def test_raises_for_non_task_objects(self):
        """Test function raises TypeError for non-Task objects."""
        for value in (None, "not a task", 123, {}):
            with pytest.raises(TypeError):
                is_task_interrupted(value)

    def test_returns_true_for_interrupted_task_states(self, mock_task):
        """Test function returns True when task state is in INTERRUPT_TASK_STATES."""
        for interrupt_state in INTERRUPT_TASK_STATES:
            mock_task.status.state = interrupt_state
            assert is_task_interrupted(mock_task) is True

    def test_returns_false_for_non_interrupted_task_states(self, mock_task):
        """Test function returns False when task state is not in INTERRUPT_TASK_STATES."""
        # Test with common non-interrupt states
        non_interrupt_states = [
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_WORKING,
        ]

        for state in non_interrupt_states:
            if state not in INTERRUPT_TASK_STATES:
                mock_task.status.state = state
                assert is_task_interrupted(mock_task) is False

    def test_handles_task_without_status(self):
        """Test function handles task without proper status attribute."""
        task_without_status = Mock(spec=Task)
        task_without_status.status = None

        # This should raise an AttributeError when trying to access .state
        with pytest.raises(AttributeError):
            is_task_interrupted(task_without_status)

    def test_handles_status_without_state(self, mock_task):
        """Test function handles status without state attribute."""
        mock_task.status.state = None

        # Should return False since None is not in INTERRUPT_TASK_STATES
        assert is_task_interrupted(mock_task) is False
