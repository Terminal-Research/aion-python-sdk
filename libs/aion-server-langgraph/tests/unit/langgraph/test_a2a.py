from unittest.mock import Mock, AsyncMock, patch

import pytest
from a2a.types import Task, TaskState, InvalidParamsError
from a2a.utils.errors import ServerError

from aion.server.langgraph.a2a.agent import LanggraphAgent
from aion.server.langgraph.a2a.agent_executor import LanggraphAgentExecutor
from aion.server.langgraph.a2a.event_producer import LanggraphA2AEventProducer


class TestLanggraphAgentUnit:
    """Unit tests for LanggraphAgent core logic."""

    def test_get_action_config_structure(self):
        """Test session config generation - critical for thread management."""
        session_id = "test_session_123"
        config = LanggraphAgent._get_action_config(session_id)

        expected = {"configurable": {"thread_id": session_id}}
        assert config == expected

    def test_supported_content_types_defined(self):
        """Test that supported content types are properly defined."""
        content_types = LanggraphAgent.SUPPORTED_CONTENT_TYPES

        assert "text" in content_types
        assert "text/plain" in content_types
        assert isinstance(content_types, list)


class TestLanggraphAgentExecutorUnit:
    """Unit tests for LanggraphAgentExecutor task management logic."""

    @pytest.fixture
    def executor(self):
        """Create executor with mock graph."""
        mock_graph = Mock()
        return LanggraphAgentExecutor(mock_graph)

    @pytest.fixture
    def mock_context(self):
        """Create mock request context."""
        context = Mock()
        context.get_user_input.return_value = "test input"
        return context

    @pytest.mark.asyncio
    async def test_get_task_for_execution_with_interrupted_task(self, executor):
        """Test resuming interrupted tasks - critical business logic."""
        # Create mock interrupted task with proper status mock
        interrupted_task = Mock(spec=Task)
        mock_status = Mock()
        mock_status.state = TaskState.input_required
        interrupted_task.status = mock_status

        context = Mock()
        context.current_task = interrupted_task

        # Mock the interrupt check function
        with patch('aion.server.langgraph.a2a.agent_executor.check_if_task_is_interrupted', return_value=True):
            task, is_new = await executor._get_task_for_execution(context)

        assert task == interrupted_task
        assert is_new is False

    @pytest.mark.asyncio
    async def test_get_task_for_execution_with_terminal_task(self, executor):
        """Test handling of terminal state tasks - critical validation."""
        # Create mock terminal task with proper status mock
        terminal_task = Mock(spec=Task)
        terminal_task.id = "terminal_task_123"
        mock_status = Mock()
        mock_status.state = TaskState.completed  # Fixed: use 'completed' instead of 'complete'
        terminal_task.status = mock_status

        context = Mock()
        context.current_task = terminal_task

        # Mock the interrupt check function
        with patch('aion.server.langgraph.a2a.agent_executor.check_if_task_is_interrupted', return_value=False):
            with pytest.raises(ServerError) as exc_info:
                await executor._get_task_for_execution(context)

        # Verify it's the right type of error
        assert isinstance(exc_info.value.error, InvalidParamsError)

    @pytest.mark.asyncio
    async def test_get_task_for_execution_new_task(self, executor):
        """Test new task creation when no current task exists."""
        context = Mock()
        context.current_task = None
        context.message = Mock()

        with patch('aion.server.langgraph.a2a.agent_executor.new_task') as mock_new_task:
            mock_task = Mock(spec=Task)
            mock_new_task.return_value = mock_task

            task, is_new = await executor._get_task_for_execution(context)

        assert task == mock_task
        assert is_new is True
        mock_new_task.assert_called_once_with(context.message)


