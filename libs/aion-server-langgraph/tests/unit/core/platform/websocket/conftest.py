from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_websocket_transport():
    """Mock WebSocket transport for testing."""
    transport = AsyncMock()
    transport.connect = AsyncMock()
    transport.close = AsyncMock()

    # Mock websocket attribute for ping functionality
    websocket_mock = AsyncMock()
    websocket_mock.ping = AsyncMock()
    transport.websocket = websocket_mock

    return transport


@pytest.fixture
def mock_ws_transport_factory(mock_websocket_transport):
    """Mock WebSocket transport factory."""
    factory = AsyncMock()
    factory.create_transport = AsyncMock(return_value=mock_websocket_transport)
    return factory


@pytest.fixture
def ws_manager(mock_ws_transport_factory):
    """Create AionWebSocketManager instance with mocked dependencies."""
    from aion.server.core.platform.websocket import AionWebSocketManager

    return AionWebSocketManager(
        ws_transport_factory=mock_ws_transport_factory,
        ping_interval=0.1,  # Short interval for faster tests
        reconnect_delay=0.1  # Short delay for faster tests
    )


@pytest.fixture
async def started_ws_manager(ws_manager):
    """Pre-started WebSocket manager for tests that need active connection."""
    await ws_manager.start()
    yield ws_manager
    await ws_manager.stop()
