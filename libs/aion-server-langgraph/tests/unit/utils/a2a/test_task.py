from unittest.mock import Mock
import pytest
from a2a.types import Task, TaskState, TaskStatus
from aion.server.types import INTERRUPT_TASK_STATES
from aion.server.utils import check_if_task_is_interrupted


class TestCheckIfTaskIsInterrupted:
    """Tests for check_if_task_is_interrupted function."""

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

    def test_returns_false_for_non_task_objects(self):
        """Test function returns False for non-Task objects."""
        assert check_if_task_is_interrupted(None) is False
        assert check_if_task_is_interrupted("not a task") is False
        assert check_if_task_is_interrupted(123) is False
        assert check_if_task_is_interrupted({}) is False

    def test_returns_true_for_interrupted_task_states(self, mock_task):
        """Test function returns True when task state is in INTERRUPT_TASK_STATES."""
        for interrupt_state in INTERRUPT_TASK_STATES:
            mock_task.status.state = interrupt_state
            assert check_if_task_is_interrupted(mock_task) is True

    def test_returns_false_for_non_interrupted_task_states(self, mock_task):
        """Test function returns False when task state is not in INTERRUPT_TASK_STATES."""
        # Test with common non-interrupt states
        non_interrupt_states = [
            TaskState.completed,
            TaskState.failed
        ]

        for state in non_interrupt_states:
            if state not in INTERRUPT_TASK_STATES:
                mock_task.status.state = state
                assert check_if_task_is_interrupted(mock_task) is False

    def test_handles_task_without_status(self):
        """Test function handles task without proper status attribute."""
        task_without_status = Mock(spec=Task)
        task_without_status.status = None

        # This should raise an AttributeError when trying to access .state
        with pytest.raises(AttributeError):
            check_if_task_is_interrupted(task_without_status)

    def test_handles_status_without_state(self, mock_task):
        """Test function handles status without state attribute."""
        mock_task.status.state = None

        # Should return False since None is not in INTERRUPT_TASK_STATES
        assert check_if_task_is_interrupted(mock_task) is False
