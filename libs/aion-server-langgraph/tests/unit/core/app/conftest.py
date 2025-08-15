from unittest.mock import MagicMock, patch

import pytest
from a2a.types import AgentCard

from aion.server.core.app import AppConfig, AppFactory
from aion.server.langgraph.agent import BaseAgent


@pytest.fixture
def mock_config():
    """Mock AppConfig for testing."""
    config = MagicMock(spec=AppConfig)
    config.host = "localhost"
    config.port = 8000
    return config


@pytest.fixture
def mock_agent():
    """Mock BaseAgent for testing."""
    agent = MagicMock(spec=BaseAgent)
    agent.get_agent_card.return_value = MagicMock(spec=AgentCard)
    agent.get_compiled_graph.return_value = MagicMock()
    return agent


@pytest.fixture
def factory_with_agent(mock_config, mock_agent):
    """AppFactory instance with mocked agent for testing."""
    factory = AppFactory(mock_config)
    factory.agent = mock_agent
    return factory


@pytest.fixture
def mock_db_manager():
    with patch("aion.server.db.manager.db_manager") as db_manager:
        yield db_manager
