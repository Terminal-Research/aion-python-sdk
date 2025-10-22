import pytest
from unittest.mock import Mock, AsyncMock, patch
from a2a.server.context import ServerCallContext
from a2a.types import JSONRPCErrorResponse, InternalError, InvalidParamsError, TaskState
from a2a.utils.errors import ServerError

from aion.server.core.request_handlers import AionJSONRPCHandler, AionRequestHandler
from aion.shared.types import (
    GetContextRequest,
    GetContextsListRequest,
    GetContextResponse,
    GetContextSuccessResponse,
    GetContextsListResponse,
    GetContextsListSuccessResponse,
    ContextsList,
    Conversation,
    GetContextParams,
    GetContextsListParams,
    ConversationTaskStatus
)


# !! Test Data Factories !!
def create_test_conversation(context_id="test_ctx", state=TaskState.completed):
    """Factory function to create test conversation objects."""
    return Conversation(
        context_id=context_id,
        history=[],
        artifacts=[],
        status=ConversationTaskStatus(state=state)
    )


def create_test_tasks(count=2):
    """Factory function to create test task objects."""
    tasks = []
    for i in range(count):
        task = Mock()
        task.history = []
        task.artifacts = []
        task.status = Mock()
        task.status.state = TaskState.completed
        tasks.append(task)
    return tasks


# !! Base Fixtures !!

@pytest.fixture
def mock_context():
    """Create mock server call context."""
    return Mock(spec=ServerCallContext)


@pytest.fixture
def mock_agent_card():
    """Create mock agent card."""
    return Mock()


@pytest.fixture
def mock_request_handler():
    """Create mock request handler."""
    return AsyncMock()


@pytest.fixture
def mock_task_store():
    """Create mock async task store."""
    return AsyncMock()


# !! Composite Fixtures !!

@pytest.fixture
def json_rpc_handler(mock_agent_card, mock_request_handler):
    """Create JSON-RPC handler with mocked dependencies."""
    return AionJSONRPCHandler(
        agent_card=mock_agent_card,
        request_handler=mock_request_handler
    )


@pytest.fixture
def request_handler():
    """Create request handler with mocked dependencies."""
    return AionRequestHandler(
        agent_executor=Mock(),
        task_store=Mock()
    )


# !! Individual Mock Fixtures !!

@pytest.fixture
def mock_conversation_builder():
    """Mock ConversationBuilder."""
    with patch('aion.server.core.request_handlers.request_handler.ConversationBuilder') as mock:
        yield mock


@pytest.fixture
def mock_store_manager():
    """Mock store_manager."""
    with patch('aion.server.core.request_handlers.request_handler.store_manager') as mock:
        yield mock


@pytest.fixture
def configured_success_scenario(mock_conversation_builder, mock_store_manager, mock_task_store):
    """Pre-configured scenario for successful operations."""
    test_tasks = create_test_tasks(2)
    test_conversation = create_test_conversation("test_context_123")

    # Configure mocks
    mock_store_manager.get_store.return_value = mock_task_store
    mock_task_store.get_context_tasks.return_value = test_tasks
    mock_conversation_builder.build_from_tasks.return_value = test_conversation

    return {
        'conversation_builder': mock_conversation_builder,
        'store_manager': mock_store_manager,
        'task_store': mock_task_store,
        'expected_conversation': test_conversation,
        'test_tasks': test_tasks
    }


# !! JSON-RPC Handler Tests !!

