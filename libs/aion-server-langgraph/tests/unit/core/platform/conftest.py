from unittest.mock import patch, AsyncMock

import pytest


@pytest.fixture
def mock_aion_jwt_manager():
    """Mock Aion JWT manager."""
    with patch('aion.server.core.platform.aion_ws_manager.aion_jwt_manager') as mock:
        mock.get_token = AsyncMock(return_value="test_token")
        yield mock


@pytest.fixture
def mock_aion_api_settings():
    """Create a mock Aion API settings."""
    with patch('aion.server.core.platform.aion_ws_manager.aion_api_settings') as mock_settings:
        mock_settings.ws_gql_url = "wss://example.com/graphql"
        mock_settings.keepalive = 30
        yield mock_settings


@pytest.fixture
def mock_jwt_manager():
    """Create a fresh JWT manager instance for each test."""
    with patch('aion.server.core.platform.aion_ws_manager.aion_jwt_manager') as mock_jwt_manager:
        mock_jwt_manager.get_token = AsyncMock(return_value="test_token")
        yield mock_jwt_manager
