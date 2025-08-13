import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from gql.transport.websockets import WebsocketsTransport

from aion.server.core.platform import AionWebSocketManager

# Add package sources to sys.path for tests
PROJECT_ROOT = Path(__file__).resolve().parents[1]
src_path = PROJECT_ROOT / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Ensure local a2a SDK is discoverable if present
A2A_ROOT = PROJECT_ROOT.parent / "_a2a-python" / "src"
if A2A_ROOT.exists() and str(A2A_ROOT) not in sys.path:
    sys.path.insert(0, str(A2A_ROOT))


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocket manager instance for each test."""
    return AionWebSocketManager(ping_interval=0.1, reconnect_delay=0.1)


@pytest.fixture
def mock_ws_transport():
    """Create a mock WebSocket transport."""
    transport = AsyncMock(spec=WebsocketsTransport)
    transport.websocket = MagicMock()
    transport.websocket.ping = AsyncMock()
    return transport