class TestAionJSONRPCHandler:
    """Unit tests for AionJSONRPCHandler error handling and response formatting."""

    @pytest.mark.asyncio
    async def test_get_context_success(self, json_rpc_handler, mock_context):
        """Test successful context retrieval and response formatting."""
        # Setup
        request = GetContextRequest(
            id="test_request_123",
            params=GetContextParams(context_id="test_context_456")
        )
        mock_conversation = Mock(spec=Conversation)
        json_rpc_handler.request_handler.on_get_context = AsyncMock(return_value = mock_conversation)

        # Execute
        result = await json_rpc_handler.on_get_context(request, mock_context)

        # Verify
        assert isinstance(result, GetContextResponse)
        assert isinstance(result.root, GetContextSuccessResponse)
        assert result.root.id == request.id
        assert result.root.jsonrpc == "2.0"
        assert result.root.result == mock_conversation
        json_rpc_handler.request_handler.on_get_context.assert_called_once_with(
            request.params, mock_context
        )

    @pytest.mark.parametrize("error_type,expected_error", [
        (InvalidParamsError(message="Context not found"), InvalidParamsError),
        (InternalError(), InternalError),
    ])
    @pytest.mark.asyncio
    async def test_get_context_server_errors(self, json_rpc_handler, mock_context, error_type, expected_error):
        """Test handling of different ServerError types in get_context."""
        # Setup
        request = GetContextRequest(
            id="test_request_123",
            params=GetContextParams(context_id="test_context_456")
        )
        server_error = ServerError(error=error_type)
        json_rpc_handler.request_handler.on_get_context.side_effect = server_error

        # Execute
        result = await json_rpc_handler.on_get_context(request, mock_context)

        # Verify
        assert isinstance(result, GetContextResponse)
        assert isinstance(result.root, JSONRPCErrorResponse)
        assert result.root.id == request.id
        assert isinstance(result.root.error, expected_error)

    @pytest.mark.asyncio
    async def test_get_contexts_list_success(self, json_rpc_handler, mock_context):
        """Test successful contexts list retrieval and response formatting."""
        # Setup
        request = GetContextsListRequest(
            id="test_request_789",
            params=GetContextsListParams()
        )
        mock_context_ids = Mock(spec=ContextsList)
        json_rpc_handler.request_handler.on_get_contexts_list = AsyncMock(return_value = mock_context_ids)

        # Execute
        result = await json_rpc_handler.on_get_contexts_list(request, mock_context)

        # Verify
        assert isinstance(result, GetContextsListResponse)
        assert isinstance(result.root, GetContextsListSuccessResponse)
        assert result.root.id == request.id
        assert result.root.jsonrpc == "2.0"
        assert result.root.result == mock_context_ids
        json_rpc_handler.request_handler.on_get_contexts_list.assert_called_once_with(
            request.params, mock_context
        )

    @pytest.mark.asyncio
    async def test_get_contexts_list_server_error(self, json_rpc_handler, mock_context):
        """Test handling of ServerError in get_contexts_list."""
        # Setup
        request = GetContextsListRequest(
            id="test_request_789",
            params=GetContextsListParams()
        )
        custom_error = InvalidParamsError(message="Invalid parameters")
        server_error = ServerError(error=custom_error)
        json_rpc_handler.request_handler.on_get_contexts_list.side_effect = server_error

        # Execute
        result = await json_rpc_handler.on_get_contexts_list(request, mock_context)

        # Verify
        assert isinstance(result, GetContextsListResponse)
        assert isinstance(result.root, JSONRPCErrorResponse)
        assert result.root.id == request.id
        assert result.root.error == custom_error


# !! Request Handler Tests !!

