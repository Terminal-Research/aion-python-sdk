import pytest
from unittest.mock import Mock, AsyncMock, patch
from a2a.server.context import ServerCallContext
from a2a.types import TaskState

from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.core.a2a import (
    ContextsList,
    Conversation,
    GetContextParams,
    GetContextsListParams,
    ConversationTaskStatus
)


# !! Test Data Factories !!
def create_test_conversation(context_id="test_ctx", state=TaskState.TASK_STATE_COMPLETED):
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
        task.status.state = TaskState.TASK_STATE_COMPLETED
        tasks.append(task)
    return tasks


# !! Base Fixtures !!

@pytest.fixture
def mock_context():
    """Create mock server call context."""
    return Mock(spec=ServerCallContext)


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only."""
    return "asyncio"


@pytest.fixture
def mock_task_store():
    """Create mock async task store."""
    return AsyncMock()


# !! Composite Fixtures !!

@pytest.fixture
def request_handler():
    """Create request handler with mocked dependencies."""
    return AionRequestHandler(
        agent_executor=Mock(),
        task_store=Mock(),
        agent_card=Mock(),
    )


# !! Individual Mock Fixtures !!

@pytest.fixture
def mock_conversation_builder():
    """Mock ConversationBuilder."""
    with patch('aion.server.core.app.handlers.request_handler.ConversationBuilder') as mock:
        yield mock


@pytest.fixture
def mock_store_manager():
    """Mock store_manager."""
    with patch('aion.server.core.app.handlers.request_handler.store_manager') as mock:
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


# !! Request Handler Tests !!

class TestAionRequestHandler:
    """Unit tests for AionRequestHandler business logic methods."""

    @pytest.mark.anyio
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
    @pytest.mark.anyio
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

    @pytest.mark.anyio
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
    @pytest.mark.anyio
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

    @pytest.mark.anyio
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

    @pytest.mark.anyio
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


class TestVerifyDeclaredExtensions:
    """Extension declarations are verified from the request path, before any
    ActiveTask machinery: client mistakes must come back as InvalidParamsError
    (a WARNING-level request error), not as producer 'Execution failed'
    ERROR tracebacks."""

    @staticmethod
    def _call_context(requested=frozenset()):
        from types import SimpleNamespace
        return SimpleNamespace(requested_extensions=set(requested))

    @staticmethod
    def _handler_self():
        """Minimal stand-in for the handler instance (self is not consulted)."""
        from types import SimpleNamespace
        return SimpleNamespace()

    def _verify(self, params, call_context):
        AionRequestHandler._verify_declared_extensions(
            self._handler_self(), params, call_context
        )

    def test_declared_inactive_extension_rejected_as_invalid_params(self):
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        message = Message(message_id="m-1", role=Role.ROLE_USER)
        message.extensions.append(BEHAVIOUR_EVOLUTION_EXTENSION_URI_V1)
        params = SendMessageRequest(message=message)

        with pytest.raises(InvalidParamsError, match="not enabled for this agent"):
            self._verify(params, self._call_context())

    def test_header_declared_inactive_extension_rejected(self):
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))

        with pytest.raises(InvalidParamsError):
            self._verify(params, self._call_context({DAEMON_EXTENSION_URI_V1}))

    def test_request_without_declared_extensions_passes(self):
        from a2a.types import Message, Role, SendMessageRequest
        from aion.core.runtime import aion_a2a_extension_registry

        aion_a2a_extension_registry.reset_to_default()
        params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))

        self._verify(params, self._call_context())

    def test_enabled_extension_marked_unavailable_rejected(self):
        """The silent-fallback guard: an enabled extension marked unavailable
        in the registry (e.g. its toolkit is not installed) must reject the
        request with the recorded reason, not fall through to the primary graph."""
        from a2a.types import Message, Role, SendMessageRequest
        from a2a.utils.errors import InvalidParamsError
        from aion.core.constants.a2a import DAEMON_EXTENSION_URI_V1
        from aion.core.runtime import aion_a2a_extension_registry
        from google.protobuf.json_format import ParseDict

        aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
        aion_a2a_extension_registry.mark_unavailable(
            DAEMON_EXTENSION_URI_V1, "the daemon handler cannot run here"
        )
        try:
            params = SendMessageRequest(message=Message(message_id="m-1", role=Role.ROLE_USER))
            ParseDict({
                "daemonIdentity": {
                    "kind": "daemon",
                    "id": "daemon-1",
                    "networkType": "Aion",
                    "organizationId": "org-1",
                },
                "behavior": {"id": "b-1", "behaviorKey": "main", "versionId": "v-1"},
                "environment": {
                    "id": "env-1",
                    "name": "dev",
                    "projectId": "proj-1",
                    "deploymentId": "dep-1",
                    "configurationVariables": {},
                    "daemonAgentIdentityId": "daemon-1",
                },
            }, params.metadata.get_or_create_struct(DAEMON_EXTENSION_URI_V1))

            with pytest.raises(InvalidParamsError, match="daemon handler cannot run here"):
                self._verify(params, self._call_context())

            # Sanity: once availability is restored the same request passes.
            aion_a2a_extension_registry.reset_to_default()
            aion_a2a_extension_registry.activate([DAEMON_EXTENSION_URI_V1])
            self._verify(params, self._call_context())
        finally:
            aion_a2a_extension_registry.reset_to_default()
