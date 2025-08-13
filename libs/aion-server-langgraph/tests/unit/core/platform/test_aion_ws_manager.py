from unittest.mock import MagicMock, patch

import pytest

from aion.server.core.platform import AionWebSocketManager


class TestAionWebSocketManager:
    """Test cases for AionWebSocketManager critical functionality."""

    @pytest.mark.asyncio
    async def test_build_transport_no_token_raises_error(self, mock_aion_jwt_manager):
        """Test transport building fails when no token is available."""
        mock_aion_jwt_manager.get_token.return_value = None
        manager = AionWebSocketManager()

        with pytest.raises(ValueError, match="No token received from authentication"):
            await manager._build_transport()

    @pytest.mark.asyncio
    async def test_build_transport_success(self, mock_aion_api_settings, mock_jwt_manager):
        """Test successful transport building with valid token."""
        manager = AionWebSocketManager()

        with patch('aion.server.core.platform.aion_ws_manager.WebsocketsTransport') as mock_transport_class:
            mock_transport = MagicMock()
            mock_transport_class.return_value = mock_transport

            await manager._build_transport()

            # Verify correct URL with token
            mock_transport_class.assert_called_once_with(
                url="wss://example.com/graphql?token=test_token",
                ping_interval=30,
                subprotocols=["graphql-transport-ws"]
            )

    def test_is_connected_logic(self, ws_manager):
        """Test is_connected property logic for different states."""
        # Initially disconnected (returns None when no task)
        assert not ws_manager.is_connected

        # Set up connected state
        ws_manager._websocket_task = MagicMock()
        ws_manager._websocket_task.done.return_value = False
        ws_manager._connection_ready.set()
        assert ws_manager.is_connected is True

        # Shutdown state
        ws_manager._shutdown_event.set()
        assert ws_manager.is_connected is False

    @pytest.mark.asyncio
    async def test_stop_sets_shutdown_event(self, ws_manager):
        """Test stop method sets shutdown event."""
        await ws_manager.stop()
        assert ws_manager._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_run_ping_loop_handles_ping_failure(self, ws_manager, mock_ws_transport):
        """Test ping loop exits gracefully on ping failure."""
        mock_ws_transport.websocket.ping.side_effect = Exception("Ping failed")
        ws_manager._transport = mock_ws_transport

        await ws_manager._run_ping_loop()

        # Should attempt ping and then close transport
        mock_ws_transport.websocket.ping.assert_called_once()
        mock_ws_transport.close.assert_called_once()