class TestAionRequestHandler:
    """Unit tests for AionRequestHandler business logic methods."""

    @pytest.mark.asyncio
    async def test_get_context_success(self, request_handler, mock_context, configured_success_scenario):
        """Test successful context retrieval with proper data flow."""
        # Setup
        params = GetContextParams(
            context_id="test_context_123",
            history_length=50,
            history_offset=0
        )
        scenario = configured_success_scenario

        # Execute
        result = await request_handler.on_get_context(params, mock_context)

        # Verify
        assert result == scenario['expected_conversation']
        assert result.context_id == "test_context_123"
        scenario['task_store'].get_context_tasks.assert_called_once_with(
            context_id=params.context_id,
            limit=params.history_length,
            offset=params.history_offset
        )
        scenario['conversation_builder'].build_from_tasks.assert_called_once_with(
            context_id=params.context_id,
            tasks=scenario['test_tasks']
        )

    @pytest.mark.parametrize("history_length,history_offset", [
        (20, 10),
        (100, 0),
        (5, 25),
    ])
    @pytest.mark.asyncio
    async def test_get_context_custom_pagination(
            self,
            request_handler,
            mock_context,
            configured_success_scenario,
            history_length,
            history_offset
    ):
        """Test context retrieval with different pagination parameters."""
        # Setup
        params = GetContextParams(
            context_id="test_context_456",
            history_length=history_length,
            history_offset=history_offset
        )
        scenario = configured_success_scenario

        # Execute
        await request_handler.on_get_context(params, mock_context)

        # Verify pagination parameters
        scenario['task_store'].get_context_tasks.assert_called_once_with(
            context_id="test_context_456",
            limit=history_length,
            offset=history_offset
        )

    @pytest.mark.asyncio
    async def test_get_contexts_list_success(self, request_handler, mock_context, mock_store_manager, mock_task_store):
        """Test successful contexts list retrieval with proper data flow."""
        # Setup
        params = GetContextsListParams(history_length=100, history_offset=0)
        mock_context_ids_data = ["ctx_1", "ctx_2", "ctx_3"]

        mock_store_manager.get_store.return_value = mock_task_store
        mock_task_store.get_context_ids.return_value = mock_context_ids_data

        # Execute
        result = await request_handler.on_get_contexts_list(params, mock_context)

        # Verify
        assert isinstance(result, ContextsList)
        assert result.root == mock_context_ids_data
        mock_task_store.get_context_ids.assert_called_once_with(
            limit=params.history_length,
            offset=params.history_offset
        )

    @pytest.mark.parametrize("exception_msg,method_name", [
        ("Database error", "get_context_tasks"),
        ("Connection timeout", "get_context_ids"),
    ])
    @pytest.mark.asyncio
    async def test_store_error_propagation(
            self,
            request_handler,
            mock_context,
            mock_store_manager,
            mock_task_store,
            exception_msg,
            method_name
    ):
        """Test that store errors are properly propagated."""
        # Setup
        mock_store_manager.get_store.return_value = mock_task_store
        getattr(mock_task_store, method_name).side_effect = Exception(exception_msg)

        # Choose appropriate params and method based on test case
        if method_name == "get_context_tasks":
            params = GetContextParams(context_id="test_context")
            test_method = request_handler.on_get_context
        else:
            params = GetContextsListParams()
            test_method = request_handler.on_get_contexts_list

        # Execute & Verify
        with pytest.raises(Exception, match=exception_msg):
            await test_method(params, mock_context)

    @pytest.mark.asyncio
    async def test_get_context_empty_tasks(self, request_handler, mock_context, mock_conversation_builder,
                                           mock_store_manager, mock_task_store):
        """Test handling of empty task list."""
        # Setup
        params = GetContextParams(context_id="empty_context")
        empty_conversation = create_test_conversation("empty_context")

        mock_store_manager.get_store.return_value = mock_task_store
        mock_task_store.get_context_tasks.return_value = []
        mock_conversation_builder.build_from_tasks.return_value = empty_conversation

        # Execute
        result = await request_handler.on_get_context(params, mock_context)

        # Verify
        assert result.context_id == "empty_context"
        assert len(result.history) == 0
        mock_conversation_builder.build_from_tasks.assert_called_once_with(
            context_id="empty_context",
            tasks=[]
        )

    @pytest.mark.asyncio
    async def test_get_contexts_list_empty_result(self, request_handler, mock_context, mock_store_manager,
                                                  mock_task_store):
        """Test handling of empty contexts list."""
        # Setup
        params = GetContextsListParams()

        mock_store_manager.get_store.return_value = mock_task_store
        mock_task_store.get_context_ids.return_value = []

        # Execute
        result = await request_handler.on_get_contexts_list(params, mock_context)

        # Verify
        assert isinstance(result, ContextsList)
        assert result.root == []