class TestLanggraphA2AEventProducerUnit:
    """Unit tests for LanggraphA2AEventProducer routing logic."""

    @pytest.fixture
    def event_producer(self):
        """Create event producer with mocks."""
        mock_queue = AsyncMock()
        mock_task = Mock(spec=Task)
        mock_task.id = "test_task_123"
        mock_task.context_id = "test_context_456"
        # Add artifacts property to the mock task
        mock_task.artifacts = []

        return LanggraphA2AEventProducer(mock_queue, mock_task)

    @pytest.mark.asyncio
    async def test_handle_event_invalid_type(self, event_producer):
        """Test handling of invalid event types - critical validation."""
        with pytest.raises(ValueError, match="Unhandled event"):
            await event_producer.handle_event("invalid_type", {})

    @pytest.mark.asyncio
    async def test_handle_event_valid_types_routing(self, event_producer):
        """Test that valid event types are routed correctly."""
        # Mock all the handler methods
        event_producer._stream_message = AsyncMock()
        event_producer._emit_langgraph_values = AsyncMock()
        event_producer._emit_langgraph_event = AsyncMock()
        event_producer._handle_interrupt = AsyncMock()
        event_producer._handle_complete = AsyncMock()

        # Test each valid event type routes to correct handler
        test_cases = [
            ("messages", "test_event", event_producer._stream_message),
            ("values", "test_event", event_producer._emit_langgraph_values),
            ("custom", "test_event", event_producer._emit_langgraph_event),
            ("interrupt", "test_event", event_producer._handle_interrupt),
            ("complete", "test_event", event_producer._handle_complete),
        ]

        for event_type, event_data, expected_handler in test_cases:
            await event_producer.handle_event(event_type, event_data)
            expected_handler.assert_called_once_with(event_data)
            expected_handler.reset_mock()

    def test_streaming_artifact_builder_caching(self, event_producer):
        """Test that streaming artifact builder is cached properly."""
        # First access should create the builder
        builder1 = event_producer.streaming_artifact_builder

        # Second access should return the same instance
        builder2 = event_producer.streaming_artifact_builder

        assert builder1 is builder2
        assert hasattr(event_producer, '_streaming_artifact_builder')

    @pytest.mark.asyncio
    async def test_handle_interrupt_with_empty_interrupts(self, event_producer):
        """Test interrupt handling with empty interrupts list."""
        event_producer.add_stream_artefact = AsyncMock()
        event_producer.updater = AsyncMock()

        # Should handle empty interrupts gracefully
        await event_producer._handle_interrupt([])

        # Should not call updater when no interrupts
        event_producer.updater.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_interrupt_with_dict_value_and_type(self, event_producer):
        """Test interrupt processing with dict value containing type."""
        event_producer.add_stream_artefact = AsyncMock()
        event_producer.updater = AsyncMock()

        # Mock the streaming artifact builder methods to avoid the artifacts dependency
        mock_builder = Mock()
        mock_builder.build_meta_complete_event.return_value = AsyncMock()
        event_producer._streaming_artifact_builder = mock_builder

        interrupt = Mock()
        interrupt.value = {
            "type": "input_required",
            "message": "User input needed"
        }

        await event_producer._handle_interrupt([interrupt])

        # Should parse TaskState from type field
        call_args = event_producer.updater.update_status.call_args
        assert call_args[0][0] == TaskState.input_required

    @pytest.mark.asyncio
    async def test_handle_interrupt_with_dict_value_invalid_type(self, event_producer):
        """Test interrupt processing with dict value containing invalid type."""
        event_producer.add_stream_artefact = AsyncMock()
        event_producer.updater = AsyncMock()

        # Mock the streaming artifact builder methods to avoid the artifacts dependency
        mock_builder = Mock()
        mock_builder.build_meta_complete_event.return_value = AsyncMock()
        event_producer._streaming_artifact_builder = mock_builder

        interrupt = Mock()
        interrupt.value = {
            "type": "invalid_state",
            "message": "Some message"
        }

        await event_producer._handle_interrupt([interrupt])

        # Should fallback to input_required for invalid TaskState
        call_args = event_producer.updater.update_status.call_args
        assert call_args[0][0] == TaskState.input_required

    @pytest.mark.asyncio
    async def test_handle_interrupt_with_string_value(self, event_producer):
        """Test interrupt processing with string value."""
        event_producer.add_stream_artefact = AsyncMock()
        event_producer.updater = AsyncMock()

        # Mock the streaming artifact builder methods to avoid the artifacts dependency
        mock_builder = Mock()
        mock_builder.build_meta_complete_event.return_value = AsyncMock()
        event_producer._streaming_artifact_builder = mock_builder

        interrupt = Mock()
        interrupt.value = "Simple string interrupt message"

        await event_producer._handle_interrupt([interrupt])

        # Should use input_required as default state
        call_args = event_producer.updater.update_status.call_args
        assert call_args[0][0] == TaskState.input_required
