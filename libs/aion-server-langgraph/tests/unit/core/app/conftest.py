from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from a2a.types import AgentCard

from aion.server.core.app import AppFactory
from aion.server.core.app.factory import AppContext
from aion.server.langgraph.agent import BaseAgent
from aion.shared.aion_config import AgentConfig
from aion.server.db.manager import DbManager
from aion.server.tasks import StoreManager


@pytest.fixture
def mock_agent_config():
    """Mock AgentConfig for testing."""
    config = MagicMock(spec=AgentConfig)
    config.path = "test.agent.TestAgent"
    config.port = 8000
    config.name = "TestAgent"
    config.description = "Test agent"
    config.version = "1.0.0"
    return config


@pytest.fixture
def mock_agent():
    """Mock BaseAgent for testing."""
    agent = MagicMock(spec=BaseAgent)
    agent.card = MagicMock(spec=AgentCard)
    agent.get_compiled_graph.return_value = MagicMock()
    return agent


@pytest.fixture
def mock_db_manager():
    """Mock DbManager for testing."""
    with patch("aion.server.db.manager.db_manager") as db_manager:
        db_manager_mock = MagicMock(spec=DbManager)
        db_manager_mock.initialize.return_value = None
        db_manager_mock.close.return_value = None
        db_manager_mock.is_initialized = False
        yield db_manager_mock


@pytest.fixture
def mock_store_manager():
    """Mock StoreManager for testing."""
    store_manager = MagicMock(spec=StoreManager)
    store_manager.initialize.return_value = None
    store_manager.get_store.return_value = MagicMock()
    return store_manager


@pytest.fixture
def mock_app_context(mock_db_manager, mock_store_manager):
    """Mock AppContext for testing."""
    return AppContext(
        db_manager=mock_db_manager,
        store_manager=mock_store_manager
    )


@pytest.fixture
def factory_with_agent(mock_agent_config, mock_agent, mock_app_context):
    """AppFactory instance with mocked agent and context for testing."""
    factory = AppFactory(
        agent_id="test_agent",
        agent_config=mock_agent_config,
        context=mock_app_context,
        base_path=Path("/test/path")
    )
    factory.agent = mock_agent
    return factory


@pytest.fixture
def factory_without_agent(mock_agent_config, mock_app_context):
    """AppFactory instance without agent for testing initialization."""
    return AppFactory(
        agent_id="test_agent",
        agent_config=mock_agent_config,
        context=mock_app_context,
        base_path=Path("/test/path")
    )
